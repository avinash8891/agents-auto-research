"""Tests for L3: validator rules recovering legacy prompt requirements.

Each rule has a positive (well-formed) and negative (rejected) test.
"""

from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _base_thesis(thesis_id: str, dimension: str = "signal_quality") -> dict:
    """Fixture meeting all the new L3 requirements by default."""
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Require close confirmation to reduce intrabar false-break entries.",
        "mechanism": "Close-confirmed entries avoid wick-only stop triggers because the wick is noise.",
        "mechanism_dimension": dimension,
        "dimension_novelty": (
            "This tests confirmation logic in engine behavior, a different lever "
            "family from numeric parameter tuning of entry timing."
        ),
        "causal_cluster": "close-confirmed adverse-selection reduction",
        "dominant_cluster_overlap": "medium",
        "underexplored_dimensions_considered": [
            "portfolio_construction",
            "regime_conditioning",
        ],
        "novel_connection": (
            "Connects wick-only false-break evidence with engine-level close confirmation."
        ),
        "closest_prior_theses_considered": ["prior_signal_quality_baseline"],
        "orthogonality_defense": (
            "Changes engine entry-confirmation logic, a different lever family than threshold tuning."
        ),
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
            {"source": "analyst", "citation": "round-3 analyst: wick-only stops 37% of total"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:detect_pullback contains the entry filter; "
            "the close-confirmation gate would be added here as an additional check."
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
                "rationale": "Tighter entry filtering should not collapse trade frequency.",
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
        "why_not_overfit": "Mechanism is structural and evaluated across all train years.",
    }


# ── alternatives_considered >=2 ───────────────────────────────────────────


def test_validator_rejects_fewer_than_two_alternatives() -> None:
    raw = _base_thesis("alt_one")
    raw["alternatives_considered"] = [
        {
            "mechanism": "wider stop-distance cap",
            "why_rejected": "would not address the root wick-only false-break cause",
        }
    ]
    with pytest.raises(ThesisValidationError, match="alternatives_considered"):
        validate_thesis_dict(raw)


def test_validator_accepts_two_alternatives() -> None:
    raw = _base_thesis("alt_two")
    obj = validate_thesis_dict(raw)
    assert len(obj.alternatives_considered) == 2


# ── expected_effects >=2 ───────────────────────────────────────────────────


def test_validator_rejects_only_one_expected_effect() -> None:
    raw = _base_thesis("ee_one")
    raw["expected_effects"] = [raw["expected_effects"][0]]
    with pytest.raises(ThesisValidationError, match="expected_effects"):
        validate_thesis_dict(raw)


# ── underexplored_dimensions_considered >=2 ───────────────────────────────


def test_validator_rejects_only_one_underexplored_dimension_when_priors_exist() -> None:
    raw = _base_thesis("ud_one")
    raw["underexplored_dimensions_considered"] = ["portfolio_construction"]
    prior = [
        {
            "thesis_id": "prior_x",
            "config_changes": {"some_key": True},
            "outcome": "compiled",
            "mechanism_dimension": "regime_conditioning",
        }
    ]
    with pytest.raises(ThesisValidationError, match="underexplored_dimensions_considered"):
        validate_thesis_dict(raw, prior_theses=prior)


# ── closest_prior_theses_considered required when prior_theses ────────────


def test_validator_rejects_empty_closest_prior_when_prior_theses_exist() -> None:
    raw = _base_thesis("cp_empty")
    raw["closest_prior_theses_considered"] = []
    prior = [
        {
            "thesis_id": "prior_x",
            "config_changes": {"some_key": True},
            "outcome": "compiled",
            "mechanism_dimension": "regime_conditioning",
        }
    ]
    with pytest.raises(ThesisValidationError, match="closest_prior_theses_considered"):
        validate_thesis_dict(raw, prior_theses=prior)


# ── orthogonality_defense >=40 chars when prior_theses ─────────────────────


def test_validator_rejects_short_orthogonality_defense_when_prior_exists() -> None:
    raw = _base_thesis("od_short")
    raw["orthogonality_defense"] = "too short"
    prior = [
        {
            "thesis_id": "prior_x",
            "config_changes": {"some_key": True},
            "outcome": "compiled",
            "mechanism_dimension": "regime_conditioning",
        }
    ]
    with pytest.raises(ThesisValidationError, match="orthogonality_defense"):
        validate_thesis_dict(raw, prior_theses=prior)


# ── evidence_strength must be valid (not empty) ────────────────────────────


def test_validator_rejects_empty_evidence_strength() -> None:
    raw = _base_thesis("es_empty")
    raw["evidence_strength"] = ""
    with pytest.raises(ThesisValidationError, match="evidence_strength"):
        validate_thesis_dict(raw)


def test_validator_accepts_valid_evidence_strength() -> None:
    raw = _base_thesis("es_valid")
    raw["evidence_strength"] = "direct"
    obj = validate_thesis_dict(raw)
    assert obj.evidence_strength == "direct"


# ── Don't repeat thesis_id ─────────────────────────────────────────────────


def test_validator_rejects_repeated_thesis_id() -> None:
    raw = _base_thesis("repeat_me")
    prior = [
        {
            "thesis_id": "repeat_me",  # same id as new thesis
            "config_changes": {"some_key": True},
            "outcome": "compiled",
            "mechanism_dimension": "regime_conditioning",
        }
    ]
    with pytest.raises(ThesisValidationError, match="(?i)thesis_id.*(repeat|duplicate|already)"):
        validate_thesis_dict(raw, prior_theses=prior)


# ── causal_cluster not a config-key name ───────────────────────────────────


def test_validator_rejects_config_key_like_causal_cluster() -> None:
    raw = _base_thesis("cc_keylike")
    raw["causal_cluster"] = "min_stop_distance_pct"  # looks like a config key
    with pytest.raises(ThesisValidationError, match="(?i)causal_cluster.*(config|key)"):
        validate_thesis_dict(raw)


def test_validator_accepts_human_phrased_causal_cluster() -> None:
    raw = _base_thesis("cc_human")
    raw["causal_cluster"] = "wick-only false-break filtering"
    obj = validate_thesis_dict(raw)
    assert "false" in obj.causal_cluster
