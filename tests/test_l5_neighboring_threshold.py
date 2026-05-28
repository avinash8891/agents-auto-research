"""Tests for L5: neighboring-threshold detector (legacy §2 + §12).

Reject when thesis A sets X=0.005 and thesis B sets X=0.01 — same key with a
small numeric delta is parameter tuning, not a new mechanism.
"""

from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError
from thesis_validator import validate_thesis_dict as _validate_thesis_dict

_VALID_PROCESS_TOOLS = {"list_experiment_results", "web_search"}


def validate_thesis_dict(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    kwargs.setdefault("research_round_id", "job-test-round-1")
    kwargs.setdefault("attempt_number", 1)
    return _validate_thesis_dict(*args, **kwargs)


def _base_thesis(thesis_id: str, config_changes: dict) -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Adjust gap-exclude threshold to filter unrepresentative gap-up days.",
        "mechanism": "Large opening gaps create regime shifts that the EMA pullback edge does not capture.",
        "mechanism_dimension": "regime_conditioning",
        "dimension_novelty": (
            "This narrows the gap-exclude band; whether it is genuinely a different "
            "mechanism requires structural justification, not a number nudge."
        ),
        "causal_cluster": "gap-up regime exclusion",
        "dominant_cluster_overlap": "low",
        "underexplored_dimensions_considered": ["portfolio_construction", "risk_structure"],
        "novel_connection": "Connects gap-magnitude to overnight-imbalance decay rate.",
        "closest_prior_theses_considered": ["prior_gap_filter"],
        "orthogonality_defense": (
            "Gap exclusion is the same lever as the prior thesis; this submission must "
            "either justify a structural boundary or be rejected as parameter tuning."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "alternative volatility-regime gate using ATR",
                "why_rejected": "needs new diagnostics; gap-magnitude is already measurable",
            },
            {
                "mechanism": "session-time exclusion of post-gap windows",
                "why_rejected": "is a proxy; gap_exclude_pct directly controls the inclusion rule",
            },
        ],
        "config_changes": config_changes,
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "fewer regime-mismatched trades",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "The selective gap filter should reduce but not collapse activity.",
            },
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count drops more than 50%",
                "kind": "metric_threshold",
            },
            {
                "name": "no_gap_separation",
                "condition": "If quintile PF spread by gap magnitude is below 0.2 the mechanism does not hold.",
                "kind": "mechanism_evidence",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "intraday gap research"},
            {"source": "analyst", "citation": "round-2 analyst: gap-day PF dispersion"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame applies gap_exclude_pct as "
            "a top-of-day filter; tighter values reduce the included population."
        ),
        # Required post-refactor: falsification_or_alternative must be
        # present and ≥80 chars. Was previously optional.
        "falsification_or_alternative": (
            "If quintile PF spread by gap magnitude is below 0.2 across the train and "
            "validation samples, the gap-regime mechanism does not hold."
        ),
    }


# Each config dict in these tests includes extra unique keys so the broader
# Jaccard rule (≥0.5 overlap) doesn't fire first; we want to specifically
# exercise the L5 neighboring-threshold detector.


def _prior(thesis_id: str, config_changes: dict) -> dict:
    return {
        "thesis_id": thesis_id,
        "config_changes": config_changes,
        "outcome": "compiled",
        "mechanism_dimension": "regime_conditioning",
    }


def test_validator_rejects_neighboring_threshold_same_key_small_delta() -> None:
    """gap_exclude_pct=0.005 vs prior 0.01 → 2x apart → reject (with extra
    unique keys so Jaccard < 0.5 and L5 is the rule that fires)."""
    raw = _base_thesis(
        "nudge_gap_exclude",
        {"gap_exclude_pct": 0.005, "unique_key_a": 1, "unique_key_b": 2, "unique_key_c": 3},
    )
    prior = [
        _prior(
            "prior_gap_filter",
            {
                "gap_exclude_pct": 0.01,
                "prior_only_a": True,
                "prior_only_b": False,
                "prior_only_c": "x",
            },
        )
    ]
    with pytest.raises(ThesisValidationError, match="(?i)neighboring|threshold|parameter"):
        validate_thesis_dict(raw, prior_theses=prior)


def test_validator_accepts_large_delta_on_same_key() -> None:
    """gap_exclude_pct=0.001 vs prior 0.01 → 10x apart → not a tuning nudge."""
    raw = _base_thesis(
        "widen_gap_exclude",
        {"gap_exclude_pct": 0.001, "unique_key_a": 1, "unique_key_b": 2, "unique_key_c": 3},
    )
    prior = [
        _prior(
            "prior_gap_filter",
            {
                "gap_exclude_pct": 0.01,
                "prior_only_a": True,
                "prior_only_b": False,
                "prior_only_c": "x",
            },
        )
    ]
    obj = validate_thesis_dict(raw, prior_theses=prior)
    assert obj.thesis_id == "job-test-round-1-attempt-1"


def test_validator_does_not_fire_when_keys_differ() -> None:
    """Different keys = different mechanism = not a tuning nudge."""
    raw = _base_thesis(
        "different_key",
        {"min_relative_volume": 1.5, "extra1": True, "extra2": "x"},
    )
    prior = [
        _prior(
            "prior_gap_filter",
            {"gap_exclude_pct": 0.01, "prior_extra_a": True, "prior_extra_b": "x"},
        )
    ]
    obj = validate_thesis_dict(raw, prior_theses=prior)
    assert obj.thesis_id == "job-test-round-1-attempt-1"


def test_validator_does_not_fire_for_non_numeric_values() -> None:
    """Boolean/string config changes can't be ratio-compared.

    Use unique extra keys so the broader Jaccard rule doesn't fire on the
    shared boolean key alone.
    """
    raw = _base_thesis(
        "flag_change",
        {"opening_drive_gate_enabled": True, "extra1": "x", "extra2": "y", "extra3": "z"},
    )
    prior = [
        _prior(
            "prior_gap_filter",
            {"opening_drive_gate_enabled": False, "p1": True, "p2": "x", "p3": "y"},
        )
    ]
    obj = validate_thesis_dict(raw, prior_theses=prior)
    assert obj.thesis_id == "job-test-round-1-attempt-1"
