"""``honba-data live`` Typer sub-app."""

from __future__ import annotations

import logging
import os
import signal
from typing import Annotated

import typer

from honba_data.live import yfinance_driver

live_app = typer.Typer(help="Live (polling) market data drivers", no_args_is_help=True)


@live_app.command("yfinance")
def yfinance_cmd(
    symbols: Annotated[
        str,
        typer.Option("--symbols", "-s", help="Comma-separated, e.g. RELIANCE,TCS.BSE,NIFTY50.NSE"),
    ],
    server: Annotated[str, typer.Option("--server", help="Honba REST base URL")],
    interval: Annotated[
        str, typer.Option("--interval", "-i", help="Bar interval: 1m, 5m, 15m, 1h")
    ] = "1m",
    token_env: Annotated[
        str, typer.Option("--token-env", help="Env var holding the bearer token (write:orders)")
    ] = "HONBA_TOKEN",
    poll_seconds: Annotated[
        float, typer.Option("--poll-seconds", help="Seconds between polls")
    ] = 60.0,
    backfill: Annotated[
        bool,
        typer.Option("--backfill/--no-backfill", help="Post already-closed bars on first poll"),
    ] = False,
) -> None:
    """Poll yfinance and POST closed bars to `honba serve --bar-ingest` (POST /bars).

    yfinance is an UNOFFICIAL, DELAYED data source: for development and paper trading only.
    The token is read from the environment, never from argv.
    """
    token = os.environ.get(token_env)
    if not token:
        typer.echo(f"error: environment variable {token_env} is not set", err=True)
        raise typer.Exit(code=2)
    names = [s.strip() for s in symbols.split(",") if s.strip()]
    if not names:
        typer.echo("error: --symbols is empty", err=True)
        raise typer.Exit(code=2)
    logging.basicConfig(level=logging.INFO)
    typer.echo("note: yfinance data is delayed and unofficial; dev/paper use only", err=True)
    driver = yfinance_driver.YFinanceBarDriver(
        symbols=names,
        interval=interval,
        server=server,
        token=token,
        poll_seconds=poll_seconds,
        backfill=backfill,
    )
    try:
        signal.signal(signal.SIGINT, lambda *_: driver.stop())
        signal.signal(signal.SIGTERM, lambda *_: driver.stop())
    except ValueError:  # not in main thread
        pass
    try:
        driver.run()
    except yfinance_driver.IngestFatalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
