"""Tests for thesis validator stage-1 behavior."""

from __future__ import annotations

from typing import Any

import pytest

from backtest_run_db import research_thesis_attempt_id
from thesis_validator import VALID_PROCESS_TOOLS as _VALID_PROCESS_TOOLS
from thesis_validator import (
    ThesisValidationError,
)
from thesis_validator import validate_research_thesis as _validate_research_thesis
from thesis_validator import (
    validate_stage_1,
)
from thesis_validator import validate_thesis_dict as _validate_thesis_dict


def validate_research_thesis(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    return _validate_research_thesis(*args, **kwargs)


def validate_thesis_dict(*args: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    kwargs.setdefault("research_round_id", "job-test-round-1")
    kwargs.setdefault("attempt_number", 1)
    kwargs.setdefault("assign_thesis_id", research_thesis_attempt_id)
    return _validate_thesis_dict(*args, **kwargs)


def _base_thesis() -> dict:
    return {
        "thesis_id": "stage1_test_thesis",
        "strategy_family": "ema",
        "hypothesis": "Filter setups by minimum opening volatility to avoid noise.",
        "mechanism": "Low-volatility opens have weaker microstructure signals.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": (
            "This tests a volatility floor on the alert candle, a fundamentally "
            "different lever than entry-time gating which has been explored before."
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
                    "If average bar-volatility quintile PF spread is below 0.2 in the data, "
                    "the volatility-quality mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "closest_prior_theses_considered": ["prior_volatility_baseline"],
        "orthogonality_defense": (
            "Volatility floor on the alert candle is a different lever family than entry timing."
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
        # Required field post-refactor: doctrine was always to require a
        # disconfirmer; the validator now enforces presence, not just length.
        "falsification_or_alternative": (
            "If high-volatility and low-volatility setups show the same PF distribution, "
            "the volatility-quality mechanism does not hold."
        ),
    }


# ── Stage 1 sanity ────────────────────────────────────────────────────────


def test_validate_stage_1_accepts_well_formed_thesis() -> None:
    validated = validate_thesis_dict(_base_thesis())
    out = validate_stage_1(
        validated,
        prior_theses=[],
        tools_called=_VALID_PROCESS_TOOLS,
    )
    assert out.thesis_id == "job-test-round-1-attempt-1"


def test_validate_stage_1_rejects_missing_mechanism() -> None:
    raw = _base_thesis()
    raw["mechanism"] = ""
    with pytest.raises(ThesisValidationError, match="mechanism"):
        validate_thesis_dict(raw)


def test_validate_research_thesis_does_not_run_alignment_at_stage_1() -> None:
    """Stage 1 does not reject solely because config keys and hypothesis text diverge."""
    raw = _base_thesis()
    # Force misalignment: keys we know are in KEY_CONCEPTS but the hypothesis
    # talks about volatility, not these.
    raw["config_changes"] = {
        "entry_cutoff_time": "10:00",
        "rr_ratio": 2.5,
        "trail_after_r": 1.0,
        "max_hold_bars": 30,
    }
    raw["hypothesis"] = "Filter setups by minimum opening volatility to avoid noise."
    raw["mechanism"] = "Low-volatility opens have weaker microstructure signals."

    # Stage 1 must accept this.
    validated = validate_thesis_dict(raw)
    assert validated.thesis_id == "job-test-round-1-attempt-1"
    # validate_research_thesis must also accept (it's the Stage 1 alias).
    again = validate_research_thesis(validated, prior_theses=[])
    assert again.thesis_id == "job-test-round-1-attempt-1"
