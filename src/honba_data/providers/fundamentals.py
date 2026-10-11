"""DuckDB-backed Fundamental Data Downloader (Yearly & Quarterly Financials).

Downloads and stores yearly and quarterly financial statements, balance sheets, cash flows,
and computed fundamental ratios (P/E, ROCE, ROE, margins, debt/equity) for Indian equities
(Nifty 100 Large-Cap and Nifty Midcap 150).

STRICT INVARIANT:
Does NOT store daily or intraday stock/equity price bars. Only financial statements,
quarterly filings, balance sheets, and fundamental ratios are persisted in DuckDB.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import io
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yfinance as yf

from honba_data.store.fundamentals import DuckDbFundamentalsStore

logger = logging.getLogger(__name__)

NSE_NIFTY_100_URL = "https://archives.nseindia.com/content/indices/ind_nifty100list.csv"
NSE_NIFTY_MIDCAP_150_URL = (
    "https://archives.nseindia.com/content/indices/ind_niftymidcap150list.csv"
)


@dataclasses.dataclass(frozen=True)
class CompanyMetadata:
    """Metadata for an Indian listed company."""

    symbol: str
    company_name: str
    industry: str
    series: str
    isin_code: str
    cap_category: str  # "LARGE_CAP" | "MID_CAP"
    index_name: str  # "NIFTY 100" | "NIFTY MIDCAP 150"


def fetch_large_midcap_universe(category: str = "ALL") -> list[CompanyMetadata]:
    """Fetch official constituent lists for Nifty 100 and Nifty Midcap 150 from NSE archives."""
    category_norm = category.upper()
    results: list[CompanyMetadata] = []
    headers = {"User-Agent": "Mozilla/5.0"}

    def _load_csv(url: str, cap: str, idx: str) -> list[CompanyMetadata]:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        items: list[CompanyMetadata] = []
        for row in reader:
            sym = row.get("Symbol", "").strip()
            if sym:
                items.append(
                    CompanyMetadata(
                        symbol=sym,
                        company_name=row.get("Company Name", "").strip(),
                        industry=row.get("Industry", "").strip(),
                        series=row.get("Series", "EQ").strip(),
                        isin_code=row.get("ISIN Code", "").strip(),
                        cap_category=cap,
                        index_name=idx,
                    )
                )
        return items

    if category_norm in ("LARGE_CAP", "LARGE", "ALL"):
        try:
            results.extend(_load_csv(NSE_NIFTY_100_URL, "LARGE_CAP", "NIFTY 100"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            logger.warning(f"Failed to fetch Nifty 100 list from NSE: {e}")

    if category_norm in ("MID_CAP", "MID", "ALL"):
        try:
            results.extend(_load_csv(NSE_NIFTY_MIDCAP_150_URL, "MID_CAP", "NIFTY MIDCAP 150"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            logger.warning(f"Failed to fetch Nifty Midcap 150 list from NSE: {e}")

    # Deduplicate by symbol preserving order
    seen: set[str] = set()
    deduped: list[CompanyMetadata] = []
    for c in results:
        if c.symbol not in seen:
            seen.add(c.symbol)
            deduped.append(c)

    return deduped


def _safe_get(df: pd.DataFrame | None, keys: list[str], col: Any) -> float | None:
    if df is None or df.empty:
        return None
    target_col = col
    if col not in df.columns:
        matching_cols = [c for c in df.columns if c <= col]
        if not matching_cols:
            return None
        target_col = max(matching_cols)

    for k in keys:
        if k in df.index:
            try:
                v = df.loc[k, target_col]
                if pd.notna(v):
                    return float(v)
            except (ValueError, TypeError, AttributeError, KeyError):
                continue
    return None


def fetch_and_parse_company(
    comp: CompanyMetadata,
) -> tuple[dict[str, Any], list[Any], list[Any], list[Any]]:
    """Download yearly and quarterly statements via yfinance and compute financial ratios."""
    ticker_sym = f"{comp.symbol}.NS"
    t = yf.Ticker(ticker_sym)

    fin_y = t.financials
    fin_q = t.quarterly_financials
    bs_y = t.balance_sheet
    bs_q = t.quarterly_balance_sheet
    cf_y = t.cashflow
    cf_q = t.quarterly_cashflow

    try:
        info = t.info or {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        info = {}

    comp_dict: dict[str, Any] = {
        "symbol": comp.symbol,
        "company_name": comp.company_name,
        "industry": comp.industry,
        "sector": info.get("sector") or comp.industry,
        "series": comp.series,
        "isin_code": comp.isin_code,
        "cap_category": comp.cap_category,
        "index_name": comp.index_name,
        "market_cap": info.get("marketCap"),
        "pe_trailing": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "pb_ratio": info.get("priceToBook"),
        "trailing_eps": info.get("trailingEps"),
        "forward_eps": info.get("forwardEps"),
    }

    statements_records: list[tuple[str, str, str, str, str, float]] = []

    def _extract_statements(df: pd.DataFrame | None, period: str, stmt_name: str) -> None:
        if df is None or df.empty:
            return
        for col in df.columns:
            try:
                d_str = str(col.date())
            except (ValueError, TypeError, AttributeError):
                continue
            series_or_val = df[col]
            if hasattr(series_or_val, "items"):
                for item, val in series_or_val.items():
                    try:
                        v_float = float(val)  # type: ignore[arg-type]
                        if pd.notna(v_float):
                            statements_records.append(
                                (comp.symbol, period, stmt_name, d_str, str(item), v_float)
                            )
                    except (ValueError, TypeError):
                        continue

    _extract_statements(fin_y, "FY", "INCOME_STATEMENT")
    _extract_statements(fin_q, "FQ", "INCOME_STATEMENT")
    _extract_statements(bs_y, "FY", "BALANCE_SHEET")
    _extract_statements(bs_q, "FQ", "BALANCE_SHEET")
    _extract_statements(cf_y, "FY", "CASH_FLOW")
    _extract_statements(cf_q, "FQ", "CASH_FLOW")

    ratios_records: list[tuple[Any, ...]] = []
    metrics_records: list[tuple[Any, ...]] = []

    def _compute_period_ratios(
        fin_df: pd.DataFrame | None,
        bs_df: pd.DataFrame | None,
        cf_df: pd.DataFrame | None,
        period: str,
    ) -> None:
        if fin_df is None or fin_df.empty:
            return
        dates = [c for c in fin_df.columns]
        for d in dates:
            try:
                d_str = str(d.date())
                d_date = d.date()
                as_of_ns = int(
                    dt.datetime.combine(d_date, dt.time(18, 0), tzinfo=dt.timezone.utc).timestamp()
                    * 1e9
                )
            except (ValueError, TypeError, AttributeError):
                continue

            rev = _safe_get(
                fin_df,
                ["Total Revenue", "Operating Revenue", "Gross Revenue", "Net Interest Income"],
                d,
            )
            ebitda = _safe_get(fin_df, ["EBITDA", "Normalized EBITDA"], d)
            ebit = _safe_get(
                fin_df, ["EBIT", "Operating Income", "Pretax Income", "Net Operating Profit"], d
            )
            net_inc = _safe_get(
                fin_df,
                [
                    "Net Income Common Stockholders",
                    "Net Income",
                    "Net Income Continuous Operations",
                ],
                d,
            )
            eps = _safe_get(fin_df, ["Diluted EPS", "Basic EPS"], d)

            tot_assets = _safe_get(bs_df, ["Total Assets"], d)
            cur_liab = _safe_get(bs_df, ["Current Liabilities"], d) or 0.0
            equity = _safe_get(
                bs_df,
                [
                    "Stockholders Equity",
                    "Common Stock Equity",
                    "Total Equity Gross Minority Interest",
                ],
                d,
            )
            total_debt = _safe_get(bs_df, ["Total Debt", "Net Debt"], d)
            cash = _safe_get(
                bs_df,
                ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
                d,
            )

            ocf = _safe_get(
                cf_df, ["Operating Cash Flow", "Cash Flows From Used In Operating Activities"], d
            )
            fcf = _safe_get(cf_df, ["Free Cash Flow"], d)
            if fcf is None and ocf is not None:
                capex = _safe_get(cf_df, ["Capital Expenditure"], d) or 0.0
                fcf = ocf - abs(capex)

            cap_employed = (
                (tot_assets - cur_liab) if tot_assets and tot_assets > cur_liab else tot_assets
            )
            roce = (
                (ebit / cap_employed * 100) if ebit and cap_employed and cap_employed > 0 else None
            )
            roe = (net_inc / equity * 100) if net_inc and equity and equity > 0 else None
            op_margin = (ebit / rev * 100) if ebit and rev and rev > 0 else None
            net_margin = (net_inc / rev * 100) if net_inc and rev and rev > 0 else None
            debt_eq = (total_debt / equity) if total_debt and equity and equity > 0 else None

            ratios_records.append(
                (
                    comp.symbol,
                    period,
                    d_str,
                    rev,
                    ebitda,
                    ebit,
                    net_inc,
                    tot_assets,
                    cur_liab,
                    cap_employed,
                    equity,
                    total_debt,
                    cash,
                    ocf,
                    fcf,
                    roce,
                    roe,
                    op_margin,
                    net_margin,
                    debt_eq,
                    eps,
                )
            )

            if roce is not None:
                metrics_records.append(
                    (
                        "NSE",
                        comp.symbol,
                        "return_on_capital_employed",
                        period,
                        "1D",
                        roce,
                        None,
                        d_str,
                        as_of_ns,
                    )
                )
            if roe is not None:
                metrics_records.append(
                    (
                        "NSE",
                        comp.symbol,
                        "return_on_equity_fq" if period == "FQ" else "return_on_equity",
                        period,
                        "1D",
                        roe,
                        None,
                        d_str,
                        as_of_ns,
                    )
                )
            if eps is not None:
                metrics_records.append(
                    (
                        "NSE",
                        comp.symbol,
                        "earnings_per_share_diluted_ttm"
                        if period == "FQ"
                        else "earnings_per_share",
                        period,
                        "1D",
                        eps,
                        None,
                        d_str,
                        as_of_ns,
                    )
                )
            if rev is not None:
                metrics_records.append(
                    ("NSE", comp.symbol, "total_revenue", period, "1D", rev, None, d_str, as_of_ns)
                )
            if net_inc is not None:
                metrics_records.append(
                    ("NSE", comp.symbol, "net_income", period, "1D", net_inc, None, d_str, as_of_ns)
                )

    _compute_period_ratios(fin_y, bs_y, cf_y, "FY")
    _compute_period_ratios(fin_q, bs_q, cf_q, "FQ")

    today_date = dt.datetime.now(tz=dt.timezone.utc).date()
    today_ns = int(dt.datetime.now(tz=dt.timezone.utc).timestamp() * 1e9)
    if info.get("trailingPE"):
        metrics_records.append(
            (
                "NSE",
                comp.symbol,
                "price_earnings_ttm",
                "TTM",
                "1D",
                float(info["trailingPE"]),
                None,
                str(today_date),
                today_ns,
            )
        )
    if info.get("priceToBook"):
        metrics_records.append(
            (
                "NSE",
                comp.symbol,
                "price_book",
                "TTM",
                "1D",
                float(info["priceToBook"]),
                None,
                str(today_date),
                today_ns,
            )
        )

    return comp_dict, statements_records, ratios_records, metrics_records


def download_fundamentals_to_duckdb(
    universe_category: str = "ALL",
    db_path: Path | str | None = None,
    max_workers: int = 8,
    limit: int | None = None,
    skip_existing: bool = True,
    progress_callback: Any = None,
) -> dict[str, Any]:
    """Download yearly and quarterly financials for large and mid cap companies and store in DuckDB."""
    store = DuckDbFundamentalsStore(db_path=db_path)
    companies = fetch_large_midcap_universe(universe_category)

    if skip_existing:
        try:
            with store._get_connection() as con:
                existing = {r[0] for r in con.execute("SELECT symbol FROM companies").fetchall()}
            companies = [c for c in companies if c.symbol not in existing]
        except (duckdb.Error, OSError):
            pass

    if limit is not None and limit > 0:
        companies = companies[:limit]

    total_comps = len(companies)
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_comp = {
            executor.submit(fetch_and_parse_company, comp): comp for comp in companies
        }

        for i, future in enumerate(as_completed(future_to_comp), start=1):
            comp = future_to_comp[future]
            try:
                comp_meta, stmts, ratios, metrics = future.result()
                store.save_company_data(comp_meta, stmts, ratios, metrics)
                success_count += 1
                if progress_callback:
                    progress_callback(i, total_comps, comp.symbol, True)
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
            ) as e:
                fail_count += 1
                logger.error(f"Error processing {comp.symbol}: {e}")
                if progress_callback:
                    progress_callback(i, total_comps, comp.symbol, False)

    summary = store.get_summary()
    summary["success_count"] = success_count
    summary["fail_count"] = fail_count
    return summary
