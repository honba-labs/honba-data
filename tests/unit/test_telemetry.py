"""Unit tests for structured ingestion telemetry and IngestionCompleted domain event."""

from __future__ import annotations

import json

from honba_data.domain.events import IngestionCompleted
from honba_data.telemetry import (
    IngestionTelemetry,
    clear_telemetry_listeners,
    emit_ingestion_telemetry,
    register_telemetry_listener,
)


def test_telemetry_schema_exact_match():
    """Verify that IngestionTelemetry dictionary matches the master plan schema exactly."""
    fixed_ts = "2026-10-24T13:45:00Z"
    telem = IngestionTelemetry(
        universe="NIFTY_100",
        symbols_attempted=100,
        symbols_succeeded=100,
        symbols_failed=0,
        rows_written=2450,
        coverage_delta_days=365,
        duration_seconds=18.4,
        timestamp=fixed_ts,
    )

    data = telem.to_dict()
    assert data == {
        "timestamp": "2026-10-24T13:45:00Z",
        "event": "ingestion_completed",
        "universe": "NIFTY_100",
        "symbols_attempted": 100,
        "symbols_succeeded": 100,
        "symbols_failed": 0,
        "rows_written": 2450,
        "coverage_delta_days": 365,
        "duration_seconds": 18.4,
    }

    # Verify JSON roundtrip
    json_str = telem.to_json()
    parsed = json.loads(json_str)
    assert parsed == data


def test_emit_ingestion_telemetry_listener():
    """Verify that emit_ingestion_telemetry notifies registered listeners."""
    events_received: list[IngestionTelemetry] = []

    def _listener(evt: IngestionTelemetry) -> None:
        events_received.append(evt)

    clear_telemetry_listeners()
    register_telemetry_listener(_listener)
    try:
        evt = emit_ingestion_telemetry(
            universe="NIFTY_MIDCAP_150",
            symbols_attempted=150,
            symbols_succeeded=148,
            symbols_failed=2,
            rows_written=4200,
            coverage_delta_days=180,
            duration_seconds=12.3456,
        )

        assert len(events_received) == 1
        assert events_received[0] == evt
        assert evt.universe == "NIFTY_MIDCAP_150"
        assert evt.duration_seconds == 12.3456
        assert evt.to_dict()["duration_seconds"] == 12.35
    finally:
        clear_telemetry_listeners()


def test_ingestion_completed_domain_event():
    """Verify IngestionCompleted domain event integration."""
    event = IngestionCompleted(
        universe="ALL",
        symbols_attempted=250,
        symbols_succeeded=250,
        symbols_failed=0,
        rows_written=15000,
        coverage_delta_days=365,
        duration_seconds=30.0,
    )

    assert event.event == "ingestion_completed"
    dict_payload = event.to_dict()
    assert dict_payload["universe"] == "ALL"
    assert dict_payload["symbols_attempted"] == 250
    assert dict_payload["rows_written"] == 15000

    telem = event.to_telemetry()
    assert isinstance(telem, IngestionTelemetry)
    assert telem.universe == "ALL"
