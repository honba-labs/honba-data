"""Tests for the yfinance -> Honba POST /bars polling driver (no network)."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

import pytest
from typer.testing import CliRunner

from honba_data.live import yfinance_driver as yd

UTC = dt.timezone.utc
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T0 = dt.datetime(2026, 1, 5, 4, 0, tzinfo=UTC)  # 09:30 IST


def row(minute: int, px: float = 100.0) -> yd.Row:
    return yd.Row(T0 + dt.timedelta(minutes=minute), px, px + 1, px - 1, px + 0.5, 10.0)


class Clock:
    def __init__(self, now: dt.datetime) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> dt.datetime:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)


class FakeHttp:
    def __init__(self, responses: Sequence[tuple[int, dict[str, Any]]] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, url: str, token: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, token, payload))
        if self.responses:
            return self.responses.pop(0)
        return 200, {"data": {"accepted": len(payload["bars"])}}


def make(rows_by_symbol, http, clock, **kw):
    kw.setdefault("backfill", True)
    return yd.YFinanceBarDriver(
        symbols=list(rows_by_symbol),
        interval="1m",
        server="http://h:1",
        token="SECRET-TOKEN",
        fetch=lambda sym, interval: rows_by_symbol[sym],
        http_post=http,
        clock=clock,
        sleep=clock.sleep,
        **kw,
    )


def ts_of(bar: dict[str, Any]) -> int:
    return int(bar["ts_event"]["unix_nanos"])


def test_to_wire_bar_shape_and_ist_naive_conversion():
    naive = yd.Row(dt.datetime(2026, 1, 5, 9, 30), 1.0, 2.0, 0.5, 1.5, 7.0)
    bar = yd.row_to_bar(naive, "RELIANCE", "NSE", "1m")
    expected = int(dt.datetime(2026, 1, 5, 9, 30, tzinfo=IST).timestamp()) * 10**9
    assert ts_of(bar) == expected == ts_of({"ts_event": bar["ts_init"]})
    assert bar["ts_event"]["iso"].startswith("2026-01-05T04:00:00")
    assert bar["bar_type"] == {
        "instrument_id": {"symbol": "RELIANCE", "exchange": "NSE"},
        "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
    }
    assert (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]) == (
        1.0,
        2.0,
        0.5,
        1.5,
        7.0,
    )


def test_aware_timestamp_is_converted_to_utc():
    aware = yd.Row(dt.datetime(2026, 1, 5, 9, 30, tzinfo=IST), 1, 2, 0.5, 1.5, 7)
    assert ts_of(yd.row_to_bar(aware, "X", "NSE", "5m")) == int(T0.timestamp()) * 10**9
    assert yd.row_to_bar(aware, "X", "NSE", "5m")["bar_type"]["spec"]["step"] == 5


def test_instrument_helpers():
    assert yd.parse_symbol("RELIANCE.NSE") == ("RELIANCE", "NSE")
    assert yd.parse_symbol("TCS") == ("TCS", "NSE")
    assert yd.to_yf_ticker("RELIANCE", "NSE") == "RELIANCE.NS"
    assert yd.to_yf_ticker("TCS", "BSE") == "TCS.BO"
    assert yd.interval_to_spec("1h") == (1, "hour")
    assert yd.interval_to_spec("90m") == (90, "minute")
    with pytest.raises(ValueError):
        yd.interval_to_spec("3x")


def test_invalid_rows_are_dropped():
    bad = yd.Row(T0, float("nan"), 2, 1, 1.5, 1)
    inverted = yd.Row(T0 + dt.timedelta(minutes=1), 1, 0.5, 2, 1.5, 1)
    assert (
        yd.select_new_bars([bad, inverted], "X", "NSE", "1m", T0 + dt.timedelta(hours=1), None)
        == []
    )


def test_closed_bar_only():
    # bar at minute 2 closes at minute 3; now = minute 3 + 30s -> bars 0,1,2 closed
    now = T0 + dt.timedelta(minutes=3, seconds=30)
    rows = [row(i) for i in range(5)]  # 3 and 4 still forming/future
    bars = yd.select_new_bars(rows, "X", "NSE", "1m", now, None)
    assert [ts_of(b) for b in bars] == [
        int((T0 + dt.timedelta(minutes=i)).timestamp()) * 10**9 for i in range(3)
    ]
    # exactly at close boundary counts as closed
    assert len(yd.select_new_bars(rows, "X", "NSE", "1m", T0 + dt.timedelta(minutes=3), None)) == 3


def test_ordering_dedupe_and_watermark():
    now = T0 + dt.timedelta(minutes=10)
    rows = [row(3), row(1), row(2), row(2)]
    bars = yd.select_new_bars(rows, "X", "NSE", "1m", now, None)
    ts = [ts_of(b) for b in bars]
    assert ts == sorted(set(ts)) and len(ts) == 3
    after = yd.select_new_bars(rows, "X", "NSE", "1m", now, ts[1])
    assert [ts_of(b) for b in after] == [ts[2]]


def test_dedupe_across_polls_posts_each_bar_once():
    clock = Clock(T0 + dt.timedelta(minutes=3))
    data = {"A": [row(0), row(1), row(2)]}
    http = FakeHttp()
    d = make(data, http, clock)
    assert d.poll_once() == 3
    assert d.poll_once() == 0
    data["A"] = [row(0), row(1), row(2), row(3), row(4)]
    clock.now = T0 + dt.timedelta(minutes=5)
    assert d.poll_once() == 2
    posted = [b for _, _, p in http.calls for b in p["bars"]]
    assert len(posted) == 5 and len({ts_of(b) for b in posted}) == 5
    assert [ts_of(b) for b in posted] == sorted(ts_of(b) for b in posted)


def test_no_backfill_seeds_watermark_without_posting():
    clock = Clock(T0 + dt.timedelta(minutes=3))
    data = {"A": [row(0), row(1), row(2)]}
    http = FakeHttp()
    d = make(data, http, clock, backfill=False)
    assert d.poll_once() == 0 and http.calls == []
    data["A"].append(row(3))
    clock.now = T0 + dt.timedelta(minutes=4)
    assert d.poll_once() == 1


def test_multi_instrument_stream_is_globally_ascending():
    clock = Clock(T0 + dt.timedelta(minutes=10))
    http = FakeHttp()
    d = make({"A": [row(0), row(2)], "B.BSE": [row(1), row(3)]}, http, clock)
    d.poll_once()
    bars = [b for _, _, p in http.calls for b in p["bars"]]
    assert [ts_of(b) for b in bars] == sorted(ts_of(b) for b in bars)
    assert {b["bar_type"]["instrument_id"]["exchange"] for b in bars} == {"NSE", "BSE"}


def test_batching_at_1000():
    clock = Clock(T0 + dt.timedelta(minutes=3000))
    http = FakeHttp()
    d = make({"A": [row(i) for i in range(2300)]}, http, clock)
    assert d.poll_once() == 2300
    assert [len(p["bars"]) for _, _, p in http.calls] == [1000, 1000, 300]
    assert http.calls[0][0] == "http://h:1/bars"
    assert http.calls[0][1] == "SECRET-TOKEN"


def test_429_backs_off_then_succeeds_without_duplicates():
    clock = Clock(T0 + dt.timedelta(minutes=10))
    http = FakeHttp(
        [(429, {"error": {"code": "rate_limited", "context": {"reason": "bar_ingest_full"}}})] * 2
    )
    d = make({"A": [row(0), row(1)]}, http, clock, backoff_seconds=1.0)
    assert d.poll_once() == 2
    assert len(http.calls) == 3
    assert clock.sleeps == [1.0, 2.0]
    assert d.poll_once() == 0


def test_429_exhausted_keeps_watermark_and_retries_next_poll():
    clock = Clock(T0 + dt.timedelta(minutes=10))
    full: tuple[int, dict[str, Any]] = (429, {"error": {"code": "rate_limited"}})
    http = FakeHttp([full] * 4)
    d = make({"A": [row(0)]}, http, clock, max_retries=3)
    assert d.poll_once() == 0
    assert d.poll_once() == 1  # same bar re-sent and now accepted


def test_503_is_fatal_with_clear_message():
    clock = Clock(T0 + dt.timedelta(minutes=10))
    body = {
        "error": {"code": "market_data_unavailable", "context": {"reason": "bar_ingest_disabled"}}
    }
    d = make({"A": [row(0)]}, FakeHttp([(503, body)]), clock)
    with pytest.raises(yd.IngestFatalError, match="--bar-ingest"):
        d.poll_once()


@pytest.mark.parametrize("status", [401, 403, 422])
def test_other_rejections_are_fatal(status):
    clock = Clock(T0 + dt.timedelta(minutes=10))
    d = make({"A": [row(0)]}, FakeHttp([(status, {"error": {"code": "x"}})]), clock)
    with pytest.raises(yd.IngestFatalError, match=str(status)):
        d.poll_once()


def test_fetch_error_does_not_kill_driver():
    clock = Clock(T0 + dt.timedelta(minutes=10))
    calls = {"n": 0}

    def fetch(sym, interval):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("yahoo down")
        return [row(0)]

    d = yd.YFinanceBarDriver(
        symbols=["A"],
        interval="1m",
        server="http://h",
        token="t",
        fetch=fetch,
        http_post=FakeHttp(),
        clock=clock,
        sleep=clock.sleep,
        backfill=True,
    )
    assert d.poll_once() == 0
    assert d.poll_once() == 1


def test_run_stops_gracefully_and_sleeps_poll_interval():
    clock = Clock(T0 + dt.timedelta(minutes=10))
    d = make({"A": [row(0)]}, FakeHttp(), clock, poll_seconds=30)
    polls = {"n": 0}
    orig = d.poll_once

    def counting():
        polls["n"] += 1
        if polls["n"] == 3:
            d.stop()
        return orig()

    d.poll_once = counting  # type: ignore[method-assign]
    d.run()
    assert polls["n"] == 3
    assert clock.sleeps[:2] == [30, 30]


def test_token_never_logged(caplog):
    clock = Clock(T0 + dt.timedelta(minutes=10))
    http = FakeHttp([(429, {"error": {}}), (200, {"data": {"accepted": 1}})])
    with caplog.at_level(logging.DEBUG):
        d = make({"A": [row(0)]}, http, clock)
        d.poll_once()
    assert "SECRET-TOKEN" not in caplog.text
    assert "SECRET-TOKEN" not in repr(d)


def test_default_http_post_sends_bearer(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        def read(self):
            return b'{"data": {"accepted": 1}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        return Resp()

    monkeypatch.setattr(yd.urllib.request, "urlopen", fake_urlopen)
    status, body = yd.default_http_post("http://h/bars", "tok", {"bars": []})
    assert (status, body["data"]["accepted"]) == (200, 1)
    assert seen == {"auth": "Bearer tok", "url": "http://h/bars"}


def test_cli_wiring(monkeypatch):
    from honba_data.cli.main import app

    captured = {}

    class FakeDriver:
        def __init__(self, **kw):
            captured.update(kw)

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(yd, "YFinanceBarDriver", FakeDriver)
    monkeypatch.setenv("HONBA_TOKEN", "envtok")
    res = CliRunner().invoke(
        app,
        [
            "live",
            "yfinance",
            "--symbols",
            "RELIANCE,TCS.BSE",
            "--interval",
            "5m",
            "--server",
            "http://x:9",
            "--poll-seconds",
            "7",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured["symbols"] == ["RELIANCE", "TCS.BSE"]
    assert captured["token"] == "envtok" and "envtok" not in res.output
    assert captured["interval"] == "5m" and captured["poll_seconds"] == 7
    assert captured["ran"] is True
    assert "delayed" in CliRunner().invoke(app, ["live", "yfinance", "--help"]).output.lower()


def test_cli_missing_token_env_fails(monkeypatch):
    from honba_data.cli.main import app

    monkeypatch.delenv("HONBA_TOKEN", raising=False)
    res = CliRunner().invoke(app, ["live", "yfinance", "--symbols", "A", "--server", "http://x"])
    assert res.exit_code != 0
