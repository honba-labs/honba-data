"""Hexagonal outbound port: FundamentalsRepository protocol."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from honba_data.domain.models import Company, FinancialRatios, FinancialStatement


@runtime_checkable
class FundamentalsRepository(Protocol):
    """Port interface for fundamental company data persistence."""

    def save_company(self, company: Company) -> None:
        """Persist or update company directory metadata."""
        ...

    def save_statements(self, statements: Sequence[FinancialStatement]) -> None:
        """Persist or upsert financial statement line items."""
        ...

    def save_ratios(self, ratios: Sequence[FinancialRatios]) -> None:
        """Persist or upsert computed financial ratios."""
        ...

    def find_by_symbol(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]:
        """Retrieve financial statements for a symbol and period type."""
        ...

    def find_ratios_by_symbol(self, symbol: str, period: str = "FY") -> Sequence[FinancialRatios]:
        """Retrieve financial ratios for a symbol and period type."""
        ...

    def query_point_in_time(
        self,
        as_of_date: dt.date | str,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> Sequence[FinancialRatios]:
        """Point-in-time query for financial ratios strictly filed on or before as_of_date."""
        ...

    def query_point_in_time_statements(
        self,
        as_of_date: dt.date | str,
        statement_type: str | None = None,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> Sequence[FinancialStatement]:
        """Point-in-time query for financial statements filed on or before as_of_date."""
        ...
