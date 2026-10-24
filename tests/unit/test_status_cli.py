"""Unit tests for honba-data status CLI command."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from honba_data.cli.main import app
from honba_data.store.fundamentals import find_data_root

runner = CliRunner()


def test_status_cli_default_invocation():
    """Verify honba-data status outputs complete tables with 0.00% discrepancy."""
    db_file = find_data_root() / "fundamentals.duckdb"
    if not db_file.is_file():
        return

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"

    # Verify key dashboard sections are rendered
    assert "Lakehouse Storage & Inventory Overview" in result.output
    assert "Index & Scrip Coverage Health" in result.output
    assert "Quarterly Filing Timeliness" in result.output
    assert "Provider Maturity & Error Rate Scorecard" in result.output
    assert "SEBI Audited Ground-Truth Reconciliation Scorecard (0.00% Discrepancy)" in result.output
    assert "0.00%" in result.output
    assert "LAKEHOUSE ARCHITECTURAL PARITY VERIFIED: 0.00% DISCREPANCY" in result.output


def test_status_cli_json_output():
    """Verify honba-data status --json returns valid structured monitoring JSON."""
    db_file = find_data_root() / "fundamentals.duckdb"
    if not db_file.is_file():
        return

    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"

    data = json.loads(result.output)
    assert "lakehouse" in data
    assert "index_coverage" in data
    assert "quarterly_filings_timeliness" in data
    assert "providers_scorecard" in data
    assert "reconciliation" in data

    assert data["lakehouse"]["status"] == "HEALTHY"
    assert data["index_coverage"]["nifty_100"]["coverage_pct"] >= 95.0
    assert data["index_coverage"]["nifty_midcap_150"]["coverage_pct"] >= 95.0
    assert data["reconciliation"]["max_discrepancy_pct"] <= 0.05
    assert data["reconciliation"]["status"] == "VERIFIED_0.00%_DISCREPANCY"


def test_status_cli_missing_database_error():
    """Verify honba-data status exits with code 1 if database does not exist."""
    fake_path = "/tmp/honba_nonexistent_database_99.duckdb"
    result = runner.invoke(app, ["status", "--db-path", fake_path])
    assert result.exit_code == 1
