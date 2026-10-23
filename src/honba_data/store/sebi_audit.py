# pyright: reportMissingTypeStubs=false
"""DuckDB-backed SEBI Regulatory Audit Trail.

Under SEBI's algorithmic trading regulations (including the Retail Algo Circular),
all order attempts, cancellations, modifications, risk refusals, broker acks,
and fills must be recorded with millisecond timestamps in an append-only audit ledger.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, cast

import duckdb
import pandas as pd


class DuckDbAuditLedger:
    """Append-only SEBI regulatory audit ledger backed by DuckDB."""

    conn: duckdb.DuckDBPyConnection

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None or str(db_path) == ":memory:":
            self.conn = duckdb.connect(":memory:")
        else:
            p = Path(db_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self.conn = duckdb.connect(str(p))

        self._init_schema()

    def _init_schema(self) -> None:
        _ = self.conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_sebi_audit_id START 1;")
        _ = self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sebi_audit_ledger (
                id BIGINT DEFAULT nextval('seq_sebi_audit_id'),
                timestamp_ms BIGINT NOT NULL,
                timestamp_utc VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                algo_id VARCHAR NOT NULL,
                client_id VARCHAR NOT NULL,
                correlation_id VARCHAR NOT NULL,
                client_order_id VARCHAR NOT NULL,
                broker_order_id VARCHAR,
                exchange_order_id VARCHAR,
                instrument VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                order_type VARCHAR NOT NULL,
                quantity VARCHAR NOT NULL,
                price VARCHAR,
                status VARCHAR NOT NULL,
                refusal_reason VARCHAR,
                latency_us BIGINT,
                metadata_json VARCHAR
            );
            """
        )

    def record_order_attempt(
        self,
        algo_id: str,
        client_id: str,
        correlation_id: str,
        client_order_id: str,
        instrument: str,
        side: str,
        order_type: str,
        quantity: str | int | float,
        price: str | float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> int:
        """Persist an order submission attempt."""
        now = dt.datetime.now(dt.timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        ts_utc = now.isoformat()
        meta_str = json.dumps(metadata) if metadata else None
        p_str = str(price) if price is not None else None

        res = self.conn.execute(
            """
            INSERT INTO sebi_audit_ledger (
                timestamp_ms, timestamp_utc, event_type, algo_id, client_id,
                correlation_id, client_order_id, broker_order_id, exchange_order_id,
                instrument, side, order_type, quantity, price, status, refusal_reason,
                latency_us, metadata_json
            ) VALUES (?, ?, 'order_attempt', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, 'pending_new', NULL, NULL, ?)
            RETURNING id;
            """,
            [
                ts_ms,
                ts_utc,
                algo_id,
                client_id,
                correlation_id,
                client_order_id,
                instrument,
                side,
                order_type,
                str(quantity),
                p_str,
                meta_str,
            ],
        ).fetchone()

        return int(cast(int, res[0])) if res and res[0] is not None else 0

    def record_risk_refusal(
        self,
        algo_id: str,
        client_id: str,
        correlation_id: str,
        client_order_id: str,
        instrument: str,
        side: str,
        order_type: str,
        quantity: str | int | float,
        refusal_reason: str,
        latency_us: int,
        price: str | float | None = None,
    ) -> int:
        """Persist a pre-trade risk refusal."""
        now = dt.datetime.now(dt.timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        ts_utc = now.isoformat()
        p_str = str(price) if price is not None else None

        res = self.conn.execute(
            """
            INSERT INTO sebi_audit_ledger (
                timestamp_ms, timestamp_utc, event_type, algo_id, client_id,
                correlation_id, client_order_id, broker_order_id, exchange_order_id,
                instrument, side, order_type, quantity, price, status, refusal_reason,
                latency_us, metadata_json
            ) VALUES (?, ?, 'risk_refusal', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, 'refused', ?, ?, NULL)
            RETURNING id;
            """,
            [
                ts_ms,
                ts_utc,
                algo_id,
                client_id,
                correlation_id,
                client_order_id,
                instrument,
                side,
                order_type,
                str(quantity),
                p_str,
                refusal_reason,
                latency_us,
            ],
        ).fetchone()

        return int(cast(int, res[0])) if res and res[0] is not None else 0

    def record_broker_ack(
        self,
        algo_id: str,
        client_id: str,
        correlation_id: str,
        client_order_id: str,
        broker_order_id: str,
        instrument: str,
        side: str,
        order_type: str,
        quantity: str | int | float,
        status: str,
        latency_us: int,
        exchange_order_id: str | None = None,
        price: str | float | None = None,
    ) -> int:
        """Persist a broker order placement acknowledgement."""
        now = dt.datetime.now(dt.timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        ts_utc = now.isoformat()
        p_str = str(price) if price is not None else None

        res = self.conn.execute(
            """
            INSERT INTO sebi_audit_ledger (
                timestamp_ms, timestamp_utc, event_type, algo_id, client_id,
                correlation_id, client_order_id, broker_order_id, exchange_order_id,
                instrument, side, order_type, quantity, price, status, refusal_reason,
                latency_us, metadata_json
            ) VALUES (?, ?, 'broker_ack', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
            RETURNING id;
            """,
            [
                ts_ms,
                ts_utc,
                algo_id,
                client_id,
                correlation_id,
                client_order_id,
                broker_order_id,
                exchange_order_id,
                instrument,
                side,
                order_type,
                str(quantity),
                p_str,
                status,
                latency_us,
            ],
        ).fetchone()

        return int(cast(int, res[0])) if res and res[0] is not None else 0

    def record_cancellation(
        self,
        algo_id: str,
        client_id: str,
        correlation_id: str,
        broker_order_id: str,
        instrument: str,
        reason: str,
        latency_us: int,
    ) -> int:
        """Persist an order cancellation event."""
        now = dt.datetime.now(dt.timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        ts_utc = now.isoformat()

        res = self.conn.execute(
            """
            INSERT INTO sebi_audit_ledger (
                timestamp_ms, timestamp_utc, event_type, algo_id, client_id,
                correlation_id, client_order_id, broker_order_id, exchange_order_id,
                instrument, side, order_type, quantity, price, status, refusal_reason,
                latency_us, metadata_json
            ) VALUES (?, ?, 'cancellation', ?, ?, ?, '', ?, NULL, ?, 'NONE', 'cancel', '0', NULL, 'cancelled', ?, ?, NULL)
            RETURNING id;
            """,
            [
                ts_ms,
                ts_utc,
                algo_id,
                client_id,
                correlation_id,
                broker_order_id,
                instrument,
                reason,
                latency_us,
            ],
        ).fetchone()

        return int(cast(int, res[0])) if res and res[0] is not None else 0

    def record_modification(
        self,
        algo_id: str,
        client_id: str,
        correlation_id: str,
        broker_order_id: str,
        instrument: str,
        new_quantity: str | int | float,
        new_price: str | float | None = None,
        latency_us: int = 0,
    ) -> int:
        """Persist an order modification event."""
        now = dt.datetime.now(dt.timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        ts_utc = now.isoformat()
        p_str = str(new_price) if new_price is not None else None

        res = self.conn.execute(
            """
            INSERT INTO sebi_audit_ledger (
                timestamp_ms, timestamp_utc, event_type, algo_id, client_id,
                correlation_id, client_order_id, broker_order_id, exchange_order_id,
                instrument, side, order_type, quantity, price, status, refusal_reason,
                latency_us, metadata_json
            ) VALUES (?, ?, 'modification', ?, ?, ?, '', ?, NULL, ?, 'NONE', 'modification', ?, ?, 'modified', NULL, ?, NULL)
            RETURNING id;
            """,
            [
                ts_ms,
                ts_utc,
                algo_id,
                client_id,
                correlation_id,
                broker_order_id,
                instrument,
                str(new_quantity),
                p_str,
                latency_us,
            ],
        ).fetchone()

        return int(cast(int, res[0])) if res and res[0] is not None else 0

    def record_fill(
        self,
        algo_id: str,
        client_id: str,
        correlation_id: str,
        client_order_id: str,
        broker_order_id: str,
        fill_id: str,
        instrument: str,
        side: str,
        quantity: str | int | float,
        price: str | float,
    ) -> int:
        """Persist an execution fill event."""
        now = dt.datetime.now(dt.timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        ts_utc = now.isoformat()
        meta_str = json.dumps({"fill_id": fill_id})

        res = self.conn.execute(
            """
            INSERT INTO sebi_audit_ledger (
                timestamp_ms, timestamp_utc, event_type, algo_id, client_id,
                correlation_id, client_order_id, broker_order_id, exchange_order_id,
                instrument, side, order_type, quantity, price, status, refusal_reason,
                latency_us, metadata_json
            ) VALUES (?, ?, 'fill', ?, ?, ?, ?, ?, NULL, ?, ?, 'fill', ?, ?, 'filled', NULL, NULL, ?)
            RETURNING id;
            """,
            [
                ts_ms,
                ts_utc,
                algo_id,
                client_id,
                correlation_id,
                client_order_id,
                broker_order_id,
                instrument,
                side,
                str(quantity),
                str(price),
                meta_str,
            ],
        ).fetchone()

        return int(cast(int, res[0])) if res and res[0] is not None else 0

    def query_records(
        self,
        algo_id: str | None = None,
        correlation_id: str | None = None,
        event_type: str | None = None,
    ) -> pd.DataFrame:
        """Query audit records as a pandas DataFrame."""
        query = "SELECT * FROM sebi_audit_ledger WHERE 1=1"
        params: list[object] = []

        if algo_id is not None:
            query += " AND algo_id = ?"
            params.append(algo_id)

        if correlation_id is not None:
            query += " AND correlation_id = ?"
            params.append(correlation_id)

        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp_ms ASC, id ASC;"
        df = self.conn.execute(query, params).fetch_df()
        if not df.empty and "metadata_json" in df.columns:
            df["metadata"] = df["metadata_json"].apply(
                lambda v: cast(dict[str, Any] | None, json.loads(v)) if isinstance(v, str) else None
            )
        return df

    def count(self) -> int:
        """Return total number of audit records."""
        row = self.conn.execute("SELECT COUNT(*) FROM sebi_audit_ledger;").fetchone()
        return int(cast(int, row[0])) if row and row[0] is not None else 0

    def export_parquet(self, output_path: Path | str) -> Path:
        """Export the audit ledger to Parquet for archival or lakehouse query."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        _ = self.conn.execute(f"COPY sebi_audit_ledger TO '{out}' (FORMAT PARQUET);")
        return out
