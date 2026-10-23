"""Unit tests for Point-in-Time Fundamentals and Look-Ahead Bias Elimination."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

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
