#!/usr/bin/env python3
"""scripts/verify_fundamentals.py: Cross-verify DuckDB fundamental data against official audited corporate filings.

Runs automated reconciliations:
1. Ground Truth Benchmark Comparison (TCS, Reliance, Britannia) against BSE/NSE audited annual filings.
2. GAAP/IndAS Accounting Identity Invariant Audits across all 250 Large & Mid-Cap companies.
3. Renders a Rich verification scorecard.

Usage::

    python scripts/verify_fundamentals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add honba_data src to path
REPO_ROOT = Path(__file__).resolve().parents[1]
HONBA_DATA_SRC = REPO_ROOT / "src"
if str(HONBA_DATA_SRC) not in sys.path:
    sys.path.insert(0, str(HONBA_DATA_SRC))

import duckdb
from rich.console import Console
from rich.table import Table

from honba_data.store.fundamentals import find_data_root

console = Console()

# Official Audited Ground Truth Benchmarks from BSE/NSE annual reports
BENCHMARKS = [
    {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "Total Revenue (₹ Cr)",
        "expected": 240893.0,
        "query_col": "revenue / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "Operating EBIT (₹ Cr)",
        "expected": 62775.0,
        "query_col": "ebit / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "Net Income (PAT) (₹ Cr)",
        "expected": 45908.0,
        "query_col": "net_income / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "Stockholders Equity (₹ Cr)",
        "expected": 90489.0,
        "query_col": "stockholders_equity / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "ROCE %",
        "expected": 62.56,
        "query_col": "roce_pct",
        "tolerance_pct": 1.0,
    },
    {
        "symbol": "TCS",
        "company_name": "Tata Consultancy Services Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "ROE %",
        "expected": 50.73,
        "query_col": "roe_pct",
        "tolerance_pct": 1.0,
    },
    {
        "symbol": "RELIANCE",
        "company_name": "Reliance Industries Ltd. (Consolidated)",
        "fiscal_date": "2024-03-31",
        "metric": "Consolidated Revenue (₹ Cr)",
        "expected": 901064.0,
        "query_col": "revenue / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "RELIANCE",
        "company_name": "Reliance Industries Ltd. (Consolidated)",
        "fiscal_date": "2024-03-31",
        "metric": "Operating EBIT (₹ Cr)",
        "expected": 126112.0,
        "query_col": "ebit / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "RELIANCE",
        "company_name": "Reliance Industries Ltd. (Consolidated)",
        "fiscal_date": "2024-03-31",
        "metric": "Net Income (PAT) (₹ Cr)",
        "expected": 69621.0,
        "query_col": "net_income / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "RELIANCE",
        "company_name": "Reliance Industries Ltd. (Consolidated)",
        "fiscal_date": "2024-03-31",
        "metric": "Consolidated Equity (₹ Cr)",
        "expected": 793481.0,
        "query_col": "stockholders_equity / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "BRITANNIA",
        "company_name": "Britannia Industries Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "Revenue (₹ Cr)",
        "expected": 16546.2,
        "query_col": "revenue / 1e7",
        "tolerance_pct": 0.5,
    },
    {
        "symbol": "BRITANNIA",
        "company_name": "Britannia Industries Ltd.",
        "fiscal_date": "2024-03-31",
        "metric": "Net Profit (PAT) (₹ Cr)",
        "expected": 2139.8,
        "query_col": "net_income / 1e7",
        "tolerance_pct": 0.5,
    },
]


def main() -> int:
    db_path = find_data_root() / "fundamentals.duckdb"
    console.print(
        f"[bold cyan]Cross-Verifying Fundamentals Database:[/bold cyan] [green]{db_path}[/green]\n"
    )

    if not db_path.is_file():
        console.print(f"[bold red]Error: Database file not found at {db_path}[/bold red]")
        return 1

    con = duckdb.connect(str(db_path), read_only=True)

    # 1. Ground Truth Reconciliation Table
    table = Table(
        title="1. Audited Ground Truth Benchmark Reconciliations (BSE / NSE Filings)",
        header_style="bold magenta",
    )
    table.add_column("Symbol", style="cyan")
    table.add_column("Company Name")
    table.add_column("Fiscal Date", style="blue")
    table.add_column("Metric", style="yellow")
    table.add_column("Audited Filing", justify="right")
    table.add_column("DuckDB Stored", justify="right")
    table.add_column("Discrepancy %", justify="right")
    table.add_column("Status", justify="center")

    all_passed = True

    for b in BENCHMARKS:
        res = con.execute(f"""
            SELECT {b["query_col"]}
            FROM financial_ratios
            WHERE symbol = '{b["symbol"]}' AND period_type = 'FY' AND fiscal_date = '{b["fiscal_date"]}'
        """).fetchone()

        if not res or res[0] is None:
            table.add_row(
                b["symbol"],
                b["company_name"],
                b["fiscal_date"],
                b["metric"],
                f"{b['expected']:,.2f}",
                "MISSING",
                "100.0%",
                "[red]FAIL ❌[/red]",
            )
            all_passed = False
            continue

        actual = float(res[0])
        expected = b["expected"]
        diff_pct = abs(actual - expected) / expected * 100

        is_ok = diff_pct <= b["tolerance_pct"]
        if not is_ok:
            all_passed = False

        status_str = (
            "[bold green]MATCH ✅[/bold green]" if is_ok else "[bold red]FAIL ❌[/bold red]"
        )
        diff_str = f"{diff_pct:.2f}%" if diff_pct > 0.001 else "0.00%"

        table.add_row(
            b["symbol"],
            b["company_name"],
            b["fiscal_date"],
            b["metric"],
            f"{expected:,.2f}",
            f"{actual:,.2f}",
            diff_str,
            status_str,
        )

    console.print(table)

    # 2. Accounting Identities Invariant Audit
    console.print(
        "\n[bold magenta]2. Accounting Identity Invariant Audits across All 250 Companies:[/bold magenta]"
    )

    invariants = [
        (
            "Capital Employed = Assets - Current Liabilities",
            """
            SELECT count(*) FROM financial_ratios
            WHERE capital_employed IS NOT NULL
              AND total_assets IS NOT NULL
              AND current_liabilities IS NOT NULL
              AND total_assets > current_liabilities
              AND abs(capital_employed - (total_assets - current_liabilities)) > 1.0;
            """,
        ),
        (
            "ROCE % = (EBIT / Capital Employed) * 100",
            """
            SELECT count(*) FROM financial_ratios
            WHERE roce_pct IS NOT NULL
              AND ebit IS NOT NULL
              AND capital_employed IS NOT NULL
              AND capital_employed > 0
              AND abs(roce_pct - (ebit / capital_employed * 100)) > 0.01;
            """,
        ),
        (
            "ROE % = (Net Income / Equity) * 100",
            """
            SELECT count(*) FROM financial_ratios
            WHERE roe_pct IS NOT NULL
              AND net_income IS NOT NULL
              AND stockholders_equity IS NOT NULL
              AND stockholders_equity > 0
              AND abs(roe_pct - (net_income / stockholders_equity * 100)) > 0.01;
            """,
        ),
        (
            "Operating Margin % = (EBIT / Revenue) * 100",
            """
            SELECT count(*) FROM financial_ratios
            WHERE operating_margin_pct IS NOT NULL
              AND ebit IS NOT NULL
              AND revenue IS NOT NULL
              AND revenue > 0
              AND abs(operating_margin_pct - (ebit / revenue * 100)) > 0.01;
            """,
        ),
        (
            "Non-negative Total Assets Invariant",
            """
            SELECT count(*) FROM financial_ratios
            WHERE total_assets IS NOT NULL AND total_assets <= 0;
            """,
        ),
    ]

    inv_table = Table(show_header=True, header_style="bold cyan")
    inv_table.add_column("Accounting Invariant Rule", style="yellow")
    inv_table.add_column("Violations Found", justify="right")
    inv_table.add_column("Audit Result", justify="center")

    for rule_name, query in invariants:
        r = con.execute(query).fetchone()
        violations = int(r[0]) if r else 0
        if violations == 0:
            inv_table.add_row(rule_name, "0", "[bold green]VERIFIED (0 errors) ✅[/bold green]")
        else:
            inv_table.add_row(
                rule_name,
                str(violations),
                f"[bold red]{violations} VIOLATIONS ❌[/bold red]",
            )
            all_passed = False

    console.print(inv_table)
    con.close()

    if all_passed:
        console.print(
            "\n[bold green]🎉 All cross-source and accounting verification audits PASSED successfully![/bold green]\n"
        )
        return 0
    else:
        console.print(
            "\n[bold red]⚠️ Some verification audits failed. Please inspect the discrepancy table above.[/bold red]\n"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
