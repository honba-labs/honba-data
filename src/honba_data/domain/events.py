"""Domain events for honba-data decoupled consumption."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field


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
