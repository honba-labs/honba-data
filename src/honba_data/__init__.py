"""honba-data: Financial Data Lakehouse and Service Layer for Honba Labs.

Provides:
- DuckDB-backed Company Fundamentals Store (Yearly and Quarterly Statements & Ratios)
- Parquet-backed Time-Series Bar Store (OHLCV Market Data)
- Market and Fundamental Data Loaders (NSE Bhavcopy, Yahoo Finance, Corporate Filings)
- Gap Planning, Coverage Ledger, and Data Service
"""

from __future__ import annotations

from honba_data.loaders.fundamentals import (
    CompanyMetadata,
    download_fundamentals_to_duckdb,
    fetch_large_midcap_universe,
)
from honba_data.store.fundamentals import DuckDbFundamentalsStore, find_data_root

__version__ = "0.1.0"

__all__ = [
    "CompanyMetadata",
    "DuckDbFundamentalsStore",
    "download_fundamentals_to_duckdb",
    "fetch_large_midcap_universe",
    "find_data_root",
]
