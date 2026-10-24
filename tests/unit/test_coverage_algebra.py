"""Property-based tests for interval algebra and coverage ledger invariants using Hypothesis."""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from honba_data.adapters.ledger.json_ledger import JsonCoverageRepository
from honba_data.domain.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
    merge_close_intervals,
    merge_intervals,
    subtract_intervals,
)
from honba_data.domain.models import Bar, InstrumentId
from honba_data.store.parquet import ParquetBarStore

# Limit date range to avoid overflow / excessive memory in tests
BASE_START = dt.date(2025, 1, 1)
BASE_END = dt.date(2025, 12, 31)

date_strategy = st.dates(min_value=BASE_START, max_value=BASE_END)


@st.composite
def valid_date_interval(draw: st.DrawFn) -> DateInterval:
    """Generate a non-empty half-open DateInterval [start, end)."""
    d1 = draw(date_strategy)
    d2 = draw(date_strategy)
    start = min(d1, d2)
    end = max(d1, d2)
    if start == end:
        end = start + dt.timedelta(days=1)
    return DateInterval(start, end)


@st.composite
def date_interval_list(draw: st.DrawFn) -> list[DateInterval]:
    """Generate a list of non-empty DateIntervals."""
    return draw(st.lists(valid_date_interval(), min_size=0, max_size=15))


# =============================================================================
# Pure Interval Algebra Properties
# =============================================================================


class TestIntervalAlgebraProperties:
    @given(intervals=date_interval_list())
    @settings(max_examples=100)
    def test_merge_intervals_monotonic_and_non_overlapping(
        self, intervals: list[DateInterval]
    ) -> None:
        """Invariant 2: The union of contiguous coverage intervals is monotonic and non-overlapping."""
        merged = merge_intervals(intervals)

        # 1. Monotonic & strictly non-overlapping (and non-adjacent, because adjacent are merged)
        for i in range(len(merged)):
            assert merged[i].start < merged[i].end, "Every merged interval must be non-empty"
            if i > 0:
                assert merged[i - 1].end < merged[i].start, (
                    f"Merged intervals must be disjoint: {merged[i - 1]} and {merged[i]}"
                )

        # 2. Idempotence: merge(merge(x)) == merge(x)
        assert merge_intervals(merged) == merged

    @given(intervals=date_interval_list())
    @settings(max_examples=100)
    def test_merge_intervals_point_coverage_conservation(
        self, intervals: list[DateInterval]
    ) -> None:
        """Any date covered in input must be covered in output, and vice versa."""
        merged = merge_intervals(intervals)

        # Sample a subset of dates from the inputs
        input_dates: set[dt.date] = set()
        for iv in intervals:
            # Check boundary points and midpoints
            input_dates.add(iv.start)
            input_dates.add(iv.end - dt.timedelta(days=1))
            mid = iv.start + (iv.end - iv.start) // 2
            input_dates.add(mid)

        for d in input_dates:
            in_input = any(iv.contains(d) for iv in intervals)
            in_merged = any(m.contains(d) for m in merged)
            assert in_input == in_merged, (
                f"Discrepancy for date {d}: input={in_input}, merged={in_merged}"
            )

    @given(required=valid_date_interval(), covered=date_interval_list())
    @settings(max_examples=100)
    def test_subtract_intervals_properties(
        self, required: DateInterval, covered: list[DateInterval]
    ) -> None:
        """Verification of subtract_intervals (gaps = required - covered)."""
        gaps = subtract_intervals(required, covered)

        # Gaps are strictly disjoint and inside required
        for i, gap in enumerate(gaps):
            assert gap.start >= required.start
            assert gap.end <= required.end
            assert gap.start < gap.end
            if i > 0:
                assert gaps[i - 1].end < gap.start

            # Gaps must NOT intersect any covered interval
            for cov in covered:
                # Two half-open intervals [s1, e1) and [s2, e2) intersect iff max(s1, s2) < min(e1, e2)
                overlap_start = max(gap.start, cov.start)
                overlap_end = min(gap.end, cov.end)
                assert overlap_start >= overlap_end, (
                    f"Gap {gap} overlaps with covered interval {cov}"
                )

    @given(intervals=date_interval_list(), max_gap_days=st.integers(min_value=0, max_value=7))
    @settings(max_examples=100)
    def test_merge_close_intervals_properties(
        self, intervals: list[DateInterval], max_gap_days: int
    ) -> None:
        """merge_close_intervals bridges gaps <= max_gap_days."""
        closed = merge_close_intervals(intervals, max_gap_days=max_gap_days)
        for i in range(1, len(closed)):
            gap = (closed[i].start - closed[i - 1].end).days
            assert gap > max_gap_days, (
                f"Intervals {closed[i - 1]} and {closed[i]} have gap {gap} <= {max_gap_days}"
            )


# =============================================================================
# Ledger & Parquet Store Invariant Tests
# =============================================================================


class TestLedgerAndLakehouseInvariants:
    @given(
        start_day=st.integers(min_value=1, max_value=200),
        seq_lengths=st.lists(st.integers(min_value=1, max_value=10), min_size=1, max_size=5),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_invariant_1_appending_continuous_sequence_never_creates_gap(
        self, tmp_path_factory: pytest.TempPathFactory, start_day: int, seq_lengths: list[int]
    ) -> None:
        """Invariant 1: Appending a continuous sequence of bars never creates a coverage gap."""
        tmp_dir = tmp_path_factory.mktemp("ledger_inv1")
        ledger_file = tmp_dir / "ledger.json"
        repo = JsonCoverageRepository(ledger_file)

        cur_start = BASE_START + dt.timedelta(days=start_day)
        total_days = sum(seq_lengths)
        full_end = cur_start + dt.timedelta(days=total_days)

        running_start = cur_start
        for length in seq_lengths:
            chunk_end = running_start + dt.timedelta(days=length)
            rec = CoverageRecord(
                exchange="NSE",
                symbol="TCS",
                timeframe="1D",
                interval=DateInterval(running_start, chunk_end),
                status=CoverageStatus.FINAL,
                source="test",
                row_count=length,
            )
            repo.append_record(rec)
            running_start = chunk_end

        # Query the full required interval
        full_required = DateInterval(cur_start, full_end)
        gaps = repo.detect_gaps("NSE", "TCS", "1D", full_required)

        assert len(gaps) == 0, f"Expected 0 gaps for continuous sequence, found: {gaps}"

        # Ledger records should have merged into 1 contiguous interval
        records = repo.load_records("NSE", "TCS", "1D")
        assert len(records) == 1
        assert records[0].interval == full_required
        assert records[0].row_count == total_days

    @given(intervals=st.lists(valid_date_interval(), min_size=1, max_size=8))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_invariant_2_ledger_records_are_monotonic_and_non_overlapping(
        self, tmp_path_factory: pytest.TempPathFactory, intervals: list[DateInterval]
    ) -> None:
        """Invariant 2: The union of contiguous coverage intervals is monotonic and non-overlapping in the ledger."""
        tmp_dir = tmp_path_factory.mktemp("ledger_inv2")
        ledger_file = tmp_dir / "ledger.json"
        repo = JsonCoverageRepository(ledger_file)

        for iv in intervals:
            rec = CoverageRecord(
                exchange="NSE",
                symbol="INFY",
                timeframe="1D",
                interval=iv,
                status=CoverageStatus.FINAL,
                source="test",
                row_count=(iv.end - iv.start).days,
            )
            repo.append_record(rec)

        records = repo.load_records("NSE", "INFY", "1D")
        for i in range(len(records)):
            assert records[i].interval.start < records[i].interval.end
            if i > 0:
                assert records[i - 1].interval.end < records[i].interval.start

    @given(
        start_day=st.integers(min_value=1, max_value=250),
        num_days=st.integers(min_value=1, max_value=10),
        duplicate_appends=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_invariant_3_querying_covered_interval_returns_exact_bars_zero_duplication(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        start_day: int,
        num_days: int,
        duplicate_appends: int,
    ) -> None:
        """Invariant 3: Querying a covered interval returns the exact bars appended, with zero duplication."""
        tmp_dir = tmp_path_factory.mktemp("parquet_inv3")
        store = ParquetBarStore(tmp_dir)

        inst = InstrumentId("RELIANCE", "NSE")
        start_date = BASE_START + dt.timedelta(days=start_day)
        end_date = start_date + dt.timedelta(days=num_days)

        # Generate 1 bar per day at 09:15 UTC
        bars: list[Bar] = []
        for i in range(num_days):
            d = start_date + dt.timedelta(days=i)
            # Nanoseconds for d at 09:15 UTC
            ts_ns = int(
                dt.datetime(d.year, d.month, d.day, 9, 15, tzinfo=dt.timezone.utc).timestamp()
                * 10**9
            )
            bars.append(
                Bar(
                    instrument_id=inst,
                    ts=ts_ns,
                    open=2500.0 + i,
                    high=2550.0 + i,
                    low=2480.0 + i,
                    close=2520.0 + i,
                    volume=10000.0 + i * 100,
                )
            )

        rec = CoverageRecord(
            exchange="NSE",
            symbol="RELIANCE",
            timeframe="1D",
            interval=DateInterval(start_date, end_date),
            status=CoverageStatus.FINAL,
            source="test",
            row_count=len(bars),
        )

        # Append once or multiple times (idempotency check)
        for _ in range(duplicate_appends):
            store.append(rec, bars)

        # Read back exactly over [start_date, end_date)
        read_bars = store.read(inst, "1D", DateInterval(start_date, end_date))

        assert len(read_bars) == num_days, f"Expected {num_days} bars, got {len(read_bars)}"
        for orig, fetched in zip(bars, read_bars):
            assert orig.ts == fetched.ts
            assert orig.open == fetched.open
            assert orig.high == fetched.high
            assert orig.low == fetched.low
            assert orig.close == fetched.close
            assert orig.volume == fetched.volume

        # Timestamps must be strictly ascending (zero duplicate timestamps)
        read_ts = [b.ts for b in read_bars]
        assert read_ts == sorted(set(read_ts))
