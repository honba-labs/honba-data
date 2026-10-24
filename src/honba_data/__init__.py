"""honba-data: Financial Data Lakehouse and Service Layer for Honba Labs.

Provides:
- Tactical Hexagonal Architecture: DataService port & DataServiceImpl
- DuckDB-backed Company Fundamentals Store & FundamentalsRepository
- JSON & Atomic Checksummed Coverage Ledger & CoverageRepository
- DuckDB-backed Corporate Actions Store (Splits, Bonuses, Dividends & OHLCV Adjustments)
- Automated Data Quality & Coverage Ledgers (Missing bars, duplicates, volume spikes, OHLC anomalies)
- DuckDB-backed Mutual Funds Store (Schemes, NAV, TER, AUM, Holdings & Equity Cross-Ref)
- Parquet-backed Time-Series Bar Store (OHLCV Market Data with Hive standard partitioning)
- Market and Fundamental Data Providers (NSE Bhavcopy, Yahoo Finance, Corporate Filings)
"""

from __future__ import annotations

from honba_data.adapters.ledger.json_ledger import JsonCoverageRepository
from honba_data.coverage.data_quality import (
    DataQualityChecker,
    DataQualityReport,
    QualityIssue,
    QualityIssueType,
    QualitySeverity,
)
from honba_data.domain.coverage import CoverageRecord, CoverageStatus, DateInterval
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
    as_bar,
    as_instrument_id,
)
from honba_data.ports.coverage_repo import CoverageRepository
from honba_data.ports.fundamentals_repo import FundamentalsRepository
from honba_data.ports.provider import FundamentalsProvider
from honba_data.ports.service import DataService
from honba_data.providers.fundamentals import (
    CompanyMetadata,
    download_fundamentals_to_duckdb,
    fetch_large_midcap_universe,
)
from honba_data.service.service import DataServiceImpl
from honba_data.store.corporate_actions import (
    CorporateAction,
    DuckDbCorporateActionsStore,
)
from honba_data.store.fundamentals import DuckDbFundamentalsStore, find_data_root
from honba_data.store.mutual_funds import (
    DuckDbMutualFundsStore,
    FundHolding,
    FundStat,
    SchemeMetadata,
)
from honba_data.store.parquet import ParquetBarStore
from honba_data.store.sebi_audit import DuckDbAuditLedger

__version__ = "0.1.0"

__all__ = [
    "Bar",
    "BarProtocol",
    "Company",
    "CompanyMetadata",
    "CorporateAction",
    "CoverageLedgerUpdated",
    "CoverageRecord",
    "CoverageRepository",
    "CoverageStatus",
    "DataQualityAlertRaised",
    "DataQualityChecker",
    "DataQualityReport",
    "DataService",
    "DataServiceImpl",
    "DateInterval",
    "DomainEvent",
    "DuckDbAuditLedger",
    "DuckDbCorporateActionsStore",
    "DuckDbFundamentalsStore",
    "DuckDbMutualFundsStore",
    "FinancialRatios",
    "FinancialStatement",
    "FundHolding",
    "FundStat",
    "FundamentalsIngested",
    "FundamentalsProvider",
    "FundamentalsRepository",
    "IngestionFailed",
    "InstrumentId",
    "InstrumentIdProtocol",
    "JsonCoverageRepository",
    "ParquetBarStore",
    "QualityIssue",
    "QualityIssueType",
    "QualitySeverity",
    "SchemeMetadata",
    "as_bar",
    "as_instrument_id",
    "download_fundamentals_to_duckdb",
    "fetch_large_midcap_universe",
    "find_data_root",
]
