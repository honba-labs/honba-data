# pyright: reportMissingTypeStubs=false
"""DuckDB repository adapter implementing FundamentalsRepository."""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import duckdb
import pandas as pd

from honba_data.adapters.duckdb.schema import init_duckdb_schema
from honba_data.domain.events import DomainEvent, FundamentalsIngested
from honba_data.domain.models import Company, FinancialRatios, FinancialStatement
from honba_data.ports.fundamentals_repo import FundamentalsRepository

logger = logging.getLogger(__name__)

DATA_DIR_ENV = "HONBA_DATA_DIR"


def find_data_root(start_path: Path | None = None) -> Path:
    """Resolve data root directory for storing parquet files and DuckDB databases."""
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


class DuckDbFundamentalsStore(FundamentalsRepository):
    """Embedded DuckDB repository adapter for company financial statements and ratios."""

    db_path: Path | str
    _mem_con: duckdb.DuckDBPyConnection | None
    _event_listeners: list[Callable[[DomainEvent], None]]

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._event_listeners = []
        self._mem_con = None

        if db_path is None:
            data_root = find_data_root()
            data_root.mkdir(parents=True, exist_ok=True)
            self.db_path = data_root / "fundamentals.duckdb"
        elif str(db_path) == ":memory:":
            self.db_path = ":memory:"
            self._mem_con = duckdb.connect(":memory:")
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def subscribe(self, listener: Callable[[DomainEvent], None]) -> None:
        """Register a callback listener for published domain events."""
        self._event_listeners.append(listener)

    def _publish_event(self, event: DomainEvent) -> None:
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as exc:
                logger.error("Error in domain event listener: %s", exc)

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        if self._mem_con is not None:
            # For :memory:, return cursor on persistent connection
            return self._mem_con.cursor()
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
        catalog_dir = None
        if isinstance(self.db_path, Path):
            catalog_dir = self.db_path.parent / "catalog"

        with self._get_connection() as con:
            init_duckdb_schema(con, catalog_dir)

    # -------------------------------------------------------------------------
    # FundamentalsRepository Protocol Implementation
    # -------------------------------------------------------------------------

    def save_company(self, company: Company) -> None:
        """Persist or update company directory metadata."""
        with self._get_connection() as con:
            _ = con.execute(
                """
                INSERT INTO companies (
                    symbol, company_name, industry, sector, series, isin_code,
                    cap_category, index_name, updated_at
                ) VALUES (?, ?, ?, '', ?, ?, ?, ?, ?)
                ON CONFLICT (symbol) DO UPDATE SET
                    company_name=excluded.company_name,
                    industry=excluded.industry,
                    series=excluded.series,
                    isin_code=excluded.isin_code,
                    cap_category=excluded.cap_category,
                    index_name=excluded.index_name,
                    updated_at=excluded.updated_at;
                """,
                [
                    company.symbol,
                    company.company_name,
                    company.industry,
                    company.series,
                    company.isin_code,
                    company.cap_category,
                    company.index_name,
                    dt.datetime.now(tz=dt.timezone.utc),
                ],
            )

    def save_statements(self, statements: Sequence[FinancialStatement]) -> None:
        """Persist domain FinancialStatement entities into DuckDB and emit event."""
        if not statements:
            return

        rows: list[tuple[Any, ...]] = []
        symbols: set[str] = set()
        stmt_types: set[str] = set()

        for stmt in statements:
            symbols.add(stmt.symbol)
            stmt_types.add(stmt.statement_type)
            for item_name, item_val in stmt.line_items.items():
                rows.append(
                    (
                        stmt.symbol,
                        stmt.period_type,
                        stmt.statement_type,
                        stmt.fiscal_date,
                        stmt.fiscal_date,
                        stmt.filing_date,
                        item_name,
                        float(item_val),
                    )
                )

        with self._get_connection() as con:
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
                rows,
            )

        for sym in symbols:
            self._publish_event(
                FundamentalsIngested(
                    symbol=sym,
                    period_type=statements[0].period_type,
                    fiscal_date=statements[0].fiscal_date,
                    filing_date=statements[0].filing_date,
                    statement_types=tuple(sorted(stmt_types)),
                    rows_written=len(rows),
                )
            )

    def save_ratios(self, ratios: Sequence[FinancialRatios]) -> None:
        """Persist domain FinancialRatios entities into DuckDB and emit event."""
        if not ratios:
            return

        rows: list[tuple[Any, ...]] = []
        symbols: set[str] = set()

        for r in ratios:
            symbols.add(r.symbol)
            rows.append(
                (
                    r.symbol,
                    r.period_type,
                    r.fiscal_date,
                    r.fiscal_date,
                    r.filing_date,
                    None,  # revenue
                    None,  # ebitda
                    None,  # ebit
                    None,  # net_income
                    None,  # total_assets
                    None,  # current_liabilities
                    None,  # capital_employed
                    None,  # stockholders_equity
                    None,  # total_debt
                    None,  # cash_and_equivalents
                    None,  # operating_cash_flow
                    r.free_cash_flow,
                    r.roce_pct,
                    r.roe_pct,
                    r.operating_margin_pct,
                    r.net_margin_pct,
                    r.debt_to_equity,
                    None,  # eps
                )
            )

        with self._get_connection() as con:
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
                    free_cash_flow=COALESCE(excluded.free_cash_flow, financial_ratios.free_cash_flow),
                    roce_pct=COALESCE(excluded.roce_pct, financial_ratios.roce_pct),
                    roe_pct=COALESCE(excluded.roe_pct, financial_ratios.roe_pct),
                    operating_margin_pct=COALESCE(excluded.operating_margin_pct, financial_ratios.operating_margin_pct),
                    net_margin_pct=COALESCE(excluded.net_margin_pct, financial_ratios.net_margin_pct),
                    debt_to_equity=COALESCE(excluded.debt_to_equity, financial_ratios.debt_to_equity);
                """,
                rows,
            )

        for sym in symbols:
            self._publish_event(
                FundamentalsIngested(
                    symbol=sym,
                    period_type=ratios[0].period_type,
                    fiscal_date=ratios[0].fiscal_date,
                    filing_date=ratios[0].filing_date,
                    statement_types=("RATIOS",),
                    rows_written=len(rows),
                )
            )

    def find_by_symbol(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]:
        """Retrieve financial statements for a symbol and period type."""
        query = """
            SELECT symbol, period_type, statement_type, fiscal_date, filing_date, line_item, value
            FROM financial_statements
            WHERE symbol = ? AND period_type = ?
            ORDER BY fiscal_date, statement_type, line_item;
        """
        with self._get_connection() as con:
            rows = con.execute(query, [symbol.upper(), period.upper()]).fetchall()

        # Group by (symbol, period_type, statement_type, fiscal_date, filing_date)
        grouped: dict[tuple[str, str, str, dt.date, dt.date], dict[str, float]] = {}
        for r in rows:
            sym, ptype, stype, fdate, filing_date, item, val = r
            fd = (
                fdate.date()
                if hasattr(fdate, "date") and callable(getattr(fdate, "date"))
                else fdate
            )
            fld = (
                filing_date.date()
                if hasattr(filing_date, "date") and callable(getattr(filing_date, "date"))
                else (filing_date or fd)
            )
            key = (str(sym), str(ptype), str(stype), fd, fld)
            grouped.setdefault(key, {})[str(item)] = float(val)

        statements: list[FinancialStatement] = []
        for (sym, ptype, stype, fd, fld), items in grouped.items():
            statements.append(
                FinancialStatement(
                    symbol=sym,
                    period_type=ptype,
                    statement_type=stype,
                    fiscal_date=fd,
                    filing_date=fld,
                    line_items=items,
                )
            )
        return statements

    def find_ratios_by_symbol(self, symbol: str, period: str = "FY") -> Sequence[FinancialRatios]:
        """Retrieve financial ratios for a symbol and period type."""
        query = """
            SELECT symbol, period_type, fiscal_date, filing_date,
                   roce_pct, roe_pct, operating_margin_pct, net_margin_pct,
                   debt_to_equity, free_cash_flow
            FROM financial_ratios
            WHERE symbol = ? AND period_type = ?
            ORDER BY fiscal_date;
        """
        with self._get_connection() as con:
            rows = con.execute(query, [symbol.upper(), period.upper()]).fetchall()

        results: list[FinancialRatios] = []
        for r in rows:
            sym, ptype, fd, fld, roce, roe, op_m, net_m, d2e, fcf = r
            fdate = fd.date() if hasattr(fd, "date") and callable(getattr(fd, "date")) else fd
            filing = (
                fld.date()
                if hasattr(fld, "date") and callable(getattr(fld, "date"))
                else (fld or fdate)
            )
            results.append(
                FinancialRatios(
                    symbol=str(sym),
                    fiscal_date=fdate,
                    filing_date=filing,
                    period_type=str(ptype),
                    roce_pct=float(roce) if roce is not None else None,
                    roe_pct=float(roe) if roe is not None else None,
                    operating_margin_pct=float(op_m) if op_m is not None else None,
                    net_margin_pct=float(net_m) if net_m is not None else None,
                    debt_to_equity=float(d2e) if d2e is not None else None,
                    free_cash_flow=float(fcf) if fcf is not None else None,
                )
            )
        return results

    def query_point_in_time(
        self,
        as_of_date: dt.date | str,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> Sequence[FinancialRatios]:
        """Point-in-time query for financial ratios strictly filed on or before as_of_date."""
        raw_records = self.query_point_in_time_ratios(as_of_date, symbols, period_type)
        ratios: list[FinancialRatios] = []
        for rec in raw_records:
            ratios.append(
                FinancialRatios(
                    symbol=str(rec["symbol"]),
                    fiscal_date=rec["fiscal_date"],
                    filing_date=rec.get("filing_date") or rec["fiscal_date"],
                    period_type=str(rec.get("period_type", period_type)),
                    roce_pct=rec.get("roce_pct"),
                    roe_pct=rec.get("roe_pct"),
                    operating_margin_pct=rec.get("operating_margin_pct"),
                    net_margin_pct=rec.get("net_margin_pct"),
                    debt_to_equity=rec.get("debt_to_equity"),
                    free_cash_flow=rec.get("free_cash_flow"),
                )
            )
        return ratios

    def query_point_in_time_statements(
        self,
        as_of_date: dt.date | str,
        statement_type: str | None = None,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> Sequence[FinancialStatement]:
        """Point-in-time query for financial statements filed on or before as_of_date."""
        raw_items = self.query_point_in_time_statements_raw(
            as_of_date, statement_type, symbols, period_type
        )
        grouped: dict[tuple[str, str, str, dt.date, dt.date], dict[str, float]] = {}
        for r in raw_items:
            key = (
                r["symbol"],
                r["period_type"],
                r["statement_type"],
                r["fiscal_date"],
                r.get("filing_date") or r["fiscal_date"],
            )
            grouped.setdefault(key, {})[r["line_item"]] = float(r["value"])

        stmts: list[FinancialStatement] = []
        for (sym, ptype, stype, fd, fld), items in grouped.items():
            stmts.append(
                FinancialStatement(
                    symbol=sym,
                    period_type=ptype,
                    statement_type=stype,
                    fiscal_date=fd,
                    filing_date=fld,
                    line_items=items,
                )
            )
        return stmts

    # -------------------------------------------------------------------------
    # Backward Compatibility Methods
    # -------------------------------------------------------------------------

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
        """Point-in-time query for financial ratios strictly filed on or before ``as_of_date``."""
        as_of_str = str(as_of_date)
        sym_clause = ""
        params: list[Any] = [as_of_str, period_type]

        if symbols is not None and len(symbols) > 0:
            placeholders = ", ".join(["?"] * len(symbols))
            sym_clause = f"AND symbol IN ({placeholders})"
            params.extend(symbols)

        query = f"""
            WITH ranked AS (\n                SELECT *,
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

    def query_point_in_time_statements_raw(
        self,
        as_of_date: dt.date | str,
        statement_type: str | None = None,
        symbols: Sequence[str] | None = None,
        period_type: str = "FY",
    ) -> list[dict[str, Any]]:
        """Raw dictionary point-in-time query for statement line items filed on or before as_of_date."""
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
            WITH ranked AS (\n                SELECT *,
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
        """Perform a point-in-time ASOF JOIN between price bars and financial ratios."""
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
