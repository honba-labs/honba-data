"""Structured Ingestion Telemetry for Honba Data Lakehouse.

Implements structured JSON telemetry events for offline and batch ingestion runs,
monitoring universe coverage, symbol success/failure rates, written row counts,
and elapsed execution duration.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("honba_data.telemetry")


@dataclasses.dataclass(frozen=True)
class IngestionTelemetry:
    """Structured telemetry payload emitted upon completion of an ingestion run."""

    universe: str
    symbols_attempted: int
    symbols_succeeded: int
    symbols_failed: int
    rows_written: int
    coverage_delta_days: int
    duration_seconds: float
    timestamp: str = dataclasses.field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    event: str = "ingestion_completed"

    def to_dict(self) -> dict[str, Any]:
        """Convert telemetry event to a canonical dictionary matching lakehouse schema."""
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "universe": self.universe,
            "symbols_attempted": self.symbols_attempted,
            "symbols_succeeded": self.symbols_succeeded,
            "symbols_failed": self.symbols_failed,
            "rows_written": self.rows_written,
            "coverage_delta_days": self.coverage_delta_days,
            "duration_seconds": round(float(self.duration_seconds), 2),
        }

    def to_json(self, indent: int | None = None) -> str:
        """Serialize telemetry payload to formatted JSON."""
        return json.dumps(self.to_dict(), indent=indent)


_TELEMETRY_LISTENERS: list[Callable[[IngestionTelemetry], None]] = []


def register_telemetry_listener(listener: Callable[[IngestionTelemetry], None]) -> None:
    """Register a custom listener/sink for structured ingestion telemetry."""
    _TELEMETRY_LISTENERS.append(listener)


def clear_telemetry_listeners() -> None:
    """Clear all registered telemetry listeners."""
    _TELEMETRY_LISTENERS.clear()


def emit_ingestion_telemetry(
    universe: str,
    symbols_attempted: int,
    symbols_succeeded: int,
    symbols_failed: int,
    rows_written: int,
    coverage_delta_days: int,
    duration_seconds: float,
    timestamp: str | None = None,
) -> IngestionTelemetry:
    """Construct, emit, and log a structured ingestion telemetry event."""
    event = IngestionTelemetry(
        universe=universe,
        symbols_attempted=symbols_attempted,
        symbols_succeeded=symbols_succeeded,
        symbols_failed=symbols_failed,
        rows_written=rows_written,
        coverage_delta_days=coverage_delta_days,
        duration_seconds=duration_seconds,
        **(dict(timestamp=timestamp) if timestamp else {}),
    )

    # Log structured JSON payload
    logger.info("Structured Ingestion Event: %s", event.to_json())

    for listener in _TELEMETRY_LISTENERS:
        try:
            listener(event)
        except Exception as exc:
            logger.error("Error invoking telemetry listener: %s", exc)

    return event
