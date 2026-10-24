from __future__ import annotations

import datetime as dt
import logging
import math
from collections.abc import Sequence
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from honba_data.adapters.ledger.json_ledger import JsonCoverageRepository
from honba_data.domain.coverage import (
    CoverageRecord,
    DateInterval,
)
from honba_data.domain.models import Bar, InstrumentId, as_bar, as_instrument_id

logger = logging.getLogger(__name__)

BAR_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)

_EPOCH = dt.date(1970, 1, 1)
_NS_PER_DAY = 86_400 * 1_000_000_000


def _utc_midnight_ns(day: dt.date) -> int:
    """Unix nanoseconds of ``day`` 00:00 UTC (exact integer arithmetic, no local timezone)."""
    return (day - _EPOCH).days * _NS_PER_DAY


def validate_bar(bar: Bar | object) -> bool:
    """Validate bar invariants (finite prices/volume, low <= open/close <= high, volume >= 0, UTC nanoseconds)."""
    ts = getattr(bar, "ts", getattr(bar, "timestamp", None))
    if ts is None or not isinstance(ts, int) or ts <= 0:
        raise ValueError(f"bar timestamp must be a positive integer in UTC nanoseconds, got {ts}")

    for val, name in (
        (getattr(bar, "open"), "open"),
        (getattr(bar, "high"), "high"),
        (getattr(bar, "low"), "low"),
        (getattr(bar, "close"), "close"),
        (getattr(bar, "volume"), "volume"),
    ):
        fval = float(val)
        if not math.isfinite(fval):
            raise ValueError(f"bar {name} must be finite, got {fval}")

    b_open = float(getattr(bar, "open"))
    b_high = float(getattr(bar, "high"))
    b_low = float(getattr(bar, "low"))
    b_close = float(getattr(bar, "close"))
    b_vol = float(getattr(bar, "volume"))

    if b_low > b_high:
        raise ValueError(f"high must be >= low, got high={b_high}, low={b_low}")

    if not (b_low <= b_open <= b_high):
        raise ValueError(
            f"open must be between low and high, got open={b_open}, high={b_high}, low={b_low}"
        )

    if not (b_low <= b_close <= b_high):
        raise ValueError(
            f"close must be between low and high, got close={b_close}, high={b_high}, low={b_low}"
        )

    if b_vol < 0:
        raise ValueError(f"volume must be >= 0, got {b_vol}")

    return True


class ParquetBarStore:
    """Persistent BarStore backed by Hive-partitioned Parquet files and an atomic coverage ledger."""

    data_dir: Path
    catalog_dir: Path
    ledger_file: Path
    use_hive_partitions: bool
    _coverage_repo: JsonCoverageRepository

    def __init__(
        self,
        data_dir: Path | str | None = None,
        use_hive_partitions: bool = True,
    ) -> None:
        """``data_dir`` defaults to :func:`find_data_root`, resolved once here."""
        from honba_data.store.fundamentals import find_data_root

        self.data_dir = Path(data_dir if data_dir is not None else find_data_root()).resolve()
        self.catalog_dir = self.data_dir / "catalog"
        self.ledger_file = self.data_dir / "coverage_ledger.json"
        self.use_hive_partitions = use_hive_partitions
        self._coverage_repo = JsonCoverageRepository(self.ledger_file)

    @property
    def coverage_repo(self) -> JsonCoverageRepository:
        """The underlying JsonCoverageRepository."""
        return self._coverage_repo

    def coverage(self, instrument: InstrumentId | object, timeframe: str) -> list[CoverageRecord]:
        """Query the coverage ledger for an instrument and timeframe."""
        inst = as_instrument_id(instrument)
        records = self._coverage_repo.load_records(inst.exchange, inst.symbol, timeframe)
        return list(records)

    def _hive_file_path(self, instrument: InstrumentId | object, timeframe: str, year: int) -> Path:
        """Canonical Hive-partitioned path: catalog/timeframe=1D/exchange=NSE/symbol=RELIANCE/year=2025.parquet"""
        inst = as_instrument_id(instrument)
        return (
            self.catalog_dir
            / f"timeframe={timeframe.upper()}"
            / f"exchange={inst.exchange.upper()}"
            / f"symbol={inst.symbol.upper()}"
            / f"year={year}.parquet"
        )

    def _legacy_file_path(
        self, instrument: InstrumentId | object, timeframe: str, year: int
    ) -> Path:
        """Legacy partition path: catalog/1D/NSE/RELIANCE/2025.parquet"""
        inst = as_instrument_id(instrument)
        return (
            self.catalog_dir
            / timeframe.upper()
            / inst.exchange.upper()
            / inst.symbol.upper()
            / f"{year}.parquet"
        )

    def _resolve_read_path(
        self, instrument: InstrumentId | object, timeframe: str, year: int
    ) -> Path:
        """Check Hive partitioned path first; fall back to legacy directory path."""
        hive_p = self._hive_file_path(instrument, timeframe, year)
        if hive_p.exists():
            return hive_p
        legacy_p = self._legacy_file_path(instrument, timeframe, year)
        if legacy_p.exists():
            return legacy_p
        # Default destination for write or missing check
        return hive_p if self.use_hive_partitions else legacy_p

    def _resolve_write_path(
        self, instrument: InstrumentId | object, timeframe: str, year: int
    ) -> Path:
        """Destination file path for writes."""
        return (
            self._hive_file_path(instrument, timeframe, year)
            if self.use_hive_partitions
            else self._legacy_file_path(instrument, timeframe, year)
        )

    def read(
        self, instrument: InstrumentId | object, timeframe: str, interval: DateInterval
    ) -> list[Bar]:
        """Read and deduplicate bars from partitioned parquet files.

        ``interval`` is a half-open range of UTC calendar dates: the window is
        ``[start 00:00 UTC, end 00:00 UTC)``.
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
            fpath = self._resolve_read_path(inst, timeframe, y)
            if not fpath.exists():
                continue
            try:
                table = pq.read_table(fpath)
                ts_arr: Sequence[object] = table.column("ts").to_pylist()
                op_arr: Sequence[object] = table.column("open").to_pylist()
                hi_arr: Sequence[object] = table.column("high").to_pylist()
                lo_arr: Sequence[object] = table.column("low").to_pylist()
                cl_arr: Sequence[object] = table.column("close").to_pylist()
                vo_arr: Sequence[object] = table.column("volume").to_pylist()

                for ts_raw, op_raw, hi_raw, lo_raw, cl_raw, vo_raw in zip(
                    ts_arr, op_arr, hi_arr, lo_arr, cl_arr, vo_arr
                ):
                    ts = int(str(ts_raw))
                    if start_ns <= ts < end_ns:
                        bars_by_ts[ts] = Bar(
                            instrument_id=inst,
                            ts=ts,
                            open=float(str(op_raw)),
                            high=float(str(hi_raw)),
                            low=float(str(lo_raw)),
                            close=float(str(cl_raw)),
                            volume=float(str(vo_raw)),
                        )
            except (OSError, pa.ArrowException) as exc:
                logger.error("Error reading %s: %s", fpath, exc)

        return [bars_by_ts[t] for t in sorted(bars_by_ts.keys())]

    def append(self, record: CoverageRecord, bars: Sequence[Bar | object]) -> None:
        """Append bars into partitioned parquet files and update coverage ledger atomically."""
        inst = as_instrument_id(InstrumentId(record.symbol, record.exchange))
        tf = record.timeframe.upper()

        # Group bars by year
        bars_by_year: dict[int, list[Bar]] = {}
        for raw_b in bars:
            b = as_bar(raw_b)
            _ = validate_bar(b)
            ts = getattr(b, "ts", getattr(b, "timestamp", 0))
            bar_date = dt.datetime.fromtimestamp(ts / 1e9, tz=dt.timezone.utc).date()
            bars_by_year.setdefault(bar_date.year, []).append(b)

        # Write each year partition
        for year, year_bars in bars_by_year.items():
            fpath = self._resolve_write_path(inst, tf, year)
            fpath.parent.mkdir(parents=True, exist_ok=True)

            existing_bars: dict[int, Bar] = {}
            read_path = self._resolve_read_path(inst, tf, year)
            if read_path.exists():
                try:
                    table = pq.read_table(read_path)
                    r_ts: Sequence[object] = table.column("ts").to_pylist()
                    r_op: Sequence[object] = table.column("open").to_pylist()
                    r_hi: Sequence[object] = table.column("high").to_pylist()
                    r_lo: Sequence[object] = table.column("low").to_pylist()
                    r_cl: Sequence[object] = table.column("close").to_pylist()
                    r_vo: Sequence[object] = table.column("volume").to_pylist()

                    for ts_raw, op_raw, hi_raw, lo_raw, cl_raw, vo_raw in zip(
                        r_ts, r_op, r_hi, r_lo, r_cl, r_vo
                    ):
                        ts_val = int(str(ts_raw))
                        existing_bars[ts_val] = Bar(
                            instrument_id=inst,
                            ts=ts_val,
                            open=float(str(op_raw)),
                            high=float(str(hi_raw)),
                            low=float(str(lo_raw)),
                            close=float(str(cl_raw)),
                            volume=float(str(vo_raw)),
                        )
                except (OSError, pa.ArrowException) as exc:
                    logger.warning("Could not read existing parquet %s: %s", read_path, exc)

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
            _ = tmp_path.replace(fpath)

        # Update ledger atomically through JsonCoverageRepository
        self._coverage_repo.update_interval(record)
