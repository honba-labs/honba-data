"""Unit tests for DuckDbFundamentalsStore as a FundamentalsRepository adapter."""

from __future__ import annotations

import datetime as dt

from honba_data.adapters.duckdb.store import DuckDbFundamentalsStore
from honba_data.domain.events import DomainEvent, FundamentalsIngested
from honba_data.domain.models import Company, FinancialRatios, FinancialStatement
from honba_data.ports.fundamentals_repo import FundamentalsRepository


def test_duckdb_store_conforms_to_repository_protocol() -> None:
    store = DuckDbFundamentalsStore(db_path=":memory:")
    assert isinstance(store, FundamentalsRepository)


def test_duckdb_store_memory_mode_and_company_crud() -> None:
    store = DuckDbFundamentalsStore(db_path=":memory:")

    comp = Company(
        symbol="RELIANCE",
        company_name="Reliance Industries Limited",
        industry="Refineries",
        series="EQ",
        isin_code="INE002A01018",
        cap_category="LARGE_CAP",
        index_name="NIFTY 50",
    )
    store.save_company(comp)

    symbols = store.get_existing_symbols()
    assert "RELIANCE" in symbols


def test_duckdb_store_save_statements_and_event() -> None:
    store = DuckDbFundamentalsStore(db_path=":memory:")

    events: list[DomainEvent] = []
    store.subscribe(lambda e: events.append(e))

    stmt1 = FinancialStatement(
        symbol="INFY",
        period_type="FY",
        statement_type="INCOME",
        fiscal_date=dt.date(2024, 3, 31),
        filing_date=dt.date(2024, 4, 18),
        line_items={"Total Revenue": 1500000000000.0, "Net Income": 260000000000.0},
    )
    stmt2 = FinancialStatement(
        symbol="INFY",
        period_type="FY",
        statement_type="BALANCE_SHEET",
        fiscal_date=dt.date(2024, 3, 31),
        filing_date=dt.date(2024, 4, 18),
        line_items={"Total Assets": 1200000000000.0, "Total Equity": 850000000000.0},
    )

    store.save_statements([stmt1, stmt2])

    # Event verification
    assert len(events) == 1
    assert isinstance(events[0], FundamentalsIngested)
    assert events[0].symbol == "INFY"
    assert events[0].rows_written == 4

    # Query back
    found = store.find_by_symbol("INFY", period="FY")
    assert len(found) == 2
    types = {s.statement_type for s in found}
    assert "INCOME" in types
    assert "BALANCE_SHEET" in types

    income_stmt = next(s for s in found if s.statement_type == "INCOME")
    assert income_stmt.line_items["Net Income"] == 260000000000.0
    assert income_stmt.filing_date == dt.date(2024, 4, 18)


def test_duckdb_store_ratios_and_point_in_time() -> None:
    store = DuckDbFundamentalsStore(db_path=":memory:")

    ratio_q1 = FinancialRatios(
        symbol="HDFCBANK",
        fiscal_date=dt.date(2024, 6, 30),
        filing_date=dt.date(2024, 7, 20),
        period_type="FQ",
        roce_pct=16.5,
        roe_pct=17.2,
        net_margin_pct=22.0,
        debt_to_equity=6.5,
    )
    ratio_q2 = FinancialRatios(
        symbol="HDFCBANK",
        fiscal_date=dt.date(2024, 9, 30),
        filing_date=dt.date(2024, 10, 19),
        period_type="FQ",
        roce_pct=17.0,
        roe_pct=17.8,
        net_margin_pct=22.5,
        debt_to_equity=6.3,
    )

    store.save_ratios([ratio_q1, ratio_q2])

    # 1. Query before Q1 filing (2024-07-15) -> 0 rows
    pit_july = store.query_point_in_time(
        as_of_date=dt.date(2024, 7, 15),
        symbols=["HDFCBANK"],
        period_type="FQ",
    )
    assert len(pit_july) == 0

    # 2. Query between Q1 and Q2 filing (2024-08-01) -> Q1 results
    pit_aug = store.query_point_in_time(
        as_of_date=dt.date(2024, 8, 1),
        symbols=["HDFCBANK"],
        period_type="FQ",
    )
    assert len(pit_aug) == 1
    assert pit_aug[0].fiscal_date == dt.date(2024, 6, 30)
    assert pit_aug[0].roce_pct == 16.5

    # 3. Query after Q2 filing (2024-10-25) -> Q2 results
    pit_oct = store.query_point_in_time(
        as_of_date=dt.date(2024, 10, 25),
        symbols=["HDFCBANK"],
        period_type="FQ",
    )
    assert len(pit_oct) == 1
    assert pit_oct[0].fiscal_date == dt.date(2024, 9, 30)
    assert pit_oct[0].roce_pct == 17.0
