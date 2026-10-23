# pyright: reportMissingTypeStubs=false
"""DuckDB storage and adjustment calculation pipeline for corporate actions.

Supports:
- Stock splits (e.g. 10:1, 5:1, 2:1)
- Bonus issues (e.g. 1:1, 1:2, 3:1)
- Rights issues
- Extraordinary / cash dividends

Calculates backward-adjusted OHLCV series for backtesting fidelity:
    adj_price = unadjusted_price * cumulative_factor
    adj_volume = unadjusted_volume / cumulative_factor
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import duckdb
import pandas as pd

from honba_data.store.fundamentals import find_data_root


def _to_date(val: object) -> dt.date:
    """Safely convert a pandas Timestamp, datetime, date, or ISO string to dt.date."""
    if hasattr(val, "date") and callable(getattr(val, "date")):
        return getattr(val, "date")()
    if isinstance(val, dt.date):
        return val
    return dt.date.fromisoformat(str(val)[:10])


@dataclasses.dataclass(frozen=True)
class CorporateAction:
    """Canonical representation of an equity corporate action."""

    symbol: str
    action_type: str  # "SPLIT" | "BONUS" | "RIGHTS" | "DIVIDEND"
    ex_date: dt.date
    record_date: dt.date | None = None
    ratio_numerator: float = 1.0  # new shares (or bonus shares)
    ratio_denominator: float = 1.0  # old shares
    dividend_amount: float | None = None
    adjustment_factor: float | None = None
    currency: str = "INR"
    remarks: str = ""

    def computed_factor(self, reference_price: float | None = None) -> float:
        """Calculate the price multiplier for historical bars preceding ex_date."""
        if self.adjustment_factor is not None and self.adjustment_factor > 0:
            return float(self.adjustment_factor)

        atype = self.action_type.upper().strip()
        if atype == "SPLIT":
            # N new shares for D old shares -> price factor = D / N
            # e.g. 5:1 split (1 old becomes 5): factor = 1 / 5 = 0.2
            if self.ratio_numerator > 0:
                return float(self.ratio_denominator / self.ratio_numerator)
            return 1.0

        if atype == "BONUS":
            # N bonus shares for D existing shares held:
            # Total new shares = D + N. Price factor = D / (D + N)
            # e.g. 1:1 bonus -> factor = 1 / (1 + 1) = 0.5
            # e.g. 1:2 bonus (1 for every 2 held) -> 2 / (2 + 1) = 0.6667
            total = self.ratio_denominator + self.ratio_numerator
            if total > 0:
                return float(self.ratio_denominator / total)
            return 1.0

        if atype == "DIVIDEND" and self.dividend_amount is not None:
            # Factor = (P - D) / P
            if reference_price and reference_price > self.dividend_amount:
                return float((reference_price - self.dividend_amount) / reference_price)
            return 1.0

        return 1.0


class DuckDbCorporateActionsStore:
    """Embedded DuckDB storage and adjustment engine for Indian corporate actions."""

    db_path: Path

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            data_root = find_data_root()
            data_root.mkdir(parents=True, exist_ok=True)
            self.db_path = data_root / "fundamentals.duckdb"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.db_path))

    def _init_schema(self) -> None:
        with self._get_connection() as con:
            _ = con.execute("""
                CREATE TABLE IF NOT EXISTS corporate_actions (
                    symbol VARCHAR,
                    action_type VARCHAR,
                    ex_date DATE,
                    record_date DATE,
                    ratio_numerator DOUBLE,
                    ratio_denominator DOUBLE,
                    dividend_amount DOUBLE,
                    adjustment_factor DOUBLE,
                    currency VARCHAR DEFAULT 'INR',
                    remarks VARCHAR,
                    PRIMARY KEY (symbol, action_type, ex_date)
                );
            """)
            _ = con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_pk ON corporate_actions(symbol, action_type, ex_date);"
            )

    def upsert_actions(self, actions: Sequence[CorporateAction]) -> None:
        """Insert or update corporate actions in DuckDB."""
        if not actions:
            return

        rows = []
        for a in actions:
            factor = a.computed_factor()
            rows.append(
                (
                    a.symbol.upper().strip(),
                    a.action_type.upper().strip(),
                    a.ex_date,
                    a.record_date,
                    a.ratio_numerator,
                    a.ratio_denominator,
                    a.dividend_amount,
                    factor,
                    a.currency,
                    a.remarks,
                )
            )

        with self._get_connection() as con:
            _ = con.executemany(
                """
                INSERT INTO corporate_actions (
                    symbol, action_type, ex_date, record_date, ratio_numerator,
                    ratio_denominator, dividend_amount, adjustment_factor, currency, remarks
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (symbol, action_type, ex_date) DO UPDATE SET
                    record_date = excluded.record_date,
                    ratio_numerator = excluded.ratio_numerator,
                    ratio_denominator = excluded.ratio_denominator,
                    dividend_amount = excluded.dividend_amount,
                    adjustment_factor = excluded.adjustment_factor,
                    remarks = excluded.remarks;
                """,
                rows,
            )

    def get_actions(
        self,
        symbol: str | None = None,
        action_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve corporate actions ordered by ex_date."""
        clauses = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper().strip())
        if action_type:
            clauses.append("action_type = ?")
            params.append(action_type.upper().strip())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM corporate_actions {where} ORDER BY symbol, ex_date ASC;"

        with self._get_connection() as con:
            df = con.execute(query, params).df()
            return cast(list[dict[str, Any]], df.to_dict(orient="records"))

    def compute_cumulative_factors(
        self,
        symbol: str,
        dates: Sequence[dt.date | str],
    ) -> dict[dt.date, float]:
        """Compute the cumulative multiplier for each date strictly prior to ex-dates.

        For each bar date t, cumulative_factor(t) = product of adjustment_factors
        for all corporate actions on this symbol where ex_date > t.
        """
        actions = self.get_actions(symbol=symbol)
        date_objs = [_to_date(d) for d in dates]

        factor_map: dict[dt.date, float] = {}
        for d in date_objs:
            cum_factor = 1.0
            for a in actions:
                a_ex_date = _to_date(a["ex_date"])
                if a_ex_date > d:
                    factor = float(a["adjustment_factor"]) if a.get("adjustment_factor") else 1.0
                    cum_factor *= factor
            factor_map[d] = cum_factor

        return factor_map

    def adjust_ohlcv_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply corporate action adjustments to an unadjusted OHLCV DataFrame.

        Required columns in ``df``:
            ['symbol', 'bar_date', 'open', 'high', 'low', 'close', 'volume']

        Returns a new DataFrame enriched with:
            ['adj_factor', 'adj_open', 'adj_high', 'adj_low', 'adj_close', 'adj_volume']
        """
        if df.empty:
            out = df.copy()
            for col in ["adj_factor", "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume"]:
                out[col] = []
            return out

        res = df.copy()
        all_actions = self.get_actions()
        actions_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for a in all_actions:
            actions_by_symbol.setdefault(a["symbol"], []).append(a)

        factors: list[float] = []
        for _, row in res.iterrows():
            sym = str(row["symbol"]).upper().strip()
            bdate = _to_date(row["bar_date"])

            sym_actions = actions_by_symbol.get(sym, [])
            cum_factor = 1.0
            for a in sym_actions:
                a_ex = _to_date(a["ex_date"])
                if a_ex > bdate:
                    cum_factor *= float(a["adjustment_factor"] or 1.0)
            factors.append(cum_factor)

        res["adj_factor"] = factors
        res["adj_open"] = (res["open"] * res["adj_factor"]).round(4)
        res["adj_high"] = (res["high"] * res["adj_factor"]).round(4)
        res["adj_low"] = (res["low"] * res["adj_factor"]).round(4)
        res["adj_close"] = (res["close"] * res["adj_factor"]).round(4)
        res["adj_volume"] = (res["volume"] / res["adj_factor"]).round(2)

        return res

    def create_adjusted_bars_view(
        self,
        view_name: str = "bars_adjusted_1d",
        unadjusted_view: str = "bars_1d",
    ) -> None:
        """Create or replace a DuckDB view computing adjusted bars dynamically."""
        with self._get_connection() as con:
            # Check if unadjusted view or table exists
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
            if unadjusted_view not in tables:
                return

            _ = con.execute(f"""
                CREATE OR REPLACE VIEW {view_name} AS
                WITH factors AS (
                    SELECT
                        b.symbol,
                        b.bar_date,
                        COALESCE(product(a.adjustment_factor), 1.0) AS adj_factor
                    FROM {unadjusted_view} b
                    LEFT JOIN corporate_actions a
                      ON b.symbol = a.symbol AND a.ex_date > b.bar_date
                    GROUP BY b.symbol, b.bar_date
                )
                SELECT
                    b.symbol,
                    b.bar_date,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    f.adj_factor,
                    round(b.open * f.adj_factor, 4) AS adj_open,
                    round(b.high * f.adj_factor, 4) AS adj_high,
                    round(b.low * f.adj_factor, 4) AS adj_low,
                    round(b.close * f.adj_factor, 4) AS adj_close,
                    round(b.volume / f.adj_factor, 2) AS adj_volume
                FROM {unadjusted_view} b
                JOIN factors f ON b.symbol = f.symbol AND b.bar_date = f.bar_date;
            """)
