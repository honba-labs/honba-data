"""Canonical DataServiceImpl coordinating repositories and market data providers."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

from honba_data.domain.coverage import CoverageRecord, DateInterval
from honba_data.domain.models import (
    Bar,
    BarProtocol,
    FinancialRatios,
    FinancialStatement,
    InstrumentId,
    InstrumentIdProtocol,
    as_instrument_id,
)
from honba_data.ports.coverage_repo import CoverageRepository
from honba_data.ports.fundamentals_repo import FundamentalsRepository
from honba_data.ports.service import DataService

logger = logging.getLogger(__name__)


class DataServiceImpl(DataService):
    """Canonical implementation of the DataService hexagonal inbound port."""

    _fundamentals_repo: FundamentalsRepository
    _coverage_repo: CoverageRepository | None
    _market_data_store: Any

    def __init__(
        self,
        fundamentals_repo: FundamentalsRepository,
        coverage_repo: CoverageRepository | None = None,
        market_data_store: Any = None,
    ) -> None:
        self._fundamentals_repo = fundamentals_repo
        self._coverage_repo = coverage_repo
        self._market_data_store = market_data_store

    @property
    def fundamentals_repo(self) -> FundamentalsRepository:
        """Underlying fundamentals repository."""
        return self._fundamentals_repo

    @property
    def coverage_repo(self) -> CoverageRepository | None:
        """Underlying coverage repository."""
        return self._coverage_repo

    @property
    def market_data_store(self) -> Any:
        """Underlying market data store delegate."""
        return self._market_data_store

    def get_fundamentals(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]:
        """Fetch historical financial statements (FY or FQ) for a company."""
        return self._fundamentals_repo.find_by_symbol(symbol=symbol, period=period)

    def get_ratios(self, symbol: str, as_of: dt.date | None = None) -> Sequence[FinancialRatios]:
        """Fetch financial ratios, optionally point-in-time as-of a given date."""
        if as_of is not None:
            return self._fundamentals_repo.query_point_in_time(
                as_of_date=as_of,
                symbols=[symbol],
                period_type="FY",
            )
        return self._fundamentals_repo.find_ratios_by_symbol(symbol=symbol, period="FY")

    def get_bars(
        self,
        instrument: InstrumentIdProtocol | InstrumentId | Any,
        timeframe: str,
        interval: DateInterval,
    ) -> Sequence[BarProtocol | Bar]:
        """Retrieve historical market bars for an instrument over a date interval."""
        if self._market_data_store is not None:
            if hasattr(self._market_data_store, "read"):
                return self._market_data_store.read(instrument, timeframe, interval)
            raise TypeError(
                f"Configured market data store {type(self._market_data_store)} lacks a 'read' method"
            )
        raise NotImplementedError(
            "Market data delegate (honba-me / BarReader) not configured on DataService"
        )

    def coverage(
        self,
        instrument: InstrumentIdProtocol | InstrumentId | Any,
        timeframe: str,
    ) -> Sequence[CoverageRecord]:
        """Query the coverage ledger for an instrument and timeframe."""
        inst = as_instrument_id(instrument)
        if self._coverage_repo is not None:
            return self._coverage_repo.load_records(
                exchange=inst.exchange,
                symbol=inst.symbol,
                timeframe=timeframe,
            )
        if self._market_data_store is not None and hasattr(self._market_data_store, "coverage"):
            return self._market_data_store.coverage(inst, timeframe)
        return []
