"""DuckDB schema definitions, DDL migrations, and table versioning."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def init_duckdb_schema(con: duckdb.DuckDBPyConnection, catalog_dir: Path | None = None) -> None:
    """Initialize DuckDB tables, indexes, and views for company fundamentals and ratios."""
    # Version metadata
    _ = con.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

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
    _ = con.execute("ALTER TABLE financial_statements ADD COLUMN IF NOT EXISTS filing_date DATE;")

    # 3. Standardized financial ratios per fiscal date
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
    _ = con.execute("ALTER TABLE financial_ratios ADD COLUMN IF NOT EXISTS period_end_date DATE;")
    _ = con.execute("ALTER TABLE financial_ratios ADD COLUMN IF NOT EXISTS filing_date DATE;")

    # 4. Canonical Honba screener facts table
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

    # Indexes for ON CONFLICT target resolution
    _ = con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_symbol ON companies(symbol);")
    _ = con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_statements_pk ON financial_statements(symbol, period_type, statement_type, fiscal_date, line_item);"
    )
    _ = con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_ratios_pk ON financial_ratios(symbol, period_type, fiscal_date);"
    )
    _ = con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_metrics_pk ON instrument_metrics(symbol, metric_key, period, fiscal_date);"
    )

    # 5. Price bars view over Parquet catalog (supports both Hive standard and legacy layout)
    if catalog_dir is not None and catalog_dir.is_dir():
        # Check if Hive partitioned or legacy layout exists
        hive_glob = (catalog_dir / "timeframe=1D" / "exchange=NSE" / "*" / "*.parquet").resolve()
        legacy_glob = (catalog_dir / "1D" / "NSE" / "*" / "*.parquet").resolve()

        # Pick whichever exists, or fallback to Hive glob
        chosen_glob = hive_glob if any(catalog_dir.glob("timeframe=1D")) else legacy_glob
        try:
            _ = con.execute(f"""
                CREATE OR REPLACE VIEW bars_1d AS
                SELECT
                    COALESCE(regexp_extract(filename, 'symbol=([^/]+)/', 1), regexp_extract(filename, 'NSE/([^/]+)/', 1)) AS symbol,
                    epoch_ms(ts // 1000000)::DATE AS bar_date,
                    open,
                    high,
                    low,
                    close,
                    volume
                FROM read_parquet('{chosen_glob}', filename=true);
            """)
        except Exception as exc:
            logger.debug("Could not create bars_1d view: %s", exc)

    _ = con.execute(
        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
        [SCHEMA_VERSION],
    )
