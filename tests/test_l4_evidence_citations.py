"""Tests for L4: typed evidence_citations require >=1 web_search + >=1 analyst."""

from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _base_thesis() -> dict:
    return {
        "thesis_id": "ec_test",
        "strategy_family": "ema",
        "hypothesis": "Require close confirmation to reduce intrabar false-break entries.",
        "mechanism": "Close-confirmed entries avoid wick-only stop triggers because the wick is noise.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": (
            "This tests confirmation logic in engine behavior, a different lever family."
        ),
        "causal_cluster": "close-confirmed adverse-selection reduction",
        "dominant_cluster_overlap": "low",
        "underexplored_dimensions_considered": [
            "portfolio_construction",
            "regime_conditioning",
        ],
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
                "condition": "If wick-only stop-out rate is no lower for close-confirmed entries.",
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "requires_code_change": True,
        "requested_primitives": ["close_confirmed_entry_gate"],
        "evidence_citations": [
            {"source": "web_search", "citation": "Cont et al on order-flow imbalance"},
            {"source": "analyst", "citation": "round-3 analyst: wick-only losses 37% of stops"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:detect_pullback contains the entry filter; "
            "the close-confirmation gate would be added here as an additional check."
        ),
    }


def test_validator_accepts_evidence_citations_with_required_sources() -> None:
    obj = validate_thesis_dict(_base_thesis())
    assert obj.thesis_id == "ec_test"


def test_validator_rejects_when_evidence_citations_missing_web_search() -> None:
    raw = _base_thesis()
    raw["evidence_citations"] = [
        {"source": "analyst", "citation": "round-3 analyst finding"},
        {"source": "memory", "citation": "prior session note"},
    ]
    with pytest.raises(ThesisValidationError, match="(?i)web_search"):
        validate_thesis_dict(raw)


def test_validator_rejects_when_evidence_citations_missing_analyst() -> None:
    raw = _base_thesis()
    raw["evidence_citations"] = [
        {"source": "web_search", "citation": "Cont et al"},
        {"source": "experiment_result", "citation": "round-3 PF 1.61"},
    ]
    with pytest.raises(ThesisValidationError, match="(?i)analyst"):
        validate_thesis_dict(raw)


def test_validator_rejects_empty_evidence_citations() -> None:
    raw = _base_thesis()
    raw["evidence_citations"] = []
    with pytest.raises(ThesisValidationError, match="(?i)evidence_citations"):
        validate_thesis_dict(raw)
