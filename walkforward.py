from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from autoresearch_constants import (
    research_engine_walkforward_step_months,
    research_engine_walkforward_survival_pct,
    research_engine_walkforward_test_months,
    research_engine_walkforward_train_months,
)
from causal_model import load_model, save_model
from experiment_evaluator import _direction_passed as _registered_direction_passed
from persistence_utils import write_json_atomic

LOWER_IS_BETTER = {"max_drawdown"}


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str


def build_windows(
    start: str,
    end: str,
    *,
    train_months: int = 6,
    test_months: int = 3,
    step_months: int = 3,
) -> list[WalkForwardWindow]:
    """Build calendar walk-forward windows.

    The train window is recorded for future fit-based changes. Current v2
    config-change evaluation uses only the test window metrics.
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    windows: list[WalkForwardWindow] = []
    train_start = start_ts
    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_end = train_end + pd.DateOffset(months=test_months)
        if test_end > end_ts:
            break
        windows.append(
            WalkForwardWindow(
                train_start=train_start.isoformat(),
                train_end=train_end.isoformat(),
                test_start=train_end.isoformat(),
                test_end=test_end.isoformat(),
            )
        )
        train_start = train_start + pd.DateOffset(months=step_months)
    return windows


def evaluate_walkforward(
    *,
    family: str,
    thesis_id: str,
    runtime_root: Path,
    db_path: Path,
    run_id: str,
    windows: Sequence[WalkForwardWindow],
    predictions: Sequence[dict[str, Any]],
    baseline_metrics: Sequence[dict[str, Any]],
    candidate_metrics: Sequence[dict[str, Any]],
    config: dict[str, Any] | None = None,
    factor_rule: str = "",
) -> dict[str, Any]:
    survival_pct = research_engine_walkforward_survival_pct(config or {})
    rows: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        baseline = baseline_metrics[index]
        candidate = candidate_metrics[index]
        prediction_results = [
            _prediction_result(prediction, baseline, candidate) for prediction in predictions
        ]
        directions_hold = bool(prediction_results) and all(
            result["direction_passed"] for result in prediction_results
        )
        rows.append(
            {
                "window": index + 1,
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "directions_hold": directions_hold,
                "prediction_results": prediction_results,
            }
        )
    passed = sum(1 for row in rows if row["directions_hold"])
    survival_rate = (passed / len(rows)) if rows else 0.0
    graduated = bool(rows) and survival_rate >= survival_pct
    verdict = "graduated" if graduated else "demoted"
    report = {
        "family": family,
        "thesis_id": thesis_id,
        "survival_pct": survival_pct,
        "survival_rate": survival_rate,
        "graduated": graduated,
        "verdict": verdict,
        "windows": rows,
    }
    report_path = runtime_root / "walkforward" / f"{thesis_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(report_path, report)
    _write_graduation_to_run_row(db_path, run_id, graduated)
    if not graduated and factor_rule:
        _demote_factor(family, factor_rule, report)
    return report


def walkforward_config(config: dict[str, Any]) -> dict[str, int | float]:
    return {
        "train_months": research_engine_walkforward_train_months(config),
        "test_months": research_engine_walkforward_test_months(config),
        "step_months": research_engine_walkforward_step_months(config),
        "survival_pct": research_engine_walkforward_survival_pct(config),
    }


def _prediction_result(
    prediction: dict[str, Any],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    metric = str(prediction.get("metric") or "")
    direction = str(prediction.get("direction") or "")
    baseline_value = float(baseline[metric])
    candidate_value = float(candidate[metric])
    return {
        "metric": metric,
        "direction": direction,
        "baseline": baseline_value,
        "candidate": candidate_value,
        "direction_passed": _direction_passed(metric, direction, baseline_value, candidate_value),
    }


def _direction_passed(
    metric: str,
    direction: str,
    baseline_value: float,
    candidate_value: float,
) -> bool:
    return _registered_direction_passed(metric, direction, baseline_value, candidate_value)


def _write_graduation_to_run_row(db_path: Path, run_id: str, graduated: bool) -> None:
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute(
                "ALTER TABLE backtest_runs ADD COLUMN graduated INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        conn.execute(
            "UPDATE backtest_runs SET graduated = ? WHERE run_id = ?",
            (1 if graduated else 0, run_id),
        )
        conn.commit()


def _demote_factor(family: str, factor_rule: str, report: dict[str, Any]) -> None:
    model = load_model(family)
    lesson = (
        f"Walk-forward demoted: survival_rate={report['survival_rate']:.3f} "
        f"below survival_pct={report['survival_pct']:.3f}."
    )
    updated = [
        (
            factor.model_copy(update={"status": "demoted", "lesson": lesson})
            if factor.rule == factor_rule
            else factor
        )
        for factor in model.factors
    ]
    if updated != model.factors:
        save_model(model.model_copy(update={"version": model.version + 1, "factors": updated}))
