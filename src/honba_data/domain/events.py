"""Domain events for honba-data decoupled consumption."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from honba_data.telemetry import IngestionTelemetry


@dataclass(frozen=True)
class DomainEvent:
    """Base domain event class."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


@dataclass(frozen=True)
class FundamentalsIngested(DomainEvent):
    """Emitted when new company financial statements or ratios are persisted."""

    symbol: str = ""
    period_type: str = "FY"  # "FY" or "FQ"
    fiscal_date: dt.date | None = None
    filing_date: dt.date | None = None
    statement_types: tuple[str, ...] = ()
    rows_written: int = 0


@dataclass(frozen=True)
class CoverageLedgerUpdated(DomainEvent):
    """Emitted when bar coverage ledger intervals are created, extended, or verified."""

    symbol: str = ""
    exchange: str = "NSE"
    timeframe: str = "1D"
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    status: str = "final"
    row_count: int = 0


@dataclass(frozen=True)
class DataQualityAlertRaised(DomainEvent):
    """Emitted when data quality validation identifies an anomaly."""

    issue_type: str = ""
    severity: str = "WARNING"
    symbol: str = ""
    details: str = ""


@dataclass(frozen=True)
class IngestionFailed(DomainEvent):
    """Emitted when an ingestion run for a provider or symbol fails."""

    provider: str = ""
    symbol: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class IngestionCompleted(DomainEvent):
    """Emitted upon completion of an offline or batch ingestion pipeline run."""

    event: str = "ingestion_completed"
    universe: str = ""
    symbols_attempted: int = 0
    symbols_succeeded: int = 0
    symbols_failed: int = 0
    rows_written: int = 0
    coverage_delta_days: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Format as structured dictionary matching telemetry schema."""
        ts_str = self.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "timestamp": ts_str,
            "event": self.event,
            "universe": self.universe,
            "symbols_attempted": self.symbols_attempted,
            "symbols_succeeded": self.symbols_succeeded,
            "symbols_failed": self.symbols_failed,
            "rows_written": self.rows_written,
            "coverage_delta_days": self.coverage_delta_days,
            "duration_seconds": round(float(self.duration_seconds), 2),
        }

    def to_telemetry(self) -> IngestionTelemetry:
        """Convert into structured IngestionTelemetry object."""
        return IngestionTelemetry(
            universe=self.universe,
            symbols_attempted=self.symbols_attempted,
            symbols_succeeded=self.symbols_succeeded,
            symbols_failed=self.symbols_failed,
            rows_written=self.rows_written,
            coverage_delta_days=self.coverage_delta_days,
            duration_seconds=self.duration_seconds,
            timestamp=self.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            event=self.event,
        )


__all__ = [
    "DomainEvent",
    "FundamentalsIngested",
    "CoverageLedgerUpdated",
    "DataQualityAlertRaised",
    "IngestionFailed",
    "IngestionCompleted",
    "IngestionTelemetry",
]
