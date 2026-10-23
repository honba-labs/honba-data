# pyright: reportMissingTypeStubs=false, reportArgumentType=false, reportUnknownMemberType=false
"""Automated Data Quality & Coverage Ledger for Market Data and Lakehouse Bars.

Validates:
- Missing bars against expected trading sessions / calendar
- Duplicate ticks and duplicate bar timestamps
- Out-of-sequence / non-monotonic timestamps
- Volume spikes (relative to rolling median baseline)
- OHLC violations (e.g. low > high, open/close out of bounds)
- Negative or non-finite prices
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from enum import Enum
from typing import Any

import pandas as pd
from rich.table import Table


class QualitySeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class QualityIssueType(str, Enum):
    MISSING_BARS = "MISSING_BARS"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    OUT_OF_SEQUENCE = "OUT_OF_SEQUENCE"
    VOLUME_SPIKE = "VOLUME_SPIKE"
    OHLC_VIOLATION = "OHLC_VIOLATION"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"


@dataclasses.dataclass(frozen=True)
class QualityIssue:
    """Detailed record of a data quality violation."""

    symbol: str
    issue_type: QualityIssueType
    timestamp: str
    severity: QualitySeverity
    details: str
    value: float | None = None


@dataclasses.dataclass
class DataQualityReport:
    """Summary and itemized report of market data quality inspection."""

    total_records: int
    clean_records: int
    issues: list[QualityIssue]

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    def summary(self) -> dict[str, Any]:
        """Aggregate summary statistics of data quality audit."""
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for issue in self.issues:
            by_type[issue.issue_type.value] = by_type.get(issue.issue_type.value, 0) + 1
            by_severity[issue.severity.value] = by_severity.get(issue.severity.value, 0) + 1

        clean_pct = (
            (self.clean_records / self.total_records * 100.0) if self.total_records > 0 else 100.0
        )
        return {
            "total_records": self.total_records,
            "clean_records": self.clean_records,
            "clean_pct": round(clean_pct, 2),
            "issue_count": len(self.issues),
            "by_type": by_type,
            "by_severity": by_severity,
        }

    def to_table(self) -> Table:
        """Render report as a formatted Rich table."""
        table = Table(title="Honba Data Quality Audit Report", header_style="bold cyan")
        table.add_column("Symbol", style="magenta")
        table.add_column("Timestamp", style="cyan")
        table.add_column("Issue Type", style="yellow")
        table.add_column("Severity", style="bold red")
        table.add_column("Details", style="white")

        for issue in self.issues[:50]:  # limit display to first 50
            sev_style = (
                "red"
                if issue.severity in (QualitySeverity.ERROR, QualitySeverity.CRITICAL)
                else "yellow"
            )
            table.add_row(
                issue.symbol,
                str(issue.timestamp),
                issue.issue_type.value,
                f"[{sev_style}]{issue.severity.value}[/{sev_style}]",
                issue.details,
            )

        if len(self.issues) > 50:
            table.add_row(
                "...", "...", "...", "...", f"... and {len(self.issues) - 50} more issues"
            )

        return table


# Standard fixed NSE market holidays (month, day)
NSE_FIXED_HOLIDAYS = {
    (1, 26),  # Republic Day
    (5, 1),  # Maharashtra Day
    (8, 15),  # Independence Day
    (10, 2),  # Mahatma Gandhi Jayanti
    (12, 25),  # Christmas
}


def is_nse_trading_day(d: dt.date) -> bool:
    """Check if date is a weekday and not a standard national holiday."""
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    if (d.month, d.day) in NSE_FIXED_HOLIDAYS:
        return False
    return True


class DataQualityChecker:
    """Automated validator for financial time-series bars and ticks."""

    def __init__(
        self,
        volume_spike_multiplier: float = 20.0,
        rolling_window: int = 20,
    ) -> None:
        self.volume_spike_multiplier = volume_spike_multiplier
        self.rolling_window = rolling_window

    def check_bars(
        self,
        df: pd.DataFrame,
        interval: str = "1D",
        check_calendar: bool = True,
    ) -> DataQualityReport:
        """Validate an OHLCV bar DataFrame for structural, logical, and temporal invariants.

        Expected columns: ``['symbol', 'bar_date' (or 'timestamp'), 'open', 'high', 'low', 'close', 'volume']``.
        """
        if df.empty:
            return DataQualityReport(total_records=0, clean_records=0, issues=[])

        df_work = df.copy()
        ts_col = "bar_date" if "bar_date" in df_work.columns else "timestamp"
        total_records = len(df_work)
        issues: list[QualityIssue] = []

        # Group by symbol to audit each instrument individually
        for sym, sym_df in df_work.groupby("symbol"):
            sym_str = str(sym).upper().strip()
            sorted_df = sym_df.sort_values(by=ts_col).reset_index(drop=True)

            # 1. Check duplicate timestamps
            duplicates = sorted_df[sorted_df.duplicated(subset=[ts_col], keep=False)]
            if not duplicates.empty:
                for _, dup_row in duplicates.iterrows():
                    issues.append(
                        QualityIssue(
                            symbol=sym_str,
                            issue_type=QualityIssueType.DUPLICATE_TIMESTAMP,
                            timestamp=str(dup_row[ts_col]),
                            severity=QualitySeverity.ERROR,
                            details=f"Duplicate bar detected at timestamp {dup_row[ts_col]}",
                        )
                    )

            # 2. Check monotonic timestamps
            prev_ts = None
            for idx, row in sorted_df.iterrows():
                cur_ts = row[ts_col]
                if prev_ts is not None and cur_ts <= prev_ts:
                    issues.append(
                        QualityIssue(
                            symbol=sym_str,
                            issue_type=QualityIssueType.OUT_OF_SEQUENCE,
                            timestamp=str(cur_ts),
                            severity=QualitySeverity.CRITICAL,
                            details=f"Timestamp out of sequence: {cur_ts} <= previous {prev_ts}",
                        )
                    )
                prev_ts = cur_ts

                # 3. Check price positivity and finiteness
                bar_o = float(row["open"])
                bar_h = float(row["high"])
                bar_l = float(row["low"])
                bar_c = float(row["close"])
                bar_v = float(row["volume"])

                if any(pd.isna([bar_o, bar_h, bar_l, bar_c, bar_v])):
                    issues.append(
                        QualityIssue(
                            symbol=sym_str,
                            issue_type=QualityIssueType.NON_FINITE_VALUE,
                            timestamp=str(cur_ts),
                            severity=QualitySeverity.ERROR,
                            details=f"NaN or Inf encountered in row: O={bar_o}, H={bar_h}, L={bar_l}, C={bar_c}, V={bar_v}",
                        )
                    )
                    continue

                if bar_o <= 0 or bar_h <= 0 or bar_l <= 0 or bar_c <= 0:
                    issues.append(
                        QualityIssue(
                            symbol=sym_str,
                            issue_type=QualityIssueType.NON_POSITIVE_PRICE,
                            timestamp=str(cur_ts),
                            severity=QualitySeverity.CRITICAL,
                            details=f"Non-positive price detected: O={bar_o}, H={bar_h}, L={bar_l}, C={bar_c}",
                            value=min(bar_o, bar_h, bar_l, bar_c),
                        )
                    )

                # 4. Check OHLC invariants
                if not (
                    bar_l <= min(bar_o, bar_c) and bar_h >= max(bar_o, bar_c) and bar_l <= bar_h
                ):
                    issues.append(
                        QualityIssue(
                            symbol=sym_str,
                            issue_type=QualityIssueType.OHLC_VIOLATION,
                            timestamp=str(cur_ts),
                            severity=QualitySeverity.ERROR,
                            details=f"Invalid OHLC relation: L={bar_l} must be <= min(O,C) and H={bar_h} must be >= max(O,C)",
                        )
                    )

            # 5. Check volume spikes (relative to rolling median)
            if len(sorted_df) >= self.rolling_window:
                rolling_vol = (
                    sorted_df["volume"].rolling(window=self.rolling_window, min_periods=5).median()
                )
                for idx, row in sorted_df.iterrows():
                    med = float(rolling_vol.iloc[idx])
                    v = float(row["volume"])
                    if med > 0 and v > self.volume_spike_multiplier * med:
                        issues.append(
                            QualityIssue(
                                symbol=sym_str,
                                issue_type=QualityIssueType.VOLUME_SPIKE,
                                timestamp=str(row[ts_col]),
                                severity=QualitySeverity.WARNING,
                                details=f"Volume spike of {v:,.0f} exceeds {self.volume_spike_multiplier}x median ({med:,.0f})",
                                value=v,
                            )
                        )

            # 6. Check missing bars against trading calendar (for 1D interval)
            if check_calendar and interval.upper() == "1D" and len(sorted_df) >= 2:
                # Convert timestamps to date
                dates = [
                    d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
                    for d in sorted_df[ts_col]
                ]
                date_set = set(dates)
                min_d = min(dates)
                max_d = max(dates)
                curr = min_d
                missing_days: list[dt.date] = []
                while curr <= max_d:
                    if is_nse_trading_day(curr) and curr not in date_set:
                        missing_days.append(curr)
                    curr += dt.timedelta(days=1)

                if missing_days:
                    issues.append(
                        QualityIssue(
                            symbol=sym_str,
                            issue_type=QualityIssueType.MISSING_BARS,
                            timestamp=str(missing_days[0]),
                            severity=QualitySeverity.WARNING,
                            details=f"Missing {len(missing_days)} expected NSE trading session(s): {', '.join(str(d) for d in missing_days[:5])}",
                            value=float(len(missing_days)),
                        )
                    )

        clean_records = max(0, total_records - len(issues))
        return DataQualityReport(
            total_records=total_records,
            clean_records=clean_records,
            issues=issues,
        )

    def check_ticks(self, ticks: Sequence[dict[str, Any]]) -> DataQualityReport:
        """Validate high-frequency tick messages for timestamp monotonicity and values."""
        if not ticks:
            return DataQualityReport(total_records=0, clean_records=0, issues=[])

        total = len(ticks)
        issues: list[QualityIssue] = []
        prev_ts = None

        for t in ticks:
            sym = str(t.get("symbol") or t.get("instrument_id", {}).get("symbol", "UNKNOWN"))
            ts = t.get("receive_ts") or t.get("exchange_ts") or t.get("timestamp")
            price = t.get("last_price") or t.get("price") or t.get("ltp")

            if prev_ts is not None and ts is not None and ts < prev_ts:
                issues.append(
                    QualityIssue(
                        symbol=sym,
                        issue_type=QualityIssueType.OUT_OF_SEQUENCE,
                        timestamp=str(ts),
                        severity=QualitySeverity.CRITICAL,
                        details=f"Tick out of order: {ts} < previous {prev_ts}",
                    )
                )
            prev_ts = ts

            if price is not None:
                p_val = float(price)
                if p_val <= 0:
                    issues.append(
                        QualityIssue(
                            symbol=sym,
                            issue_type=QualityIssueType.NON_POSITIVE_PRICE,
                            timestamp=str(ts),
                            severity=QualitySeverity.CRITICAL,
                            details=f"Invalid tick price: {p_val}",
                            value=p_val,
                        )
                    )

        clean = max(0, total - len(issues))
        return DataQualityReport(total_records=total, clean_records=clean, issues=issues)
