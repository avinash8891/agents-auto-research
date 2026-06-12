"""Experiment evaluator u2014 mechanical evaluation of backtest results against thesis predictions.

Checks:
  1. Did expected effects happen?
  2. Did any disqualifier trigger?
  3. Accept / reject / inconclusive verdict.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from autoresearch_constants import (
    research_engine_min_trades,
    research_engine_noise_floor_pct,
    research_engine_prediction_tolerance_pct,
)
from autoresearch_logging import get_logger
from diagnostic_contracts import build_required_diagnostic_specs
from research_types import (
    BacktestVerdict,
    Disqualifier,
    ExpectedEffect,
    HarvestVerdict,
    MetricName,
    ResearchThesis,
)

log = get_logger(__name__)

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


def evaluate_effect(
    effect: ExpectedEffect,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> bool | None:
    """Check if a single expected effect holds."""
    b = baseline.get(effect.metric)
    c = candidate.get(effect.metric)

    if b is None or c is None:
        return None

    b = float(b)
    c = float(c)
    threshold = effect.threshold or 0

    if effect.direction == "increase":
        return c >= b + threshold

    if effect.direction == "decrease":
        return c <= b - threshold

    if effect.direction == "increase_or_same":
        return c >= b

    if effect.direction == "decrease_or_same":
        return c <= b

    if effect.direction == "not_worse_than":
        # "not worse" depends on the metric:
        # - For max_drawdown: worse = bigger number
        # - For profit_factor: worse = smaller number
        # Use threshold as percent tolerance.
        # Convention: drawdown is stored as a positive fraction (0.22 = 22%)
        pct = threshold if threshold else 0
        try:
            lower_is_better = _lower_is_better_metric(effect.metric)
        except ValueError as exc:
            # Custom required_diagnostics metrics are allowed by the validator
            # but have no registered direction: the effect is unjudgeable, not
            # a reason to abort the round. None means "could not evaluate".
            log.warning("expected effect cannot be judged: %s | treating as inconclusive", exc)
            return None
        if lower_is_better:
            return c <= b * (1 + pct / 100)
        return c >= b * (1 - pct / 100)

    return False  # unknown direction


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


def evaluate_disqualifier(
    dq: Disqualifier,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[bool, bool]:
    """Check if a disqualifier condition is triggered.

    Returns True if triggered (bad).

    Disqualifier conditions are natural-language strings. We evaluate
    common patterns mechanically:
      - "X decreases/increases by more than N percent versus baseline"
      - "more than N percent of net profit comes from one month"

    For conditions we can't parse mechanically, we return False
    (not triggered) u2014 the conductor can check these in the next round.
    """
    condition = dq.condition.lower()

    # Pattern: "{metric} decreases by more than {N} percent"
    m = re.search(
        r"(\w+)\s+(?:decreases|drops|falls)\s+by\s+more\s+than\s+(\d+(?:\.\d+)?)\s*percent",
        condition,
    )
    if m:
        metric_name = m.group(1)
        pct = float(m.group(2))
        b = baseline.get(metric_name)
        c = candidate.get(metric_name)
        if b is not None and c is not None:
            b, c = float(b), float(c)
            if b > 0:
                return c < b * (1 - pct / 100), False
        return False, False

    # Pattern: "{metric} increases/worsens by more than {N} percent"
    m = re.search(
        r"(\w+)\s+(?:increases|worsens|grows)\s+by\s+more\s+than\s+(\d+(?:\.\d+)?)\s*percent",
        condition,
    )
    if m:
        metric_name = m.group(1)
        pct = float(m.group(2))
        b = baseline.get(metric_name)
        c = candidate.get(metric_name)
        if b is not None and c is not None:
            b, c = float(b), float(c)
            if b > 0:
                return c > b * (1 + pct / 100), False
        return False, False

    metric_compare = re.search(
        r"(\w+)\s*(<=|>=|<|>|==)\s*(-?\d+(?:\.\d+)?)",
        condition,
    )
    if metric_compare:
        metric_name = metric_compare.group(1)
        operator = metric_compare.group(2)
        threshold = float(metric_compare.group(3))
        value = candidate.get(metric_name)
        if value is None:
            return False, False
        current = float(value)
        if operator == "<":
            return current < threshold, False
        if operator == "<=":
            return current <= threshold, False
        if operator == ">":
            return current > threshold, False
        if operator == ">=":
            return current >= threshold, False
        return current == threshold, False

    # Can't parse mechanically — surface loudly instead of silently accepting.
    return False, True


def evaluate_backtest(
    thesis: ResearchThesis,
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    contract_id: str = "",
    strategy_diagnostics: dict[str, Any] | None = None,
) -> BacktestVerdict:
    """Evaluate a backtest result against thesis predictions."""
    passed: list[str] = []
    failed: list[str] = []
    triggered: list[str] = []
    unparsed_disqualifiers: list[str] = []
    missing_required_diagnostics: list[str] = []

    for effect in thesis.expected_effects:
        effect_result = evaluate_effect(effect, baseline_metrics, candidate_metrics)
        if effect_result is True:
            passed.append(effect.metric)
        else:
            failed.append(effect.metric)

    for dq in thesis.disqualifiers:
        triggered_now, unparsed = evaluate_disqualifier(dq, baseline_metrics, candidate_metrics)
        if triggered_now:
            triggered.append(dq.name)
        if unparsed:
            unparsed_disqualifiers.append(dq.name)

    required_specs = build_required_diagnostic_specs(
        thesis.required_diagnostics,
        [spec.model_dump() for spec in thesis.required_diagnostic_specs],
    )
    strategy_diagnostics = strategy_diagnostics or {}
    available_diagnostics = strategy_diagnostics if isinstance(strategy_diagnostics, dict) else {}
    for spec in required_specs:
        tokens = [spec.key, *(spec.aliases or [])]
        matched_key = next((token for token in tokens if token in available_diagnostics), None)
        if matched_key is None:
            missing_required_diagnostics.append(spec.key)
            continue
        payload = available_diagnostics.get(matched_key)
        if isinstance(payload, dict) and payload.get("missing_inputs"):
            missing_required_diagnostics.append(spec.key)

    # Determine verdict
    hard_fails = [
        dq.name
        for dq in thesis.disqualifiers
        if dq.severity == "hard_fail" and dq.name in triggered
    ]

    if hard_fails:
        status = "rejected"
    elif unparsed_disqualifiers:
        status = "inconclusive"
    elif missing_required_diagnostics:
        status = "inconclusive"
    elif triggered:  # only soft_fails
        status = "inconclusive"
    elif failed:
        status = "inconclusive"
    else:
        status = "accepted"

    # Build summary
    parts: list[str] = []
    if passed:
        parts.append(f"Passed: {', '.join(passed)}")
    if failed:
        parts.append(f"Failed: {', '.join(failed)}")
    if triggered:
        parts.append(f"Disqualifiers triggered: {', '.join(triggered)}")
    if unparsed_disqualifiers:
        parts.append(f"Unparsed disqualifiers: {', '.join(unparsed_disqualifiers)}")
    if missing_required_diagnostics:
        parts.append(f"Missing required diagnostics: {', '.join(missing_required_diagnostics)}")
    if available_diagnostics:
        ec = available_diagnostics.get("event_counts", {})
        rb = available_diagnostics.get("rejection_breakdown", {})
        if ec:
            parts.append(f"Signal funnel: {ec}")
        if rb:
            top_rejections = sorted(rb.items(), key=lambda x: x[1], reverse=True)[:3]
            parts.append(f"Top rejections: {dict(top_rejections)}")
    summary = ". ".join(parts) if parts else "No effects evaluated."

    return BacktestVerdict(
        contract_id=contract_id,
        thesis_id=thesis.thesis_id,
        status=status,
        passed_effects=passed,
        failed_effects=failed,
        triggered_disqualifiers=triggered,
        unparsed_disqualifiers=unparsed_disqualifiers,
        missing_required_diagnostics=missing_required_diagnostics,
        summary=summary,
    )
