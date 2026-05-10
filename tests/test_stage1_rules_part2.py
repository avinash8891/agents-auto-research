"""Tests for Stage 1 rules B2 (direction whipsaw) and B5 (qualitative disqualifier)."""

from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _base_thesis(thesis_id: str) -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Filter setups by minimum opening volatility to avoid noise.",
        "mechanism": "Low-volatility opens have weaker microstructure signals.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": "Volatility floor on alert candle, different lever family.",
        "config_changes": {"alert_min_atr_pct": 0.001},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups should follow through more reliably.",
            }
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_volatility_separation",
                "condition": (
                    "If average bar-volatility quintile PF spread is below 0.2 in the data, "
                    "the volatility-quality mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "theme_keywords": ["volatility_floor"],
        "causal_cluster": "alert-candle volatility filter",
        "underexplored_dimensions_considered": ["portfolio_construction", "regime_conditioning"],
        "closest_prior_theses_considered": ["prior_volatility_baseline"],
        "orthogonality_defense": (
            "Volatility floor on alert candle is a different lever family than entry-time gating."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "RVOL gate on stocks-in-play list",
                "why_rejected": "we lack the relative-volume data for the train period",
            },
            {
                "mechanism": "opening drive directional gate",
                "why_rejected": "weaker disconfirmer than the volatility-floor mechanism here",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "intraday volatility microstructure paper"},
            {"source": "analyst", "citation": "round-3 analyst: low-vol opens have weaker PF"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:detect_alert_candle is where the volatility "
            "floor on the alert candle would gate entries by ATR-percent."
        ),
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups follow through more reliably.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "Filtering low-volatility setups reduces but does not collapse counts.",
            },
        ],
    }


def _prior(thesis_id: str, *, theme_keywords: list[str]) -> dict:
    return {
        "thesis_id": thesis_id,
        "config_changes": {f"key_{thesis_id}": True},
        "outcome": "compiled",
        "mechanism_dimension": "signal_quality",
        "thesis_details": {"theme_keywords": theme_keywords},
    }


# ── B5 qualitative disqualifier ───────────────────────────────────────────


def test_b5_rejects_when_all_disqualifiers_are_metric_threshold() -> None:
    thesis = _base_thesis("only_metric_disqualifiers")
    thesis["disqualifiers"] = [
        {
            "name": "pf_no_improvement",
            "condition": "profit_factor does not improve by 5 percent",
            "severity": "hard_fail",
            "kind": "metric_threshold",
        },
        {
            "name": "trade_count_collapse",
            "condition": "trade_count decreases by more than 50 percent",
            "severity": "hard_fail",
            "kind": "metric_threshold",
        },
    ]
    with pytest.raises(ThesisValidationError, match="(?i)mechanism.evidence"):
        validate_thesis_dict(thesis)


def test_b5_accepts_when_at_least_one_mechanism_evidence_disqualifier_present() -> None:
    thesis = _base_thesis("has_mechanism_evidence_disqualifier")
    obj = validate_thesis_dict(thesis)
    assert obj.thesis_id == "has_mechanism_evidence_disqualifier"


# ── B2 direction whipsaw ───────────────────────────────────────────────────


def test_b2_rejects_when_thesis_flips_lever_direction_without_acknowledging() -> None:
    """Prior thesis 'tightened' a stop_distance lever; new thesis 'widens' the same lever
    without citing the prior in prior_lever_outcomes → reject."""
    thesis = _base_thesis("widen_max_stop_distance_pct_cap_removal")
    thesis["theme_keywords"] = ["stop_distance"]
    thesis["hypothesis"] = "Widening max stop distance to capture larger setups."
    thesis["prior_lever_outcomes"] = []

    prior_theses = [
        _prior("tighten_min_stop_distance_pct_floor", theme_keywords=["stop_distance"]),
    ]

    with pytest.raises(ThesisValidationError, match="(?i)whipsaw|direction|prior_lever_outcomes"):
        validate_thesis_dict(thesis, prior_theses=prior_theses)


def test_b2_accepts_lever_flip_when_prior_lever_outcomes_cites_prior() -> None:
    """Same flip as above, but with an explicit citation in prior_lever_outcomes."""
    thesis = _base_thesis("widen_max_stop_distance_pct_cap_removal_v2")
    thesis["theme_keywords"] = ["stop_distance"]
    thesis["hypothesis"] = "Widening max stop distance to capture larger setups."
    thesis["prior_lever_outcomes"] = [
        {
            "prior_thesis_id": "tighten_min_stop_distance_pct_floor",
            "lever": "stop_distance_pct",
            "direction_then": "tightened",
            "outcome": "trade count fell with no PF gain; the floor was too aggressive",
            "why_retry": (
                "the analyst's volatility quintile data suggests the inverse direction "
                "may capture a different population entirely"
            ),
        }
    ]

    prior_theses = [
        _prior("tighten_min_stop_distance_pct_floor", theme_keywords=["stop_distance"]),
    ]

    obj = validate_thesis_dict(thesis, prior_theses=prior_theses)
    assert obj.thesis_id == "widen_max_stop_distance_pct_cap_removal_v2"


def test_b2_does_not_fire_when_no_lever_overlap() -> None:
    thesis = _base_thesis("unrelated_thesis")
    thesis["theme_keywords"] = ["regime_filter"]

    prior_theses = [
        _prior("tighten_min_stop_distance_pct_floor", theme_keywords=["stop_distance"]),
    ]

    obj = validate_thesis_dict(thesis, prior_theses=prior_theses)
    assert obj.thesis_id == "unrelated_thesis"


def test_b2_does_not_fire_when_no_opposing_direction_word_in_thesis_id() -> None:
    thesis = _base_thesis("filter_signals_by_volatility_quintile")
    thesis["theme_keywords"] = ["stop_distance"]  # overlap, but no direction word
    thesis["hypothesis"] = "Filter signals by volatility quintile."

    prior_theses = [
        _prior("tighten_min_stop_distance_pct_floor", theme_keywords=["stop_distance"]),
    ]

    obj = validate_thesis_dict(thesis, prior_theses=prior_theses)
    assert obj.thesis_id == "filter_signals_by_volatility_quintile"
