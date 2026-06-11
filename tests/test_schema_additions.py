"""Tests for new ResearchThesis schema fields and validator enforcement."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backtest_run_db import research_thesis_attempt_id
from research_types import (
    Disqualifier,
    FACTOR_STATUS_DESCRIPTIONS,
    HARVEST_OBSERVABLE_METRIC_NAMES,
    PriorLeverOutcome,
    Prediction,
    ResearchThesis,
)
from thesis_validator import VALID_PROCESS_TOOLS as _VALID_PROCESS_TOOLS
from thesis_validator import validate_thesis_dict as _validate_thesis_dict


def validate_thesis_dict(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    kwargs.setdefault("research_round_id", "job-test-round-1")
    kwargs.setdefault("attempt_number", 1)
    kwargs.setdefault("assign_thesis_id", research_thesis_attempt_id)
    return _validate_thesis_dict(*args, **kwargs)


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


def _base_engine_change_thesis(thesis_id: str, dimension: str) -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Require close confirmation to reduce intrabar false-break entries.",
        "mechanism": "A close-confirmed entry gate should avoid wick-only stop triggers.",
        "mechanism_dimension": dimension,
        "dimension_novelty": (
            "This tests confirmation logic in engine behavior, not a numeric "
            "parameter variation seen earlier."
        ),
        "causal_cluster": "close-confirmed adverse-selection reduction",
        "dominant_cluster_overlap": "medium",
        "underexplored_dimensions_considered": [
            "portfolio_construction",
            "regime_conditioning",
        ],
        "novel_connection": (
            "Connects wick-only false-break evidence with engine-level close "
            "confirmation rather than another nearby threshold change."
        ),
        "config_changes": {"requires_engine_change": True},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Fewer false-break entries should improve realized edge.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "Confirmation gating should not collapse trade frequency.",
            },
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_close_confirmation_separation",
                "condition": (
                    "If wick-only stop-out rate is no lower for close-confirmed entries, "
                    "the mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": "would not address the wick-only false-break root cause directly",
            },
            {
                "mechanism": "session-time entry filter",
                "why_rejected": "is a proxy for the wick problem rather than the structural fix",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "Cont et al on order-flow imbalance"},
            {"source": "analyst", "citation": "round-3 analyst: wick-only stops 37%"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame contains the entry filter; "
            "the close-confirmation gate would be added here as an additional check."
        ),
        "requires_code_change": True,
        "requested_primitives": ["close_confirmed_entry_gate"],
        "why_not_overfit": "Mechanism is structural and evaluated across all train years.",
    }


def test_research_thesis_has_theme_keywords_field_default_empty() -> None:
    payload = _base_engine_change_thesis("t1", "signal_quality")
    obj = ResearchThesis.model_validate(payload)
    assert obj.theme_keywords == []


def test_research_thesis_accepts_theme_keywords() -> None:
    payload = _base_engine_change_thesis("t2", "signal_quality")
    payload["theme_keywords"] = ["close_confirmation", "wick_filter"]
    obj = ResearchThesis.model_validate(payload)
    assert obj.theme_keywords == ["close_confirmation", "wick_filter"]


def test_prior_lever_outcome_model_requires_core_fields() -> None:
    obj = PriorLeverOutcome(
        prior_thesis_id="prior_a",
        lever="min_stop_distance_pct",
        direction_then="tightened",
        outcome="trade_count fell 50% with no PF gain",
        why_retry="testing the inverse direction with a different acceptance proxy now",
    )
    assert obj.prior_thesis_id == "prior_a"


def test_prior_lever_outcome_rejects_short_why_retry() -> None:
    with pytest.raises(ValidationError):
        PriorLeverOutcome(
            prior_thesis_id="prior_a",
            lever="x",
            direction_then="tightened",
            outcome="bad",
            why_retry="too short",
        )


def test_research_thesis_has_prior_lever_outcomes_default_empty() -> None:
    payload = _base_engine_change_thesis("t3", "signal_quality")
    obj = ResearchThesis.model_validate(payload)
    assert obj.prior_lever_outcomes == []


def test_research_thesis_accepts_prior_lever_outcomes() -> None:
    payload = _base_engine_change_thesis("t4", "signal_quality")
    payload["prior_lever_outcomes"] = [
        {
            "prior_thesis_id": "prior_a",
            "lever": "min_stop_distance_pct",
            "direction_then": "tightened",
            "outcome": "trade_count fell 50% with no PF gain",
            "why_retry": "testing the inverse direction with a different acceptance proxy now",
        }
    ]
    obj = ResearchThesis.model_validate(payload)
    assert len(obj.prior_lever_outcomes) == 1
    assert obj.prior_lever_outcomes[0].lever == "min_stop_distance_pct"


def test_disqualifier_has_kind_field_defaulting_to_metric_threshold() -> None:
    obj = Disqualifier(name="x", condition="y")
    assert obj.kind == "metric_threshold"


def test_disqualifier_accepts_mechanism_evidence_kind() -> None:
    obj = Disqualifier(name="x", condition="y", kind="mechanism_evidence")
    assert obj.kind == "mechanism_evidence"


def test_disqualifier_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        Disqualifier(name="x", condition="y", kind="some_other_kind")


def test_validator_no_longer_enforces_falsification_or_alternative_min_length() -> None:
    payload = _base_engine_change_thesis("t5", "signal_quality")
    payload["falsification_or_alternative"] = "too short to be a real disconfirmer"
    obj = validate_thesis_dict(payload)
    assert obj.falsification_or_alternative == "too short to be a real disconfirmer"


def test_validator_accepts_substantive_falsification_or_alternative() -> None:
    payload = _base_engine_change_thesis("t6", "signal_quality")
    payload["falsification_or_alternative"] = (
        "If close-confirmed entries do not reduce wick-only stop-outs by at least "
        "20 percent at the per-bar level, the mechanism does not hold and the "
        "improvement is likely a regime artifact rather than a real edge."
    )
    obj = validate_thesis_dict(payload)
    assert obj.thesis_id == "job-test-round-1-attempt-1"
