"""JSON Coverage Ledger repository adapter implementing CoverageRepository."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from honba_data.domain.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
    subtract_intervals,
)
from honba_data.domain.events import CoverageLedgerUpdated, DomainEvent
from honba_data.ports.coverage_repo import CoverageRepository

logger = logging.getLogger(__name__)


def compute_interval_checksum(
    exchange: str, symbol: str, timeframe: str, start: str, end: str, row_count: int
) -> str:
    """Compute deterministic SHA-256 checksum for a coverage record interval."""
    payload = f"{exchange.upper()}:{symbol.upper()}:{timeframe.upper()}:{start}:{end}:{row_count}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class JsonCoverageRepository(CoverageRepository):
    """Atomic, checksummed JSON ledger repository managing coverage intervals."""

    ledger_path: Path
    _event_listeners: list[Callable[[DomainEvent], None]]

    def __init__(self, ledger_path: Path | str) -> None:
        self.ledger_path = Path(ledger_path).resolve()
        self._event_listeners = []

    def subscribe(self, listener: Callable[[DomainEvent], None]) -> None:
        """Register a callback listener for domain events."""
        self._event_listeners.append(listener)

    def _publish_event(self, event: DomainEvent) -> None:
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as exc:
                logger.error("Error in domain event listener: %s", exc)

    def _load_raw(self) -> dict[str, list[dict[str, Any]]]:
        if not self.ledger_path.exists():
            return {}
        try:
            content = self.ledger_path.read_text(encoding="utf-8")
            if not content.strip():
                return {}
            raw = json.loads(content)
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, list):
                # Legacy list format -> convert to dict
                result: dict[str, list[dict[str, Any]]] = {}
                for item in raw:
                    key = f"{item.get('exchange', 'NSE').upper()}:{item.get('symbol', '').upper()}:{item.get('timeframe', '1D').upper()}"
                    result.setdefault(key, []).append(item)
                return result
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read ledger %s: %s", self.ledger_path, exc)
            return {}

    def _save_raw(self, data: dict[str, list[dict[str, Any]]]) -> None:
        """Atomically persist ledger data using .tmp -> replace."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.ledger_path.with_suffix(f".tmp.{os.getpid()}")
        payload = json.dumps(data, indent=2, sort_keys=True)
        tmp_file.write_text(payload, encoding="utf-8")
        tmp_file.replace(self.ledger_path)

    @staticmethod
    def _make_key(exchange: str, symbol: str, timeframe: str) -> str:
        return f"{exchange.upper().strip()}:{symbol.upper().strip()}:{timeframe.upper().strip()}"

    def load_records(self, exchange: str, symbol: str, timeframe: str) -> Sequence[CoverageRecord]:
        """Load and return all coverage records for an instrument and timeframe."""
        key = self._make_key(exchange, symbol, timeframe)
        ledger = self._load_raw()
        raw_list = ledger.get(key, [])
        records: list[CoverageRecord] = []

        for r in raw_list:
            records.append(
                CoverageRecord(
                    exchange=str(r.get("exchange") or r.get("venue") or exchange).upper(),
                    symbol=str(r["symbol"]).upper(),
                    timeframe=str(r["timeframe"]).upper(),
                    interval=DateInterval(
                        dt.date.fromisoformat(r["start"]),
                        dt.date.fromisoformat(r["end"]),
                    ),
                    status=CoverageStatus(r["status"]),
                    source=str(r.get("source", "unknown")),
                    row_count=int(r.get("row_count", 0)),
                    checksum=r.get("checksum"),
                    fetched_at_ns=int(r.get("fetched_at_ns", 0)),
                )
            )
        return records

    def append_record(self, record: CoverageRecord) -> None:
        """Append a record without immediately returning merged records."""
        self.update_interval(record)

    def update_interval(self, record: CoverageRecord) -> Sequence[CoverageRecord]:
        """Atomically record, merge contiguous/overlapping intervals, and emit event."""
        key = self._make_key(record.exchange, record.symbol, record.timeframe)
        ledger = self._load_raw()
        records_list = ledger.setdefault(key, [])

        chk = record.checksum or compute_interval_checksum(
            record.exchange,
            record.symbol,
            record.timeframe,
            record.interval.start.isoformat(),
            record.interval.end.isoformat(),
            record.row_count,
        )

        records_list.append(
            {
                "exchange": record.exchange.upper(),
                "symbol": record.symbol.upper(),
                "timeframe": record.timeframe.upper(),
                "start": record.interval.start.isoformat(),
                "end": record.interval.end.isoformat(),
                "status": record.status.value,
                "source": record.source,
                "row_count": record.row_count,
                "checksum": chk,
                "fetched_at_ns": record.fetched_at_ns,
            }
        )

        merged = self._merge_entries(records_list)
        ledger[key] = merged
        self._save_raw(ledger)

        # Emit domain event
        self._publish_event(
            CoverageLedgerUpdated(
                symbol=record.symbol.upper(),
                exchange=record.exchange.upper(),
                timeframe=record.timeframe.upper(),
                start_date=record.interval.start,
                end_date=record.interval.end,
                status=record.status.value,
                row_count=record.row_count,
            )
        )

        return self.load_records(record.exchange, record.symbol, record.timeframe)

    def detect_gaps(
        self, exchange: str, symbol: str, timeframe: str, required: DateInterval
    ) -> Sequence[DateInterval]:
        """Compute missing date intervals for the required range against covered records."""
        records = self.load_records(exchange, symbol, timeframe)
        # Filter to completed/final or partial intervals
        covered_intervals = [
            r.interval
            for r in records
            if r.status in (CoverageStatus.FINAL, CoverageStatus.PARTIAL)
        ]
        return subtract_intervals(required, covered_intervals)

    @staticmethod
    def _merge_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge adjacent and overlapping ledger entries with same status and exchange/symbol/tf."""
        if not entries:
            return []

        sorted_entries = sorted(entries, key=lambda x: (x["start"], x["end"]))
        merged: list[dict[str, Any]] = [sorted_entries[0].copy()]

        for cur in sorted_entries[1:]:
            prev = merged[-1]
            prev_end = dt.date.fromisoformat(prev["end"])
            cur_start = dt.date.fromisoformat(cur["start"])
            cur_end = dt.date.fromisoformat(cur["end"])

            if prev.get("status") == cur.get("status") and cur_start <= prev_end:
                if cur_end > prev_end:
                    prev["end"] = cur["end"]
                prev["row_count"] = prev.get("row_count", 0) + cur.get("row_count", 0)
                prev["fetched_at_ns"] = max(
                    prev.get("fetched_at_ns", 0), cur.get("fetched_at_ns", 0)
                )
                prev["checksum"] = compute_interval_checksum(
                    prev.get("exchange", "NSE"),
                    prev["symbol"],
                    prev["timeframe"],
                    prev["start"],
                    prev["end"],
                    prev["row_count"],
                )
            else:
                merged.append(cur.copy())

        return merged
