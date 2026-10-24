"""Reusable contract verification suite for DataService implementations and adapters.

Any implementation conforming to the DataService port MUST pass all tests
defined in ``BaseDataServiceContractTests``.
"""

from __future__ import annotations

import abc
import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from honba_data.adapters.duckdb.store import DuckDbFundamentalsStore
from honba_data.adapters.ledger.json_ledger import JsonCoverageRepository
from honba_data.domain.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba_data.domain.models import Bar, FinancialRatios, FinancialStatement, InstrumentId
from honba_data.ports.coverage_repo import CoverageRepository
from honba_data.ports.fundamentals_repo import FundamentalsRepository
from honba_data.ports.service import DataService
from honba_data.service.service import DataServiceImpl
from honba_data.store.parquet import ParquetBarStore

# =============================================================================
# In-Memory Adapters for Testing
# =============================================================================


class InMemoryFundamentalsRepo(FundamentalsRepository):
    """In-memory stub repository for contract verification."""

    def __init__(self) -> None:
        self.statements: list[FinancialStatement] = []
        self.ratios: list[FinancialRatios] = []

    def save_company(self, company: Any) -> None:
        pass

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
    """In-memory stub coverage repository for contract verification."""

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


class InMemoryBarStore:
    """In-memory stub market data store with read and coverage methods."""

    def __init__(self) -> None:
        self.bars: dict[tuple[str, str, str], list[Bar]] = {}
        self.coverage_records: dict[tuple[str, str, str], list[CoverageRecord]] = {}

    def append(self, record: CoverageRecord, bars: Sequence[Bar]) -> None:
        key = (record.exchange.upper(), record.symbol.upper(), record.timeframe.upper())
        self.bars.setdefault(key, []).extend(bars)
        self.coverage_records.setdefault(key, []).append(record)

    def read(
        self,
        instrument: InstrumentId | Any,
        timeframe: str,
        interval: DateInterval,
    ) -> list[Bar]:
        key = (instrument.exchange.upper(), instrument.symbol.upper(), timeframe.upper())
        all_bars = self.bars.get(key, [])
        start_ns = int(
            dt.datetime(
                interval.start.year,
                interval.start.month,
                interval.start.day,
                tzinfo=dt.timezone.utc,
            ).timestamp()
            * 10**9
        )
        end_ns = int(
            dt.datetime(
                interval.end.year,
                interval.end.month,
                interval.end.day,
                tzinfo=dt.timezone.utc,
            ).timestamp()
            * 10**9
        )
        return [b for b in all_bars if start_ns <= b.ts < end_ns]

    def coverage(self, instrument: InstrumentId | Any, timeframe: str) -> list[CoverageRecord]:
        key = (instrument.exchange.upper(), instrument.symbol.upper(), timeframe.upper())
        return self.coverage_records.get(key, [])


# =============================================================================
# Base Reusable Contract Test Suite
# =============================================================================


class BaseDataServiceContractTests(abc.ABC):
    """Abstract contract test suite verifying compliance with the DataService interface."""

    @abc.abstractmethod
    def create_service(
        self,
        statements: Sequence[FinancialStatement] = (),
        ratios: Sequence[FinancialRatios] = (),
        coverage_records: Sequence[CoverageRecord] = (),
        bars: Sequence[tuple[CoverageRecord, Sequence[Bar]]] = (),
        include_market_delegate: bool = True,
    ) -> DataService:
        """Create and seed a DataService instance."""
        ...

    def test_implements_data_service_protocol(self) -> None:
        service = self.create_service()
        assert isinstance(service, DataService)

    def test_get_fundamentals_by_symbol_and_period(self) -> None:
        stmt_fy = FinancialStatement(
            symbol="TCS",
            fiscal_date=dt.date(2024, 3, 31),
            filing_date=dt.date(2024, 4, 15),
            period_type="FY",
            statement_type="INCOME",
            line_items={"Total Revenue": 2400000000.0, "Net Income": 460000000.0},
        )
        stmt_fq = FinancialStatement(
            symbol="TCS",
            fiscal_date=dt.date(2024, 6, 30),
            filing_date=dt.date(2024, 7, 18),
            period_type="FQ",
            statement_type="INCOME",
            line_items={"Total Revenue": 620000000.0, "Net Income": 120000000.0},
        )
        service = self.create_service(statements=[stmt_fy, stmt_fq])

        # Query FY
        fy_results = service.get_fundamentals("TCS", period="FY")
        assert len(fy_results) == 1
        assert fy_results[0].symbol == "TCS"
        assert fy_results[0].period_type == "FY"
        assert fy_results[0].line_items["Total Revenue"] == 2400000000.0

        # Query FQ
        fq_results = service.get_fundamentals("TCS", period="FQ")
        assert len(fq_results) == 1
        assert fq_results[0].period_type == "FQ"
        assert fq_results[0].line_items["Total Revenue"] == 620000000.0

    def test_get_fundamentals_unknown_symbol_returns_empty(self) -> None:
        service = self.create_service()
        assert service.get_fundamentals("NONEXISTENT", period="FY") == []

    def test_get_ratios_without_as_of(self) -> None:
        ratio = FinancialRatios(
            symbol="RELIANCE",
            fiscal_date=dt.date(2024, 3, 31),
            filing_date=dt.date(2024, 4, 25),
            period_type="FY",
            roce_pct=14.5,
            roe_pct=12.0,
            debt_to_equity=0.38,
        )
        service = self.create_service(ratios=[ratio])

        ratios = service.get_ratios("RELIANCE")
        assert len(ratios) == 1
        assert ratios[0].symbol == "RELIANCE"
        assert ratios[0].roce_pct == 14.5

    def test_get_ratios_point_in_time_look_ahead_bias(self) -> None:
        """Contract: When as_of is specified, filings after as_of MUST NOT be returned."""
        ratio = FinancialRatios(
            symbol="INFY",
            fiscal_date=dt.date(2024, 3, 31),
            filing_date=dt.date(2024, 5, 15),  # Filed on May 15
            period_type="FY",
            roce_pct=32.0,
            roe_pct=29.0,
        )
        service = self.create_service(ratios=[ratio])

        # Before filing date: must return empty
        before = service.get_ratios("INFY", as_of=dt.date(2024, 5, 1))
        assert len(before) == 0

        # On or after filing date: returns ratio
        after = service.get_ratios("INFY", as_of=dt.date(2024, 5, 15))
        assert len(after) == 1
        assert after[0].roce_pct == 32.0

    def test_get_bars_date_interval(self) -> None:
        inst = InstrumentId("TCS", "NSE")
        start_date = dt.date(2025, 1, 1)
        end_date = dt.date(2025, 1, 4)

        ts1 = int(dt.datetime(2025, 1, 1, 9, 15, tzinfo=dt.timezone.utc).timestamp() * 10**9)
        ts2 = int(dt.datetime(2025, 1, 2, 9, 15, tzinfo=dt.timezone.utc).timestamp() * 10**9)
        ts3 = int(dt.datetime(2025, 1, 3, 9, 15, tzinfo=dt.timezone.utc).timestamp() * 10**9)

        bar1 = Bar(inst, ts1, 3800.0, 3850.0, 3790.0, 3840.0, 50000.0)
        bar2 = Bar(inst, ts2, 3840.0, 3870.0, 3830.0, 3860.0, 45000.0)
        bar3 = Bar(inst, ts3, 3860.0, 3900.0, 3850.0, 3890.0, 60000.0)

        rec = CoverageRecord(
            exchange="NSE",
            symbol="TCS",
            timeframe="1D",
            interval=DateInterval(start_date, end_date),
            status=CoverageStatus.FINAL,
            source="test",
            row_count=3,
        )

        service = self.create_service(bars=[(rec, [bar1, bar2, bar3])])

        # Fetch [2025-01-01, 2025-01-03) -> bar1 and bar2 only
        fetched = service.get_bars(
            inst, "1D", DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 3))
        )
        assert len(fetched) == 2
        assert fetched[0].close == 3840.0
        assert fetched[1].close == 3860.0

    def test_get_bars_unconfigured_delegate_raises_not_implemented(self) -> None:
        service = self.create_service(include_market_delegate=False)
        inst = InstrumentId("TCS", "NSE")
        with pytest.raises(NotImplementedError, match="Market data delegate"):
            _ = service.get_bars(inst, "1D", DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 2)))

    def test_coverage_records_retrieval(self) -> None:
        inst = InstrumentId("INFY", "NSE")
        rec = CoverageRecord(
            exchange="NSE",
            symbol="INFY",
            timeframe="1D",
            interval=DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 10)),
            status=CoverageStatus.FINAL,
            source="test",
            row_count=9,
        )
        service = self.create_service(coverage_records=[rec])

        cov = service.coverage(inst, "1D")
        assert len(cov) >= 1
        assert cov[0].symbol == "INFY"
        assert cov[0].interval.start == dt.date(2025, 1, 1)

    def test_coverage_unknown_instrument_returns_empty(self) -> None:
        service = self.create_service()
        inst = InstrumentId("UNKNOWN", "NSE")
        assert service.coverage(inst, "1D") == []


# =============================================================================
# Concrete Contract Test Implementations
# =============================================================================


class TestInMemoryDataServiceContract(BaseDataServiceContractTests):
    """Run the contract test suite against in-memory stub adapters."""

    def create_service(
        self,
        statements: Sequence[FinancialStatement] = (),
        ratios: Sequence[FinancialRatios] = (),
        coverage_records: Sequence[CoverageRecord] = (),
        bars: Sequence[tuple[CoverageRecord, Sequence[Bar]]] = (),
        include_market_delegate: bool = True,
    ) -> DataService:
        fund_repo = InMemoryFundamentalsRepo()
        fund_repo.save_statements(statements)
        fund_repo.save_ratios(ratios)

        cov_repo = InMemoryCoverageRepo()
        for rec in coverage_records:
            cov_repo.append_record(rec)

        market_store = None
        if include_market_delegate:
            market_store = InMemoryBarStore()
            for rec, b_list in bars:
                market_store.append(rec, b_list)

        return DataServiceImpl(
            fundamentals_repo=fund_repo,
            coverage_repo=cov_repo,
            market_data_store=market_store,
        )


class TestDuckDbLakehouseDataServiceContract(BaseDataServiceContractTests):
    """Run the contract test suite against real DuckDB & Parquet lakehouse adapters."""

    @pytest.fixture(autouse=True)
    def setup_lakehouse(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def create_service(
        self,
        statements: Sequence[FinancialStatement] = (),
        ratios: Sequence[FinancialRatios] = (),
        coverage_records: Sequence[CoverageRecord] = (),
        bars: Sequence[tuple[CoverageRecord, Sequence[Bar]]] = (),
        include_market_delegate: bool = True,
    ) -> DataService:
        db_file = self.tmp_path / "fundamentals.duckdb"
        fund_repo = DuckDbFundamentalsStore(db_file)
        fund_repo.save_statements(statements)
        fund_repo.save_ratios(ratios)

        ledger_file = self.tmp_path / "coverage_ledger.json"
        cov_repo = JsonCoverageRepository(ledger_file)
        for rec in coverage_records:
            cov_repo.append_record(rec)

        market_store = None
        if include_market_delegate:
            market_store = ParquetBarStore(data_dir=self.tmp_path)
            for rec, b_list in bars:
                market_store.append(rec, b_list)

        return DataServiceImpl(
            fundamentals_repo=fund_repo,
            coverage_repo=cov_repo,
            market_data_store=market_store,
        )
