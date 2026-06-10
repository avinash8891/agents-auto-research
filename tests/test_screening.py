from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import screening
from autoresearch_constants import (
    research_engine_holdout_fraction,
    research_engine_max_p_value,
    research_engine_max_population_overlap,
    research_engine_max_retries,
    research_engine_min_abs_lift,
    research_engine_min_sample,
    research_engine_retest_extension_months,
)
from backtest_run_db import BacktestRunDB
from research_types import CausalFactor, CausalModel
from screening import screen, write_screenings


def test_screening_module_docstring_documents_two_proportion_z_test_formula() -> None:
    assert screening.__doc__ is not None
    assert "z = (p1 - p2) / sqrt(pooled * (1 - pooled) * (1/n1 + 1/n2))" in (screening.__doc__)


def _feature_table() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for index in range(40):
        rows.append(
            {
                "trade_id": f"gap-down-{index}",
                "gap_pct": -1.0,
                "vol_pctile_20d": 0.2 if index % 2 else 0.8,
                "out_is_loss": index < 32,
                "out_pnl": -2.0 if index < 32 else 1.0,
            }
        )
    for index in range(60):
        rows.append(
            {
                "trade_id": f"gap-up-{index}",
                "gap_pct": 0.7,
                "vol_pctile_20d": 0.2 if index % 2 else 0.8,
                "out_is_loss": index < 24,
                "out_pnl": -1.0 if index < 24 else 2.0,
            }
        )
    return pd.DataFrame(rows)


def _model() -> CausalModel:
    return CausalModel(
        family="ema",
        version=1,
        factors=[
            CausalFactor(
                factor_id="f001",
                story="Prior gap-down mechanism.",
                rule="gap_pct < -0.5",
                direction="loss",
                status="supported",
            )
        ],
        accuracy_history=[],
    )


def test_research_engine_screening_thresholds_read_from_config_block() -> None:
    config = {
        "research_engine": {
            "min_sample": 17,
            "min_abs_lift": 0.35,
            "max_p_value": 0.04,
            "max_population_overlap": 0.60,
            "max_retries": 4,
            "retest_extension_months": 9,
            "holdout_fraction": 0.20,
        }
    }

    assert research_engine_min_sample(config) == 17
    assert research_engine_min_abs_lift(config) == 0.35
    assert research_engine_max_p_value(config) == 0.04
    assert research_engine_max_population_overlap(config) == 0.60
    assert research_engine_max_retries(config) == 4
    assert research_engine_retest_extension_months(config) == 9
    assert research_engine_holdout_fraction(config) == 0.20


@pytest.mark.parametrize(
    ("rule", "competitor_rule", "model", "expected_verdict", "overlap_with"),
    [
        ("gap_pct < 0", "gap_pct > 0", CausalModel(family="ema", version=1), "pass", None),
        ("gap_pct < 0", None, _model(), "kill_duplicate", "f001"),
        (
            "gap_pct > 0",
            "gap_pct < 0",
            CausalModel(family="ema", version=1),
            "kill_lost_to_competitor",
            None,
        ),
        ("vol_pctile_20d > 0.5", None, CausalModel(family="ema", version=1), "kill_no_lift", None),
        ("gap_pct < -2", None, CausalModel(family="ema", version=1), "kill_min_sample", None),
        ("out_pnl < 0", None, CausalModel(family="ema", version=1), "kill_bad_rule", None),
    ],
)
def test_screen_mixed_rules_return_exact_spec_verdicts(
    rule: str,
    competitor_rule: str | None,
    model: CausalModel,
    expected_verdict: str,
    overlap_with: str | None,
) -> None:
    result = screen(rule, competitor_rule, model, _feature_table())

    assert result.rule == rule
    assert result.verdict == expected_verdict
    assert result.overlap_with == overlap_with
    assert 0.0 <= result.flagged_loss_rate <= 1.0
    assert 0.0 <= result.base_loss_rate <= 1.0
    assert 0.0 <= result.p_value <= 1.0


def test_screen_rejects_empty_and_oversized_rules_as_bad_rule() -> None:
    model = CausalModel(family="ema", version=1)

    assert screen("", None, model, _feature_table()).verdict == "kill_bad_rule"
    assert screen("gap_pct > 0 " * 60, None, model, _feature_table()).verdict == "kill_bad_rule"


def test_screenings_table_exists_and_write_screenings_appends_spec_rows(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    result = screen(
        "gap_pct < 0", "gap_pct > 0", CausalModel(family="ema", version=1), _feature_table()
    )

    write_screenings(db.path, [result], round_number=3, competitor_rule="gap_pct > 0")

    with sqlite3.connect(db.path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(screenings)")]
        rows = conn.execute("""
            SELECT round_number, rule, competitor_rule, verdict, sample_count,
                   ROUND(lift, 3), overlap_with
            FROM screenings
            """).fetchall()

    assert columns == [
        "screening_id",
        "round_number",
        "rule",
        "competitor_rule",
        "verdict",
        "sample_count",
        "lift",
        "p_value",
        "overlap_with",
        "created_at_utc",
    ]
    assert rows == [(3, "gap_pct < 0", "gap_pct > 0", "pass", 40, 0.24, None)]
