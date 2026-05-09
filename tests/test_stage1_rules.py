"""Tests for Stage 1 validator rules: B1 theme cluster, B3 needs_code starvation.

(B4 alignment-as-gate already exists in the validator at threshold 0.4 — see
existing tests for hypothesis_config_misalignment.)
"""

from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _base_thesis(thesis_id: str, dimension: str = "signal_quality") -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Filter setups by minimum opening volatility to avoid noise.",
        "mechanism": "Low-volatility opens have weaker microstructure signals.",
        "mechanism_dimension": dimension,
        "dimension_novelty": (
            "This tests a volatility floor on the alert candle, a fundamentally "
            "different lever than entry-time gating."
        ),
        "config_changes": {"alert_min_atr_pct": 0.001},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups should have stronger directional follow-through.",
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
                    "If volatility quintile PF spread is below 0.2, the mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "theme_keywords": ["volatility_floor", "alert_candle"],
        "causal_cluster": "alert-candle volatility filter",
        "underexplored_dimensions_considered": [
            "portfolio_construction",
            "regime_conditioning",
        ],
    }


def _prior(thesis_id: str, *, theme_keywords: list[str], requires_code: bool = False, outcome: str = "compiled") -> dict:
    return {
        "thesis_id": thesis_id,
        "config_changes": {f"key_{thesis_id}": True},
        "outcome": outcome,
        "mechanism_dimension": "signal_quality",
        "thesis_details": {
            "theme_keywords": theme_keywords,
            "requires_code_change": requires_code,
        },
    }


# ── B1 theme-cluster fixation ─────────────────────────────────────────────


def test_b1_rejects_thesis_when_4_of_last_7_share_theme_keywords() -> None:
    """4 of last 7 (including current) share at least one keyword → reject."""
    prior_theses = [
        _prior("p1", theme_keywords=["volatility_floor", "noise_filter"]),
        _prior("p2", theme_keywords=["other_a"]),
        _prior("p3", theme_keywords=["volatility_floor"]),
        _prior("p4", theme_keywords=["other_b"]),
        _prior("p5", theme_keywords=["alert_candle"]),
        _prior("p6", theme_keywords=["other_c"]),
        # p7 will be the new thesis with theme_keywords=["volatility_floor", "alert_candle"]
    ]
    new = _base_thesis("p7")  # has volatility_floor + alert_candle

    # Overlap: p1 (volatility_floor), p3 (volatility_floor), p5 (alert_candle), and p7 itself
    # → 4 of last 7 → should reject.
    with pytest.raises(ThesisValidationError, match="(?i)theme.cluster"):
        validate_thesis_dict(new, prior_theses=prior_theses)


def test_b1_accepts_when_only_3_of_last_7_share_theme_keywords() -> None:
    """3 of last 7 (below threshold) is OK."""
    prior_theses = [
        _prior("p1", theme_keywords=["volatility_floor"]),
        _prior("p2", theme_keywords=["other_a"]),
        _prior("p3", theme_keywords=["other_b"]),
        _prior("p4", theme_keywords=["other_c"]),
        _prior("p5", theme_keywords=["alert_candle"]),
        _prior("p6", theme_keywords=["other_d"]),
    ]
    new = _base_thesis("p7")  # has volatility_floor + alert_candle
    # Overlap: p1 (volatility_floor), p5 (alert_candle), p7 itself = 3 → accepted
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "p7"


def test_b1_does_not_fire_with_few_priors() -> None:
    """Cluster fixation can't trigger with fewer than threshold priors."""
    prior_theses = [
        _prior("p1", theme_keywords=["volatility_floor"]),
        _prior("p2", theme_keywords=["volatility_floor"]),
    ]
    new = _base_thesis("p3")
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "p3"


def test_b1_does_not_fire_when_thesis_has_no_theme_keywords() -> None:
    """If theme_keywords is empty, B1 cannot evaluate; rule is skipped."""
    prior_theses = [
        _prior(f"p{i}", theme_keywords=["volatility_floor"]) for i in range(7)
    ]
    new = _base_thesis("p_new")
    new["theme_keywords"] = []
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "p_new"


# ── B3 needs_code starvation ───────────────────────────────────────────────


def test_b3_rejects_thesis_when_3_consecutive_needs_code_with_no_runs() -> None:
    """3 consecutive prior theses required code change and never ran → force no-code."""
    prior_theses = [
        _prior("p1", theme_keywords=["a"], requires_code=True, outcome="needs_code"),
        _prior("p2", theme_keywords=["b"], requires_code=True, outcome="needs_code"),
        _prior("p3", theme_keywords=["c"], requires_code=True, outcome="needs_code"),
    ]
    new = _base_thesis("p4")
    new["requires_code_change"] = True  # this would be the 4th in a row
    new["requested_primitives"] = ["x"]
    new["config_changes"] = {"requires_engine_change": True}

    with pytest.raises(ThesisValidationError, match="needs_code|engine.change"):
        validate_thesis_dict(new, prior_theses=prior_theses)


def test_b3_accepts_no_code_thesis_after_needs_code_starvation() -> None:
    """A no-code thesis breaks the streak — the rule allows it."""
    prior_theses = [
        _prior(f"p{i}", theme_keywords=[f"k{i}"], requires_code=True, outcome="needs_code")
        for i in range(1, 4)
    ]
    new = _base_thesis("p_break")
    new["requires_code_change"] = False  # breaks the streak
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "p_break"


def test_b3_does_not_fire_when_streak_was_broken_by_a_run() -> None:
    """If one of the recent priors actually ran, the streak resets."""
    prior_theses = [
        _prior("p1", theme_keywords=["a"], requires_code=True, outcome="needs_code"),
        _prior("p2", theme_keywords=["b"], requires_code=False, outcome="compiled"),  # ran
        _prior("p3", theme_keywords=["c"], requires_code=True, outcome="needs_code"),
    ]
    new = _base_thesis("p4")
    new["requires_code_change"] = True
    new["requested_primitives"] = ["x"]
    new["config_changes"] = {"requires_engine_change": True}
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "p4"
