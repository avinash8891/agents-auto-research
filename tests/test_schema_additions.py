"""Tests for new ResearchThesis schema fields and validator enforcement."""

from __future__ import annotations

from research_types import (
    FACTOR_STATUS_DESCRIPTIONS,
    HARVEST_OBSERVABLE_METRIC_NAMES,
    Prediction,
)


def test_causal_factor_schema_documents_demoted_status() -> None:
    assert FACTOR_STATUS_DESCRIPTIONS["demoted"] == (
        "failed walk-forward survival after being harvested; excluded from prediction and "
        "duplicate screening"
    )


def test_prediction_schema_documents_harvest_observable_metric_subset() -> None:
    assert HARVEST_OBSERVABLE_METRIC_NAMES == (
        "profit_factor",
        "trade_count",
        "max_drawdown",
        "median_expectancy",
    )
    metric_schema = Prediction.model_json_schema()["properties"]["metric"]
    assert "win_rate" in metric_schema["description"]
    assert "pnl_weighted_accuracy" in metric_schema["description"]
