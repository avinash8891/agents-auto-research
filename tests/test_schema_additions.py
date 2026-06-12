"""Tests for new ResearchThesis schema fields and validator enforcement."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_types import (
    FACTOR_STATUS_DESCRIPTIONS,
    HARVEST_OBSERVABLE_METRIC_NAMES,
    Disqualifier,
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


def test_disqualifier_has_kind_field_defaulting_to_metric_threshold() -> None:
    obj = Disqualifier(name="x", condition="y")
    assert obj.kind == "metric_threshold"


def test_disqualifier_accepts_mechanism_evidence_kind() -> None:
    obj = Disqualifier(name="x", condition="y", kind="mechanism_evidence")
    assert obj.kind == "mechanism_evidence"


def test_disqualifier_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        Disqualifier(name="x", condition="y", kind="some_other_kind")
