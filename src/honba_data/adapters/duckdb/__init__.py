"""DuckDB infrastructure adapters for honba-data."""

from __future__ import annotations

from honba_data.adapters.duckdb.corporate import (
    CorporateAction,
    DuckDbCorporateActionsStore,
)
from honba_data.adapters.duckdb.schema import (
    SCHEMA_VERSION,
    init_duckdb_schema,
)
from honba_data.adapters.duckdb.store import (
    DuckDbFundamentalsStore,
    find_data_root,
)

__all__ = [
    "CorporateAction",
    "DuckDbCorporateActionsStore",
    "DuckDbFundamentalsStore",
    "SCHEMA_VERSION",
    "find_data_root",
    "init_duckdb_schema",
]
