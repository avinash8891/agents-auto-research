from __future__ import annotations

from experiment_evaluator import evaluate_effect
from research_types import ExpectedEffect

def test_not_worse_than_profit_factor_allows_small_declines_and_rejects_craters() -> None:
    profit_factor_effect = ExpectedEffect(
        metric="profit_factor",
        direction="not_worse_than",
        threshold=10.0,
        rationale="Profit factor may drift slightly but must not collapse.",
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
            profit_factor_effect,
            baseline={"profit_factor": 2.0},
            candidate={"profit_factor": 0.0},
        )
        is False
    )


def test_not_worse_than_max_drawdown_keeps_lower_is_better_direction() -> None:
    max_drawdown_effect = ExpectedEffect(
        metric="max_drawdown",
        direction="not_worse_than",
        threshold=10.0,
        rationale="Drawdown may widen only inside tolerance.",
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
            candidate={"max_drawdown": 0.21},
        )
        is True
    )
    assert (
        evaluate_effect(
            max_drawdown_effect,
            baseline={"max_drawdown": 0.20},
            candidate={"max_drawdown": 0.18},
        )
        is True
    )
