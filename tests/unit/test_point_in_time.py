"""Unit tests for Point-in-Time Fundamentals and Look-Ahead Bias Elimination."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from honba_data.domain.models import FinancialRatios, FinancialStatement
from honba_data.service.service import DataServiceImpl
from honba_data.store.fundamentals import DuckDbFundamentalsStore


@pytest.fixture
def store(tmp_path: Path) -> DuckDbFundamentalsStore:
    db_file = tmp_path / "test_pit.duckdb"
    return DuckDbFundamentalsStore(db_file)


def test_point_in_time_look_ahead_bias_elimination(store: DuckDbFundamentalsStore) -> None:
    comp_meta: dict[str, object] = {
        "symbol": "RELIANCE",
        "company_name": "Reliance Industries Ltd",
        "industry": "Oil & Gas",
        "sector": "Energy",
        "series": "EQ",
        "isin_code": "INE002A01018",
        "cap_category": "LARGE_CAP",
        "index_name": "NIFTY 50",
    }

    # Two quarterly filings:
    # Q1 ends 2025-06-30, officially filed on 2025-08-10
    # Q2 ends 2025-09-30, officially filed on 2025-11-12
    ratios = [
        # (symbol, period_type, fiscal_date, period_end_date, filing_date, revenue, ebitda, ... eps)
        (
            "RELIANCE",
            "FQ",
            "2025-06-30",
            "2025-06-30",
            "2025-08-10",  # Filing Date
            2_300_000_000_000.0,  # revenue
            400_000_000_000.0,
            300_000_000_000.0,
            180_000_000_000.0,
            15_000_000_000_000.0,
            4_000_000_000_000.0,
            11_000_000_000_000.0,
            8_000_000_000_000.0,
            3_000_000_000_000.0,
            500_000_000_000.0,
            250_000_000_000.0,
            200_000_000_000.0,
            14.5,  # ROCE
            12.0,  # ROE
            13.0,
            7.8,
            0.38,
            28.5,
        ),
        (
            "RELIANCE",
            "FQ",
            "2025-09-30",
            "2025-09-30",
            "2025-11-12",  # Filing Date
            2_450_000_000_000.0,  # revenue grew
            430_000_000_000.0,
            320_000_000_000.0,
            195_000_000_000.0,
            15_500_000_000_000.0,
            4_100_000_000_000.0,
            11_400_000_000_000.0,
            8_200_000_000_000.0,
            3_100_000_000_000.0,
            520_000_000_000.0,
            270_000_000_000.0,
            210_000_000_000.0,
            15.8,  # ROCE grew
            13.2,
            13.1,
            8.0,
            0.37,
            30.2,
        ),
    ]

    store.save_company_data(comp_meta, [], ratios, [])

    # Case 1: Strategy evaluates on 2025-07-15 (after Q1 period end, but BEFORE Q1 filing date of 2025-08-10)
    # MUST return 0 rows (Q1 results were not yet published to the market, avoiding look-ahead bias!)
    pit_july = store.query_point_in_time_ratios(as_of_date=dt.date(2025, 7, 15), period_type="FQ")
    assert len(pit_july) == 0

    # Case 2: Strategy evaluates on 2025-08-15 (after Q1 filing date, but before Q2 filing date)
    # MUST return Q1 results with revenue = 2.3T and ROCE = 14.5%
    pit_aug = store.query_point_in_time_ratios(as_of_date=dt.date(2025, 8, 15), period_type="FQ")
    assert len(pit_aug) == 1
    assert pit_aug[0]["period_end_date"] == dt.date(2025, 6, 30)
    assert pit_aug[0]["filing_date"] == dt.date(2025, 8, 10)
    assert pit_aug[0]["roce_pct"] == 14.5

    # Case 3: Strategy evaluates on 2025-11-15 (after Q2 filing date of 2025-11-12)
    # MUST return latest Q2 results with revenue = 2.45T and ROCE = 15.8%
    pit_nov = store.query_point_in_time_ratios(as_of_date=dt.date(2025, 11, 15), period_type="FQ")
    assert len(pit_nov) == 1
    assert pit_nov[0]["period_end_date"] == dt.date(2025, 9, 30)
    assert pit_nov[0]["filing_date"] == dt.date(2025, 11, 12)
    assert pit_nov[0]["roce_pct"] == 15.8


def test_statements_point_in_time_visibility_invariant(store: DuckDbFundamentalsStore) -> None:
    """Invariant: Given a query as_of = '2024-05-01', statements filed on '2024-05-15'

    (even for Q4 2023 / period ended 2024-03-31) MUST NOT be visible.
    """
    stmt_fy23 = FinancialStatement(
        symbol="INFY",
        period_type="FQ",
        statement_type="INCOME",
        fiscal_date=dt.date(2024, 3, 31),
        filing_date=dt.date(2024, 5, 15),  # Filed after 2024-05-01
        line_items={"Total Revenue": 379230000000.0, "Net Income": 79690000000.0},
    )
    store.save_statements([stmt_fy23])

    # 1. Query strictly on or before 2024-05-01 -> MUST be completely invisible
    res_before = store.query_point_in_time_statements(
        as_of_date=dt.date(2024, 5, 1),
        symbols=["INFY"],
        period_type="FQ",
    )
    assert len(res_before) == 0, (
        f"Statement filed on 2024-05-15 must not be visible on 2024-05-01, got: {res_before}"
    )

    raw_before = store.query_point_in_time_statements_raw(
        as_of_date="2024-05-01",
        symbols=["INFY"],
        period_type="FQ",
    )
    assert len(raw_before) == 0

    # 2. Query on or after filing date 2024-05-15 -> MUST be visible
    res_after = store.query_point_in_time_statements(
        as_of_date=dt.date(2024, 5, 15),
        symbols=["INFY"],
        period_type="FQ",
    )
    assert len(res_after) == 1
    assert res_after[0].symbol == "INFY"
    assert res_after[0].fiscal_date == dt.date(2024, 3, 31)
    assert res_after[0].filing_date == dt.date(2024, 5, 15)
    assert res_after[0].line_items["Net Income"] == 79690000000.0


def test_fundamental_screener_zero_look_ahead_bias(store: DuckDbFundamentalsStore) -> None:
    """Verify zero look-ahead bias across fundamental screener queries with staggered filing dates."""
    # Three companies with staggered Q4 earnings release dates:
    # 1. TCS: FY ends 2024-03-31, filed 2024-04-12, ROCE 55.0%
    # 2. INFY: FY ends 2024-03-31, filed 2024-04-25, ROCE 32.0%
    # 3. WIPRO: FY ends 2024-03-31, filed 2024-05-10, ROCE 18.0%
    ratios = [
        FinancialRatios(
            symbol="TCS",
            fiscal_date=dt.date(2024, 3, 31),
            filing_date=dt.date(2024, 4, 12),
            period_type="FY",
            roce_pct=55.0,
            roe_pct=50.0,
            operating_margin_pct=25.0,
            net_margin_pct=20.0,
        ),
        FinancialRatios(
            symbol="INFY",
            fiscal_date=dt.date(2024, 3, 31),
            filing_date=dt.date(2024, 4, 25),
            period_type="FY",
            roce_pct=32.0,
            roe_pct=29.0,
            operating_margin_pct=21.0,
            net_margin_pct=17.0,
        ),
        FinancialRatios(
            symbol="WIPRO",
            fiscal_date=dt.date(2024, 3, 31),
            filing_date=dt.date(2024, 5, 10),
            period_type="FY",
            roce_pct=18.0,
            roe_pct=15.0,
            operating_margin_pct=15.0,
            net_margin_pct=12.0,
        ),
    ]
    store.save_ratios(ratios)

    # Screener objective: Find all IT companies with ROCE > 20%
    def run_screener(as_of: dt.date) -> list[str]:
        results = store.query_point_in_time(as_of_date=as_of, period_type="FY")
        return [r.symbol for r in results if r.roce_pct is not None and r.roce_pct > 20.0]

    # As-of April 10, 2024: Neither has filed -> 0 companies pass screener
    assert run_screener(dt.date(2024, 4, 10)) == []

    # As-of April 15, 2024: Only TCS has filed -> only TCS passes screener
    assert run_screener(dt.date(2024, 4, 15)) == ["TCS"]

    # As-of May 1, 2024: TCS and INFY have filed -> TCS and INFY pass screener, WIPRO still not filed
    assert sorted(run_screener(dt.date(2024, 5, 1))) == ["INFY", "TCS"]

    # As-of May 15, 2024: All 3 have filed -> TCS and INFY pass (WIPRO has ROCE 18% <= 20%)
    assert sorted(run_screener(dt.date(2024, 5, 15))) == ["INFY", "TCS"]


def test_data_service_point_in_time_ratios_integration(store: DuckDbFundamentalsStore) -> None:
    """Verify DataService facade strictly enforces point-in-time invariant via get_ratios(as_of=...)."""
    service = DataServiceImpl(fundamentals_repo=store)

    ratio = FinancialRatios(
        symbol="HDFCBANK",
        fiscal_date=dt.date(2024, 3, 31),
        filing_date=dt.date(2024, 4, 20),
        period_type="FY",
        roce_pct=16.8,
        roe_pct=17.5,
    )
    store.save_ratios([ratio])

    # 1. as_of before filing date -> empty
    before = service.get_ratios("HDFCBANK", as_of=dt.date(2024, 4, 15))
    assert len(before) == 0

    # 2. as_of on or after filing date -> returns statement
    after = service.get_ratios("HDFCBANK", as_of=dt.date(2024, 4, 20))
    assert len(after) == 1
    assert after[0].roce_pct == 16.8


def test_as_of_join_prices_and_fundamentals(store: DuckDbFundamentalsStore) -> None:
    comp_meta: dict[str, object] = {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd",
        "industry": "IT",
        "sector": "Technology",
        "series": "EQ",
        "isin_code": "INE467B01029",
        "cap_category": "LARGE_CAP",
        "index_name": "NIFTY 50",
    }
    ratios = [
        (
            "TCS",
            "FY",
            "2024-03-31",
            "2024-03-31",
            "2024-04-20",  # Filed on April 20, 2024
            2_400_000_000_000.0,
            600_000_000_000.0,
            550_000_000_000.0,
            460_000_000_000.0,
            1_400_000_000_000.0,
            400_000_000_000.0,
            1_000_000_000_000.0,
            900_000_000_000.0,
            50_000_000_000.0,
            100_000_000_000.0,
            440_000_000_000.0,
            400_000_000_000.0,
            55.0,  # ROCE
            50.0,
            24.0,
            19.0,
            0.05,
            125.0,
        )
    ]
    store.save_company_data(comp_meta, [], ratios, [])

    # Price bars across the filing date
    prices = pd.DataFrame(
        [
            {
                "symbol": "TCS",
                "bar_date": dt.date(2024, 4, 15),
                "close": 3900.0,
            },  # BEFORE filing date
            {
                "symbol": "TCS",
                "bar_date": dt.date(2024, 4, 25),
                "close": 4050.0,
            },  # AFTER filing date
        ]
    )

    joined = store.as_of_join_prices_and_fundamentals(prices, period_type="FY")
    assert len(joined) == 2

    # April 15 bar: NO fundamentals attached because filing had not occurred yet!
    assert pd.isna(joined.iloc[0]["fund_filing_date"])
    assert pd.isna(joined.iloc[0]["roce_pct"])

    # April 25 bar: Fundamentals are attached!
    assert joined.iloc[1]["fund_filing_date"] == dt.date(2024, 4, 20)
    assert joined.iloc[1]["roce_pct"] == 55.0
