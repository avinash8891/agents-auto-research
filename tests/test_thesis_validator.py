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
