"""Unit tests for JsonCoverageRepository."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from honba_data.adapters.ledger.json_ledger import JsonCoverageRepository, compute_interval_checksum
from honba_data.domain.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba_data.domain.events import CoverageLedgerUpdated, DomainEvent
from honba_data.ports.coverage_repo import CoverageRepository


def test_json_ledger_conforms_to_coverage_repository_protocol(tmp_path: Path) -> None:
    ledger_file = tmp_path / "coverage_ledger.json"
    repo = JsonCoverageRepository(ledger_file)
    assert isinstance(repo, CoverageRepository)


def test_json_ledger_atomic_append_and_merge(tmp_path: Path) -> None:
    ledger_file = tmp_path / "coverage_ledger.json"
    repo = JsonCoverageRepository(ledger_file)

    events: list[DomainEvent] = []
    repo.subscribe(lambda e: events.append(e))

    rec1 = CoverageRecord(
        exchange="NSE",
        symbol="RELIANCE",
        timeframe="1D",
        interval=DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 10)),
        status=CoverageStatus.FINAL,
        source="nse_bhavcopy",
        row_count=9,
    )
    repo.update_interval(rec1)

    assert len(events) == 1
    assert isinstance(events[0], CoverageLedgerUpdated)
    assert events[0].symbol == "RELIANCE"
    assert events[0].start_date == dt.date(2025, 1, 1)
    assert events[0].end_date == dt.date(2025, 1, 10)

    # Contiguous interval [2025-01-10, 2025-01-20)
    rec2 = CoverageRecord(
        exchange="NSE",
        symbol="RELIANCE",
        timeframe="1D",
        interval=DateInterval(dt.date(2025, 1, 10), dt.date(2025, 1, 20)),
        status=CoverageStatus.FINAL,
        source="nse_bhavcopy",
        row_count=10,
    )
    merged = repo.update_interval(rec2)

    # Should be merged into a single interval [2025-01-01, 2025-01-20)
    assert len(merged) == 1
    assert merged[0].interval.start == dt.date(2025, 1, 1)
    assert merged[0].interval.end == dt.date(2025, 1, 20)
    assert merged[0].row_count == 19
    assert merged[0].checksum is not None

    # Load back from file fresh
    repo_reloaded = JsonCoverageRepository(ledger_file)
    records = repo_reloaded.load_records("NSE", "RELIANCE", "1D")
    assert len(records) == 1
    assert records[0].interval.end == dt.date(2025, 1, 20)


def test_json_ledger_detect_gaps(tmp_path: Path) -> None:
    ledger_file = tmp_path / "coverage_ledger.json"
    repo = JsonCoverageRepository(ledger_file)

    # Cover [2025-01-05, 2025-01-15)
    rec = CoverageRecord(
        exchange="NSE",
        symbol="TCS",
        timeframe="1D",
        interval=DateInterval(dt.date(2025, 1, 5), dt.date(2025, 1, 15)),
        status=CoverageStatus.FINAL,
        source="test",
        row_count=10,
    )
    repo.update_interval(rec)

    # Query required [2025-01-01, 2025-01-20)
    required = DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 20))
    gaps = repo.detect_gaps("NSE", "TCS", "1D", required)

    assert len(gaps) == 2
    assert gaps[0] == DateInterval(dt.date(2025, 1, 1), dt.date(2025, 1, 5))
    assert gaps[1] == DateInterval(dt.date(2025, 1, 15), dt.date(2025, 1, 20))


def test_compute_interval_checksum() -> None:
    chk1 = compute_interval_checksum("NSE", "INFY", "1D", "2025-01-01", "2025-01-10", 9)
    chk2 = compute_interval_checksum("NSE", "INFY", "1D", "2025-01-01", "2025-01-10", 9)
    chk3 = compute_interval_checksum("NSE", "INFY", "1D", "2025-01-01", "2025-01-10", 10)

    assert chk1 == chk2
    assert chk1 != chk3
    assert len(chk1) == 16
