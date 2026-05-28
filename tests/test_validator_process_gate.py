from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _valid_thesis() -> dict:
    return {
        "thesis_id": "ema-process-gate-v1",
        "strategy_family": "ema",
        "hypothesis": "Skipping opening auction noise improves entry quality.",
        "mechanism": "The first minutes have thin liquidity and noisy fills.",
        "mechanism_dimension": "entry_timing",
        "dimension_novelty": "Tests session timing instead of threshold tuning.",
        "config_changes": {"opening_skip_minutes": 5},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Skipping noisy opening entries should improve realized edge.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "The entry filter should reduce but not collapse trade activity.",
            },
        ],
        "disqualifiers": [
            {
                "name": "opening_noise_not_concentrated",
                "condition": "Opening-window losses are not concentrated in the first five minutes.",
                "kind": "mechanism_evidence",
            }
        ],
        "falsification_or_alternative": (
            "If first-five-minute losses are not worse than later-session losses, "
            "the auction-noise mechanism is false."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": (
                    "This would tune risk after entry rather than test whether opening "
                    "entry timing is the noisy mechanism."
                ),
            },
            {
                "mechanism": "trend-strength filter",
                "why_rejected": (
                    "This would test directional continuation rather than isolate the "
                    "opening liquidity mechanism."
                ),
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "Opening auctions can have wider spreads."},
            {"source": "analyst", "citation": "round-1 analyst found opening loss clustering."},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame builds the EMA entries."
        ),
    }


def test_process_gate_passes_when_all_required_tools_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_round_results", "web_search"},
    )
    assert thesis.thesis_id == "ema-process-gate-v1"


def test_process_gate_rejects_when_web_search_not_called() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(_valid_thesis(), tools_called={"list_round_results"})
    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == ["web_search"]


def test_process_gate_rejects_when_experiment_results_not_called() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(_valid_thesis(), tools_called={"web_search"})
    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == ["list_round_results"]


def test_process_gate_batches_multiple_missing_tools() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(_valid_thesis(), tools_called=set())
    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == [
        "list_round_results",
        "web_search",
    ]


def test_process_gate_succeeds_with_extra_tools_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_round_results", "web_search", "analyze_trades"},
    )
    assert thesis.config_changes == {"opening_skip_minutes": 5}


def test_process_gate_requires_analyze_trades_when_analyst_tool_required() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(
            _valid_thesis(),
            tools_called={"list_round_results", "web_search"},
            require_analyst_tool=True,
        )

    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == ["analyze_trades"]


def test_process_gate_passes_analyst_tool_requirement_when_analyze_trades_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_round_results", "web_search", "analyze_trades"},
        require_analyst_tool=True,
    )

    assert thesis.thesis_id == "ema-process-gate-v1"
