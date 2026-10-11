"""DuckDB storage engine for company financial statements and ratios."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import duckdb

DATA_DIR_ENV = "HONBA_DATA_DIR"


def find_data_root(start_path: Path | None = None) -> Path:
    """Resolve data root directory for storing parquet files and DuckDB databases.

    Resolution hierarchy:
    1. $HONBA_DATA_DIR environment variable if defined.
    2. Nearest directory containing 'honba-data/data', 'data', or 'honba/data'.
    3. Sibling 'honba-data/data' if working within honba-labs workspace.
    4. '<cwd>/data' fallback.
    """
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()

    cur = (start_path or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        if (p / "honba-data" / "data").is_dir():
            return (p / "honba-data" / "data").resolve()
        if (p / "data").is_dir() and (p.name == "honba-data" or (p / "Cargo.toml").is_file()):
            return (p / "data").resolve()
        if (p / "honba" / "data").is_dir():
            return (p / "honba" / "data").resolve()
        if (p / "data").is_dir():
            return (p / "data").resolve()

    if (cur.parent / "honba-data" / "data").is_dir():
        return (cur.parent / "honba-data" / "data").resolve()

    return cur / "data"


class DuckDbFundamentalsStore:
    """Embedded DuckDB storage engine for company financial statements and ratios."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            data_root = find_data_root()
            data_root.mkdir(parents=True, exist_ok=True)
            self.db_path = data_root / "fundamentals.duckdb"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))

    def _init_schema(self) -> None:
        with self._get_connection() as con:
            # 1. Company directory
            con.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    symbol VARCHAR PRIMARY KEY,
                    company_name VARCHAR,
                    industry VARCHAR,
                    sector VARCHAR,
                    series VARCHAR,
                    isin_code VARCHAR,
                    cap_category VARCHAR,
                    index_name VARCHAR,
                    market_cap DOUBLE,
                    pe_trailing DOUBLE,
                    pe_forward DOUBLE,
                    pb_ratio DOUBLE,
                    trailing_eps DOUBLE,
                    forward_eps DOUBLE,
                    updated_at TIMESTAMP
                );
            """)

            # 2. Financial statements line items (Income, Balance Sheet, Cash Flow)
            con.execute("""
                CREATE TABLE IF NOT EXISTS financial_statements (
                    symbol VARCHAR,
                    period_type VARCHAR,
                    statement_type VARCHAR,
                    fiscal_date DATE,
                    line_item VARCHAR,
                    value DOUBLE,
                    currency VARCHAR DEFAULT 'INR',
                    PRIMARY KEY (symbol, period_type, statement_type, fiscal_date, line_item)
                );
            """)

            # 3. Standardized financial ratios per fiscal date
            con.execute("""
                CREATE TABLE IF NOT EXISTS financial_ratios (
                    symbol VARCHAR,
                    period_type VARCHAR,
                    fiscal_date DATE,
                    revenue DOUBLE,
                    ebitda DOUBLE,
                    ebit DOUBLE,
                    net_income DOUBLE,
                    total_assets DOUBLE,
                    current_liabilities DOUBLE,
                    capital_employed DOUBLE,
                    stockholders_equity DOUBLE,
                    total_debt DOUBLE,
                    cash_and_equivalents DOUBLE,
                    operating_cash_flow DOUBLE,
                    free_cash_flow DOUBLE,
                    roce_pct DOUBLE,
                    roe_pct DOUBLE,
                    operating_margin_pct DOUBLE,
                    net_margin_pct DOUBLE,
                    debt_to_equity DOUBLE,
                    eps DOUBLE,
                    PRIMARY KEY (symbol, period_type, fiscal_date)
                );
            """)

            # 4. Canonical Honba screener facts table (Design.md Section 14.3)
            con.execute("""
                CREATE TABLE IF NOT EXISTS instrument_metrics (
                    venue VARCHAR DEFAULT 'NSE',
                    symbol VARCHAR,
                    metric_key VARCHAR,
                    period VARCHAR,
                    timeframe VARCHAR DEFAULT '1D',
                    value_num DOUBLE,
                    value_text VARCHAR,
                    fiscal_date DATE,
                    as_of_ns BIGINT,
                    PRIMARY KEY (symbol, metric_key, period, fiscal_date)
                );
            """)

    def save_company_data(
        self,
        company_meta: dict[str, Any],
        statements: list[tuple[str, str, str, str, str, float]],
        ratios: list[tuple[Any, ...]],
        metrics: list[tuple[Any, ...]],
    ) -> None:
        """Atomically upsert all financial data for a single company."""
        with self._get_connection() as con:
            # Upsert company
            con.execute(
                """
                INSERT INTO companies (
                    symbol, company_name, industry, sector, series, isin_code,
                    cap_category, index_name, market_cap, pe_trailing, pe_forward,
                    pb_ratio, trailing_eps, forward_eps, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    company_name=excluded.company_name,
                    industry=excluded.industry,
                    sector=excluded.sector,
                    market_cap=excluded.market_cap,
                    pe_trailing=excluded.pe_trailing,
                    pe_forward=excluded.pe_forward,
                    pb_ratio=excluded.pb_ratio,
                    trailing_eps=excluded.trailing_eps,
                    forward_eps=excluded.forward_eps,
                    updated_at=excluded.updated_at;
                """,
                [
                    company_meta["symbol"],
                    company_meta["company_name"],
                    company_meta["industry"],
                    company_meta.get("sector", ""),
                    company_meta["series"],
                    company_meta["isin_code"],
                    company_meta["cap_category"],
                    company_meta["index_name"],
                    company_meta.get("market_cap"),
                    company_meta.get("pe_trailing"),
                    company_meta.get("pe_forward"),
                    company_meta.get("pb_ratio"),
                    company_meta.get("trailing_eps"),
                    company_meta.get("forward_eps"),
                    dt.datetime.now(tz=dt.timezone.utc),
                ],
            )

            # Insert/replace statements
            if statements:
                con.executemany(
                    """
                    INSERT INTO financial_statements (
                        symbol, period_type, statement_type, fiscal_date, line_item, value
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (symbol, period_type, statement_type, fiscal_date, line_item)
                    DO UPDATE SET value=excluded.value;
                    """,
                    statements,
                )

            # Insert/replace ratios
            if ratios:
                con.executemany(
                    """
                    INSERT INTO financial_ratios (
                        symbol, period_type, fiscal_date, revenue, ebitda, ebit, net_income,
                        total_assets, current_liabilities, capital_employed, stockholders_equity,
                        total_debt, cash_and_equivalents, operating_cash_flow, free_cash_flow,
                        roce_pct, roe_pct, operating_margin_pct, net_margin_pct, debt_to_equity, eps
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (symbol, period_type, fiscal_date) DO UPDATE SET
                        revenue=excluded.revenue,
                        ebitda=excluded.ebitda,
                        ebit=excluded.ebit,
                        net_income=excluded.net_income,
                        total_assets=excluded.total_assets,
                        current_liabilities=excluded.current_liabilities,
                        capital_employed=excluded.capital_employed,
                        stockholders_equity=excluded.stockholders_equity,
                        total_debt=excluded.total_debt,
                        cash_and_equivalents=excluded.cash_and_equivalents,
                        operating_cash_flow=excluded.operating_cash_flow,
                        free_cash_flow=excluded.free_cash_flow,
                        roce_pct=excluded.roce_pct,
                        roe_pct=excluded.roe_pct,
                        operating_margin_pct=excluded.operating_margin_pct,
                        net_margin_pct=excluded.net_margin_pct,
                        debt_to_equity=excluded.debt_to_equity,
                        eps=excluded.eps;
                    """,
                    ratios,
                )

            # Insert/replace screener metrics facts
            if metrics:
                con.executemany(
                    """
                    INSERT INTO instrument_metrics (
                        venue, symbol, metric_key, period, timeframe, value_num, value_text, fiscal_date, as_of_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (symbol, metric_key, period, fiscal_date) DO UPDATE SET
                        value_num=excluded.value_num,
                        value_text=excluded.value_text,
                        as_of_ns=excluded.as_of_ns;
                    """,
                    metrics,
                )

    def get_summary(self) -> dict[str, Any]:
        """Return dataset counts and inventory statistics."""
        with self._get_connection() as con:

            def _fetch_scalar(query: str) -> int:
                r = con.execute(query).fetchone()
                return int(r[0]) if r else 0

            comp_count = _fetch_scalar("SELECT count(*) FROM companies")
            stmt_count = _fetch_scalar("SELECT count(*) FROM financial_statements")
            ratio_count = _fetch_scalar("SELECT count(*) FROM financial_ratios")
            facts_count = _fetch_scalar("SELECT count(*) FROM instrument_metrics")

            dates = con.execute(
                "SELECT min(fiscal_date), max(fiscal_date) FROM financial_ratios"
            ).fetchone()

            large_count = _fetch_scalar(
                "SELECT count(*) FROM companies WHERE cap_category='LARGE_CAP'"
            )
            mid_count = _fetch_scalar("SELECT count(*) FROM companies WHERE cap_category='MID_CAP'")

        return {
            "db_path": str(self.db_path),
            "companies_total": comp_count,
            "large_cap_companies": large_count,
            "mid_cap_companies": mid_count,
            "statements_records": stmt_count,
            "ratios_records": ratio_count,
            "screener_facts": facts_count,
            "min_fiscal_date": str(dates[0]) if dates and dates[0] else None,
            "max_fiscal_date": str(dates[1]) if dates and dates[1] else None,
        }
