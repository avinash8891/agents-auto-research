from __future__ import annotations

from pathlib import Path

from thesis_validator import infer_rejection_code

_RESEARCH_CONDUCTOR_SOURCE = Path("research_conductor.py").read_text()


def test_conductor_no_longer_exports_web_search_order_gate() -> None:
    assert "def _check_web_search_called_first" not in _RESEARCH_CONDUCTOR_SOURCE


def test_conductor_no_longer_exports_experiment_results_gate() -> None:
    assert "def _check_experiment_results_consulted" not in _RESEARCH_CONDUCTOR_SOURCE


def test_legacy_l6_message_no_longer_maps_to_process_required_tools_code() -> None:
    assert (
        infer_rejection_code(
            "ERROR: HARD GATE — call web_search at least once before analyze_trades."
        )
        == "unspecified_validation_error"
    )


def test_legacy_l7_message_no_longer_maps_to_process_required_tools_code() -> None:
    assert (
        infer_rejection_code(
            "ERROR: HARD GATE — call list_experiment_results at least once before proposing a thesis."
        )
        == "unspecified_validation_error"
    )
