"""Pure interval algebra, coverage models, and gap planning.

Backward compatibility module: models have moved to :mod:`honba_data.domain.coverage`.
"""

from __future__ import annotations

import pyarrow as pa

from honba_data.domain.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
    QualityIssue,
    merge_close_intervals,
    merge_intervals,
    plan_gaps,
    subtract_intervals,
)

BAR_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)

__all__ = [
    "BAR_SCHEMA",
    "CoverageRecord",
    "CoverageStatus",
    "DateInterval",
    "QualityIssue",
    "merge_close_intervals",
    "merge_intervals",
    "plan_gaps",
    "subtract_intervals",
]
