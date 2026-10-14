"""honba-data: Financial Data Lakehouse and Service Layer for Honba Labs.

Provides:
- DuckDB-backed Company Fundamentals Store (Yearly and Quarterly Statements & Ratios)
- DuckDB-backed Mutual Funds Store (Schemes, NAV, TER, AUM, Holdings & Equity Cross-Ref)
- Parquet-backed Time-Series Bar Store (OHLCV Market Data)
- Market and Fundamental Data Providers (NSE Bhavcopy, Yahoo Finance, Corporate Filings)
- Gap Planning, Coverage Ledger, and Data Service
"""

from __future__ import annotations

from honba_data.providers.fundamentals import (
    CompanyMetadata,
    download_fundamentals_to_duckdb,
    fetch_large_midcap_universe,
)
from honba_data.store.fundamentals import DuckDbFundamentalsStore, find_data_root
from honba_data.store.mutual_funds import (
    DuckDbMutualFundsStore,
    FundHolding,
    FundStat,
    SchemeMetadata,
)

__version__ = "0.1.0"

__all__ = [
    "CompanyMetadata",
    "DuckDbFundamentalsStore",
    "DuckDbMutualFundsStore",
    "FundHolding",
    "FundStat",
    "SchemeMetadata",
    "download_fundamentals_to_duckdb",
    "fetch_large_midcap_universe",
    "find_data_root",
]
