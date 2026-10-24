"""Unit and contract tests for DataService hexagonal port and implementation."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Sequence

import pytest

from honba_data.domain.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba_data.domain.models import Bar, FinancialRatios, FinancialStatement, InstrumentId
from honba_data.ports.coverage_repo import CoverageRepository
from honba_data.ports.fundamentals_repo import FundamentalsRepository
from honba_data.ports.service import DataService
from honba_data.service.service import DataServiceImpl
from honba_data.store.parquet import ParquetBarStore


class InMemoryFundamentalsRepo(FundamentalsRepository):
    """In-memory stub repository for testing DataServiceImpl."""

    def __init__(self) -> None:
        self.companies: dict[str, Any] = {}
        self.statements: list[FinancialStatement] = []
        self.ratios: list[FinancialRatios] = []

    def save_company(self, company: Any) -> None:
        self.companies[company.symbol] = company

    def save_statements(self, statements: Sequence[FinancialStatement]) -> None:
        self.statements.extend(statements)

    def save_ratios(self, ratios: Sequence[FinancialRatios]) -> None:
        self.ratios.extend(ratios)

    def find_by_symbol(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]:
        return [
            s
            for s in self.statements
            if s.symbol.upper() == symbol.upper() and s.period_type.upper() == period.upper()
        ]

    def find_ratios_by_symbol(self, symbol: str, period: str = "FY") -> Sequence[FinancialRatios]:
        return [
            r
            for r in self.ratios
            if r.symbol.upper() == symbol.upper() and r.period_type.upper() == period.upper()
        ]

    def query_point_in_time(
        self,
        as_of_date: dt.date | str,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> Sequence[FinancialRatios]:
        as_of = dt.date.fromisoformat(as_of_date) if isinstance(as_of_date, str) else as_of_date
        sym_set = {s.upper() for s in symbols} if symbols else None
        matching = [
            r
            for r in self.ratios
            if r.period_type.upper() == period_type.upper()
            and r.filing_date <= as_of
            and (sym_set is None or r.symbol.upper() in sym_set)
        ]
        # Return latest filing per symbol
        latest_by_sym: dict[str, FinancialRatios] = {}
        for r in sorted(matching, key=lambda x: (x.filing_date, x.fiscal_date)):
            latest_by_sym[r.symbol] = r
        return list(latest_by_sym.values())

    def query_point_in_time_statements(
        self,
        as_of_date: dt.date | str,
        statement_type: str | None = None,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> Sequence[FinancialStatement]:
        as_of = dt.date.fromisoformat(as_of_date) if isinstance(as_of_date, str) else as_of_date
        sym_set = {s.upper() for s in symbols} if symbols else None
        return [
            s
            for s in self.statements
            if s.period_type.upper() == period_type.upper()
            and s.filing_date <= as_of
            and (statement_type is None or s.statement_type.upper() == statement_type.upper())
            and (sym_set is None or s.symbol.upper() in sym_set)
        ]


class InMemoryCoverageRepo(CoverageRepository):
    """In-memory stub coverage repository for testing."""

    def __init__(self) -> None:
        self.records: list[CoverageRecord] = []

    def load_records(self, exchange: str, symbol: str, timeframe: str) -> Sequence[CoverageRecord]:
        return [
            r
            for r in self.records
            if r.exchange.upper() == exchange.upper()
            and r.symbol.upper() == symbol.upper()
            and r.timeframe.upper() == timeframe.upper()
        ]

    def update_interval(self, record: CoverageRecord) -> Sequence[CoverageRecord]:
        self.records.append(record)
        return self.load_records(record.exchange, record.symbol, record.timeframe)

    def detect_gaps(
        self, exchange: str, symbol: str, timeframe: str, required: DateInterval
    ) -> Sequence[DateInterval]:
        return []

    def append_record(self, record: CoverageRecord) -> None:
        self.records.append(record)


def test_data_service_protocol_conformance() -> None:
    fundamentals_repo = InMemoryFundamentalsRepo()
    coverage_repo = InMemoryCoverageRepo()
    service = DataServiceImpl(
        fundamentals_repo=fundamentals_repo,
        coverage_repo=coverage_repo,
    )

    assert isinstance(service, DataService)


def test_data_service_fundamentals_and_ratios() -> None:
    fundamentals_repo = InMemoryFundamentalsRepo()
    coverage_repo = InMemoryCoverageRepo()
    service = DataServiceImpl(
        fundamentals_repo=fundamentals_repo,
        coverage_repo=coverage_repo,
    )

    stmt = FinancialStatement(
        symbol="TCS",
        fiscal_date=dt.date(2024, 3, 31),
        filing_date=dt.date(2024, 4, 15),
        period_type="FY",
        statement_type="INCOME",
        line_items={"Total Revenue": 2400000000.0, "Net Income": 460000000.0},
    )
    ratio = FinancialRatios(
        symbol="TCS",
        fiscal_date=dt.date(2024, 3, 31),
        filing_date=dt.date(2024, 4, 15),
        period_type="FY",
        roce_pct=55.0,
        roe_pct=50.0,
        debt_to_equity=0.05,
    )
    fundamentals_repo.save_statements([stmt])
    fundamentals_repo.save_ratios([ratio])

    # 1. Fetch statements
    stmts = service.get_fundamentals("TCS", period="FY")
    assert len(stmts) == 1
    assert stmts[0].symbol == "TCS"
    assert stmts[0].line_items["Net Income"] == 460000000.0

    # 2. Fetch ratios without as_of
    ratios = service.get_ratios("TCS")
    assert len(ratios) == 1
    assert ratios[0].roce_pct == 55.0

    # 3. Fetch ratios with as_of BEFORE filing date -> empty (no lookahead bias)
    ratios_before = service.get_ratios("TCS", as_of=dt.date(2024, 4, 10))
    assert len(ratios_before) == 0

    # 4. Fetch ratios with as_of AFTER filing date -> returns statement
    ratios_after = service.get_ratios("TCS", as_of=dt.date(2024, 4, 20))
    assert len(ratios_after) == 1
    assert ratios_after[0].roce_pct == 55.0


def test_data_service_bars_not_implemented_when_delegate_missing() -> None:
    fundamentals_repo = InMemoryFundamentalsRepo()
    service = DataServiceImpl(fundamentals_repo=fundamentals_repo)

    inst = InstrumentId("INFY", "NSE")
    interval = DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 10))

    with pytest.raises(NotImplementedError, match="Market data delegate"):
        service.get_bars(inst, "1D", interval)


def test_data_service_bars_with_parquet_store_delegate(tmp_path: Path) -> None:
    fundamentals_repo = InMemoryFundamentalsRepo()
    parquet_store = ParquetBarStore(data_dir=tmp_path)
    service = DataServiceImpl(
        fundamentals_repo=fundamentals_repo,
        market_data_store=parquet_store,
    )

    inst = InstrumentId("INFY", "NSE")
    base_ts = 1_735_689_600_000_000_000
    bar = Bar(
        instrument_id=inst,
        ts=base_ts,
        open=1500.0,
        high=1520.0,
        low=1490.0,
        close=1510.0,
        volume=10000.0,
    )
    rec = CoverageRecord(
        exchange="NSE",
        symbol="INFY",
        timeframe="1D",
        interval=DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2)),
        status=CoverageStatus.FINAL,
        source="test",
        row_count=1,
    )
    parquet_store.append(rec, [bar])

    bars = service.get_bars(inst, "1D", DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2)))
    assert len(bars) == 1
    assert bars[0].close == 1510.0

    cov = service.coverage(inst, "1D")
    assert len(cov) == 1
    assert cov[0].symbol == "INFY"
