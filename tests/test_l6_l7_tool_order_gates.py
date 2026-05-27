from __future__ import annotations

import research_conductor
from thesis_validator import infer_rejection_code


def test_conductor_no_longer_exports_web_search_order_gate() -> None:
    assert not hasattr(research_conductor, "_check_web_search_called_first")


def test_conductor_no_longer_exports_experiment_results_gate() -> None:
    assert not hasattr(research_conductor, "_check_experiment_results_consulted")


def test_legacy_l6_message_maps_to_process_required_tools_code() -> None:
    assert (
        infer_rejection_code(
            "ERROR: HARD GATE — call web_search at least once before analyze_trades."
        )
        == "process_required_tools_not_called"
    )


def test_legacy_l7_message_maps_to_process_required_tools_code() -> None:
    assert (
        infer_rejection_code(
            "ERROR: HARD GATE — call list_experiment_results at least once before proposing a thesis."
        )
        == "process_required_tools_not_called"
    )
