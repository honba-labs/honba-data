"""Service layer for honba-data lakehouse."""

from __future__ import annotations

from honba_data.ports.service import DataService
from honba_data.service.service import DataServiceImpl

__all__ = [
    "DataService",
    "DataServiceImpl",
]
