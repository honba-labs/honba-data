"""Domain entities, value objects, and boundary protocols for honba-data."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from typing_extensions import override


def _cast_float(v: object) -> float:
    """Safely convert an arbitrary scalar or object to float without unconstrained Any."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return float(v)
    return float(str(v))


def _cast_int(v: object) -> int:
    """Safely convert an arbitrary scalar or object to int without unconstrained Any."""
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        return int(float(v))
    return int(str(v))


@runtime_checkable
class InstrumentIdProtocol(Protocol):
    """Structural protocol for instrument identifiers."""

    symbol: str
    exchange: str


@runtime_checkable
class BarProtocol(Protocol):
    """Structural protocol for canonical OHLCV bars."""

    instrument_id: InstrumentIdProtocol | InstrumentId | object
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """Canonical instrument identifier: symbol and exchange."""

    symbol: str
    exchange: str = "NSE"

    @override
    def __str__(self) -> str:
        return f"{self.symbol}.{self.exchange}"

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol, "exchange": self.exchange}


# Alias for backward-compatible fallback naming
LocalInstrumentId = InstrumentId


@dataclass(frozen=True, slots=True)
class Bar:
    """Canonical OHLCV bar with integer nanosecond timestamp in UTC."""

    instrument_id: InstrumentIdProtocol | InstrumentId | object
    ts: int  # unix nanoseconds UTC
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def timestamp(self) -> int:
        """Alias for ts for cross-ecosystem compatibility."""
        return self.ts


# Alias for backward-compatible fallback naming
LocalBar = Bar


def as_instrument_id(val: object) -> InstrumentId:
    """Convert an arbitrary identifier object or string to an InstrumentId."""
    if isinstance(val, InstrumentId):
        return val
    if hasattr(val, "symbol") and hasattr(val, "exchange"):
        sym = str(getattr(val, "symbol", "")).upper()
        exch = str(getattr(val, "exchange", "NSE")).upper()
        return InstrumentId(symbol=sym, exchange=exch)
    if isinstance(val, str):
        # Support "RELIANCE.NSE", "NSE:RELIANCE", or "RELIANCE"
        if "." in val:
            parts = val.split(".", 1)
            return InstrumentId(symbol=parts[0].upper(), exchange=parts[1].upper())
        if ":" in val:
            parts = val.split(":", 1)
            return InstrumentId(symbol=parts[1].upper(), exchange=parts[0].upper())
        return InstrumentId(symbol=val.upper(), exchange="NSE")
    if isinstance(val, Mapping):
        sym_obj = val.get("symbol", "")
        exch_obj = val.get("exchange", "NSE")
        sym = str(sym_obj if sym_obj is not None else "").upper()
        exch = str(exch_obj if exch_obj is not None else "NSE").upper()
        return InstrumentId(symbol=sym, exchange=exch)
    raise TypeError(f"Cannot adapt {type(val)} to InstrumentId: {val!r}")


def as_bar(val: object) -> Bar:
    """Adapt an arbitrary object or mapping to a canonical Bar."""
    if isinstance(val, Bar):
        return val
    if hasattr(val, "instrument_id") and hasattr(val, "open"):
        inst = as_instrument_id(getattr(val, "instrument_id"))
        ts_raw = getattr(val, "ts", getattr(val, "timestamp", 0))
        ts = _cast_int(ts_raw)
        return Bar(
            instrument_id=inst,
            ts=ts,
            open=_cast_float(getattr(val, "open")),
            high=_cast_float(getattr(val, "high")),
            low=_cast_float(getattr(val, "low")),
            close=_cast_float(getattr(val, "close")),
            volume=_cast_float(getattr(val, "volume")),
        )
    if isinstance(val, Mapping):
        inst_raw = val.get("instrument_id") or val.get("symbol", "")
        inst = as_instrument_id(inst_raw)
        ts_raw = val.get("ts", val.get("timestamp", 0))
        ts = _cast_int(ts_raw)
        return Bar(
            instrument_id=inst,
            ts=ts,
            open=_cast_float(val["open"]),
            high=_cast_float(val["high"]),
            low=_cast_float(val["low"]),
            close=_cast_float(val["close"]),
            volume=_cast_float(val["volume"]),
        )
    raise TypeError(f"Cannot adapt {type(val)} to Bar: {val!r}")


# ---------------------------------------------------------------------------
# Fundamentals & Corporate Entities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Company:
    """Indian listed equity company metadata."""

    symbol: str
    company_name: str
    industry: str = ""
    series: str = "EQ"
    isin_code: str = ""
    cap_category: str = "LARGE_CAP"  # LARGE_CAP | MID_CAP | SMALL_CAP
    index_name: str = ""


@dataclass(frozen=True)
class FinancialStatement:
    """Point-in-time financial statement (Balance Sheet, Income Statement, Cash Flow)."""

    symbol: str
    fiscal_date: dt.date
    filing_date: dt.date
    period_type: str  # "FY" (Annual) or "FQ" (Quarterly)
    statement_type: str  # "INCOME", "BALANCE_SHEET", "CASH_FLOW"
    line_items: dict[str, float] = field(default_factory=dict)
    currency: str = "INR"

    def __post_init__(self) -> None:
        if self.period_type not in ("FY", "FQ"):
            raise ValueError(f"period_type must be 'FY' or 'FQ', got {self.period_type!r}")
        if self.statement_type not in ("INCOME", "BALANCE_SHEET", "CASH_FLOW"):
            raise ValueError(f"Unknown statement_type: {self.statement_type!r}")


@dataclass(frozen=True)
class FinancialRatios:
    """Standardized fundamental financial ratios for a company filing."""

    symbol: str
    fiscal_date: dt.date
    filing_date: dt.date
    period_type: str  # "FY" or "FQ"
    roce_pct: float | None = None
    roe_pct: float | None = None
    operating_margin_pct: float | None = None
    net_margin_pct: float | None = None
    debt_to_equity: float | None = None
    free_cash_flow: float | None = None
