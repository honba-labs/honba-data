"""Tests for DuckDbAuditLedger SEBI regulatory audit trail."""

import tempfile
from pathlib import Path

from honba_data.store.sebi_audit import DuckDbAuditLedger


def test_duckdb_sebi_audit_ledger_lifecycle():
    ledger = DuckDbAuditLedger(":memory:")

    algo_id = "algo-duck-01"
    client_id = "client-inst-01"
    corr_id = "corr-1111-2222"
    client_order_id = "order-3333-4444"
    inst = "NSE:INFY"

    # 1. Order Attempt
    id1 = ledger.record_order_attempt(
        algo_id=algo_id,
        client_id=client_id,
        correlation_id=corr_id,
        client_order_id=client_order_id,
        instrument=inst,
        side="BUY",
        order_type="Limit",
        quantity=150,
        price=1820.50,
        metadata={"tag": "vwap"},
    )
    assert id1 == 1

    # 2. Risk Refusal
    id2 = ledger.record_risk_refusal(
        algo_id=algo_id,
        client_id=client_id,
        correlation_id=corr_id,
        client_order_id=client_order_id,
        instrument=inst,
        side="BUY",
        order_type="Limit",
        quantity=150,
        refusal_reason="SEBI OTR threshold breached: 51.2 > 50.0",
        latency_us=55,
        price=1820.50,
    )
    assert id2 == 2

    # 3. Broker Ack
    id3 = ledger.record_broker_ack(
        algo_id=algo_id,
        client_id=client_id,
        correlation_id=corr_id,
        client_order_id=client_order_id,
        broker_order_id="BRK-INFY-99",
        instrument=inst,
        side="BUY",
        order_type="Limit",
        quantity=150,
        status="new",
        latency_us=1100,
        exchange_order_id="EX-INFY-88",
        price=1820.50,
    )
    assert id3 == 3

    # 4. Modification
    id4 = ledger.record_modification(
        algo_id=algo_id,
        client_id=client_id,
        correlation_id=corr_id,
        broker_order_id="BRK-INFY-99",
        instrument=inst,
        new_quantity=150,
        new_price=1825.00,
        latency_us=210,
    )
    assert id4 == 4

    # 5. Cancellation
    id5 = ledger.record_cancellation(
        algo_id=algo_id,
        client_id=client_id,
        correlation_id=corr_id,
        broker_order_id="BRK-INFY-99",
        instrument=inst,
        reason="Manual user cancel",
        latency_us=180,
    )
    assert id5 == 5

    # 6. Fill
    id6 = ledger.record_fill(
        algo_id=algo_id,
        client_id=client_id,
        correlation_id=corr_id,
        client_order_id=client_order_id,
        broker_order_id="BRK-INFY-99",
        fill_id="FILL-001",
        instrument=inst,
        side="BUY",
        quantity=150,
        price=1820.50,
    )
    assert id6 == 6

    # Verify count
    assert ledger.count() == 6

    # Query records by algo_id
    df_algo = ledger.query_records(algo_id=algo_id)
    assert len(df_algo) == 6
    assert list(df_algo["event_type"]) == [
        "order_attempt",
        "risk_refusal",
        "broker_ack",
        "modification",
        "cancellation",
        "fill",
    ]

    # Query records by event_type
    df_refusal = ledger.query_records(event_type="risk_refusal")
    assert len(df_refusal) == 1
    assert "SEBI OTR threshold breached" in df_refusal.iloc[0]["refusal_reason"]
    assert df_refusal.iloc[0]["latency_us"] == 55

    # Export to Parquet
    with tempfile.TemporaryDirectory() as tmpdir:
        pq_path = Path(tmpdir) / "sebi_audit.parquet"
        exported = ledger.export_parquet(pq_path)
        assert exported.exists()
        assert exported.stat().st_size > 0
