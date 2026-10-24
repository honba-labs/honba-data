"""honba-data Typer CLI interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from honba_data.cli.status import status_cmd
from honba_data.coverage.data_quality import DataQualityChecker
from honba_data.live.cli import live_app
from honba_data.providers.fundamentals import download_fundamentals_to_duckdb
from honba_data.store.corporate_actions import DuckDbCorporateActionsStore
from honba_data.store.fundamentals import DuckDbFundamentalsStore, find_data_root

app = typer.Typer(
    help="honba-data CLI: manage market data, fundamentals, and DuckDB store", no_args_is_help=True
)
app.add_typer(live_app, name="live")
app.command("status")(status_cmd)
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


@app.command("corporate-actions")
def actions_cmd(
    symbol: Annotated[
        str | None,
        typer.Option("--symbol", "-s", help="Filter by symbol"),
    ] = None,
    action_type: Annotated[
        str | None,
        typer.Option("--type", "-t", help="Filter by action type: SPLIT, BONUS, DIVIDEND"),
    ] = None,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Custom DuckDB destination path"),
    ] = None,
) -> None:
    """List recorded corporate actions and adjustment factors."""
    dest = Path(db_path) if db_path else find_data_root() / "fundamentals.duckdb"
    store = DuckDbCorporateActionsStore(db_path=dest)
    actions = store.get_actions(symbol=symbol, action_type=action_type)

    table = Table(title="Corporate Actions Ledger", header_style="bold cyan")
    table.add_column("Symbol", style="magenta")
    table.add_column("Action", style="green")
    table.add_column("Ex-Date", style="yellow")
    table.add_column("Ratio (New:Old)", style="white")
    table.add_column("Dividend", style="cyan")
    table.add_column("Adj Factor", style="bold red")
    table.add_column("Remarks", style="dim")

    for a in actions:
        ratio = f"{a['ratio_numerator']}:{a['ratio_denominator']}" if a["ratio_numerator"] else "-"
        div = f"₹{a['dividend_amount']:.2f}" if a["dividend_amount"] is not None else "-"
        factor = f"{a['adjustment_factor']:.4f}" if a["adjustment_factor"] is not None else "-"
        table.add_row(
            a["symbol"],
            a["action_type"],
            str(a["ex_date"]),
            ratio,
            div,
            factor,
            a.get("remarks") or "",
        )

    console.print(table)


@app.command("quality")
def quality_cmd(
    parquet_path: Annotated[
        str,
        typer.Argument(help="Path to Parquet bar file or CSV to validate"),
    ],
    interval: Annotated[
        str,
        typer.Option("--interval", "-i", help="Bar interval (e.g. 1D, 1h, 5m)"),
    ] = "1D",
) -> None:
    """Run automated data quality and coverage audit on OHLCV market data."""
    p = Path(parquet_path)
    if not p.is_file():
        err_console.print(f"[red]File not found: {p}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Auditing data quality for {p}...[/bold cyan]")
    if p.suffix == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p)

    checker = DataQualityChecker()
    report = checker.check_bars(df, interval=interval)

    console.print(report.to_table())
    s = report.summary()
    status_color = "green" if report.is_clean else "yellow"
    console.print(
        f"[{status_color}]Result: {s['clean_records']}/{s['total_records']} clean bars ({s['clean_pct']}%), {s['issue_count']} quality issues detected.[/{status_color}]"
    )


if __name__ == "__main__":
    app()
