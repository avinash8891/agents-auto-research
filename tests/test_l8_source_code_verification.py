"""Tests for L8: source_code_verification field required (≥40 chars)."""

from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _base() -> dict:
    return {
        "thesis_id": "scv_test",
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
    }


def test_validator_rejects_empty_source_code_verification() -> None:
    raw = _base()
    raw["source_code_verification"] = ""
    with pytest.raises(ThesisValidationError, match="(?i)source_code_verification"):
        validate_thesis_dict(raw)


def test_validator_rejects_short_source_code_verification() -> None:
    raw = _base()
    raw["source_code_verification"] = "too short"
    with pytest.raises(ThesisValidationError, match="(?i)source_code_verification"):
        validate_thesis_dict(raw)


def test_validator_accepts_substantive_source_code_verification() -> None:
    raw = _base()
    raw["source_code_verification"] = (
        "strategies/ema/signals.py:detect_pullback uses min_stop_distance_pct as a hard "
        "floor in the entry filter; the close-confirmation gate would extend that filter."
    )
    obj = validate_thesis_dict(raw)
    assert obj.thesis_id == "scv_test"
