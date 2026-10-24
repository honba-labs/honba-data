"""Polling yfinance -> Honba ``POST /bars`` driver.

WARNING: yfinance is an UNOFFICIAL, DELAYED data source (scraped from Yahoo, typically
15+ minutes behind for Indian exchanges, rate limited, no SLA). Use this driver for
development and paper trading only, never for live capital.

RUNTIME & CONCURRENCY ISOLATION:
`honba-data` is an offline lakehouse system decoupled from `honba-ya`'s pinned-thread actor
runtime. Uncoordinated `ThreadPoolExecutor` execution in live trading paths is prohibited.

Behaviour:

* Only CLOSED bars are posted (``bar_start + interval <= now``); the forming bar is never sent.
* A per-instrument high-water mark guarantees each bar is posted once and ascending.
* All instruments in a poll are merged into one globally ascending stream and posted in
  batches of at most 1000 bars (the server's atomic-batch limit).
* 429 (``bar_ingest_full``): exponential backoff and retry; 503 (``bar_ingest_disabled``)
  and other rejections are fatal.
* Wire format: Honba ``Bar`` JSON. ``ts_event``/``ts_init`` are the bar START time (same
  convention as ``honba.data.loaders.yfinance``) in UTC ns. Naive yfinance timestamps for
  NSE/BSE are interpreted as IST explicitly, never as the machine's local zone.
* Pure logic takes ``now`` as an argument; the driver gets an injectable clock/sleep/fetch/http.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from honba_data.concurrency import verify_lakehouse_runtime

logger = logging.getLogger(__name__)

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")
MAX_BATCH = 1000
INDIA_EXCHANGES = frozenset({"NSE", "BSE"})
_UNIT = {"m": ("minute", 60), "h": ("hour", 3600), "d": ("day", 86400)}


class IngestFatalError(RuntimeError):
    """The server refused ingestion in a way retrying will not fix."""


@dataclass(frozen=True)
class Row:
    """One fetched OHLCV row; ``ts`` is the bar START (naive => exchange-local)."""

    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


FetchFn = Callable[[str, str], Sequence[Row]]
HttpPostFn = Callable[[str, str, dict[str, Any]], tuple[int, dict[str, Any]]]


# --------------------------------------------------------------------------- pure helpers


def parse_symbol(spec: str, default_exchange: str = "NSE") -> tuple[str, str]:
    """``RELIANCE.NSE`` -> ("RELIANCE", "NSE"); bare ``TCS`` -> ("TCS", "NSE")."""
    spec = spec.strip()
    if "." in spec:
        sym, exch = spec.rsplit(".", 1)
        if exch.upper() in {"NSE", "BSE", "NASDAQ", "NYSE"}:
            return sym, exch.upper()
    return spec, default_exchange


def to_yf_ticker(symbol: str, exchange: str) -> str:
    if symbol.startswith("^"):
        return symbol
    return {"NSE": f"{symbol}.NS", "BSE": f"{symbol}.BO"}.get(exchange.upper(), symbol)


def interval_to_spec(interval: str) -> tuple[int, str]:
    """``1m`` -> (1, "minute"); ``1h`` -> (1, "hour")."""
    m = re.fullmatch(r"(\d+)([mhd])", interval.strip().lower())
    if not m or int(m.group(1)) < 1:
        raise ValueError(f"Unsupported interval {interval!r}; use e.g. 1m, 5m, 15m, 1h")
    return int(m.group(1)), _UNIT[m.group(2)][0]


def interval_seconds(interval: str) -> int:
    step, _ = interval_to_spec(interval)
    return step * _UNIT[interval.strip().lower()[-1]][1]


def row_start_utc(ts: dt.datetime, exchange: str) -> dt.datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST if exchange.upper() in INDIA_EXCHANGES else UTC)
    return ts.astimezone(UTC)


def _unix_nanos(t: dt.datetime) -> dict[str, str]:
    ns = int(t.timestamp()) * 10**9 + t.microsecond * 1000
    iso = t.strftime("%Y-%m-%dT%H:%M:%S") + f".{ns % 10**9:09d}Z"
    return {"iso": iso, "unix_nanos": str(ns)}


def _valid(r: Row) -> bool:
    vals = (r.open, r.high, r.low, r.close, r.volume)
    return (
        all(math.isfinite(v) for v in vals)
        and r.volume >= 0
        and r.low <= min(r.open, r.close)
        and r.high >= max(r.open, r.close)
    )


def row_to_bar(row: Row, symbol: str, exchange: str, interval: str) -> dict[str, Any]:
    """Convert a row to Honba ``Bar`` wire JSON."""
    step, agg = interval_to_spec(interval)
    t = _unix_nanos(row_start_utc(row.ts, exchange))
    return {
        "bar_type": {
            "instrument_id": {"symbol": symbol, "exchange": exchange},
            "spec": {"step": step, "aggregation": agg, "price_type": "last"},
        },
        "open": float(row.open),
        "high": float(row.high),
        "low": float(row.low),
        "close": float(row.close),
        "volume": float(row.volume),
        "ts_event": t,
        "ts_init": dict(t),
    }


def select_new_bars(
    rows: Sequence[Row],
    symbol: str,
    exchange: str,
    interval: str,
    now: dt.datetime,
    watermark_ns: int | None,
) -> list[dict[str, Any]]:
    """Closed, valid, de-duplicated bars strictly newer than ``watermark_ns``, ascending."""
    span = dt.timedelta(seconds=interval_seconds(interval))
    by_ts: dict[int, dict[str, Any]] = {}
    for r in rows:
        if not _valid(r) or row_start_utc(r.ts, exchange) + span > now:
            continue
        bar = row_to_bar(r, symbol, exchange, interval)
        ts = int(bar["ts_event"]["unix_nanos"])
        if watermark_ns is None or ts > watermark_ns:
            by_ts[ts] = bar
    return [by_ts[k] for k in sorted(by_ts)]


# --------------------------------------------------------------------------- I/O defaults


def default_http_post(url: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        return exc.code, body


def default_fetch(symbol_spec: str, interval: str) -> list[Row]:
    """Fetch recent bars from yfinance (delayed, unofficial)."""
    import yfinance as yf  # local import: heavy

    sym, exch = parse_symbol(symbol_spec)
    period = "1d" if interval_seconds(interval) < 3600 else "5d"
    df = yf.Ticker(to_yf_ticker(sym, exch)).history(period=period, interval=interval.lower())
    rows: list[Row] = []
    for idx, r in df.iterrows():
        ix: Any = idx
        v: Any = r
        rows.append(
            Row(
                ix.to_pydatetime(),
                float(v["Open"]),
                float(v["High"]),
                float(v["Low"]),
                float(v["Close"]),
                float(v["Volume"]),
            )
        )
    return rows


# --------------------------------------------------------------------------- driver


def _reason(body: dict[str, Any]) -> str:
    err = body.get("error") or {}
    return str((err.get("context") or {}).get("reason") or err.get("code") or "")


class YFinanceBarDriver:
    """Poll yfinance and POST newly closed bars to ``{server}/bars``. Dev/paper use only."""

    def __init__(
        self,
        symbols: Sequence[str],
        interval: str,
        server: str,
        token: str,
        *,
        fetch: FetchFn = default_fetch,
        http_post: HttpPostFn = default_http_post,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(UTC),
        sleep: Callable[[float], None] | None = None,
        poll_seconds: float = 60.0,
        backoff_seconds: float = 1.0,
        max_retries: int = 5,
        backfill: bool = False,
    ) -> None:
        verify_lakehouse_runtime()
        interval_to_spec(interval)
        self.symbols = list(symbols)
        self.interval = interval
        self.url = server.rstrip("/") + "/bars"
        self._token = token
        self._fetch = fetch
        self._post = http_post
        self._clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self.poll_seconds = poll_seconds
        self.backoff_seconds = backoff_seconds
        self.max_retries = max_retries
        self.backfill = backfill
        self._watermark: dict[tuple[str, str], int] = {}

    def __repr__(self) -> str:
        return f"YFinanceBarDriver(symbols={self.symbols!r}, interval={self.interval!r}, url={self.url!r})"

    def stop(self) -> None:
        self._stop.set()

    def _collect(self, now: dt.datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for spec in self.symbols:
            sym, exch = parse_symbol(spec)
            try:
                rows = self._fetch(spec, self.interval)
            except Exception as exc:  # network/Yahoo failures are transient
                logger.warning("fetch failed for %s: %s", spec, exc)
                continue
            key = (sym, exch)
            bars = select_new_bars(rows, sym, exch, self.interval, now, self._watermark.get(key))
            if not self.backfill and key not in self._watermark:
                if bars:
                    self._watermark[key] = int(bars[-1]["ts_event"]["unix_nanos"])
                continue
            out.extend(bars)
        out.sort(key=lambda b: int(b["ts_event"]["unix_nanos"]))
        return out

    def _send(self, batch: list[dict[str, Any]]) -> bool:
        """POST one batch. True if accepted, False if gave up after 429s; raises if fatal."""
        for attempt in range(self.max_retries + 1):
            status, body = self._post(self.url, self._token, {"bars": batch})
            if status == 200:
                return True
            if status == 429:
                if attempt < self.max_retries:
                    delay = self.backoff_seconds * (2**attempt)
                    logger.warning("server busy (%s); retrying in %.1fs", _reason(body), delay)
                    self._sleep(delay)
                continue
            if status == 503:
                raise IngestFatalError(
                    f"server answered 503 ({_reason(body) or 'market_data_unavailable'}): "
                    "bar ingest is disabled; start it with `honba serve --bar-ingest`"
                )
            raise IngestFatalError(f"server rejected batch with HTTP {status}: {_reason(body)}")
        logger.warning("server still busy after %d retries; will retry next poll", self.max_retries)
        return False

    def poll_once(self) -> int:
        """Fetch, filter and post once. Returns the number of bars accepted."""
        bars = self._collect(self._clock())
        accepted = 0
        for i in range(0, len(bars), MAX_BATCH):
            batch = bars[i : i + MAX_BATCH]
            if not self._send(batch):
                break
            for b in batch:
                iid = b["bar_type"]["instrument_id"]
                self._watermark[(iid["symbol"], iid["exchange"])] = int(b["ts_event"]["unix_nanos"])
            accepted += len(batch)
        if accepted:
            logger.info("posted %d bars", accepted)
        return accepted

    def run(self) -> None:
        verify_lakehouse_runtime()
        while not self._stop.is_set():
            self.poll_once()
            if self._stop.is_set():
                break
            self._sleep(self.poll_seconds)
