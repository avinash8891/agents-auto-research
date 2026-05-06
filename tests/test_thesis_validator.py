from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, config_key_overlap, validate_thesis_dict


def _base_engine_change_thesis(thesis_id: str, dimension: str) -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Require close confirmation to reduce intrabar false-break entries.",
        "mechanism": "A close-confirmed entry gate should avoid wick-only stop triggers.",
        "mechanism_dimension": dimension,
        "dimension_novelty": (
            "This tests confirmation logic in engine behavior, not a numeric "
            "parameter variation of a previous thesis."
        ),
        "config_changes": {"requires_engine_change": True},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Fewer false-break entries should improve realized edge.",
            }
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
            }
        ],
        "requires_code_change": True,
        "requested_primitives": ["close_confirmed_entry_gate"],
        "why_not_overfit": "Mechanism is structural and evaluated across all train years.",
    }


def test_config_key_overlap_ignores_engine_change_sentinel_only() -> None:
    is_duplicate, reason = config_key_overlap(
        {"requires_engine_change": True},
        [
            {
                "thesis_id": "prior_engine_change",
                "config_changes": {"requires_engine_change": True},
            }
        ],
    )

    assert is_duplicate is False
    assert reason == ""


def test_config_key_overlap_still_rejects_real_overlapping_keys_with_sentinel() -> None:
    is_duplicate, reason = config_key_overlap(
        {"requires_engine_change": True, "entry_cutoff_time": "10:00"},
        [
            {
                "thesis_id": "prior_entry_cutoff",
                "config_changes": {
                    "requires_engine_change": True,
                    "entry_cutoff_time": "09:45",
                },
            }
        ],
    )

    assert is_duplicate is True
    assert "entry_cutoff_time" in reason
    assert "requires_engine_change" not in reason


def test_config_key_overlap_compares_nested_engine_change_keys() -> None:
    is_duplicate, reason = config_key_overlap(
        {
            "requires_engine_change": True,
            "new_config_keys_needed": {
                "entry_confirmation_mode": "close_beyond_break",
                "entry_acceptance_buffer_pct": 0.0001,
            },
        },
        [
            {
                "thesis_id": "momentum_gated_trailing_activation",
                "config_changes": {
                    "requires_engine_change": True,
                    "new_config_keys_needed": {
                        "momentum_activation_enabled": True,
                        "trail_activation_r": 1.5,
                    },
                },
            }
        ],
    )

    assert is_duplicate is False
    assert reason == ""


def test_config_key_overlap_rejects_same_nested_engine_change_key() -> None:
    is_duplicate, reason = config_key_overlap(
        {
            "requires_engine_change": True,
            "new_config_keys_needed": {
                "entry_confirmation_mode": "close_beyond_break",
            },
        },
        [
            {
                "thesis_id": "prior_entry_confirmation",
                "config_changes": {
                    "requires_engine_change": True,
                    "new_config_keys_needed": {
                        "entry_confirmation_mode": "touch_then_close",
                    },
                },
            }
        ],
    )

    assert is_duplicate is True
    assert "new_config_keys_needed.entry_confirmation_mode" in reason


def test_validate_engine_change_thesis_not_rejected_for_sentinel_overlap() -> None:
    thesis = _base_engine_change_thesis("close_confirmed_break_entry_gate", "signal_quality")
    prior = [
        {
            "thesis_id": "time_of_day_slippage_model_open_penalty",
            "config_changes": {"requires_engine_change": True},
            "mechanism_dimension": "market_microstructure",
        }
    ]

    validated = validate_thesis_dict(thesis, prior_theses=prior)

    assert validated.thesis_id == "close_confirmed_break_entry_gate"


def test_validate_real_config_overlap_still_rejected_with_sentinel() -> None:
    thesis = _base_engine_change_thesis("duplicate_entry_cutoff", "entry_timing")
    thesis["config_changes"] = {
        "requires_engine_change": True,
        "entry_cutoff_time": "10:00",
    }
    prior = [
        {
            "thesis_id": "prior_entry_cutoff",
            "config_changes": {
                "requires_engine_change": True,
                "entry_cutoff_time": "09:45",
            },
            "mechanism_dimension": "risk_structure",
        }
    ]

    with pytest.raises(ThesisValidationError, match="Config-key overlap"):
        validate_thesis_dict(thesis, prior_theses=prior)
