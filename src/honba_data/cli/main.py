"""honba-data Typer CLI interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from honba_data.live.cli import live_app
from honba_data.providers.fundamentals import download_fundamentals_to_duckdb
from honba_data.store.fundamentals import DuckDbFundamentalsStore, find_data_root

app = typer.Typer(
    help="honba-data CLI: manage market data, fundamentals, and DuckDB store", no_args_is_help=True
)
app.add_typer(live_app, name="live")
console = Console()
err_console = Console(stderr=True)


@app.command("fundamentals")
def fundamentals_cmd(
    universe: Annotated[
        str,
        typer.Option(
            "--universe",
            "-u",
            help="Target universe: large_cap (Nifty 100), mid_cap (Nifty Midcap 150), or all (250 stocks)",
        ),
    ] = "all",
    workers: Annotated[
        int,
        typer.Option("--workers", "-w", help="Number of concurrent download threads"),
    ] = 8,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Limit number of companies to download"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Re-download companies even if already present"),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Custom DuckDB destination path"),
    ] = None,
) -> None:
    """Download yearly and quarterly financial statements and ratios into DuckDB."""
    dest = Path(db_path) if db_path else find_data_root() / "fundamentals.duckdb"
    console.print(
        f"[bold cyan]Downloading {universe.upper()} Fundamentals into {dest}...[/bold cyan]"
    )
    res = download_fundamentals_to_duckdb(
        universe_category=universe,
        db_path=dest,
        max_workers=workers,
        limit=limit,
        skip_existing=not force,
    )
    console.print(
        f"[bold green]Done! Total companies in DuckDB: {res['companies_total']}[/bold green]"
    )


@app.command("summary")
def summary_cmd(
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Custom DuckDB destination path"),
    ] = None,
) -> None:
    """Show dataset statistics and row counts for DuckDB fundamentals store."""
    dest = Path(db_path) if db_path else find_data_root() / "fundamentals.duckdb"
    if not dest.is_file():
        err_console.print(f"[red]Database not found at {dest}[/red]")
        raise typer.Exit(code=1)

    store = DuckDbFundamentalsStore(db_path=dest)
    s = store.get_summary()

    table = Table(title="DuckDB Fundamental Store Summary", header_style="bold magenta")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Database File", str(s["db_path"]))
    table.add_row("Total Companies", str(s["companies_total"]))
    table.add_row("Large-Cap Companies", str(s["large_cap_companies"]))
    table.add_row("Mid-Cap Companies", str(s["mid_cap_companies"]))
    table.add_row("Statement Line Items", f"{s['statements_records']:,}")
    table.add_row("Financial Ratio Rows", f"{s['ratios_records']:,}")
    table.add_row("Screener Facts", f"{s['screener_facts']:,}")
    table.add_row("Fiscal Date Coverage", f"{s['min_fiscal_date']} → {s['max_fiscal_date']}")

    console.print(table)


if __name__ == "__main__":
    app()
