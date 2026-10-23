#!/usr/bin/env python3
"""Automated verification script for market data quality and coverage ledgers.

Checks:
1. Missing bars against NSE trading calendar
2. Duplicate timestamps per instrument
3. Out-of-sequence / non-monotonic timestamps
4. Volume spikes relative to rolling median
5. OHLC invariant violations and non-positive prices
"""

from __future__ import annotations

import datetime as dt
import sys

import pandas as pd
from rich.console import Console

from honba_data.coverage.data_quality import DataQualityChecker, QualityIssueType

console = Console()


def create_synthetic_test_market_data() -> pd.DataFrame:
    """Generate a test OHLCV series containing deliberate anomalies to verify detection."""
    base_date = dt.date(2025, 1, 1)
    dates = []
    curr = base_date
    while len(dates) < 30:
        if curr.weekday() < 5 and (curr.month, curr.day) != (1, 26):
            dates.append(curr)
        curr += dt.timedelta(days=1)

    # Intentionally skip date index 10 to simulate missing bar
    del dates[10]

    rows = []
    base_price = 1000.0
    for i, d in enumerate(dates):
        op = base_price + (i * 2.0)
        hi = op + 15.0
        lo = op - 10.0
        cl = op + 5.0
        vol = 100_000.0

        # Anomaly 1: Volume spike at index 5
        if i == 5:
            vol = 5_000_000.0  # 50x spike

        # Anomaly 2: OHLC violation at index 15 (Low > High)
        if i == 15:
            lo = hi + 10.0

        # Anomaly 3: Non-positive price at index 20
        if i == 20:
            cl = -5.0

        rows.append(
            {
                "symbol": "TESTSCRIP",
                "bar_date": d,
                "open": op,
                "high": hi,
                "low": lo,
                "close": cl,
                "volume": vol,
            }
        )

    # Anomaly 4: Duplicate row
    rows.append(dict(rows[3]))

    # Anomaly 5: Out of sequence timestamp appended
    rows.append(
        {
            "symbol": "TESTSCRIP",
            "bar_date": dt.date(2024, 12, 1),
            "open": 900.0,
            "high": 910.0,
            "low": 890.0,
            "close": 905.0,
            "volume": 50_000.0,
        }
    )

    return pd.DataFrame(rows)


def main() -> int:
    console.print("[bold cyan]Running Honba Data Quality Verification Script...[/bold cyan]")
    df = create_synthetic_test_market_data()
    console.print(f"Generated synthetic dataset with {len(df)} bars and intentional anomalies.")

    checker = DataQualityChecker(volume_spike_multiplier=15.0)
    report = checker.check_bars(df, interval="1D", check_calendar=True)

    console.print(report.to_table())

    summary = report.summary()
    console.print(f"[bold]Summary:[/bold] {summary}")

    # Verify that all 5 intentional anomalies were accurately flagged
    detected_types = {issue.issue_type for issue in report.issues}
    expected_types = {
        QualityIssueType.VOLUME_SPIKE,
        QualityIssueType.OHLC_VIOLATION,
        QualityIssueType.NON_POSITIVE_PRICE,
        QualityIssueType.DUPLICATE_TIMESTAMP,
        QualityIssueType.OUT_OF_SEQUENCE,
        QualityIssueType.MISSING_BARS,
    }

    missing_types = expected_types - detected_types
    if missing_types:
        console.print(
            f"[bold red]FAIL: Did not detect expected issue types: {missing_types}[/bold red]"
        )
        return 1

    console.print(
        "[bold green]SUCCESS: All data quality anomaly checks verified successfully![/bold green]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
