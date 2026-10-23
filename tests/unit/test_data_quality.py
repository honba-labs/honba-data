"""Unit tests for DataQualityChecker and Coverage Ledgers."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from honba_data.coverage.data_quality import (
    DataQualityChecker,
    QualityIssueType,
    QualitySeverity,
)


def test_clean_bars_data() -> None:
    # 5 consecutive trading days with clean valid prices
    dates = [
        dt.date(2025, 1, 6),  # Mon
        dt.date(2025, 1, 7),  # Tue
        dt.date(2025, 1, 8),  # Wed
        dt.date(2025, 1, 9),  # Thu
        dt.date(2025, 1, 10),  # Fri
    ]
    rows = []
    for d in dates:
        rows.append(
            {
                "symbol": "INFY",
                "bar_date": d,
                "open": 1500.0,
                "high": 1520.0,
                "low": 1490.0,
                "close": 1510.0,
                "volume": 100_000.0,
            }
        )
    df = pd.DataFrame(rows)

    checker = DataQualityChecker()
    report = checker.check_bars(df, interval="1D", check_calendar=True)

    assert report.is_clean
    assert report.issue_count == 0
    assert report.clean_records == 5
    summary = report.summary()
    assert summary["clean_pct"] == 100.0


def test_detects_ohlc_and_negative_price_violations() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "TCS",
                "bar_date": dt.date(2025, 1, 6),
                "open": 3500.0,
                "high": 3400.0,  # High < Open (Broken invariant)
                "low": 3300.0,
                "close": 3350.0,
                "volume": 50_000.0,
            },
            {
                "symbol": "TCS",
                "bar_date": dt.date(2025, 1, 7),
                "open": -100.0,  # Negative price
                "high": 3500.0,
                "low": -200.0,
                "close": 3400.0,
                "volume": 50_000.0,
            },
        ]
    )

    checker = DataQualityChecker()
    report = checker.check_bars(df, interval="1D", check_calendar=False)

    assert not report.is_clean
    issue_types = {i.issue_type for i in report.issues}
    assert QualityIssueType.OHLC_VIOLATION in issue_types
    assert QualityIssueType.NON_POSITIVE_PRICE in issue_types


def test_detects_duplicate_and_out_of_sequence_timestamps() -> None:
    df = pd.DataFrame(
        [
            {
                "symbol": "RELIANCE",
                "bar_date": dt.date(2025, 1, 6),
                "open": 2500.0,
                "high": 2520.0,
                "low": 2490.0,
                "close": 2510.0,
                "volume": 100_000.0,
            },
            {
                "symbol": "RELIANCE",
                "bar_date": dt.date(2025, 1, 6),  # Duplicate timestamp
                "open": 2500.0,
                "high": 2520.0,
                "low": 2490.0,
                "close": 2510.0,
                "volume": 100_000.0,
            },
            {
                "symbol": "RELIANCE",
                "bar_date": dt.date(2025, 1, 5),  # Out of sequence (Sunday before)
                "open": 2480.0,
                "high": 2490.0,
                "low": 2470.0,
                "close": 2485.0,
                "volume": 20_000.0,
            },
        ]
    )

    checker = DataQualityChecker()
    report = checker.check_bars(df, interval="1D", check_calendar=False)

    assert not report.is_clean
    issue_types = {i.issue_type for i in report.issues}
    assert QualityIssueType.DUPLICATE_TIMESTAMP in issue_types
    assert QualityIssueType.OUT_OF_SEQUENCE in issue_types


def test_detects_volume_spikes() -> None:
    # 25 days with median volume 10,000, and day 25 has 500,000 (50x spike)
    base_date = dt.date(2025, 1, 1)
    rows = []
    for i in range(25):
        d = base_date + dt.timedelta(days=i)
        vol = 500_000.0 if i == 24 else 10_000.0
        rows.append(
            {
                "symbol": "WIPRO",
                "bar_date": d,
                "open": 450.0,
                "high": 460.0,
                "low": 445.0,
                "close": 455.0,
                "volume": vol,
            }
        )
    df = pd.DataFrame(rows)

    checker = DataQualityChecker(volume_spike_multiplier=20.0)
    report = checker.check_bars(df, interval="1D", check_calendar=False)

    spike_issues = [i for i in report.issues if i.issue_type == QualityIssueType.VOLUME_SPIKE]
    assert len(spike_issues) >= 1
    assert spike_issues[0].severity == QualitySeverity.WARNING
    assert spike_issues[0].value == 500_000.0


def test_detects_missing_trading_days() -> None:
    # Mon Jan 6 to Fri Jan 10, skipping Wednesday Jan 8
    df = pd.DataFrame(
        [
            {
                "symbol": "SBIN",
                "bar_date": dt.date(2025, 1, 6),
                "open": 800.0,
                "high": 810.0,
                "low": 795.0,
                "close": 805.0,
                "volume": 10_000.0,
            },
            {
                "symbol": "SBIN",
                "bar_date": dt.date(2025, 1, 7),
                "open": 805.0,
                "high": 815.0,
                "low": 800.0,
                "close": 810.0,
                "volume": 10_000.0,
            },
            # Wednesday Jan 8 missing
            {
                "symbol": "SBIN",
                "bar_date": dt.date(2025, 1, 9),
                "open": 810.0,
                "high": 820.0,
                "low": 805.0,
                "close": 815.0,
                "volume": 10_000.0,
            },
            {
                "symbol": "SBIN",
                "bar_date": dt.date(2025, 1, 10),
                "open": 815.0,
                "high": 825.0,
                "low": 810.0,
                "close": 820.0,
                "volume": 10_000.0,
            },
        ]
    )

    checker = DataQualityChecker()
    report = checker.check_bars(df, interval="1D", check_calendar=True)

    missing_issues = [i for i in report.issues if i.issue_type == QualityIssueType.MISSING_BARS]
    assert len(missing_issues) == 1
    assert "2025-01-08" in missing_issues[0].details
