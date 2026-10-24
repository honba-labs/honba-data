"""Domain layer for honba-data: canonical entities, value objects, and events."""

from __future__ import annotations

from honba_data.domain.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
    QualityIssue,
    merge_close_intervals,
    merge_intervals,
    plan_gaps,
    subtract_intervals,
)
from honba_data.domain.events import (
    CoverageLedgerUpdated,
    DataQualityAlertRaised,
    DomainEvent,
    FundamentalsIngested,
    IngestionFailed,
)
from honba_data.domain.models import (
    Bar,
    BarProtocol,
    Company,
    FinancialRatios,
    FinancialStatement,
    InstrumentId,
    InstrumentIdProtocol,
    LocalBar,
    LocalInstrumentId,
    as_bar,
    as_instrument_id,
)

__all__ = [
    "Bar",
    "BarProtocol",
    "Company",
    "CoverageLedgerUpdated",
    "CoverageRecord",
    "CoverageStatus",
    "DataQualityAlertRaised",
    "DateInterval",
    "DomainEvent",
    "FinancialRatios",
    "FinancialStatement",
    "FundamentalsIngested",
    "IngestionFailed",
    "InstrumentId",
    "InstrumentIdProtocol",
    "LocalBar",
    "LocalInstrumentId",
    "QualityIssue",
    "as_bar",
    "as_instrument_id",
    "merge_close_intervals",
    "merge_intervals",
    "plan_gaps",
    "subtract_intervals",
]
