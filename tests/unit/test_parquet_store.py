"""Unit tests for ParquetBarStore and bar invariants."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from honba_data.domain.models import Bar, InstrumentId
from honba_data.store.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba_data.store.parquet import ParquetBarStore, validate_bar


def test_validate_bar_valid() -> None:
    inst = InstrumentId("TCS", "NSE")
    bar = Bar(
        instrument_id=inst,
        ts=1_700_000_000_000_000_000,
        open=3500.0,
        high=3550.0,
        low=3490.0,
        close=3520.0,
        volume=1000.0,
    )
    assert validate_bar(bar) is True


def test_validate_bar_invariants() -> None:
    inst = InstrumentId("TCS", "NSE")

    # High < Low
    with pytest.raises(ValueError, match="high must be >= low"):
        validate_bar(
            Bar(
                instrument_id=inst,
                ts=100,
                open=100.0,
                high=90.0,
                low=95.0,
                close=92.0,
                volume=10.0,
            )
        )

    # Open > High
    with pytest.raises(ValueError, match="open must be between"):
        validate_bar(
            Bar(
                instrument_id=inst,
                ts=100,
                open=120.0,
                high=110.0,
                low=90.0,
                close=100.0,
                volume=10.0,
            )
        )

    # Close < Low
    with pytest.raises(ValueError, match="close must be between"):
        validate_bar(
            Bar(
                instrument_id=inst,
                ts=100,
                open=100.0,
                high=110.0,
                low=90.0,
                close=80.0,
                volume=10.0,
            )
        )

    # Negative volume
    with pytest.raises(ValueError, match="volume must be >= 0"):
        validate_bar(
            Bar(
                instrument_id=inst,
                ts=100,
                open=100.0,
                high=110.0,
                low=90.0,
                close=100.0,
                volume=-1.0,
            )
        )

    # Non-finite price
    with pytest.raises(ValueError, match="must be finite"):
        validate_bar(
            Bar(
                instrument_id=inst,
                ts=100,
                open=float("nan"),
                high=110.0,
                low=90.0,
                close=100.0,
                volume=10.0,
            )
        )


def test_parquet_store_roundtrip(tmp_path: Path) -> None:
    store = ParquetBarStore(data_dir=tmp_path)
    inst = InstrumentId("INFY", "NSE")
    timeframe = "1D"

    # Create 3 daily bars in 2025
    base_ts = 1_735_689_600_000_000_000  # 2025-01-01 00:00:00 UTC
    ns_per_day = 86_400 * 1_000_000_000

    bars = [
        Bar(
            instrument_id=inst,
            ts=base_ts + i * ns_per_day,
            open=1500.0 + i,
            high=1520.0 + i,
            low=1490.0 + i,
            close=1510.0 + i,
            volume=50_000.0,
        )
        for i in range(3)
    ]

    record = CoverageRecord(
        exchange="NSE",
        symbol="INFY",
        timeframe=timeframe,
        interval=DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 4)),
        status=CoverageStatus.FINAL,
        source="test",
        row_count=3,
        fetched_at_ns=base_ts,
    )

    # Append
    store.append(record, bars)

    # Verify coverage ledger was updated
    cov = store.coverage(inst, timeframe)
    assert len(cov) == 1
    assert cov[0].symbol == "INFY"
    assert cov[0].interval.start == dt.date(2025, 1, 1)
    assert cov[0].interval.end == dt.date(2025, 1, 4)

    # Read bars back
    read_bars = store.read(inst, timeframe, DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 4)))
    assert len(read_bars) == 3
    assert read_bars[0].open == 1500.0
    assert read_bars[1].open == 1501.0
    assert read_bars[2].open == 1502.0

    # Query sub-interval
    sub_bars = store.read(inst, timeframe, DateInterval(dt.date(2025, 1, 2), dt.date(2025, 1, 3)))
    assert len(sub_bars) == 1
    assert sub_bars[0].open == 1501.0
