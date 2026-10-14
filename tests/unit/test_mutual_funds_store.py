"""Unit tests for DuckDbMutualFundsStore and Equity Cross-Referencing."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from honba_data.store.fundamentals import DuckDbFundamentalsStore
from honba_data.store.mutual_funds import (
    DuckDbMutualFundsStore,
    FundHolding,
    FundStat,
    SchemeMetadata,
)


@pytest.fixture
def mf_store(tmp_path: Path) -> DuckDbMutualFundsStore:
    db_file = tmp_path / "test_mf.duckdb"
    return DuckDbMutualFundsStore(db_file)


@pytest.fixture
def populated_stores(tmp_path: Path) -> tuple[DuckDbMutualFundsStore, Path]:
    # 1. Setup mock equity fundamentals DB
    eq_db = tmp_path / "test_eq.duckdb"
    DuckDbFundamentalsStore(eq_db)
    import duckdb

    con = duckdb.connect(str(eq_db))
    # Insert companies
    con.execute("""
        INSERT INTO companies (symbol, company_name, isin_code, cap_category, pe_trailing)
        VALUES
            ('RELIANCE', 'Reliance Industries Ltd', 'INE002A01018', 'LargeCap', 24.5),
            ('HDFCBANK', 'HDFC Bank Ltd', 'INE040A01034', 'LargeCap', 18.2),
            ('INFY', 'Infosys Ltd', 'INE009A01021', 'LargeCap', 26.0),
            ('DIXON', 'Dixon Technologies Ltd', 'INE935N01020', 'MidCap', 75.0);

        INSERT INTO financial_ratios (symbol, period_type, fiscal_date, roce_pct, debt_to_equity)
        VALUES
            ('RELIANCE', 'yearly', '2026-03-31', 12.5, 0.45),
            ('HDFCBANK', 'yearly', '2026-03-31', 16.8, 0.90),
            ('INFY', 'yearly', '2026-03-31', 31.2, 0.08),
            ('DIXON', 'yearly', '2026-03-31', 25.4, 0.22);
    """)
    con.close()

    # 2. Setup MF store with 2 schemes
    mf_db = tmp_path / "test_mf.duckdb"
    mf_store = DuckDbMutualFundsStore(mf_db)

    schemes = [
        SchemeMetadata(
            scheme_code="122639",
            scheme_name="Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
            amc_name="PPFAS Mutual Fund",
            category="Equity:Flexi Cap",
            plan="Direct",
            option_type="Growth",
            isin_growth="INF879O01027",
            fund_manager="Rajeev Thakkar",
            inception_date=dt.date(2013, 5, 28),
        ),
        SchemeMetadata(
            scheme_code="118989",
            scheme_name="HDFC Flexi Cap Fund - Direct Plan - Growth",
            amc_name="HDFC Mutual Fund",
            category="Equity:Flexi Cap",
            plan="Direct",
            option_type="Growth",
            isin_growth="INF179K01BE2",
            fund_manager="Roshi Jain",
            inception_date=dt.date(2013, 1, 1),
        ),
    ]
    mf_store.upsert_schemes(schemes)

    # Insert stats
    stats = [
        FundStat(
            asof_date=dt.date(2026, 10, 1),
            scheme_code="122639",
            nav=85.42,
            ter_pct=0.62,
            aum_cr=72400.0,
        ),
        FundStat(
            asof_date=dt.date(2026, 10, 1),
            scheme_code="118989",
            nav=1950.25,
            ter_pct=0.81,
            aum_cr=58120.0,
        ),
    ]
    mf_store.upsert_stats(stats)

    # Insert holdings for PPFAS (122639)
    # 8% HDFCBANK, 7% INFY, 5% RELIANCE, 3% DIXON
    holdings_ppfas = [
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="122639",
            isin="INE040A01034",
            instrument_name="HDFC Bank Ltd",
            asset_class="Equity",
            pct_aum=8.0,
        ),
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="122639",
            isin="INE009A01021",
            instrument_name="Infosys Ltd",
            asset_class="Equity",
            pct_aum=7.0,
        ),
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="122639",
            isin="INE002A01018",
            instrument_name="Reliance Industries",
            asset_class="Equity",
            pct_aum=5.0,
        ),
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="122639",
            isin="INE935N01020",
            instrument_name="Dixon Tech",
            asset_class="Equity",
            pct_aum=3.0,
        ),
    ]
    mf_store.upsert_holdings(holdings_ppfas)

    # Insert holdings for HDFC Flexi Cap (118989)
    # 9% HDFCBANK, 6% RELIANCE, 4% INFY
    holdings_hdfc = [
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="118989",
            isin="INE040A01034",
            instrument_name="HDFC Bank Ltd",
            asset_class="Equity",
            pct_aum=9.0,
        ),
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="118989",
            isin="INE002A01018",
            instrument_name="Reliance Industries",
            asset_class="Equity",
            pct_aum=6.0,
        ),
        FundHolding(
            report_date=dt.date(2026, 9, 30),
            scheme_code="118989",
            isin="INE009A01021",
            instrument_name="Infosys Ltd",
            asset_class="Equity",
            pct_aum=4.0,
        ),
    ]
    mf_store.upsert_holdings(holdings_hdfc)

    return mf_store, eq_db


def test_scheme_and_stats_roundtrip(mf_store: DuckDbMutualFundsStore) -> None:
    scheme = SchemeMetadata(
        scheme_code="100001",
        scheme_name="Test Flexi Cap - Direct - Growth",
        amc_name="Test AMC",
        category="Equity:Flexi Cap",
        plan="Direct",
        option_type="Growth",
    )
    mf_store.upsert_schemes([scheme])

    results = mf_store.get_schemes(category="Equity:Flexi Cap", plan="Direct", option="Growth")
    assert len(results) == 1
    assert results[0]["scheme_code"] == "100001"
    assert results[0]["scheme_name"] == "Test Flexi Cap - Direct - Growth"


def test_equity_cross_reference_fundamentals(
    populated_stores: tuple[DuckDbMutualFundsStore, Path],
) -> None:
    mf_store, eq_db = populated_stores

    # Query fundamentals as of 2026-10-15 (after 10-day lag on 2026-09-30 disclosure)
    funda = mf_store.get_portfolio_fundamentals(
        scheme_code="122639",
        asof_date=dt.date(2026, 10, 15),
        equity_db_path=eq_db,
        disclosure_lag_days=10,
    )

    assert funda["scheme_code"] == "122639"
    assert funda["total_equity_pct"] == 23.0  # 8 + 7 + 5 + 3
    assert funda["coverage_pct"] == 100.0
    assert funda["large_cap_pct"] == 20.0  # 8 + 7 + 5
    assert funda["mid_cap_pct"] == 3.0  # 3 (DIXON)
    assert funda["portfolio_roce"] is not None
    # Weighted ROCE: (8*16.8 + 7*31.2 + 5*12.5 + 3*25.4) / 23 = (134.4 + 218.4 + 62.5 + 76.2) / 23 = 491.5 / 23 = 21.37 -> ~21.4%
    assert 21.0 <= funda["portfolio_roce"] <= 22.0
    assert len(funda["holdings"]) == 4
    symbols = [h["symbol"] for h in funda["holdings"]]
    assert "HDFCBANK" in symbols
    assert "INFY" in symbols
    assert "RELIANCE" in symbols
    assert "DIXON" in symbols


def test_portfolio_overlap_calculation(
    populated_stores: tuple[DuckDbMutualFundsStore, Path],
) -> None:
    mf_store, _ = populated_stores

    # Overlap between PPFAS (122639) and HDFC (118989):
    # HDFCBANK: min(8, 9) = 8
    # RELIANCE: min(5, 6) = 5
    # INFY: min(7, 4) = 4
    # Total Overlap = 8 + 5 + 4 = 17%
    overlap = mf_store.get_portfolio_overlap(
        scheme_a="122639",
        scheme_b="118989",
        asof_date=dt.date(2026, 10, 15),
    )

    assert overlap["scheme_a"] == "122639"
    assert overlap["scheme_b"] == "118989"
    assert overlap["overlap_pct"] == 17.0
    assert overlap["common_holdings_count"] == 3


def test_reverse_equity_lookup_find_funds(
    populated_stores: tuple[DuckDbMutualFundsStore, Path],
) -> None:
    mf_store, eq_db = populated_stores

    # Find funds holding RELIANCE by symbol
    rel_funds = mf_store.find_funds_holding_symbol(
        symbol_or_isin="RELIANCE",
        asof_date=dt.date(2026, 10, 15),
        min_pct_aum=5.0,
        equity_db_path=eq_db,
    )
    assert len(rel_funds) == 2
    codes = {f["scheme_code"] for f in rel_funds}
    assert codes == {"122639", "118989"}

    # Find funds holding DIXON (only PPFAS has DIXON at 3%)
    dixon_funds = mf_store.find_funds_holding_symbol(
        symbol_or_isin="DIXON",
        asof_date=dt.date(2026, 10, 15),
        min_pct_aum=2.0,
        equity_db_path=eq_db,
    )
    assert len(dixon_funds) == 1
    assert dixon_funds[0]["scheme_code"] == "122639"
    assert dixon_funds[0]["pct_aum"] == 3.0
