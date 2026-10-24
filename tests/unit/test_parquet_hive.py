"""Unit tests for ParquetBarStore Hive-partitioning alignment."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from honba_data.domain.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba_data.domain.models import Bar, InstrumentId
from honba_data.store.coverage import BAR_SCHEMA
from honba_data.store.parquet import ParquetBarStore, validate_bar


def test_parquet_store_writes_hive_partitions(tmp_path: Path) -> None:
    store = ParquetBarStore(data_dir=tmp_path, use_hive_partitions=True)
    inst = InstrumentId("RELIANCE", "NSE")
    timeframe = "1D"

    base_ts = 1_735_689_600_000_000_000  # 2025-01-01 UTC
    bar = Bar(
        instrument_id=inst,
        ts=base_ts,
        open=2500.0,
        high=2550.0,
        low=2480.0,
        close=2520.0,
        volume=100_000.0,
    )
    rec = CoverageRecord(
        exchange="NSE",
        symbol="RELIANCE",
        timeframe=timeframe,
        interval=DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2)),
        status=CoverageStatus.FINAL,
        source="test",
        row_count=1,
    )

    store.append(rec, [bar])

    # Check Hive partitioned file exists
    expected_hive_path = (
        tmp_path
        / "catalog"
        / "timeframe=1D"
        / "exchange=NSE"
        / "symbol=RELIANCE"
        / "year=2025.parquet"
    )
    assert expected_hive_path.is_file()

    # Read back through store
    bars = store.read(inst, timeframe, DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2)))
    assert len(bars) == 1
    assert bars[0].close == 2520.0


def test_parquet_store_reads_legacy_partitions_transparently(tmp_path: Path) -> None:
    store = ParquetBarStore(data_dir=tmp_path, use_hive_partitions=True)
    inst = InstrumentId("TCS", "NSE")
    timeframe = "1D"

    # Manually create legacy partition file: catalog/1D/NSE/TCS/2025.parquet
    legacy_file = tmp_path / "catalog" / "1D" / "NSE" / "TCS" / "2025.parquet"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)

    base_ts = 1_735_689_600_000_000_000
    table = pa.Table.from_arrays(
        [
            pa.array([base_ts], type=pa.int64()),
            pa.array([3800.0], type=pa.float64()),
            pa.array([3850.0], type=pa.float64()),
            pa.array([3790.0], type=pa.float64()),
            pa.array([3820.0], type=pa.float64()),
            pa.array([50000.0], type=pa.float64()),
        ],
        schema=BAR_SCHEMA,
    )
    pq.write_table(table, legacy_file)

    # Read back through store even though use_hive_partitions=True
    bars = store.read(inst, timeframe, DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2)))
    assert len(bars) == 1
    assert getattr(bars[0].instrument_id, "symbol", "") == "TCS"
    assert bars[0].close == 3820.0


def test_validate_bar_nanosecond_timestamp() -> None:
    inst = InstrumentId("INFY", "NSE")

    # Negative / zero / float timestamp
    with pytest.raises(ValueError, match="positive integer in UTC nanoseconds"):
        _ = validate_bar(
            Bar(
                instrument_id=inst,
                ts=0,
                open=100.0,
                high=110.0,
                low=90.0,
                close=100.0,
                volume=10.0,
            )
        )

    with pytest.raises(ValueError, match="positive integer in UTC nanoseconds"):
        _ = validate_bar(
            Bar(
                instrument_id=inst,
                ts=-1000,
                open=100.0,
                high=110.0,
                low=90.0,
                close=100.0,
                volume=10.0,
            )
        )
