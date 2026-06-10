from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from autoresearch_constants import (
    research_engine_screening_min_lift,
    research_engine_screening_min_support,
)
from backtest_run_db import BacktestRunDB
from research_types import CausalFactor
from screening import screen_factors, write_screenings


def _feature_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trade_id": "t1", "gap_pct": -1.1, "out_is_loss": True, "out_pnl": -4.0},
            {"trade_id": "t2", "gap_pct": -0.9, "out_is_loss": True, "out_pnl": -2.0},
            {"trade_id": "t3", "gap_pct": 0.4, "out_is_loss": False, "out_pnl": 2.0},
            {"trade_id": "t4", "gap_pct": 0.3, "out_is_loss": False, "out_pnl": 3.0},
            {"trade_id": "t5", "gap_pct": -0.2, "out_is_loss": False, "out_pnl": 1.0},
        ]
    )


def test_research_engine_screening_thresholds_read_from_config_block() -> None:
    config = {"research_engine": {"screening_min_support": 7, "screening_min_lift": 0.35}}

    assert research_engine_screening_min_support(config) == 7
    assert research_engine_screening_min_lift(config) == 0.35


def test_screen_factors_executes_query_rules_and_detects_competing_hypothesis() -> None:
    factors = [
        CausalFactor(
            factor_id="f001",
            story="Gap-down entries are loss-prone.",
            rule="gap_pct < 0",
            direction="loss",
        ),
        CausalFactor(
            factor_id="f002",
            story="Gap-up entries should be loss-prone, but the data says otherwise.",
            rule="gap_pct > 0",
            direction="loss",
        ),
    ]

    results = screen_factors(
        _feature_table(),
        factors,
        research_engine_config={"screening_min_support": 2, "screening_min_lift": 0.25},
    )

    by_factor = {result.factor_id: result for result in results}
    assert by_factor["f001"].verdict == "supported"
    assert by_factor["f001"].support == 3
    assert by_factor["f001"].flagged_loss_rate == 2 / 3
    assert by_factor["f002"].verdict == "refuted"
    assert by_factor["f002"].competing_hypothesis == "rule flags wins, not losses"


def test_screenings_table_exists_and_write_screenings_persists_rows(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    results = screen_factors(
        _feature_table(),
        [
            CausalFactor(
                factor_id="f001",
                story="Gap-down entries are loss-prone.",
                rule="gap_pct < 0",
                direction="loss",
            )
        ],
        research_engine_config={"screening_min_support": 2, "screening_min_lift": 0.25},
    )

    write_screenings(
        db.path,
        results,
        family="ema",
        research_round_id="job-1-round-2",
        thresholds={"screening_min_support": 2, "screening_min_lift": 0.25},
    )

    with sqlite3.connect(db.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(screenings)")}
        rows = conn.execute(
            "SELECT factor_id, verdict, support, thresholds_json FROM screenings"
        ).fetchall()

    assert {"screening_id", "factor_id", "verdict", "thresholds_json"} <= columns
    assert rows == [
        ("f001", "supported", 3, '{"screening_min_lift":0.25,"screening_min_support":2}')
    ]
