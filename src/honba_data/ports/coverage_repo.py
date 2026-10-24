"""Hexagonal outbound port: CoverageRepository protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from honba_data.domain.coverage import CoverageRecord, DateInterval


@runtime_checkable
class CoverageRepository(Protocol):
    """Port interface for tracking data coverage intervals and ledger state."""

    def load_records(self, exchange: str, symbol: str, timeframe: str) -> Sequence[CoverageRecord]:
        """Load coverage records for an exchange/symbol/timeframe tuple."""
        ...

    def update_interval(self, record: CoverageRecord) -> Sequence[CoverageRecord]:
        """Atomically record and merge a coverage interval."""
        ...

    def detect_gaps(
        self, exchange: str, symbol: str, timeframe: str, required: DateInterval
    ) -> Sequence[DateInterval]:
        """Compute missing date intervals for the required range."""
        ...

    def append_record(self, record: CoverageRecord) -> None:
        """Append a coverage record to the persistent ledger."""
        ...
