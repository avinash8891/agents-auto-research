"""Registered prediction evaluation for harvested causal mechanisms."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from autoresearch_constants import (
    research_engine_min_trades,
    research_engine_noise_floor_pct,
    research_engine_prediction_tolerance_pct,
)
from research_types import HarvestVerdict, MetricName

LOWER_IS_BETTER = {"max_drawdown"}
HIGHER_IS_BETTER = {metric.value for metric in MetricName} - LOWER_IS_BETTER


def evaluate_predictions(
    registered_path: Path,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> HarvestVerdict:
    payload = json.loads(registered_path.read_text(encoding="utf-8"))
    effective_config = dict(config or {})
    if "research_engine" not in effective_config:
        effective_config["research_engine"] = payload.get("research_engine", {})
    min_trades = research_engine_min_trades(effective_config)
    noise_floor_pct = research_engine_noise_floor_pct(effective_config)
    tolerance_pct = research_engine_prediction_tolerance_pct(effective_config)
    thesis_id = str(payload.get("thesis_id") or "")
    predictions = payload.get("predictions") or []
    if not predictions:
        return HarvestVerdict(
            thesis_id=thesis_id,
            status="degenerate",
            summary="degenerate: empty predictions list",
        )

    trade_count = candidate.get("trade_count")
    trade_count_value = _finite_metric(trade_count)
    if trade_count_value is None or trade_count_value < float(min_trades):
        return HarvestVerdict(
            thesis_id=thesis_id,
            status="degenerate",
            summary=f"degenerate: trade_count {trade_count} below min_trades {min_trades}",
        )

    results: list[dict[str, Any]] = []
    any_refuted = False
    any_noise = False
    for prediction in predictions:
        metric = str(prediction.get("metric") or "")
        direction = str(prediction.get("direction") or "")
        baseline_value = _finite_metric(baseline.get(metric))
        candidate_value = _finite_metric(candidate.get(metric))
        predicted_value = _finite_metric(prediction.get("predicted"))
        if baseline_value is None or candidate_value is None or predicted_value is None:
            return HarvestVerdict(
                thesis_id=thesis_id,
                status="degenerate",
                prediction_results=results,
                summary=f"degenerate: NaN or missing prediction metric {metric}",
            )
        delta = candidate_value - baseline_value
        delta_pct = _delta_pct(baseline_value, candidate_value)
        magnitude_gap = candidate_value - predicted_value
        within_noise = abs(delta_pct) < noise_floor_pct
        direction_passed = _direction_passed(metric, direction, baseline_value, candidate_value)
        within_tolerance = _within_tolerance(predicted_value, candidate_value, tolerance_pct)
        result = {
            "metric": metric,
            "direction": direction,
            "baseline": baseline_value,
            "candidate": candidate_value,
            "predicted": predicted_value,
            "delta": delta,
            "delta_pct": delta_pct,
            "magnitude_gap": magnitude_gap,
            "within_tolerance": within_tolerance,
            "within_noise_floor": within_noise,
            "direction_passed": direction_passed,
        }
        results.append(result)
        if within_noise:
            any_noise = True
        elif not direction_passed:
            any_refuted = True

    if any_refuted:
        status = "refuted"
    elif any_noise:
        status = "inconclusive"
    else:
        status = "supported"
    return HarvestVerdict(
        thesis_id=thesis_id,
        status=status,
        prediction_results=results,
        summary=_harvest_summary(status, results),
    )


def _finite_metric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _delta_pct(baseline_value: float, candidate_value: float) -> float:
    if baseline_value == 0.0:
        return 0.0 if candidate_value == 0.0 else math.inf
    return ((candidate_value - baseline_value) / abs(baseline_value)) * 100.0


def _direction_passed(
    metric: str,
    direction: str,
    baseline_value: float,
    candidate_value: float,
) -> bool:
    if direction in {"increase", "increase_or_same"}:
        return candidate_value >= baseline_value
    if direction in {"decrease", "decrease_or_same"}:
        return candidate_value <= baseline_value
    if direction == "not_worse_than":
        return (
            candidate_value <= baseline_value
            if _lower_is_better_metric(metric)
            else candidate_value >= baseline_value
        )
    return False


def _within_tolerance(predicted_value: float, candidate_value: float, tolerance_pct: float) -> bool:
    if predicted_value == 0.0:
        return candidate_value == 0.0
    allowed = abs(predicted_value) * (tolerance_pct / 100.0)
    return abs(candidate_value - predicted_value) <= allowed


def _harvest_summary(status: str, results: list[dict[str, Any]]) -> str:
    parts = [
        f"{item['metric']} direction={'pass' if item['direction_passed'] else 'fail'} "
        f"delta_pct={item['delta_pct']:.3g} magnitude_gap={item['magnitude_gap']:.6g}"
        for item in results
    ]
    return f"{status}: " + "; ".join(parts)


def _lower_is_better_metric(metric: str) -> bool:
    normalized = metric.lower()
    if normalized in LOWER_IS_BETTER:
        return True
    if normalized in HIGHER_IS_BETTER:
        return False
    raise ValueError(
        f"unknown direction for metric {metric!r}: add it to LOWER_IS_BETTER or "
        "the MetricName enum before using it in a not_worse_than check"
    )
