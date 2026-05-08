"""Experiment evaluator u2014 mechanical evaluation of backtest results against thesis predictions.

Checks:
  1. Did expected effects happen?
  2. Did any disqualifier trigger?
  3. Accept / reject / inconclusive verdict.
"""

from __future__ import annotations

from typing import Any

from research_types import (
    Disqualifier,
    ExpectedEffect,
    ExperimentVerdict,
    ResearchThesis,
)


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
        # so "not worse" = candidate <= baseline * (1 + threshold/100)
        pct = threshold if threshold else 0
        return c <= b * (1 + pct / 100)

    return False  # unknown direction


def evaluate_disqualifier(
    dq: Disqualifier,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
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
    import re

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
                return c < b * (1 - pct / 100)
        return False

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
                return c > b * (1 + pct / 100)
        return False

    # Can't parse mechanically u2014 don't trigger (conductor handles in next round)
    return False


def evaluate_experiment(
    thesis: ResearchThesis,
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    experiment_id: str = "",
    strategy_diagnostics: dict[str, Any] | None = None,
) -> ExperimentVerdict:
    """Evaluate a backtest result against thesis predictions."""
    passed: list[str] = []
    failed: list[str] = []
    triggered: list[str] = []

    for effect in thesis.expected_effects:
        effect_result = evaluate_effect(effect, baseline_metrics, candidate_metrics)
        if effect_result is True:
            passed.append(effect.metric)
        else:
            failed.append(effect.metric)

    for dq in thesis.disqualifiers:
        if evaluate_disqualifier(dq, baseline_metrics, candidate_metrics):
            triggered.append(dq.name)

    # Determine verdict
    hard_fails = [
        dq.name
        for dq in thesis.disqualifiers
        if dq.severity == "hard_fail" and dq.name in triggered
    ]

    if hard_fails:
        status = "rejected"
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
    if strategy_diagnostics:
        ec = strategy_diagnostics.get("event_counts", {})
        rb = strategy_diagnostics.get("rejection_breakdown", {})
        if ec:
            parts.append(f"Signal funnel: {ec}")
        if rb:
            top_rejections = sorted(rb.items(), key=lambda x: x[1], reverse=True)[:3]
            parts.append(f"Top rejections: {dict(top_rejections)}")
    summary = ". ".join(parts) if parts else "No effects evaluated."

    return ExperimentVerdict(
        experiment_id=experiment_id,
        thesis_id=thesis.thesis_id,
        status=status,
        passed_effects=passed,
        failed_effects=failed,
        triggered_disqualifiers=triggered,
        summary=summary,
    )
