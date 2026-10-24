"""Hexagonal inbound port: DataService contract for the Honba Data Lakehouse."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from honba_data.domain.coverage import CoverageRecord, DateInterval
from honba_data.domain.models import (
    Bar,
    BarProtocol,
    FinancialRatios,
    FinancialStatement,
    InstrumentId,
    InstrumentIdProtocol,
)


@runtime_checkable
class DataService(Protocol):
    """Canonical facade interface for the Honba Data Lakehouse."""

    def get_fundamentals(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]:
        """Fetch historical financial statements (FY or FQ) for a company."""
        ...

    def get_ratios(self, symbol: str, as_of: dt.date | None = None) -> Sequence[FinancialRatios]:
        """Fetch financial ratios, optionally filtered point-in-time as-of a given date."""
        ...

    def get_bars(
        self,
        instrument: InstrumentIdProtocol | InstrumentId | Any,
        timeframe: str,
        interval: DateInterval,
    ) -> Sequence[BarProtocol | Bar]:
        """Retrieve historical market bars for an instrument over a date interval."""
        ...

    def coverage(
        self,
        instrument: InstrumentIdProtocol | InstrumentId | Any,
        timeframe: str,
    ) -> Sequence[CoverageRecord]:
        """Query the coverage ledger for an instrument and timeframe."""
        ...
