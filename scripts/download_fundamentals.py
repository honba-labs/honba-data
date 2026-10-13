#!/usr/bin/env python3
"""scripts/download_fundamentals.py: Download yearly & quarterly financials for Large & Mid-cap companies into DuckDB.

Usage::

    # Download all 250 Large-cap and Mid-cap companies
    python scripts/download_fundamentals.py

    # Download only Large-cap (Nifty 100)
    python scripts/download_fundamentals.py --universe large_cap

    # Test download on first 5 companies
    python scripts/download_fundamentals.py --limit 5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

# Add honba_data src to path
REPO_ROOT = Path(__file__).resolve().parents[1]
HONBA_DATA_SRC = REPO_ROOT / "src"
if str(HONBA_DATA_SRC) not in sys.path:
    sys.path.insert(0, str(HONBA_DATA_SRC))

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from honba_data.providers.fundamentals import (
    DuckDbFundamentalsStore,
    download_fundamentals_to_duckdb,
    fetch_large_midcap_universe,
)
from honba_data.store.fundamentals import find_data_root

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download yearly & quarterly financials for Large & Mid-cap companies into DuckDB."
    )
    parser.add_argument(
        "--universe",
        choices=["large_cap", "mid_cap", "all"],
        default="all",
        help="Target company universe (default: all - 250 stocks)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="DuckDB database path (defaults to $HONBA_DATA_DIR or data/fundamentals.duckdb)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of concurrent download worker threads (default: 8)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download companies even if already present in DuckDB",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of companies to download (useful for quick testing)",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db_path) if args.db_path else find_data_root() / "fundamentals.duckdb"
    universe_cat = args.universe.upper()

    console.print(
        f"[bold cyan]Honba Fundamental Data Downloader[/bold cyan] (Target: [yellow]{args.universe.upper()}[/yellow])"
    )
    console.print(f"Database destination: [bold green]{db_path}[/bold green]")
    console.print(
        f"Workers: [bold]{args.workers}[/bold] | Limit: [bold]{args.limit or 'All'}[/bold]"
    )
    console.print(
        "[dim]Strict Invariant: No intraday or daily stock/equity price bars stored.[/dim]\n"
    )

    # Probe universe
    companies = fetch_large_midcap_universe(universe_cat)
    if not args.force:
        try:
            store = DuckDbFundamentalsStore(db_path=db_path)
            with store._get_connection() as con:
                existing = {r[0] for r in con.execute("SELECT symbol FROM companies").fetchall()}
            already_done = len(companies) - len([c for c in companies if c.symbol not in existing])
            if already_done > 0:
                console.print(
                    f"[dim]Found {already_done} companies already stored in DuckDB (skipping).[/dim]"
                )
            companies = [c for c in companies if c.symbol not in existing]
        except (duckdb.Error, OSError):
            pass  # Database does not exist yet or empty

    if args.limit:
        companies = companies[: args.limit]

    total = len(companies)
    if total == 0:
        console.print(
            "[bold green]All requested companies are already up to date in DuckDB![/bold green]\n"
        )
        store = DuckDbFundamentalsStore(db_path=db_path)
        summary = store.get_summary()
        summary["success_count"] = 0
        summary["fail_count"] = 0
        from rich.table import Table

        t = Table(title="DuckDB Fundamental Store Status", header_style="bold magenta")
        t.add_column("Metric", style="cyan")
        t.add_column("Value", style="green")
        t.add_row("DuckDB File", str(summary["db_path"]))
        t.add_row("Total Companies Stored", str(summary["companies_total"]))
        t.add_row("Large-Cap Companies", str(summary["large_cap_companies"]))
        t.add_row("Mid-Cap Companies", str(summary["mid_cap_companies"]))
        t.add_row("Financial Statements Line Items", f"{summary['statements_records']:,}")
        t.add_row("Computed Financial Ratio Rows", f"{summary['ratios_records']:,}")
        t.add_row("Screener Facts (instrument_metrics)", f"{summary['screener_facts']:,}")
        t.add_row(
            "Fiscal Date Coverage",
            f"{summary['min_fiscal_date']} → {summary['max_fiscal_date']}",
        )
        console.print(t)
        return 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Downloading fundamentals...", total=total)

        def on_progress(current: int, total_count: int, symbol: str, success: bool) -> None:
            status_text = f"[green]{symbol}[/green]" if success else f"[red]{symbol} (err)[/red]"
            progress.update(task_id, advance=1, description=f"Fetching {status_text}")

        t0 = time.time()
        summary = download_fundamentals_to_duckdb(
            universe_category=universe_cat,
            db_path=db_path,
            max_workers=args.workers,
            limit=args.limit,
            skip_existing=not args.force,
            progress_callback=on_progress,
        )
        elapsed = time.time() - t0

    console.print(f"\n[bold green]Download completed in {elapsed:.1f}s![/bold green]\n")

    # Render summary table
    from rich.table import Table

    table = Table(title="DuckDB Fundamental Store Summary", header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("DuckDB File", str(summary["db_path"]))
    table.add_row("Total Companies Stored", str(summary["companies_total"]))
    table.add_row("Large-Cap Companies", str(summary["large_cap_companies"]))
    table.add_row("Mid-Cap Companies", str(summary["mid_cap_companies"]))
    table.add_row("Financial Statements Line Items", f"{summary['statements_records']:,}")
    table.add_row("Computed Financial Ratio Rows", f"{summary['ratios_records']:,}")
    table.add_row("Screener Facts (instrument_metrics)", f"{summary['screener_facts']:,}")
    table.add_row(
        "Fiscal Date Coverage",
        f"{summary['min_fiscal_date']} → {summary['max_fiscal_date']}",
    )
    table.add_row(
        "Run Result",
        f"Downloaded {summary['success_count']} successfully, {summary['fail_count']} failed",
    )
    console.print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
