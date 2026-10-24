"""Unit tests for honba_data domain entities, protocols, and boundary adapters."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from honba_data.domain.events import (
    CoverageLedgerUpdated,
    DataQualityAlertRaised,
    DomainEvent,
    FundamentalsIngested,
    IngestionFailed,
)
from honba_data.domain.models import (
    Bar,
    BarProtocol,
    Company,
    FinancialRatios,
    FinancialStatement,
    InstrumentId,
    InstrumentIdProtocol,
    LocalBar,
    LocalInstrumentId,
    as_bar,
    as_instrument_id,
)


def test_instrument_id_creation_and_defaults() -> None:
    inst1 = InstrumentId("TCS", "NSE")
    assert inst1.symbol == "TCS"
    assert inst1.exchange == "NSE"
    assert str(inst1) == "TCS.NSE"

    inst2 = LocalInstrumentId("INFY")
    assert inst2.symbol == "INFY"
    assert inst2.exchange == "NSE"
    assert str(inst2) == "INFY.NSE"
    assert inst2.to_dict() == {"symbol": "INFY", "exchange": "NSE"}


def test_instrument_id_protocol_conformance() -> None:
    inst = LocalInstrumentId("RELIANCE", "BSE")
    assert isinstance(inst, InstrumentIdProtocol)

    @dataclass
    class CustomInst:
        symbol: str
        exchange: str

    assert isinstance(CustomInst("HDFCBANK", "NSE"), InstrumentIdProtocol)


def test_as_instrument_id_adapter() -> None:
    # From existing InstrumentId / LocalInstrumentId
    inst1 = InstrumentId("TCS", "NSE")
    assert as_instrument_id(inst1) == inst1

    # From string with dot
    inst2 = as_instrument_id("SBIN.NSE")
    assert inst2.symbol == "SBIN"
    assert inst2.exchange == "NSE"

    # From string with colon
    inst3 = as_instrument_id("BSE:ITC")
    assert inst3.symbol == "ITC"
    assert inst3.exchange == "BSE"

    # From bare string
    inst4 = as_instrument_id("wipro")
    assert inst4.symbol == "WIPRO"
    assert inst4.exchange == "NSE"

    # From dictionary / mapping
    inst5 = as_instrument_id({"symbol": "infy", "exchange": "nse"})
    assert inst5.symbol == "INFY"
    assert inst5.exchange == "NSE"

    with pytest.raises(TypeError):
        as_instrument_id(12345)


def test_bar_creation_and_protocol_conformance() -> None:
    inst = InstrumentId("TCS", "NSE")
    bar = LocalBar(
        instrument_id=inst,
        ts=1_700_000_000_000_000_000,
        open=3500.0,
        high=3550.0,
        low=3490.0,
        close=3520.0,
        volume=100_000.0,
    )
    assert isinstance(bar, BarProtocol)
    assert bar.timestamp == 1_700_000_000_000_000_000
    assert bar.open == 3500.0
    assert bar.high == 3550.0
    assert bar.low == 3490.0
    assert bar.close == 3520.0
    assert bar.volume == 100_000.0


def test_as_bar_adapter() -> None:
    inst = InstrumentId("TCS", "NSE")
    bar = Bar(
        instrument_id=inst,
        ts=1_000_000_000,
        open=100.0,
        high=110.0,
        low=95.0,
        close=105.0,
        volume=500.0,
    )
    adapted = as_bar(bar)
    assert adapted == bar

    # From mapping
    mapping = {
        "symbol": "TCS",
        "exchange": "NSE",
        "ts": 2_000_000_000,
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 500.0,
    }
    adapted_map = as_bar(mapping)
    assert adapted_map.instrument_id.symbol == "TCS"
    assert adapted_map.ts == 2_000_000_000
    assert adapted_map.close == 105.0

    with pytest.raises(TypeError):
        as_bar("not-a-bar")


def test_financial_statement_invariants() -> None:
    stmt = FinancialStatement(
        symbol="TCS",
        fiscal_date=dt.date(2025, 3, 31),
        filing_date=dt.date(2025, 4, 15),
        period_type="FY",
        statement_type="INCOME",
        line_items={"revenue": 240000.0, "net_income": 45000.0},
    )
    assert stmt.symbol == "TCS"
    assert stmt.period_type == "FY"
    assert stmt.statement_type == "INCOME"
    assert stmt.currency == "INR"

    # Invalid period type
    with pytest.raises(ValueError, match="period_type"):
        FinancialStatement(
            symbol="TCS",
            fiscal_date=dt.date(2025, 3, 31),
            filing_date=dt.date(2025, 4, 15),
            period_type="ANNUAL",
            statement_type="INCOME",
        )

    # Invalid statement type
    with pytest.raises(ValueError, match="Unknown statement_type"):
        FinancialStatement(
            symbol="TCS",
            fiscal_date=dt.date(2025, 3, 31),
            filing_date=dt.date(2025, 4, 15),
            period_type="FY",
            statement_type="PROFIT_LOSS",
        )


def test_financial_ratios() -> None:
    ratios = FinancialRatios(
        symbol="INFY",
        fiscal_date=dt.date(2025, 3, 31),
        filing_date=dt.date(2025, 4, 18),
        period_type="FY",
        roce_pct=32.5,
        roe_pct=28.1,
        debt_to_equity=0.08,
    )
    assert ratios.symbol == "INFY"
    assert ratios.roce_pct == 32.5
    assert ratios.debt_to_equity == 0.08


def test_company_entity() -> None:
    comp = Company(
        symbol="RELIANCE",
        company_name="Reliance Industries Limited",
        industry="Oil & Gas / Telecom / Retail",
        isin_code="INE002A01018",
        cap_category="LARGE_CAP",
    )
    assert comp.symbol == "RELIANCE"
    assert comp.cap_category == "LARGE_CAP"


def test_domain_events() -> None:
    event = FundamentalsIngested(
        symbol="TCS",
        period_type="FY",
        fiscal_date=dt.date(2025, 3, 31),
        filing_date=dt.date(2025, 4, 15),
        statement_types=("INCOME", "BALANCE_SHEET"),
        rows_written=42,
    )
    assert isinstance(event, DomainEvent)
    assert event.symbol == "TCS"
    assert event.rows_written == 42
    assert event.occurred_at is not None

    alert = DataQualityAlertRaised(
        issue_type="NON_FINITE_VALUE",
        severity="ERROR",
        symbol="TEST",
        details="NaN detected in close price",
    )
    assert alert.severity == "ERROR"

    ledger_event = CoverageLedgerUpdated(
        symbol="TCS",
        exchange="NSE",
        timeframe="1D",
        start_date=dt.date(2025, 1, 1),
        end_date=dt.date(2025, 12, 31),
    )
    assert ledger_event.timeframe == "1D"

    fail_event = IngestionFailed(
        provider="NSE",
        symbol="XYZ",
        error_message="HTTP 404",
    )
    assert fail_event.provider == "NSE"
