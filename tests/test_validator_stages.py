"""Tests for the Stage 1 / Stage 2 validator split."""

from __future__ import annotations

import pytest

from thesis_validator import (
    ThesisValidationError,
    validate_stage_1,
    validate_stage_2,
    validate_thesis_dict,
)


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
                    "If average bar-volatility quintile PF spread is below 0.2 in the data, "
                    "the volatility-quality mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
    }


def test_validate_stage_1_accepts_well_formed_thesis() -> None:
    validated = validate_thesis_dict(_base_thesis())
    out = validate_stage_1(validated, prior_theses=[])
    assert out.thesis_id == "stage1_test_thesis"


def test_validate_stage_1_rejects_missing_mechanism() -> None:
    raw = _base_thesis()
    raw["mechanism"] = ""
    with pytest.raises(ThesisValidationError, match="mechanism"):
        validate_thesis_dict(raw)


def test_validate_stage_2_is_no_op_pass_through() -> None:
    """Stage 2 currently has no rules; returns the contract unchanged."""

    class FakeContract:
        contract_id = "abc123"

    contract = FakeContract()
    result = validate_stage_2(contract)
    assert result is contract


def test_validate_stage_2_accepts_none_for_now() -> None:
    """Stage 2 is permissive while no rules exist; passes whatever it receives."""
    assert validate_stage_2(None) is None
