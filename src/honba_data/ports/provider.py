"""Hexagonal outbound port: FundamentalsProvider protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from honba_data.domain.models import Company, FinancialRatios, FinancialStatement


@runtime_checkable
class FundamentalsProvider(Protocol):
    """Port interface for external fundamentals data sources."""

    def fetch_company(self, symbol: str) -> Company | None:
        """Fetch company directory information."""
        ...

    def fetch_statements(
        self, symbol: str, period_type: str = "FY"
    ) -> Sequence[FinancialStatement]:
        """Fetch corporate statements for a symbol."""
        ...

    def fetch_ratios(self, symbol: str, period_type: str = "FY") -> Sequence[FinancialRatios]:
        """Fetch standardized ratios for a symbol."""
        ...
