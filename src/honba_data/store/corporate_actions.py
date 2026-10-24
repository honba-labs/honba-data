"""Corporate actions storage and adjustments.

Backward compatibility module: core DuckDB corporate actions store has moved to
:mod:`honba_data.adapters.duckdb.corporate`.
"""

from __future__ import annotations

from honba_data.adapters.duckdb.corporate import (
    CorporateAction,
    DuckDbCorporateActionsStore,
)

__all__ = [
    "CorporateAction",
    "DuckDbCorporateActionsStore",
]
