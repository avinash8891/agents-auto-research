"""Tests for L11: LLM-as-judge for mechanism vs param-value-in-prose."""

from __future__ import annotations

import pytest

import mechanism_judge
from thesis_validator import ThesisValidationError, validate_thesis_dict


@pytest.fixture(autouse=True)
def _reset_judge():
    mechanism_judge.set_classifier(None)
    mechanism_judge.reset_cache()
    yield
    mechanism_judge.set_classifier(None)
    mechanism_judge.reset_cache()


def _base() -> dict:
    return {
        "thesis_id": "judge_test",
        "strategy_family": "ema",
        "hypothesis": "Require close confirmation to reduce intrabar false-break entries.",
        "mechanism": "Close-confirmed entries avoid wick-only stop triggers because the wick is noise.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": "Different lever family than threshold-tuning entry timing.",
        "causal_cluster": "close-confirmed adverse-selection reduction",
        "dominant_cluster_overlap": "low",
        "underexplored_dimensions_considered": ["portfolio_construction", "regime_conditioning"],
        "novel_connection": "Connects wick-only false-break evidence with engine-level confirmation.",
        "closest_prior_theses_considered": ["prior_signal_quality_baseline"],
        "orthogonality_defense": (
            "Different lever family from threshold tuning; engine-level entry confirmation."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": "would not address the root wick-only false-break cause",
            },
            {
                "mechanism": "session-time entry filter",
                "why_rejected": "is a proxy for the wick problem rather than the structural fix",
            },
        ],
        "config_changes": {"requires_engine_change": True},
        "expected_effects": [
            {"metric": "profit_factor", "direction": "increase", "rationale": "fewer false breaks"},
            {"metric": "trade_count", "direction": "decrease_or_same", "rationale": "selective filter"},
        ],
        "disqualifiers": [
            {"name": "tcc", "condition": "trade_count drops 50%", "kind": "metric_threshold"},
            {
                "name": "no_separation",
                "condition": "If wick-only stop rate is no lower with confirmation.",
                "kind": "mechanism_evidence",
            },
        ],
        "requires_code_change": True,
        "requested_primitives": ["close_confirmed_entry_gate"],
        "evidence_citations": [
            {"source": "web_search", "citation": "Cont et al"},
            {"source": "analyst", "citation": "round-3 analyst finding"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:detect_pullback contains the entry filter."
        ),
    }


def test_mechanism_judge_accepts_mechanism_classification() -> None:
    mechanism_judge.set_classifier(lambda h, m: "mechanism")
    obj = validate_thesis_dict(_base())
    assert obj.thesis_id == "judge_test"


def test_mechanism_judge_rejects_param_value_in_prose_classification() -> None:
    mechanism_judge.set_classifier(lambda h, m: "param_value_in_prose")
    with pytest.raises(ThesisValidationError, match="(?i)param.value|mechanism"):
        validate_thesis_dict(_base())


def test_mechanism_judge_skips_when_no_classifier_installed() -> None:
    """When the classifier is not installed, the rule fails open (no rejection)."""
    mechanism_judge.set_classifier(None)
    obj = validate_thesis_dict(_base())
    assert obj.thesis_id == "judge_test"


def test_mechanism_judge_caches_results() -> None:
    """Two validations of the same thesis content should call the classifier once."""
    call_counter = {"n": 0}

    def fake(h: str, m: str) -> str:
        call_counter["n"] += 1
        return "mechanism"

    mechanism_judge.set_classifier(fake)
    validate_thesis_dict(_base())
    validate_thesis_dict(_base())
    assert call_counter["n"] == 1
