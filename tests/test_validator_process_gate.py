from __future__ import annotations

from backtest_run_db import research_thesis_attempt_id
from thesis_validator import _process_signal
from thesis_validator import validate_thesis_dict as _validate_thesis_dict


def validate_thesis_dict(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("research_round_id", "job-test-round-1")
    kwargs.setdefault("attempt_number", 1)
    kwargs.setdefault("assign_thesis_id", research_thesis_attempt_id)
    return _validate_thesis_dict(*args, **kwargs)


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
    assert thesis.thesis_id == "job-test-round-1-attempt-1"


def test_process_gate_warns_when_web_search_not_called() -> None:
    thesis = validate_thesis_dict(_valid_thesis(), tools_called={"list_round_results"})

    assert thesis.thesis_id == "job-test-round-1-attempt-1"


def test_process_gate_detection_returns_behavior_signal_before_policy() -> None:
    signal = _process_signal({"list_round_results"})

    assert signal is not None
    assert signal.code == "process_missing_required_tools"
    assert signal.severity == "warn"
    assert signal.evidence == {
        "missing_tools": ["web_search"],
        "tools_called": ["list_round_results"],
    }


def test_process_gate_warns_when_experiment_results_not_called() -> None:
    thesis = validate_thesis_dict(_valid_thesis(), tools_called={"web_search"})

    assert thesis.thesis_id == "job-test-round-1-attempt-1"


def test_process_gate_warns_with_multiple_missing_tools() -> None:
    thesis = validate_thesis_dict(_valid_thesis(), tools_called=set())

    assert thesis.thesis_id == "job-test-round-1-attempt-1"
    signal = _process_signal(set())
    assert signal is not None
    assert signal.evidence == {
        "missing_tools": ["list_round_results", "web_search"],
        "tools_called": [],
    }


def test_process_gate_succeeds_with_extra_tools_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_round_results", "web_search", "analyze_trades"},
    )
    assert thesis.config_changes == {"opening_skip_minutes": 5}


def test_process_gate_warns_when_analyst_tool_required() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_round_results", "web_search"},
        require_analyst_tool=True,
    )

    assert thesis.thesis_id == "job-test-round-1-attempt-1"
    signal = _process_signal({"list_round_results", "web_search"}, require_analyst_tool=True)
    assert signal is not None
    assert signal.evidence == {
        "missing_tools": ["analyze_trades"],
        "tools_called": ["list_round_results", "web_search"],
    }


def test_process_gate_passes_analyst_tool_requirement_when_analyze_trades_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_round_results", "web_search", "analyze_trades"},
        require_analyst_tool=True,
    )

    assert thesis.thesis_id == "job-test-round-1-attempt-1"
