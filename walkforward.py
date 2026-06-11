from __future__ import annotations

import copy
import json
import math
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
from autoresearch_paths import resolve_config_path
from causal_model import CausalModelStore
from experiment_evaluator import _direction_passed as _registered_direction_passed
from persistence_utils import read_config_payload, write_json_atomic


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
    code_root: Path,
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
        _demote_factor(family, factor_rule, report, runtime_root=runtime_root, code_root=code_root)
    return report


def run_walkforward_queue(controller: Any, state: dict[str, Any]) -> int:
    current_job = _coerce_job(state.get("job"))
    records = controller.backtest_run_db.all()
    if current_job is not None:
        records = [record for record in records if _coerce_job(record.job) == current_job]
    baseline = _latest_baseline(records)
    if baseline is None:
        return _fail_walkforward(controller, state, "walkforward requires a completed baseline run")

    candidates = [
        record
        for record in records
        if record.accepted
        and not record.is_baseline
        and _registered_predictions_path(controller, record).exists()
    ]
    reports: list[dict[str, Any]] = []
    baseline_config = _record_runtime_config(controller, baseline)
    baseline_window_cache: dict[str, dict[str, Any]] = {}
    try:
        for record in sorted(candidates, key=lambda item: item.research_round_number):
            predictions = _load_registered_predictions(
                _registered_predictions_path(controller, record)
            )
            if not predictions:
                continue
            candidate_config = _record_runtime_config(controller, record)
            config_for_tunables = dict(baseline_config)
            config_for_tunables.update(candidate_config)
            start, end = _walkforward_range(candidate_config, baseline_config)
            windows = build_windows(
                start,
                end,
                train_months=research_engine_walkforward_train_months(config_for_tunables),
                test_months=research_engine_walkforward_test_months(config_for_tunables),
                step_months=research_engine_walkforward_step_months(config_for_tunables),
            )
            if not windows:
                continue
            baseline_metrics: list[dict[str, Any]] = []
            candidate_metrics: list[dict[str, Any]] = []
            for index, window in enumerate(windows):
                cache_key = _baseline_window_cache_key(baseline_config, window)
                if cache_key not in baseline_window_cache:
                    baseline_window_cache[cache_key] = _run_window_backtest(
                        controller,
                        baseline_config,
                        baseline.thesis_id,
                        index,
                        "baseline",
                        window,
                    )
                baseline_metrics.append(dict(baseline_window_cache[cache_key]))
                candidate_metrics.append(
                    _run_window_backtest(
                        controller,
                        candidate_config,
                        record.thesis_id,
                        index,
                        "candidate",
                        window,
                    )
                )
            reports.append(
                evaluate_walkforward(
                    family=controller.family.name,
                    thesis_id=record.thesis_id,
                    runtime_root=Path(controller.runtime_root),
                    code_root=Path(controller.root),
                    db_path=controller.backtest_run_db.path,
                    run_id=record.run_id,
                    windows=windows,
                    predictions=predictions,
                    baseline_metrics=baseline_metrics,
                    candidate_metrics=candidate_metrics,
                    config=config_for_tunables,
                    factor_rule=_factor_rule_for_round(
                        controller.family.name,
                        record.research_round_number,
                        runtime_root=Path(controller.runtime_root),
                        code_root=Path(controller.root),
                    ),
                )
            )
    except Exception as exc:
        return _fail_walkforward(controller, state, f"walkforward failed: {exc}")

    next_state = dict(state)
    next_state["walkforward_status"] = "completed"
    next_state["walkforward_reports"] = [
        str(Path(controller.runtime_root) / "walkforward" / f"{report['thesis_id']}.json")
        for report in reports
    ]
    next_state.pop("activity", None)
    controller.write_state(next_state)
    controller.write_current_md(next_state, controller.read_results())
    return 0


def _baseline_window_cache_key(config: dict[str, Any], window: WalkForwardWindow) -> str:
    return json.dumps(
        {
            "config": config,
            "test_start": window.test_start,
            "test_end": window.test_end,
        },
        sort_keys=True,
        default=str,
    )


def _prediction_result(
    prediction: dict[str, Any],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    metric = str(prediction.get("metric") or "")
    direction = str(prediction.get("direction") or "")
    baseline_value = _finite_metric_value(baseline, metric)
    candidate_value = _finite_metric_value(candidate, metric)
    if baseline_value is None or candidate_value is None:
        return {
            "metric": metric,
            "direction": direction,
            "baseline": baseline_value,
            "candidate": candidate_value,
            "direction_passed": False,
            "missing_metric": True,
            "reason": f"missing metric: {metric}",
        }
    return {
        "metric": metric,
        "direction": direction,
        "baseline": baseline_value,
        "candidate": candidate_value,
        "direction_passed": _registered_direction_passed(
            metric,
            direction,
            baseline_value,
            candidate_value,
        ),
    }


def _finite_metric_value(metrics: dict[str, Any], metric: str) -> float | None:
    try:
        value = float(metrics[metric])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


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


def _demote_factor(
    family: str,
    factor_rule: str,
    report: dict[str, Any],
    *,
    runtime_root: Path,
    code_root: Path,
) -> None:
    store = CausalModelStore(runtime_root=runtime_root, code_root=code_root)
    model = store.load(family)
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
        store.save(model.model_copy(update={"version": model.version + 1, "factors": updated}))


def _coerce_job(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _latest_baseline(records: Sequence[Any]) -> Any | None:
    baselines = [record for record in records if record.accepted and record.is_baseline]
    if not baselines:
        return None
    return max(baselines, key=lambda item: str(item.created_at_utc or item.timestamp or ""))


def _registered_predictions_path(controller: Any, record: Any) -> Path:
    config_path = _resolve_record_config_path(controller, record)
    return config_path.parent / "registered_predictions.json"


def _load_registered_predictions(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        return []
    return [item for item in predictions if isinstance(item, dict)]


def _record_runtime_config(controller: Any, record: Any) -> dict[str, Any]:
    path = _resolve_record_config_path(controller, record)
    if path.exists():
        payload = read_config_payload(path)
        if isinstance(payload, dict):
            runtime_config = payload.get("runtime_config")
            if isinstance(runtime_config, dict):
                return dict(runtime_config)
            return dict(payload)
    return dict(record.runtime_config)


def _resolve_record_config_path(controller: Any, record: Any) -> Path:
    return resolve_config_path(
        str(record.config_path),
        code_root=Path(controller.root),
        runtime_root=Path(controller.runtime_root),
        execution_root=getattr(getattr(controller, "ctx", None), "execution_root", None),
    )


def _walkforward_range(
    candidate_config: dict[str, Any], baseline_config: dict[str, Any]
) -> tuple[str, str]:
    validation_end = candidate_config.get("validation_end") or baseline_config.get("validation_end")
    if not validation_end:
        raise ValueError("walkforward requires validation_end in config")
    holdout_start = candidate_config.get("holdout_start") or baseline_config.get("holdout_start")
    start_ts = pd.Timestamp(validation_end, tz="UTC") + pd.Timedelta(days=1)
    if holdout_start:
        start_ts = max(start_ts, pd.Timestamp(holdout_start, tz="UTC"))
    end = (
        candidate_config.get("walkforward_end")
        or baseline_config.get("walkforward_end")
        or candidate_config.get("holdout_end")
        or baseline_config.get("holdout_end")
    )
    if not end:
        config_for_tunables = dict(baseline_config)
        config_for_tunables.update(candidate_config)
        end_ts = start_ts + pd.DateOffset(
            months=research_engine_walkforward_train_months(config_for_tunables)
            + research_engine_walkforward_test_months(config_for_tunables)
        )
    else:
        end_ts = pd.Timestamp(end, tz="UTC")
    if end_ts <= start_ts:
        raise ValueError("walkforward out-of-sample end must be after validation_end")
    return start_ts.date().isoformat(), end_ts.date().isoformat()


def _run_window_backtest(
    controller: Any,
    runtime_config: dict[str, Any],
    thesis_id: str,
    window_index: int,
    role: str,
    window: WalkForwardWindow,
) -> dict[str, Any]:
    config = copy.deepcopy(runtime_config)
    config["validation_start"] = pd.Timestamp(window.test_start).date().isoformat()
    config["validation_end"] = pd.Timestamp(window.test_end).date().isoformat()
    window_root = (
        Path(controller.runtime_root)
        / "walkforward"
        / thesis_id
        / f"window-{window_index + 1:03d}"
        / role
    )
    window_root.mkdir(parents=True, exist_ok=True)
    config_path = window_root / "config.json"
    write_json_atomic(config_path, config)
    command = controller.family.benchmark_command(str(config_path), output_dir=str(window_root))
    code, output = controller.run_command(command)
    if code != 0:
        raise RuntimeError(f"{role} window {window_index + 1} backtest failed with exit {code}")
    metric = controller.parse_metric(output, name=controller.primary_metric_name())
    details = controller.parse_benchmark_details(output)
    # Merge order is precedence order: validation_metrics must win over train
    # values and over ambiguous top-level copies (window metrics describe the
    # test window, which is the validation range of the window config).
    metrics = dict(details.get("metrics") if isinstance(details.get("metrics"), dict) else {})
    train_metrics = details.get("train_metrics")
    if isinstance(train_metrics, dict):
        metrics.update(
            {key: value for key, value in train_metrics.items() if isinstance(value, int | float)}
        )
    metrics.update({key: value for key, value in details.items() if isinstance(value, int | float)})
    validation_metrics = details.get("validation_metrics")
    if isinstance(validation_metrics, dict):
        metrics.update(
            {
                key: value
                for key, value in validation_metrics.items()
                if isinstance(value, int | float)
            }
        )
    if metric is not None:
        metrics[controller.primary_metric_name()] = metric
    return metrics


def _factor_rule_for_round(
    family: str,
    round_number: int,
    *,
    runtime_root: Path,
    code_root: Path,
) -> str:
    model = CausalModelStore(runtime_root=runtime_root, code_root=code_root).load(family)
    for factor in model.factors:
        if round_number in factor.evidence_rounds:
            return factor.rule
    return ""


def _fail_walkforward(controller: Any, state: dict[str, Any], reason: str) -> int:
    failed = dict(state)
    failed.update(
        {
            "state": "interrupted",
            "next_action": {"type": "terminated", "reason": reason},
            "blockers": [{"kind": "walkforward_failed", "detail": reason}],
        }
    )
    failed.pop("activity", None)
    controller.write_state(failed)
    controller.write_current_md(failed, controller.read_results())
    return 1
