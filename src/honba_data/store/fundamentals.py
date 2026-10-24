"""DuckDB storage engine for company financial statements and ratios.

Backward-compatibility module: core DuckDB adapter has transitioned to
:mod:`honba_data.adapters.duckdb.store`.
"""

from __future__ import annotations

from honba_data.adapters.duckdb.store import (
    DATA_DIR_ENV,
    DuckDbFundamentalsStore,
    find_data_root,
)

__all__ = [
    "DATA_DIR_ENV",
    "DuckDbFundamentalsStore",
    "find_data_root",
]
