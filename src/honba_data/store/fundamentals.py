# pyright: reportMissingTypeStubs=false
"""DuckDB storage engine for company financial statements and ratios."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import duckdb
import pandas as pd

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

    db_path: Path

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

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Provide a connection context for querying or transactions."""
        return self._get_connection()

    def get_existing_symbols(self) -> set[str]:
        """Return the set of company symbols already persisted in DuckDB."""
        with self._get_connection() as con:
            return {
                str(cast(object, r[0]))
                for r in con.execute("SELECT symbol FROM companies").fetchall()
            }

    def _init_schema(self) -> None:
        with self._get_connection() as con:
            # 1. Company directory
            _ = con.execute("""
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
            # Supports point-in-time filing_date (announcement date) vs period_end_date
            _ = con.execute("""
                CREATE TABLE IF NOT EXISTS financial_statements (
                    symbol VARCHAR,
                    period_type VARCHAR,
                    statement_type VARCHAR,
                    fiscal_date DATE,
                    period_end_date DATE,
                    filing_date DATE,
                    line_item VARCHAR,
                    value DOUBLE,
                    currency VARCHAR DEFAULT 'INR',
                    PRIMARY KEY (symbol, period_type, statement_type, fiscal_date, line_item)
                );
            """)
            _ = con.execute(
                "ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS period_end_date DATE;"
            )
            _ = con.execute(
                "ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS filing_date DATE;"
            )

            # 3. Standardized financial ratios per fiscal date
            # Supports point-in-time filing_date (announcement date) vs period_end_date
            _ = con.execute("""
                CREATE TABLE IF NOT EXISTS financial_ratios (
                    symbol VARCHAR,
                    period_type VARCHAR,
                    fiscal_date DATE,
                    period_end_date DATE,
                    filing_date DATE,
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
            _ = con.execute(
                "ALTER TABLE financial_ratios ADD COLUMN IF NOT EXISTS period_end_date DATE;"
            )
            _ = con.execute(
                "ALTER TABLE financial_ratios ADD COLUMN IF NOT EXISTS filing_date DATE;"
            )

            # 4. Canonical Honba screener facts table (Design.md Section 14.3)
            _ = con.execute("""
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

            # Ensure unique indexes for ON CONFLICT target resolution
            _ = con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_symbol ON companies(symbol);"
            )
            _ = con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_statements_pk ON financial_statements(symbol, period_type, statement_type, fiscal_date, line_item);"
            )
            _ = con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ratios_pk ON financial_ratios(symbol, period_type, fiscal_date);"
            )
            _ = con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_metrics_pk ON instrument_metrics(symbol, metric_key, period, fiscal_date);"
            )

            # 5. Canonical unified price bars view over Parquet catalog (Option A Lakehouse)
            catalog_glob = (
                self.db_path.parent / "catalog" / "1D" / "NSE" / "*" / "*.parquet"
            ).resolve()
            if (self.db_path.parent / "catalog").is_dir():
                _ = con.execute(f"""
                    CREATE OR REPLACE VIEW bars_1d AS
                    SELECT
                        regexp_extract(filename, 'NSE/([^/]+)/', 1) AS symbol,
                        epoch_ms(ts // 1000000)::DATE AS bar_date,
                        open,
                        high,
                        low,
                        close,
                        volume
                    FROM read_parquet('{catalog_glob}', filename=true);
                """)

    def save_company_data(
        self,
        company_meta: Mapping[str, object],
        statements: Sequence[tuple[object, ...]],
        ratios: Sequence[tuple[object, ...]],
        metrics: Sequence[tuple[object, ...]],
    ) -> None:
        """Atomically upsert all financial data for a single company."""
        with self._get_connection() as con:
            # Upsert company
            _ = con.execute(
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

            # Insert/replace statements with point-in-time filing_date support
            if statements:
                norm_stmts = []
                for s in statements:
                    if len(s) == 6:
                        norm_stmts.append((s[0], s[1], s[2], s[3], s[3], s[3], s[4], s[5]))
                    elif len(s) == 7:
                        norm_stmts.append((s[0], s[1], s[2], s[3], s[3], s[4], s[5], s[6]))
                    elif len(s) >= 8:
                        norm_stmts.append((s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]))

                _ = con.executemany(
                    """
                    INSERT INTO financial_statements (
                        symbol, period_type, statement_type, fiscal_date, period_end_date, filing_date, line_item, value
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (symbol, period_type, statement_type, fiscal_date, line_item)
                    DO UPDATE SET
                        period_end_date=excluded.period_end_date,
                        filing_date=excluded.filing_date,
                        value=excluded.value;
                    """,
                    norm_stmts,
                )

            # Insert/replace ratios with point-in-time filing_date support
            if ratios:
                norm_ratios = []
                for r in ratios:
                    if len(r) == 21:
                        norm_ratios.append((r[0], r[1], r[2], r[2], r[2], *r[3:]))
                    elif len(r) == 22:
                        norm_ratios.append((r[0], r[1], r[2], r[2], r[3], *r[4:]))
                    elif len(r) >= 23:
                        norm_ratios.append(r[:23])

                _ = con.executemany(
                    """
                    INSERT INTO financial_ratios (
                        symbol, period_type, fiscal_date, period_end_date, filing_date,
                        revenue, ebitda, ebit, net_income, total_assets, current_liabilities,
                        capital_employed, stockholders_equity, total_debt, cash_and_equivalents,
                        operating_cash_flow, free_cash_flow, roce_pct, roe_pct,
                        operating_margin_pct, net_margin_pct, debt_to_equity, eps
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (symbol, period_type, fiscal_date) DO UPDATE SET
                        period_end_date=excluded.period_end_date,
                        filing_date=excluded.filing_date,
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
                    norm_ratios,
                )

            # Insert/replace screener metrics facts
            if metrics:
                _ = con.executemany(
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

    def query_point_in_time_ratios(
        self,
        as_of_date: dt.date | str,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> list[dict[str, Any]]:
        """Point-in-time query for financial ratios strictly filed on or before ``as_of_date``.

        Eliminates look-ahead bias by filtering on ``COALESCE(filing_date, fiscal_date) <= as_of_date``
        and selecting the latest available filing for each company as of that date.
        """
        as_of_str = str(as_of_date)
        sym_clause = ""
        params: list[Any] = [as_of_str, period_type]

        if symbols is not None and len(symbols) > 0:
            placeholders = ", ".join(["?"] * len(symbols))
            sym_clause = f"AND symbol IN ({placeholders})"
            params.extend(symbols)

        query = f"""
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol
                           ORDER BY COALESCE(filing_date, fiscal_date) DESC, fiscal_date DESC
                       ) as rn
                FROM financial_ratios
                WHERE COALESCE(filing_date, fiscal_date) <= ?
                  AND period_type = ?
                  {sym_clause}
            )
            SELECT * EXCLUDE (rn)
            FROM ranked
            WHERE rn = 1
            ORDER BY symbol;
        """
        with self._get_connection() as con:
            df = con.execute(query, params).df()
            for col in ["fiscal_date", "period_end_date", "filing_date"]:
                if col in df.columns:
                    df[col] = df[col].apply(
                        lambda v: (
                            v.date() if hasattr(v, "date") and callable(getattr(v, "date")) else v
                        )
                    )
            return cast(list[dict[str, Any]], df.to_dict(orient="records"))

    def query_point_in_time_statements(
        self,
        as_of_date: dt.date | str,
        statement_type: str | None = None,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> list[dict[str, Any]]:
        """Point-in-time query for statement line items filed on or before ``as_of_date``."""
        as_of_str = str(as_of_date)
        stmt_clause = ""
        params: list[Any] = [as_of_str, period_type]

        if statement_type:
            stmt_clause = "AND statement_type = ?"
            params.append(statement_type)

        sym_clause = ""
        if symbols is not None and len(symbols) > 0:
            placeholders = ", ".join(["?"] * len(symbols))
            sym_clause = f"AND symbol IN ({placeholders})"
            params.extend(symbols)

        query = f"""
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol, statement_type, line_item
                           ORDER BY COALESCE(filing_date, fiscal_date) DESC, fiscal_date DESC
                       ) as rn
                FROM financial_statements
                WHERE COALESCE(filing_date, fiscal_date) <= ?
                  AND period_type = ?
                  {stmt_clause}
                  {sym_clause}
            )
            SELECT * EXCLUDE (rn)
            FROM ranked
            WHERE rn = 1
            ORDER BY symbol, statement_type, line_item;
        """
        with self._get_connection() as con:
            df = con.execute(query, params).df()
            for col in ["fiscal_date", "period_end_date", "filing_date"]:
                if col in df.columns:
                    df[col] = df[col].apply(
                        lambda v: (
                            v.date() if hasattr(v, "date") and callable(getattr(v, "date")) else v
                        )
                    )
            return cast(list[dict[str, Any]], df.to_dict(orient="records"))

    def as_of_join_prices_and_fundamentals(
        self,
        prices_df: pd.DataFrame,
        period_type: str = "FY",
    ) -> pd.DataFrame:
        """Perform a point-in-time ASOF JOIN between price bars and financial ratios.

        Guarantees that each price bar on ``bar_date`` receives only fundamentals
        published on or prior to that date (``p.bar_date >= r.filing_date``).
        """
        with self._get_connection() as con:
            con.register("prices_input", prices_df)
            query = """
                SELECT
                    p.*,
                    r.period_type AS fund_period_type,
                    r.fiscal_date AS fund_fiscal_date,
                    r.filing_date AS fund_filing_date,
                    r.revenue,
                    r.ebitda,
                    r.net_income,
                    r.roce_pct,
                    r.roe_pct,
                    r.debt_to_equity,
                    r.eps
                FROM prices_input p
                ASOF LEFT JOIN (
                    SELECT * FROM financial_ratios
                    WHERE period_type = ?
                ) r
                ON p.symbol = r.symbol AND p.bar_date >= COALESCE(r.filing_date, r.fiscal_date)
                ORDER BY p.symbol, p.bar_date;
            """
            res = con.execute(query, [period_type]).df()
            for col in ["bar_date", "fund_fiscal_date", "fund_filing_date"]:
                if col in res.columns:
                    res[col] = res[col].apply(
                        lambda v: (
                            v.date() if hasattr(v, "date") and callable(getattr(v, "date")) else v
                        )
                    )
            return res

    def get_summary(self) -> dict[str, str | int | None]:
        """Return dataset counts and inventory statistics."""
        with self._get_connection() as con:

            def _fetch_scalar(query: str) -> int:
                r = con.execute(query).fetchone()
                return int(cast(int, r[0])) if r and r[0] is not None else 0

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
            small_count = _fetch_scalar(
                "SELECT count(*) FROM companies WHERE cap_category='SMALL_CAP'"
            )

        return {
            "db_path": str(self.db_path),
            "companies_total": comp_count,
            "large_cap_companies": large_count,
            "mid_cap_companies": mid_count,
            "small_cap_companies": small_count,
            "statements_records": stmt_count,
            "ratios_records": ratio_count,
            "screener_facts": facts_count,
            "min_fiscal_date": str(cast(object, dates[0]))
            if dates and dates[0] is not None
            else None,
            "max_fiscal_date": str(cast(object, dates[1]))
            if dates and dates[1] is not None
            else None,
        }
