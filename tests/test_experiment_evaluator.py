from __future__ import annotations

from experiment_evaluator import evaluate_effect
from research_types import ExpectedEffect


def test_not_worse_than_uses_metric_direction_for_profit_factor_and_drawdown() -> None:
    profit_factor_effect = ExpectedEffect(
        metric="profit_factor",
        direction="not_worse_than",
        threshold=10.0,
    )
    max_drawdown_effect = ExpectedEffect(
        metric="max_drawdown",
        direction="not_worse_than",
        threshold=10.0,
    )

    assert (
        evaluate_effect(
            profit_factor_effect,
            baseline={"profit_factor": 2.0},
            candidate={"profit_factor": 1.7},
        )
        is False
    )
    assert (
        evaluate_effect(
            profit_factor_effect,
            baseline={"profit_factor": 2.0},
            candidate={"profit_factor": 1.9},
        )
        is True
    )
    assert (
        evaluate_effect(
            max_drawdown_effect,
            baseline={"max_drawdown": 0.20},
            candidate={"max_drawdown": 0.23},
        )
        is False
    )
    assert (
        evaluate_effect(
            max_drawdown_effect,
            baseline={"max_drawdown": 0.20},
            candidate={"max_drawdown": 0.18},
        )
        is True
    )
