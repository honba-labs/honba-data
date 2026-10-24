from __future__ import annotations

import datetime as dt
import json
import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from honba_data.domain.models import Bar, InstrumentId, as_bar, as_instrument_id
from honba_data.store.coverage import (
    BAR_SCHEMA,
    CoverageRecord,
    CoverageStatus,
    DateInterval,
)
from honba_data.store.fundamentals import find_data_root

logger = logging.getLogger(__name__)

_EPOCH = dt.date(1970, 1, 1)
_NS_PER_DAY = 86_400 * 1_000_000_000


def _utc_midnight_ns(day: dt.date) -> int:
    """Unix nanoseconds of ``day`` 00:00 UTC (exact integer arithmetic, no local timezone)."""
    return (day - _EPOCH).days * _NS_PER_DAY


def validate_bar(bar: Bar | Any) -> bool:
    """Validate bar invariants (finite prices/volume, low <= open/close <= high, volume >= 0)."""
    for val, name in (
        (bar.open, "open"),
        (bar.high, "high"),
        (bar.low, "low"),
        (bar.close, "close"),
        (bar.volume, "volume"),
    ):
        if not math.isfinite(val):
            raise ValueError(f"bar {name} must be finite, got {val}")

    if bar.low > bar.high:
        raise ValueError(f"high must be >= low, got high={bar.high}, low={bar.low}")

    if not (bar.low <= bar.open <= bar.high):
        raise ValueError(
            f"open must be between low and high, got open={bar.open}, high={bar.high}, low={bar.low}"
        )

    if not (bar.low <= bar.close <= bar.high):
        raise ValueError(
            f"close must be between low and high, got close={bar.close}, high={bar.high}, low={bar.low}"
        )

    if bar.volume < 0:
        raise ValueError(f"volume must be >= 0, got {bar.volume}")

    return True


class ParquetBarStore:
    """Persistent BarStore backed by Parquet files and a coverage ledger."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        """``data_dir`` defaults to :func:`find_data_root`, resolved once here."""
        self.data_dir = Path(data_dir if data_dir is not None else find_data_root()).resolve()
        self.catalog_dir = self.data_dir / "catalog"
        self.ledger_file = self.data_dir / "coverage_ledger.json"

    def _load_ledger(self) -> dict[str, list[dict[str, Any]]]:
        if not self.ledger_file.exists():
            return {}
        try:
            raw = json.loads(self.ledger_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, list):
                # Legacy list format -> convert to dict
                result: dict[str, list[dict[str, Any]]] = {}
                for item in raw:
                    key = f"{item.get('exchange', 'NSE').upper()}:{item.get('symbol', '').upper()}:{item.get('timeframe', '1D').upper()}"
                    result.setdefault(key, []).append(item)
                return result
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read ledger %s: %s", self.ledger_file, exc)
            return {}

    def _save_ledger(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.ledger_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_file.replace(self.ledger_file)

    def coverage(self, instrument: InstrumentId | Any, timeframe: str) -> list[CoverageRecord]:
        inst = as_instrument_id(instrument)
        key = f"{inst.exchange.upper()}:{inst.symbol.upper()}:{timeframe.upper()}"
        ledger = self._load_ledger()
        raw_records = ledger.get(key, [])
        records: list[CoverageRecord] = []
        for r in raw_records:
            records.append(
                CoverageRecord(
                    exchange=str(r.get("venue") or r.get("exchange") or "NSE"),
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    interval=DateInterval(
                        dt.date.fromisoformat(r["start"]),
                        dt.date.fromisoformat(r["end"]),
                    ),
                    status=CoverageStatus(r["status"]),
                    source=r.get("source", "unknown"),
                    row_count=r.get("row_count", 0),
                    checksum=r.get("checksum"),
                    fetched_at_ns=r.get("fetched_at_ns", 0),
                )
            )
        return records

    def _file_path(self, instrument: InstrumentId | Any, timeframe: str, year: int) -> Path:
        inst = as_instrument_id(instrument)
        return (
            self.catalog_dir
            / timeframe.upper()
            / inst.exchange.upper()
            / inst.symbol.upper()
            / f"{year}.parquet"
        )

    def read(
        self, instrument: InstrumentId | Any, timeframe: str, interval: DateInterval
    ) -> list[Bar]:
        """Read and deduplicate bars from year-partitioned parquet files.

        ``interval`` is a half-open range of UTC calendar dates: the window is
        ``[start 00:00 UTC, end 00:00 UTC)`` whatever the machine's local timezone.
        """
        inst = as_instrument_id(instrument)
        start_year = interval.start.year
        end_year = (
            (interval.end - dt.timedelta(days=1)).year
            if interval.end > interval.start
            else interval.start.year
        )

        start_ns = _utc_midnight_ns(interval.start)
        end_ns = _utc_midnight_ns(interval.end)

        bars_by_ts: dict[int, Bar] = {}

        for y in range(start_year, end_year + 1):
            fpath = self._file_path(inst, timeframe, y)
            if not fpath.exists():
                continue
            try:
                table = pq.read_table(fpath)
                ts_arr = table.column("ts").to_pylist()
                op_arr = table.column("open").to_pylist()
                hi_arr = table.column("high").to_pylist()
                lo_arr = table.column("low").to_pylist()
                cl_arr = table.column("close").to_pylist()
                vo_arr = table.column("volume").to_pylist()

                for ts, op, hi, lo, cl, vo in zip(ts_arr, op_arr, hi_arr, lo_arr, cl_arr, vo_arr):
                    if start_ns <= ts < end_ns:
                        bars_by_ts[ts] = Bar(
                            instrument_id=inst,
                            ts=ts,
                            open=float(op),
                            high=float(hi),
                            low=float(lo),
                            close=float(cl),
                            volume=float(vo),
                        )
            except (OSError, pa.ArrowException) as exc:
                logger.error("Error reading %s: %s", fpath, exc)

        return [bars_by_ts[t] for t in sorted(bars_by_ts.keys())]

    def append(self, record: CoverageRecord, bars: Sequence[Bar | Any]) -> None:
        """Append bars into partitioned parquet files and update coverage ledger."""
        inst = as_instrument_id(InstrumentId(record.symbol, record.exchange))
        tf = record.timeframe.upper()

        # Group bars by year
        bars_by_year: dict[int, list[Bar]] = {}
        for raw_b in bars:
            b = as_bar(raw_b)
            validate_bar(b)
            ts = getattr(b, "ts", getattr(b, "timestamp", 0))
            bar_date = dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date()
            bars_by_year.setdefault(bar_date.year, []).append(b)

        # Write each year partition
        for year, year_bars in bars_by_year.items():
            fpath = self._file_path(inst, tf, year)
            fpath.parent.mkdir(parents=True, exist_ok=True)

            existing_bars: dict[int, Bar] = {}
            if fpath.exists():
                try:
                    table = pq.read_table(fpath)
                    for ts, op, hi, lo, cl, vo in zip(
                        table.column("ts").to_pylist(),
                        table.column("open").to_pylist(),
                        table.column("high").to_pylist(),
                        table.column("low").to_pylist(),
                        table.column("close").to_pylist(),
                        table.column("volume").to_pylist(),
                    ):
                        existing_bars[ts] = Bar(
                            instrument_id=inst,
                            ts=ts,
                            open=float(op),
                            high=float(hi),
                            low=float(lo),
                            close=float(cl),
                            volume=float(vo),
                        )
                except (OSError, pa.ArrowException) as exc:
                    logger.warning("Could not read existing parquet %s: %s", fpath, exc)

            for b in year_bars:
                ts = getattr(b, "ts", getattr(b, "timestamp", 0))
                existing_bars[ts] = b

            sorted_ts = sorted(existing_bars.keys())
            table = pa.Table.from_arrays(
                [
                    pa.array([existing_bars[t].ts for t in sorted_ts], type=pa.int64()),
                    pa.array([existing_bars[t].open for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].high for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].low for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].close for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].volume for t in sorted_ts], type=pa.float64()),
                ],
                schema=BAR_SCHEMA,
            )

            tmp_path = fpath.with_suffix(".tmp")
            pq.write_table(table, tmp_path, compression="snappy")
            tmp_path.replace(fpath)

        # Update ledger and merge contiguous/overlapping intervals
        key = f"{inst.exchange.upper()}:{inst.symbol.upper()}:{tf}"
        ledger = self._load_ledger()
        records_list = ledger.setdefault(key, [])
        records_list.append(
            {
                "exchange": record.exchange,
                "symbol": record.symbol,
                "timeframe": tf,
                "start": record.interval.start.isoformat(),
                "end": record.interval.end.isoformat(),
                "status": record.status.value,
                "source": record.source,
                "row_count": record.row_count,
                "checksum": record.checksum,
                "fetched_at_ns": record.fetched_at_ns,
            }
        )

        merged_records = self._merge_ledger_entries(records_list)
        ledger[key] = merged_records
        self._save_ledger(ledger)

    def _merge_ledger_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge adjacent and overlapping ledger entries having the same status and exchange/symbol/tf."""
        if not entries:
            return []

        sorted_entries = sorted(entries, key=lambda x: (x["start"], x["end"]))
        merged: list[dict[str, Any]] = [sorted_entries[0].copy()]

        for cur in sorted_entries[1:]:
            prev = merged[-1]
            prev_end = dt.date.fromisoformat(prev["end"])
            cur_start = dt.date.fromisoformat(cur["start"])
            cur_end = dt.date.fromisoformat(cur["end"])

            if prev.get("status") == cur.get("status") and cur_start <= prev_end:
                if cur_end > prev_end:
                    prev["end"] = cur["end"]
                prev["row_count"] = prev.get("row_count", 0) + cur.get("row_count", 0)
                prev["fetched_at_ns"] = max(
                    prev.get("fetched_at_ns", 0), cur.get("fetched_at_ns", 0)
                )
            else:
                merged.append(cur.copy())

        return merged
