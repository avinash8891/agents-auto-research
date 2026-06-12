"""Tests for Stage 1 validator rules: B1 theme cluster, B3 needs_code starvation."""

from __future__ import annotations

import pytest

from backtest_run_db import research_thesis_attempt_id
from thesis_validator import VALID_PROCESS_TOOLS as _VALID_PROCESS_TOOLS
from thesis_validator import ThesisValidationError
from thesis_validator import validate_thesis_dict as _validate_thesis_dict


def validate_thesis_dict(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    kwargs.setdefault("research_round_id", "job-test-round-1")
    kwargs.setdefault("attempt_number", 1)
    kwargs.setdefault("assign_thesis_id", research_thesis_attempt_id)
    return _validate_thesis_dict(*args, **kwargs)


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
        "closest_prior_theses_considered": ["prior_volatility_baseline"],
        "orthogonality_defense": (
            "Volatility floor on alert candle is a different lever family than "
            "entry-time gating that has been explored before."
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
            "strategies/ema/signals.py:generate_signals_for_frame is where the volatility "
            "floor on the alert candle would gate entries by ATR-percent."
        ),
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups should follow through more reliably.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "Filtering low-volatility setups reduces but does not collapse counts.",
            },
        ],
        # Required by the live mechanical validation collector.
        "falsification_or_alternative": (
            "If low-volatility opens show the same PF as high-volatility opens, the "
            "auction-noise mechanism does not hold and the filter is not the cause."
        ),
        # B1 setups have high theme-keyword overlap with priors → computed
        # overlap = "high" → structural novel_connection gate would fire
        # before the B1 quality gate. Provide substantive text so the gate
        # under test is reachable.
        "novel_connection": (
            "Reframes the volatility floor as a session-conditioned signal, distinct "
            "from prior theses that treated it as an absolute threshold."
        ),
    }


def _prior(
    thesis_id: str,
    *,
    theme_keywords: list[str],
    requires_code: bool = False,
    outcome: str = "compiled",
) -> dict:
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


# ── Removed B1 theme-cluster fixation ─────────────────────────────────────


def test_b1_no_longer_rejects_when_4_of_last_7_share_theme_keywords() -> None:
    """Theme keyword repetition is handled by the v2 objective, not token gates."""
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

    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "job-test-round-1-attempt-1"


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
    assert obj.thesis_id == "job-test-round-1-attempt-1"


def test_b1_does_not_fire_with_few_priors() -> None:
    """Cluster fixation can't trigger with fewer than threshold priors."""
    prior_theses = [
        _prior("p1", theme_keywords=["volatility_floor"]),
        _prior("p2", theme_keywords=["volatility_floor"]),
    ]
    new = _base_thesis("p3")
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "job-test-round-1-attempt-1"


def test_b1_does_not_fire_when_thesis_has_no_theme_keywords() -> None:
    """If theme_keywords is empty, B1 cannot evaluate; rule is skipped."""
    prior_theses = [_prior(f"p{i}", theme_keywords=["volatility_floor"]) for i in range(7)]
    new = _base_thesis("p_new")
    new["theme_keywords"] = []
    obj = validate_thesis_dict(new, prior_theses=prior_theses)
    assert obj.thesis_id == "job-test-round-1-attempt-1"


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
    assert obj.thesis_id == "job-test-round-1-attempt-1"


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
    assert obj.thesis_id == "job-test-round-1-attempt-1"
