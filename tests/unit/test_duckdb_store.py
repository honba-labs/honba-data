"""Unit tests for DuckDB fundamentals loader and store."""

from __future__ import annotations

import tempfile
from pathlib import Path

from honba_data.store.fundamentals import DuckDbFundamentalsStore


def test_duckdb_fundamentals_store_init_and_schema() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.duckdb"
        store = DuckDbFundamentalsStore(db_path=db_path)

        with store.get_connection() as con:
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            assert "companies" in tables
            assert "financial_statements" in tables
            assert "financial_ratios" in tables
            assert "instrument_metrics" in tables

        summary = store.get_summary()
        assert summary["companies_total"] == 0
        assert summary["statements_records"] == 0


def test_duckdb_fundamentals_save_and_query() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.duckdb"
        store = DuckDbFundamentalsStore(db_path=db_path)

        comp_meta = {
            "symbol": "TESTCO",
            "company_name": "Test Company Ltd",
            "industry": "Software",
            "sector": "Technology",
            "series": "EQ",
            "isin_code": "INE000000001",
            "cap_category": "LARGE_CAP",
            "index_name": "NIFTY 100",
            "market_cap": 500000000000.0,
            "pe_trailing": 25.5,
            "pe_forward": 20.0,
            "pb_ratio": 4.5,
            "trailing_eps": 50.0,
            "forward_eps": 60.0,
        }

        stmts = [
            ("TESTCO", "FY", "INCOME_STATEMENT", "2025-03-31", "Total Revenue", 10000000000.0),
            ("TESTCO", "FY", "INCOME_STATEMENT", "2025-03-31", "EBIT", 2500000000.0),
            ("TESTCO", "FY", "INCOME_STATEMENT", "2025-03-31", "Net Income", 2000000000.0),
            ("TESTCO", "FQ", "INCOME_STATEMENT", "2025-06-30", "Total Revenue", 2800000000.0),
        ]

        ratios = [
            (
                "TESTCO",
                "FY",
                "2025-03-31",
                10000000000.0,  # revenue
                3000000000.0,  # ebitda
                2500000000.0,  # ebit
                2000000000.0,  # net_income
                12000000000.0,  # total_assets
                2000000000.0,  # current_liabilities
                10000000000.0,  # capital_employed
                8000000000.0,  # stockholders_equity
                1000000000.0,  # total_debt
                1500000000.0,  # cash
                2200000000.0,  # ocf
                1800000000.0,  # fcf
                25.0,  # roce_pct
                25.0,  # roe_pct
                25.0,  # op_margin
                20.0,  # net_margin
                0.125,  # debt_to_equity
                50.0,  # eps
            )
        ]

        metrics = [
            (
                "NSE",
                "TESTCO",
                "return_on_capital_employed",
                "FY",
                "1D",
                25.0,
                None,
                "2025-03-31",
                1743445800000000000,
            ),
            (
                "NSE",
                "TESTCO",
                "return_on_equity",
                "FY",
                "1D",
                25.0,
                None,
                "2025-03-31",
                1743445800000000000,
            ),
        ]

        store.save_company_data(comp_meta, stmts, ratios, metrics)

        summary = store.get_summary()
        assert summary["companies_total"] == 1
        assert summary["large_cap_companies"] == 1
        assert summary["mid_cap_companies"] == 0
        assert summary["statements_records"] == 4
        assert summary["ratios_records"] == 1
        assert summary["screener_facts"] == 2
        assert summary["min_fiscal_date"] == "2025-03-31"

        with store.get_connection() as con:
            row = con.execute(
                "SELECT roce_pct, roe_pct, debt_to_equity FROM financial_ratios WHERE symbol='TESTCO'"
            ).fetchone()
            assert row is not None
            assert row[0] == 25.0
            assert row[1] == 25.0
            assert row[2] == 0.125
