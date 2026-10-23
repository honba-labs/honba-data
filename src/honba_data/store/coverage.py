from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum


class CoverageStatus(Enum):
    FINAL = "FINAL"
    PARTIAL = "PARTIAL"


@dataclass
class DateInterval:
    start: dt.date
    end: dt.date


@dataclass
class CoverageRecord:
    exchange: str
    symbol: str
    timeframe: str
    interval: DateInterval
    status: CoverageStatus
    source: str
    row_count: int
