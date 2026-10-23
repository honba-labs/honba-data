"""Unit tests for DuckDbCorporateActionsStore and OHLCV Adjustment Pipeline."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from honba_data.store.corporate_actions import CorporateAction, DuckDbCorporateActionsStore


@pytest.fixture
def actions_store(tmp_path: Path) -> DuckDbCorporateActionsStore:
    db_file = tmp_path / "test_corp_actions.duckdb"
    return DuckDbCorporateActionsStore(db_file)


def test_split_and_bonus_factor_calculation() -> None:
    # 5:1 split: 1 old share becomes 5 new shares -> price factor = 1/5 = 0.2
    split_5_1 = CorporateAction(
        symbol="TATASTEEL",
        action_type="SPLIT",
        ex_date=dt.date(2022, 7, 28),
        ratio_numerator=5.0,
        ratio_denominator=1.0,
    )
    assert split_5_1.computed_factor() == 0.2

    # 1:1 bonus: 1 bonus for 1 held -> total 2 shares -> price factor = 1/2 = 0.5
    bonus_1_1 = CorporateAction(
        symbol="RELIANCE",
        action_type="BONUS",
        ex_date=dt.date(2024, 10, 28),
        ratio_numerator=1.0,
        ratio_denominator=1.0,
    )
    assert bonus_1_1.computed_factor() == 0.5

    # 1:2 bonus: 1 bonus for 2 held -> total 3 shares -> price factor = 2/3
    bonus_1_2 = CorporateAction(
        symbol="INFY",
        action_type="BONUS",
        ex_date=dt.date(2018, 9, 4),
        ratio_numerator=1.0,
        ratio_denominator=2.0,
    )
    assert round(bonus_1_2.computed_factor(), 4) == round(2.0 / 3.0, 4)


def test_corporate_actions_store_crud(actions_store: DuckDbCorporateActionsStore) -> None:
    act1 = CorporateAction(
        symbol="INFY",
        action_type="SPLIT",
        ex_date=dt.date(2020, 6, 1),
        ratio_numerator=2.0,
        ratio_denominator=1.0,
        remarks="2-for-1 subdivision",
    )
    act2 = CorporateAction(
        symbol="INFY",
        action_type="BONUS",
        ex_date=dt.date(2023, 1, 15),
        ratio_numerator=1.0,
        ratio_denominator=1.0,
        remarks="1:1 bonus",
    )
    actions_store.upsert_actions([act1, act2])

    retrieved = actions_store.get_actions(symbol="INFY")
    assert len(retrieved) == 2
    assert retrieved[0]["action_type"] == "SPLIT"
    assert retrieved[0]["adjustment_factor"] == 0.5
    assert retrieved[1]["action_type"] == "BONUS"
    assert retrieved[1]["adjustment_factor"] == 0.5


def test_multi_event_cumulative_factor_and_ohlcv_adjustment(
    actions_store: DuckDbCorporateActionsStore,
) -> None:
    # Action 1: Split 2:1 on 2023-01-15 (factor 0.5)
    # Action 2: Bonus 1:1 on 2024-01-15 (factor 0.5)
    actions_store.upsert_actions(
        [
            CorporateAction(
                symbol="MOCKCO",
                action_type="SPLIT",
                ex_date=dt.date(2023, 1, 15),
                ratio_numerator=2.0,
                ratio_denominator=1.0,
            ),
            CorporateAction(
                symbol="MOCKCO",
                action_type="BONUS",
                ex_date=dt.date(2024, 1, 15),
                ratio_numerator=1.0,
                ratio_denominator=1.0,
            ),
        ]
    )

    dates = [
        dt.date(2022, 6, 1),  # before both actions -> factor = 0.5 * 0.5 = 0.25
        dt.date(2023, 6, 1),  # after split, before bonus -> factor = 0.5
        dt.date(2024, 6, 1),  # after both actions -> factor = 1.0
    ]

    factor_map = actions_store.compute_cumulative_factors("MOCKCO", dates)
    assert factor_map[dt.date(2022, 6, 1)] == 0.25
    assert factor_map[dt.date(2023, 6, 1)] == 0.5
    assert factor_map[dt.date(2024, 6, 1)] == 1.0

    # Test DataFrame adjustment
    df = pd.DataFrame(
        [
            {
                "symbol": "MOCKCO",
                "bar_date": dt.date(2022, 6, 1),
                "open": 1000.0,
                "high": 1050.0,
                "low": 980.0,
                "close": 1020.0,
                "volume": 100_000.0,
            },
            {
                "symbol": "MOCKCO",
                "bar_date": dt.date(2023, 6, 1),
                "open": 600.0,
                "high": 620.0,
                "low": 590.0,
                "close": 610.0,
                "volume": 200_000.0,
            },
            {
                "symbol": "MOCKCO",
                "bar_date": dt.date(2024, 6, 1),
                "open": 350.0,
                "high": 360.0,
                "low": 340.0,
                "close": 355.0,
                "volume": 400_000.0,
            },
        ]
    )

    adj_df = actions_store.adjust_ohlcv_dataframe(df)

    # 2022 bar (factor 0.25):
    row0 = adj_df.iloc[0]
    assert row0["adj_factor"] == 0.25
    assert row0["adj_open"] == 250.0
    assert row0["adj_close"] == 255.0
    assert row0["adj_volume"] == 400_000.0

    # 2023 bar (factor 0.5):
    row1 = adj_df.iloc[1]
    assert row1["adj_factor"] == 0.5
    assert row1["adj_open"] == 300.0
    assert row1["adj_close"] == 305.0
    assert row1["adj_volume"] == 400_000.0

    # 2024 bar (factor 1.0):
    row2 = adj_df.iloc[2]
    assert row2["adj_factor"] == 1.0
    assert row2["adj_open"] == 350.0
    assert row2["adj_close"] == 355.0
    assert row2["adj_volume"] == 400_000.0
