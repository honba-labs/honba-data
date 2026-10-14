# pyright: reportMissingTypeStubs=false
"""DuckDB storage engine for mutual fund metadata, NAVs, TER, AUM, and holdings."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from honba_data.store.fundamentals import find_data_root


@dataclass(frozen=True)
class SchemeMetadata:
    scheme_code: str
    scheme_name: str
    amc_name: str
    category: str
    plan: str = "Direct"
    option_type: str = "Growth"
    benchmark_symbol: str = "NIFTY_50_TRI"
    isin_growth: str | None = None
    isin_idcw: str | None = None
    fund_manager: str | None = None
    inception_date: dt.date | None = None


@dataclass(frozen=True)
class FundHolding:
    report_date: dt.date
    scheme_code: str
    isin: str
    instrument_name: str
    asset_class: str = "Equity"
    shares_held: int | None = None
    market_value_cr: float = 0.0
    pct_aum: float = 0.0


@dataclass(frozen=True)
class FundStat:
    asof_date: dt.date
    scheme_code: str
    nav: float
    ter_pct: float
    aum_cr: float
    portfolio_turnover_pct: float = 0.0


class DuckDbMutualFundsStore:
    """Embedded DuckDB storage engine for Mutual Funds with Equity Cross-Referencing."""

    db_path: Path

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            data_root = find_data_root()
            data_root.mkdir(parents=True, exist_ok=True)
            self.db_path = data_root / "mutual_funds.duckdb"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def _init_schema(self) -> None:
        """Initialise tables for mutual fund master, stats, holdings, and benchmarks."""
        con = duckdb.connect(str(self.db_path))
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS mf_schemes (
                    scheme_code VARCHAR PRIMARY KEY,
                    isin_growth VARCHAR,
                    isin_idcw VARCHAR,
                    scheme_name VARCHAR NOT NULL,
                    amc_name VARCHAR NOT NULL,
                    category VARCHAR NOT NULL,
                    plan VARCHAR NOT NULL,
                    option_type VARCHAR NOT NULL,
                    benchmark_symbol VARCHAR NOT NULL,
                    fund_manager VARCHAR,
                    inception_date DATE
                );

                CREATE TABLE IF NOT EXISTS mf_fund_stats (
                    asof_date DATE NOT NULL,
                    scheme_code VARCHAR NOT NULL,
                    nav DOUBLE NOT NULL,
                    ter_pct DOUBLE NOT NULL,
                    aum_cr DOUBLE NOT NULL,
                    portfolio_turnover_pct DOUBLE,
                    PRIMARY KEY (asof_date, scheme_code)
                );

                CREATE TABLE IF NOT EXISTS mf_holdings (
                    report_date DATE NOT NULL,
                    scheme_code VARCHAR NOT NULL,
                    isin VARCHAR NOT NULL,
                    instrument_name VARCHAR NOT NULL,
                    asset_class VARCHAR NOT NULL,
                    shares_held BIGINT,
                    market_value_cr DOUBLE NOT NULL,
                    pct_aum DOUBLE NOT NULL,
                    PRIMARY KEY (report_date, scheme_code, isin)
                );

                CREATE TABLE IF NOT EXISTS mf_benchmarks (
                    bar_date DATE NOT NULL,
                    benchmark_symbol VARCHAR NOT NULL,
                    close DOUBLE NOT NULL,
                    PRIMARY KEY (bar_date, benchmark_symbol)
                );
            """)
        finally:
            con.close()

    def upsert_schemes(self, schemes: Sequence[SchemeMetadata]) -> None:
        if not schemes:
            return
        con = duckdb.connect(str(self.db_path))
        try:
            for s in schemes:
                con.execute(
                    """
                    INSERT INTO mf_schemes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (scheme_code) DO UPDATE SET
                        isin_growth = excluded.isin_growth,
                        isin_idcw = excluded.isin_idcw,
                        scheme_name = excluded.scheme_name,
                        amc_name = excluded.amc_name,
                        category = excluded.category,
                        plan = excluded.plan,
                        option_type = excluded.option_type,
                        benchmark_symbol = excluded.benchmark_symbol,
                        fund_manager = excluded.fund_manager,
                        inception_date = excluded.inception_date
                    """,
                    [
                        s.scheme_code,
                        s.isin_growth,
                        s.isin_idcw,
                        s.scheme_name,
                        s.amc_name,
                        s.category,
                        s.plan,
                        s.option_type,
                        s.benchmark_symbol,
                        s.fund_manager,
                        s.inception_date.isoformat() if s.inception_date else None,
                    ],
                )
        finally:
            con.close()

    def upsert_stats(self, stats: Sequence[FundStat]) -> None:
        if not stats:
            return
        con = duckdb.connect(str(self.db_path))
        try:
            for st in stats:
                con.execute(
                    """
                    INSERT INTO mf_fund_stats VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (asof_date, scheme_code) DO UPDATE SET
                        nav = excluded.nav,
                        ter_pct = excluded.ter_pct,
                        aum_cr = excluded.aum_cr,
                        portfolio_turnover_pct = excluded.portfolio_turnover_pct
                    """,
                    [
                        st.asof_date.isoformat(),
                        st.scheme_code,
                        st.nav,
                        st.ter_pct,
                        st.aum_cr,
                        st.portfolio_turnover_pct,
                    ],
                )
        finally:
            con.close()

    def upsert_holdings(self, holdings: Sequence[FundHolding]) -> None:
        if not holdings:
            return
        con = duckdb.connect(str(self.db_path))
        try:
            for h in holdings:
                con.execute(
                    """
                    INSERT INTO mf_holdings VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (report_date, scheme_code, isin) DO UPDATE SET
                        instrument_name = excluded.instrument_name,
                        asset_class = excluded.asset_class,
                        shares_held = excluded.shares_held,
                        market_value_cr = excluded.market_value_cr,
                        pct_aum = excluded.pct_aum
                    """,
                    [
                        h.report_date.isoformat(),
                        h.scheme_code,
                        h.isin,
                        h.instrument_name,
                        h.asset_class,
                        h.shares_held,
                        h.market_value_cr,
                        h.pct_aum,
                    ],
                )
        finally:
            con.close()

    def get_schemes(
        self,
        category: str | None = None,
        plan: str | None = "Direct",
        option: str | None = "Growth",
    ) -> list[dict[str, Any]]:
        con = duckdb.connect(str(self.db_path))
        try:
            conditions = ["1=1"]
            params: list[Any] = []
            if category and category.lower() != "all":
                conditions.append("lower(category) = lower(?)")
                params.append(category)
            if plan:
                conditions.append("lower(plan) = lower(?)")
                params.append(plan)
            if option:
                conditions.append("lower(option_type) = lower(?)")
                params.append(option)

            query = (
                f"SELECT * FROM mf_schemes WHERE {' AND '.join(conditions)} ORDER BY scheme_name"
            )
            cursor = con.execute(query, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row, strict=False)) for row in cursor.fetchall()]
        finally:
            con.close()

    def get_portfolio_fundamentals(
        self,
        scheme_code: str,
        asof_date: dt.date | None = None,
        equity_db_path: Path | str | None = None,
        disclosure_lag_days: int = 10,
    ) -> dict[str, Any]:
        """Cross-reference mutual fund holdings against fundamentals.duckdb.

        Joins ``mf_holdings.isin`` with ``companies.isin_code`` to calculate:
        - Weighted average portfolio ROCE (%)
        - Weighted average portfolio P/E (harmonic/linear)
        - Weighted average portfolio Debt-to-Equity
        - Market Cap distribution (% LargeCap, % MidCap, % SmallCap)
        - Top 10 equity holdings with symbols and fundamentals
        """
        if equity_db_path is None:
            data_root = find_data_root()
            equity_db_path = data_root / "fundamentals.duckdb"

        equity_path = Path(equity_db_path)
        if not equity_path.is_file():
            return {
                "scheme_code": scheme_code,
                "coverage_pct": 0.0,
                "portfolio_roce": None,
                "portfolio_pe": None,
                "portfolio_debt_to_equity": None,
                "large_cap_pct": 0.0,
                "mid_cap_pct": 0.0,
                "small_cap_pct": 0.0,
                "holdings": [],
            }

        con = duckdb.connect(str(self.db_path))
        try:
            con.execute(f"ATTACH '{equity_path}' AS eq (READ_ONLY)")

            asof = asof_date or dt.date.today()
            cutoff = (asof - dt.timedelta(days=max(0, disclosure_lag_days))).isoformat()

            # Find latest portfolio report_date on or before cutoff
            rep_date_res = con.execute(
                """
                SELECT MAX(report_date) FROM mf_holdings
                WHERE scheme_code = ? AND report_date <= ?
                """,
                [scheme_code, cutoff],
            ).fetchone()

            if not rep_date_res or not rep_date_res[0]:
                return {
                    "scheme_code": scheme_code,
                    "coverage_pct": 0.0,
                    "portfolio_roce": None,
                    "portfolio_pe": None,
                    "portfolio_debt_to_equity": None,
                    "large_cap_pct": 0.0,
                    "mid_cap_pct": 0.0,
                    "small_cap_pct": 0.0,
                    "holdings": [],
                }

            report_date_str = str(rep_date_res[0])

            query = f"""
            WITH raw_holdings AS (
                SELECT
                    h.isin,
                    h.instrument_name,
                    h.asset_class,
                    h.market_value_cr,
                    h.pct_aum,
                    c.symbol,
                    c.company_name,
                    c.cap_category,
                    c.pe_trailing,
                    r.roce_pct,
                    r.debt_to_equity
                FROM mf_holdings h
                LEFT JOIN eq.companies c ON h.isin = c.isin_code
                LEFT JOIN (
                    SELECT symbol, roce_pct, debt_to_equity,
                           ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fiscal_date DESC) as rn
                    FROM eq.financial_ratios
                    WHERE fiscal_date <= '{cutoff}'::DATE
                ) r ON c.symbol = r.symbol AND r.rn = 1
                WHERE h.scheme_code = ? AND h.report_date = '{report_date_str}'
            )
            SELECT
                isin,
                coalesce(symbol, isin) as symbol,
                coalesce(company_name, instrument_name) as name,
                asset_class,
                coalesce(cap_category, 'Unclassified') as cap_category,
                pct_aum,
                roce_pct,
                pe_trailing,
                debt_to_equity
            FROM raw_holdings
            ORDER BY pct_aum DESC
            """

            rows = con.execute(query, [scheme_code]).fetchall()

            total_equity_pct = 0.0
            covered_equity_pct = 0.0
            sum_roce_weight = 0.0
            sum_pe_weight = 0.0
            sum_pe_val = 0.0
            sum_de_weight = 0.0

            large_cap = 0.0
            mid_cap = 0.0
            small_cap = 0.0

            holdings_list = []
            for r in rows:
                isin, sym, name, a_class, cap_cat, pct, roce, pe, de = r
                pct = float(pct or 0.0)
                roce_f = float(roce) if roce is not None else None
                pe_f = float(pe) if pe is not None and pe > 0 else None
                de_f = float(de) if de is not None else None

                if a_class == "Equity":
                    total_equity_pct += pct
                    if sym != isin:  # Matched to equity DB
                        covered_equity_pct += pct
                        cap_upper = str(cap_cat).upper()
                        if "LARGE" in cap_upper:
                            large_cap += pct
                        elif "MID" in cap_upper:
                            mid_cap += pct
                        elif cap_cat == "SmallCap":
                            small_cap += pct

                        if roce_f is not None:
                            sum_roce_weight += roce_f * pct
                        if pe_f is not None:
                            sum_pe_val += pe_f * pct
                            sum_pe_weight += pct
                        if de_f is not None:
                            sum_de_weight += de_f * pct

                holdings_list.append(
                    {
                        "isin": isin,
                        "symbol": sym,
                        "name": name,
                        "asset_class": a_class,
                        "cap_category": cap_cat,
                        "pct_aum": pct,
                        "roce_pct": roce_f,
                        "pe_trailing": pe_f,
                        "debt_to_equity": de_f,
                    }
                )

            port_roce = (sum_roce_weight / covered_equity_pct) if covered_equity_pct > 0 else None
            port_pe = (sum_pe_val / sum_pe_weight) if sum_pe_weight > 0 else None
            port_de = (sum_de_weight / covered_equity_pct) if covered_equity_pct > 0 else None
            coverage = (
                (covered_equity_pct / total_equity_pct * 100) if total_equity_pct > 0 else 0.0
            )

            return {
                "scheme_code": scheme_code,
                "report_date": report_date_str,
                "total_equity_pct": round(total_equity_pct, 2),
                "covered_equity_pct": round(covered_equity_pct, 2),
                "coverage_pct": round(coverage, 1),
                "portfolio_roce": round(port_roce, 1) if port_roce is not None else None,
                "portfolio_pe": round(port_pe, 1) if port_pe is not None else None,
                "portfolio_debt_to_equity": round(port_de, 2) if port_de is not None else None,
                "large_cap_pct": round(large_cap, 1),
                "mid_cap_pct": round(mid_cap, 1),
                "small_cap_pct": round(small_cap, 1),
                "holdings": holdings_list,
            }
        finally:
            con.close()

    def get_portfolio_overlap(
        self,
        scheme_a: str,
        scheme_b: str,
        asof_date: dt.date | None = None,
    ) -> dict[str, Any]:
        """Compute the portfolio holdings overlap percentage between two schemes.

        Overlap(A, B) = SUM(min(w_A, w_B)) across common equity holdings.
        """
        con = duckdb.connect(str(self.db_path))
        try:
            asof_str = (asof_date or dt.date.today()).isoformat()
            query = """
            WITH a AS (
                SELECT isin, pct_aum as wt_a
                FROM mf_holdings
                WHERE scheme_code = ? AND report_date = (
                    SELECT MAX(report_date) FROM mf_holdings WHERE scheme_code = ? AND report_date <= ?
                )
            ),
            b AS (
                SELECT isin, pct_aum as wt_b
                FROM mf_holdings
                WHERE scheme_code = ? AND report_date = (
                    SELECT MAX(report_date) FROM mf_holdings WHERE scheme_code = ? AND report_date <= ?
                )
            )
            SELECT
                a.isin,
                a.wt_a,
                b.wt_b,
                least(a.wt_a, b.wt_b) as overlap_wt
            FROM a
            JOIN b ON a.isin = b.isin
            ORDER BY overlap_wt DESC
            """
            rows = con.execute(
                query, [scheme_a, scheme_a, asof_str, scheme_b, scheme_b, asof_str]
            ).fetchall()

            total_overlap = sum(r[3] for r in rows)
            return {
                "scheme_a": scheme_a,
                "scheme_b": scheme_b,
                "overlap_pct": round(total_overlap, 2),
                "common_holdings_count": len(rows),
                "common_holdings": [
                    {
                        "isin": r[0],
                        "weight_a": round(r[1], 2),
                        "weight_b": round(r[2], 2),
                        "overlap": round(r[3], 2),
                    }
                    for r in rows
                ],
            }
        finally:
            con.close()

    def find_funds_holding_symbol(
        self,
        symbol_or_isin: str,
        asof_date: dt.date | None = None,
        min_pct_aum: float = 0.0,
        equity_db_path: Path | str | None = None,
    ) -> list[dict[str, Any]]:
        """Institutional reverse lookup: Find all funds holding a given equity symbol or ISIN."""
        if equity_db_path is None:
            data_root = find_data_root()
            equity_db_path = data_root / "fundamentals.duckdb"

        equity_path = Path(equity_db_path)
        resolved_isin = symbol_or_isin
        if equity_path.is_file() and not symbol_or_isin.startswith("INE"):
            eq_con = duckdb.connect(str(equity_path), read_only=True)
            res = eq_con.execute(
                "SELECT isin_code FROM companies WHERE symbol = ?", [symbol_or_isin.upper()]
            ).fetchone()
            eq_con.close()
            if res and res[0]:
                resolved_isin = res[0]

        con = duckdb.connect(str(self.db_path))
        try:
            asof_str = (asof_date or dt.date.today()).isoformat()
            query = """
            SELECT
                s.scheme_code,
                s.scheme_name,
                s.category,
                s.plan,
                h.pct_aum,
                h.market_value_cr,
                h.report_date
            FROM mf_holdings h
            JOIN mf_schemes s ON h.scheme_code = s.scheme_code
            WHERE h.isin = ? AND h.pct_aum >= ?
              AND h.report_date = (
                  SELECT MAX(report_date) FROM mf_holdings WHERE scheme_code = h.scheme_code AND report_date <= ?
              )
            ORDER BY h.pct_aum DESC
            """
            rows = con.execute(query, [resolved_isin, min_pct_aum, asof_str]).fetchall()
            return [
                {
                    "scheme_code": r[0],
                    "scheme_name": r[1],
                    "category": r[2],
                    "plan": r[3],
                    "pct_aum": float(r[4]),
                    "market_value_cr": float(r[5]),
                    "report_date": str(r[6]),
                    "isin": resolved_isin,
                }
                for r in rows
            ]
        finally:
            con.close()

    def seed_reference_data(self) -> None:
        """Seed canonical mutual fund schemes, historical stats, and equity holdings."""
        schemes = [
            SchemeMetadata(
                scheme_code="122639",
                scheme_name="Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
                amc_name="PPFAS Mutual Fund",
                category="Equity:Flexi Cap",
                plan="Direct",
                option_type="Growth",
                benchmark_symbol="NIFTY_500_TRI",
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
                benchmark_symbol="NIFTY_500_TRI",
                isin_growth="INF179K01BE2",
                fund_manager="Roshi Jain",
                inception_date=dt.date(2013, 1, 1),
            ),
            SchemeMetadata(
                scheme_code="125354",
                scheme_name="Quant Flexi Cap Fund - Direct Plan - Growth",
                amc_name="Quant Mutual Fund",
                category="Equity:Flexi Cap",
                plan="Direct",
                option_type="Growth",
                benchmark_symbol="NIFTY_500_TRI",
                isin_growth="INF966L01AA3",
                fund_manager="Sandeep Tandon",
                inception_date=dt.date(2013, 1, 1),
            ),
            SchemeMetadata(
                scheme_code="120503",
                scheme_name="Axis Bluechip Fund - Direct Plan - Growth",
                amc_name="Axis Mutual Fund",
                category="Equity:Large Cap",
                plan="Direct",
                option_type="Growth",
                benchmark_symbol="NIFTY_50_TRI",
                isin_growth="INF846K01DP8",
                fund_manager="Shreyash Devalkar",
                inception_date=dt.date(2013, 1, 1),
            ),
            SchemeMetadata(
                scheme_code="118834",
                scheme_name="Mirae Asset Large & Midcap Fund - Direct Plan - Growth",
                amc_name="Mirae Asset Mutual Fund",
                category="Equity:Large & Mid Cap",
                plan="Direct",
                option_type="Growth",
                benchmark_symbol="NIFTY_LARGEMIDCAP_250_TRI",
                isin_growth="INF769K01DG0",
                fund_manager="Neelesh Surana",
                inception_date=dt.date(2013, 1, 1),
            ),
            SchemeMetadata(
                scheme_code="118778",
                scheme_name="Nippon India Small Cap Fund - Direct Plan - Growth",
                amc_name="Nippon India Mutual Fund",
                category="Equity:Small Cap",
                plan="Direct",
                option_type="Growth",
                benchmark_symbol="NIFTY_SMALLCAP_250_TRI",
                isin_growth="INF204K01E37",
                fund_manager="Samir Rachh",
                inception_date=dt.date(2013, 1, 1),
            ),
            SchemeMetadata(
                scheme_code="120828",
                scheme_name="Kotak Emerging Equity Fund - Direct Plan - Growth",
                amc_name="Kotak Mahindra Mutual Fund",
                category="Equity:Mid Cap",
                plan="Direct",
                option_type="Growth",
                benchmark_symbol="NIFTY_MIDCAP_150_TRI",
                isin_growth="INF174K01LS2",
                fund_manager="Pankaj Tibrewal",
                inception_date=dt.date(2013, 1, 1),
            ),
        ]
        self.upsert_schemes(schemes)

        stats = [
            FundStat(dt.date(2026, 10, 1), "122639", 85.42, 0.62, 72400.0, 18.5),
            FundStat(dt.date(2026, 10, 1), "118989", 1950.25, 0.81, 58120.0, 32.0),
            FundStat(dt.date(2026, 10, 1), "125354", 102.15, 0.77, 12850.0, 95.0),
            FundStat(dt.date(2026, 10, 1), "120503", 58.30, 0.85, 32100.0, 22.0),
            FundStat(dt.date(2026, 10, 1), "118834", 142.10, 0.68, 36500.0, 28.0),
            FundStat(dt.date(2026, 10, 1), "118778", 168.90, 0.69, 48900.0, 15.0),
            FundStat(dt.date(2026, 10, 1), "120828", 112.40, 0.73, 45200.0, 24.0),
        ]
        self.upsert_stats(stats)

        # Holdings using verified ISINs from fundamentals.duckdb
        holdings = [
            # PPFAS Flexi Cap (122639)
            FundHolding(
                dt.date(2026, 9, 30),
                "122639",
                "INE040A01034",
                "HDFC Bank Ltd",
                "Equity",
                pct_aum=8.5,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "122639",
                "INE002A01018",
                "Reliance Industries Ltd",
                "Equity",
                pct_aum=7.2,
            ),
            FundHolding(
                dt.date(2026, 9, 30), "122639", "INE009A01021", "Infosys Ltd", "Equity", pct_aum=6.8
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "122639",
                "INE296A01032",
                "Bajaj Finance Ltd",
                "Equity",
                pct_aum=5.4,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "122639",
                "INE238A01034",
                "Axis Bank Ltd",
                "Equity",
                pct_aum=4.8,
            ),
            # HDFC Flexi Cap (118989)
            FundHolding(
                dt.date(2026, 9, 30),
                "118989",
                "INE040A01034",
                "HDFC Bank Ltd",
                "Equity",
                pct_aum=9.1,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "118989",
                "INE002A01018",
                "Reliance Industries Ltd",
                "Equity",
                pct_aum=6.5,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "118989",
                "INE397D01024",
                "Bharti Airtel Ltd",
                "Equity",
                pct_aum=5.8,
            ),
            FundHolding(
                dt.date(2026, 9, 30), "118989", "INE009A01021", "Infosys Ltd", "Equity", pct_aum=4.5
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "118989",
                "INE117A01022",
                "ABB India Ltd",
                "Equity",
                pct_aum=3.8,
            ),
            # Quant Flexi Cap (125354)
            FundHolding(
                dt.date(2026, 9, 30),
                "125354",
                "INE002A01018",
                "Reliance Industries Ltd",
                "Equity",
                pct_aum=9.8,
            ),
            FundHolding(
                dt.date(2026, 9, 30), "125354", "INE118H01025", "BSE Ltd", "Equity", pct_aum=6.2
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "125354",
                "INE397D01024",
                "Bharti Airtel Ltd",
                "Equity",
                pct_aum=5.5,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "125354",
                "INE117A01022",
                "ABB India Ltd",
                "Equity",
                pct_aum=4.6,
            ),
            # Axis Bluechip (120503)
            FundHolding(
                dt.date(2026, 9, 30),
                "120503",
                "INE040A01034",
                "HDFC Bank Ltd",
                "Equity",
                pct_aum=9.5,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "120503",
                "INE002A01018",
                "Reliance Industries Ltd",
                "Equity",
                pct_aum=8.4,
            ),
            FundHolding(
                dt.date(2026, 9, 30), "120503", "INE009A01021", "Infosys Ltd", "Equity", pct_aum=7.9
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "120503",
                "INE021A01026",
                "Asian Paints Ltd",
                "Equity",
                pct_aum=5.1,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "120503",
                "INE192R01011",
                "Avenue Supermarts Ltd",
                "Equity",
                pct_aum=4.9,
            ),
            # Mirae Asset Large & Midcap (118834)
            FundHolding(
                dt.date(2026, 9, 30),
                "118834",
                "INE040A01034",
                "HDFC Bank Ltd",
                "Equity",
                pct_aum=7.8,
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "118834",
                "INE002A01018",
                "Reliance Industries Ltd",
                "Equity",
                pct_aum=6.4,
            ),
            FundHolding(
                dt.date(2026, 9, 30), "118834", "INE118H01025", "BSE Ltd", "Equity", pct_aum=4.2
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "118834",
                "INE117A01022",
                "ABB India Ltd",
                "Equity",
                pct_aum=3.9,
            ),
            # Nippon Small Cap (118778)
            FundHolding(
                dt.date(2026, 9, 30), "118778", "INE118H01025", "BSE Ltd", "Equity", pct_aum=4.8
            ),
            FundHolding(
                dt.date(2026, 9, 30),
                "118778",
                "INE117A01022",
                "ABB India Ltd",
                "Equity",
                pct_aum=3.5,
            ),
            # Kotak Emerging Equity (120828)
            FundHolding(
                dt.date(2026, 9, 30),
                "120828",
                "INE117A01022",
                "ABB India Ltd",
                "Equity",
                pct_aum=5.1,
            ),
            FundHolding(
                dt.date(2026, 9, 30), "120828", "INE118H01025", "BSE Ltd", "Equity", pct_aum=4.5
            ),
        ]
        self.upsert_holdings(holdings)
