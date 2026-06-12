from __future__ import annotations

import json

import pytest

from experiment_evaluator import evaluate_effect, evaluate_predictions
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


def test_not_worse_than_unknown_metric_is_inconclusive_not_guessed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    effect = ExpectedEffect(
        metric="win_loss_ratio",
        direction="not_worse_than",
        threshold=10.0,
        rationale="Win/loss ratio should not fall materially.",
    )

    # Custom required_diagnostics metrics are validator-legal but have no
    # registered direction: never guess a direction, never abort the round.
    with caplog.at_level(logging.WARNING):
        result = evaluate_effect(
            effect,
            baseline={"win_loss_ratio": 2.0},
            candidate={"win_loss_ratio": 2.3},
        )

    assert result is None
    assert "win_loss_ratio" in caplog.text


def test_not_worse_than_win_rate_is_treated_as_higher_is_better() -> None:
    effect = ExpectedEffect(
        metric="win_rate",
        direction="not_worse_than",
        threshold=10.0,
        rationale="Win rate should not fall materially.",
    )

    assert (
        evaluate_effect(
            effect,
            baseline={"win_rate": 0.50},
            candidate={"win_rate": 0.55},
        )
        is True
    )
    assert (
        evaluate_effect(
            effect,
            baseline={"win_rate": 0.50},
            candidate={"win_rate": 0.40},
        )
        is False
    )


def _registered_predictions(tmp_path, predictions: list[dict]) -> object:
    path = tmp_path / "registered_predictions.json"
    path.write_text(
        json.dumps(
            {
                "thesis_id": "thesis-001",
                "registered_at_utc": "2026-06-10T00:00:00+00:00",
                "predictions": predictions,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_evaluate_predictions_uses_direction_only_and_records_magnitude_gap(tmp_path) -> None:
    registered = _registered_predictions(
        tmp_path,
        [
            {"metric": "profit_factor", "direction": "increase", "predicted": 2.4},
            {"metric": "max_drawdown", "direction": "decrease", "predicted": 0.15},
        ],
    )

    verdict = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "max_drawdown": 0.20, "trade_count": 25},
        candidate={"profit_factor": 2.05, "max_drawdown": 0.19, "trade_count": 25},
    )

    assert verdict.status == "supported"
    assert verdict.thesis_id == "thesis-001"
    assert verdict.prediction_results[0]["direction_passed"] is True
    assert verdict.prediction_results[0]["magnitude_gap"] == pytest.approx(-0.35)


def test_evaluate_predictions_refutes_opposite_direction(tmp_path) -> None:
    registered = _registered_predictions(
        tmp_path,
        [{"metric": "profit_factor", "direction": "increase", "predicted": 2.4}],
    )

    verdict = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "trade_count": 25},
        candidate={"profit_factor": 1.8, "trade_count": 25},
    )

    assert verdict.status == "refuted"
    assert verdict.prediction_results[0]["direction_passed"] is False


def test_evaluate_predictions_rejects_empty_prediction_list(tmp_path) -> None:
    registered = _registered_predictions(tmp_path, [])

    verdict = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "trade_count": 25},
        candidate={"profit_factor": 2.1, "trade_count": 25},
    )

    assert verdict.status == "degenerate"
    assert "empty predictions" in verdict.summary


def test_evaluate_predictions_treats_invalid_trade_count_as_degenerate(tmp_path) -> None:
    registered = _registered_predictions(
        tmp_path,
        [{"metric": "profit_factor", "direction": "increase", "predicted": 2.4}],
    )

    verdict = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "trade_count": 25},
        candidate={"profit_factor": 2.1, "trade_count": "not-a-number"},
    )

    assert verdict.status == "degenerate"
    assert "trade_count" in verdict.summary


def test_evaluate_predictions_marks_noise_floor_inconclusive(tmp_path) -> None:
    registered = _registered_predictions(
        tmp_path,
        [{"metric": "profit_factor", "direction": "increase", "predicted": 2.4}],
    )

    verdict = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "trade_count": 25},
        candidate={"profit_factor": 2.01, "trade_count": 25},
    )

    assert verdict.status == "inconclusive"
    assert verdict.prediction_results[0]["within_noise_floor"] is True


def test_evaluate_predictions_marks_degenerate_results_invalid(tmp_path) -> None:
    registered = _registered_predictions(
        tmp_path,
        [{"metric": "profit_factor", "direction": "increase", "predicted": 2.4}],
    )

    low_trade_count = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "trade_count": 25},
        candidate={"profit_factor": 2.5, "trade_count": 2},
    )
    nan_metric = evaluate_predictions(
        registered,
        baseline={"profit_factor": 2.0, "trade_count": 25},
        candidate={"profit_factor": float("nan"), "trade_count": 25},
    )

    assert low_trade_count.status == "degenerate"
    assert "trade_count" in low_trade_count.summary
    assert nan_metric.status == "degenerate"
    assert "NaN" in nan_metric.summary
