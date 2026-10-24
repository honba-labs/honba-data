"""honba-data: Financial Data Lakehouse and Service Layer for Honba Labs.

Provides:
- DuckDB-backed Company Fundamentals Store (Point-in-Time Yearly and Quarterly Statements & Ratios)
- DuckDB-backed Corporate Actions Store (Splits, Bonuses, Dividends & OHLCV Adjustments)
- Automated Data Quality & Coverage Ledgers (Missing bars, duplicates, volume spikes, OHLC anomalies)
- DuckDB-backed Mutual Funds Store (Schemes, NAV, TER, AUM, Holdings & Equity Cross-Ref)
- Parquet-backed Time-Series Bar Store (OHLCV Market Data)
- Market and Fundamental Data Providers (NSE Bhavcopy, Yahoo Finance, Corporate Filings)
"""

from __future__ import annotations

from honba_data.coverage.data_quality import (
    DataQualityChecker,
    DataQualityReport,
    QualityIssue,
    QualityIssueType,
    QualitySeverity,
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
from honba_data.providers.fundamentals import (
    CompanyMetadata,
    download_fundamentals_to_duckdb,
    fetch_large_midcap_universe,
)
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
    "DataQualityChecker",
    "DataQualityReport",
    "DuckDbAuditLedger",
    "DuckDbCorporateActionsStore",
    "DuckDbFundamentalsStore",
    "DuckDbMutualFundsStore",
    "FinancialRatios",
    "FinancialStatement",
    "FundHolding",
    "FundStat",
    "InstrumentId",
    "InstrumentIdProtocol",
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
