from __future__ import annotations

import json

import pytest

from experiment_evaluator import evaluate_predictions


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
