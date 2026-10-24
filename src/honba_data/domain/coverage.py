"""Domain entities, value objects, and pure interval algebra for coverage tracking."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum


class CoverageStatus(Enum):
    FINAL = "final"
    PARTIAL = "partial"
    PROVISIONAL = "provisional"
    EMPTY = "empty"

    @classmethod
    def _missing_(cls, value: object) -> CoverageStatus | None:
        if isinstance(value, str):
            val_lower = value.lower()
            for member in cls:
                if member.value == val_lower or member.name.lower() == val_lower:
                    return member
        return None


@dataclass(frozen=True, order=True)
class DateInterval:
    """A half-open calendar date interval [start, end)."""

    start: dt.date
    end: dt.date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"Interval start {self.start} cannot be after end {self.end}")

    def is_empty(self) -> bool:
        return self.start >= self.end

    def contains(self, d: dt.date) -> bool:
        return self.start <= d < self.end

    def __contains__(self, d: dt.date) -> bool:
        return self.contains(d)


@dataclass(frozen=True)
class CoverageRecord:
    """A record in the coverage ledger."""

    exchange: str
    symbol: str
    timeframe: str
    interval: DateInterval
    status: CoverageStatus
    source: str
    row_count: int = 0
    checksum: str | None = None
    fetched_at_ns: int = 0


@dataclass(frozen=True)
class QualityIssue:
    """A data quality issue detected in financial statements or market bars."""

    issue_type: str
    severity: str  # "INFO", "WARNING", "ERROR", "CRITICAL"
    symbol: str
    details: str
    detected_at: dt.datetime = dt.datetime.now(dt.timezone.utc)


def merge_intervals(intervals: Sequence[DateInterval]) -> list[DateInterval]:
    """Merge overlapping or adjacent half-open date intervals."""
    valid = [iv for iv in intervals if not iv.is_empty()]
    if not valid:
        return []

    sorted_ivs = sorted(valid, key=lambda x: (x.start, x.end))
    merged: list[DateInterval] = [sorted_ivs[0]]

    for cur in sorted_ivs[1:]:
        prev = merged[-1]
        if cur.start <= prev.end:
            # Overlapping or adjacent
            if cur.end > prev.end:
                merged[-1] = DateInterval(prev.start, cur.end)
        else:
            merged.append(cur)

    return merged


def subtract_intervals(
    required: DateInterval,
    covered: Sequence[DateInterval],
) -> list[DateInterval]:
    """Compute `required - covered` using half-open date interval arithmetic."""
    if required.is_empty():
        return []

    merged_cov = merge_intervals(covered)
    gaps: list[DateInterval] = []
    current_start = required.start

    for cov in merged_cov:
        if cov.end <= current_start:
            continue
        if cov.start >= required.end:
            break

        if cov.start > current_start:
            gap_end = min(cov.start, required.end)
            if gap_end > current_start:
                gaps.append(DateInterval(current_start, gap_end))

        current_start = max(current_start, cov.end)
        if current_start >= required.end:
            break

    if current_start < required.end:
        gaps.append(DateInterval(current_start, required.end))

    return gaps


def merge_close_intervals(
    intervals: Sequence[DateInterval],
    max_gap_days: int = 0,
) -> list[DateInterval]:
    """Merge intervals whose gap is <= max_gap_days to avoid fragmented fetch requests."""
    merged = merge_intervals(intervals)
    if not merged or max_gap_days <= 0:
        return merged

    result: list[DateInterval] = [merged[0]]
    for cur in merged[1:]:
        prev = result[-1]
        gap_days = (cur.start - prev.end).days
        if gap_days <= max_gap_days:
            result[-1] = DateInterval(prev.start, cur.end)
        else:
            result.append(cur)

    return result


def plan_gaps(
    required: DateInterval,
    covered: Sequence[DateInterval],
    max_gap_days: int = 0,
) -> list[DateInterval]:
    """Plan missing gaps given a required range and covered intervals."""
    raw_gaps = subtract_intervals(required, covered)
    return merge_close_intervals(raw_gaps, max_gap_days=max_gap_days)
