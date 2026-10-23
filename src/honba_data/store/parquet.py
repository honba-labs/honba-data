from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId

from honba_data.store.coverage import CoverageRecord, CoverageStatus, DateInterval


class ParquetBarStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.catalog_dir = data_dir / "catalog"
        self.ledger_path = data_dir / "coverage_ledger.json"

    def _ledger(self) -> list[CoverageRecord]:
        if not self.ledger_path.exists():
            return []
        with open(self.ledger_path, "r") as f:
            data = json.load(f)
            # Reconstruct from dict
            return [
                CoverageRecord(
                    exchange=d["exchange"],
                    symbol=d["symbol"],
                    timeframe=d["timeframe"],
                    interval=DateInterval(
                        start=pd.to_datetime(d["interval"]["start"]).date(),
                        end=pd.to_datetime(d["interval"]["end"]).date(),
                    ),
                    status=CoverageStatus(d["status"]),
                    source=d["source"],
                    row_count=d["row_count"],
                )
                for d in data
            ]

    def _save_ledger(self, records: list[CoverageRecord]):
        # Save to JSON
        data = []
        for r in records:
            data.append(
                {
                    "exchange": r.exchange,
                    "symbol": r.symbol,
                    "timeframe": r.timeframe,
                    "interval": {
                        "start": r.interval.start.isoformat(),
                        "end": r.interval.end.isoformat(),
                    },
                    "status": r.status.value,
                    "source": r.source,
                    "row_count": r.row_count,
                }
            )
        with open(self.ledger_path, "w") as f:
            json.dump(data, f, indent=4)

    def append(self, record: CoverageRecord, bars: list[Bar]):
        # Append to Parquet
        # Group by year as per catalog description
        # Using simple approach for now, assuming 1D bars have a date
        for bar in bars:
            year = pd.to_datetime(bar.ts, unit="ns").year
            path = (
                self.catalog_dir
                / record.timeframe
                / record.exchange
                / record.symbol
                / f"{year}.parquet"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            # ... Parquet append logic would go here ...

        # Update ledger
        records = self._ledger()
        records.append(record)
        self._save_ledger(records)

    def read(self, instrument: InstrumentId, timeframe: str, interval: DateInterval) -> list[Bar]:
        # Read from Parquet
        # This is a stub for now
        return []

    def coverage(self, instrument: InstrumentId, timeframe: str) -> list[CoverageRecord]:
        records = self._ledger()
        return [
            r
            for r in records
            if r.symbol == instrument.symbol
            and r.exchange == instrument.exchange
            and r.timeframe == timeframe
        ]
