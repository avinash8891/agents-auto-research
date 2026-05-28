from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import thesis_validator
from research_types import Disqualifier, ExpectedEffect, ResearchThesis
from thesis_validator import (
    ThesisValidationError,
    validate_research_thesis,
    validate_stage_1,
    validate_stage_2,
)

_VALID_FALSIFICATION_TEXT = (
    "If opening-window losses do not decrease after this change, then the "
    "auction-noise mechanism is wrong and the effect is just random variation."
)


def _thesis(**overrides: Any) -> ResearchThesis:
    base: dict[str, Any] = {
        "thesis_id": "ema-tier-batching-v1",
        "strategy_family": "ema",
        "hypothesis": "Skipping the first minutes avoids opening-auction noise.",
        "mechanism": "Opening liquidity is thin, so early signals are noisy.",
        "mechanism_dimension": "entry_timing",
        "dimension_novelty": "Tests session timing instead of threshold tuning.",
        "causal_cluster": "opening-auction noise",
        "underexplored_dimensions_considered": ["risk_structure"],
        "novel_connection": (
            "Connects opening microstructure noise to entry timing rather than "
            "another stop-distance threshold change."
        ),
        "config_changes": {"opening_skip_minutes": 5},
        "expected_effects": [
            ExpectedEffect(
                metric="profit_factor",
                direction="increase",
                rationale="Skipping noisy opening entries should improve realized edge.",
            ),
            ExpectedEffect(
                metric="trade_count",
                direction="decrease_or_same",
                rationale="The entry filter should reduce but not collapse trade activity.",
            ),
        ],
        "disqualifiers": [
            Disqualifier(
                name="opening_noise_not_concentrated",
                condition=(
                    "Opening-window losses are not concentrated in the first five "
                    "minutes of the regular session."
                ),
                kind="mechanism_evidence",
            )
        ],
        "falsification_or_alternative": _VALID_FALSIFICATION_TEXT,
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
    base.update(overrides)
    return ResearchThesis(**base)


def _prior(
    thesis_id: str,
    *,
    config_changes: dict[str, Any] | None = None,
    theme_keywords: list[str] | None = None,
    requires_code_change: bool = False,
    outcome: str = "completed",
) -> dict[str, Any]:
    return {
        "thesis_id": thesis_id,
        "config_changes": config_changes or {},
        "mechanism_dimension": "entry_timing",
        "outcome": outcome,
        "validator_status": outcome,
        "thesis_details": {
            "theme_keywords": theme_keywords or [],
            "requires_code_change": requires_code_change,
        },
    }


def _cluster_priors() -> list[dict[str, Any]]:
    return [
        _prior("ema-open-a", theme_keywords=["opening_noise"]),
        _prior("ema-open-b", theme_keywords=["opening_noise"]),
        _prior("ema-open-c", theme_keywords=["opening_noise"]),
    ]


def test_mechanical_batch_collects_multiple_presence_failures() -> None:
    thesis = _thesis(
        thesis_id="",
        hypothesis="",
        mechanism="",
        dimension_novelty="x",
        expected_effects=[],
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(thesis, tools_called={"list_experiment_results", "web_search"})

    assert exc_info.value.rejection_code == "structural_mechanical_batch_failures"
    failures = exc_info.value.evidence["failures"]
    assert [failure["code"] for failure in failures] == [
        "structural_missing_thesis_id",
        "structural_missing_hypothesis",
        "structural_missing_mechanism",
        "structural_dimension_novelty_invalid",
        "structural_missing_expected_effects",
    ]


def test_process_gate_skips_when_tools_called_unobserved() -> None:
    thesis = validate_research_thesis(_thesis(), tools_called=None)

    assert thesis.thesis_id == "ema-tier-batching-v1"


def test_process_gate_runs_when_tools_called_observed_empty() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(_thesis(), tools_called=set())

    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == [
        "list_experiment_results",
        "web_search",
    ]


def test_validate_stage_1_accepts_process_tools() -> None:
    thesis = validate_stage_1(
        _thesis(),
        tools_called={"list_experiment_results", "web_search"},
    )

    assert thesis.thesis_id == "ema-tier-batching-v1"


def test_mechanical_batch_includes_disqualifiers_and_unbacked_effect_metrics() -> None:
    thesis = _thesis(
        disqualifiers=[],
        expected_effects=[
            ExpectedEffect(metric="made_up_metric_one", direction="increase"),
            ExpectedEffect(metric="made_up_metric_two", direction="decrease"),
        ],
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "structural_mechanical_batch_failures"
    failures = exc_info.value.evidence["failures"]
    assert [failure["code"] for failure in failures] == [
        "structural_missing_disqualifiers",
        "structural_expected_effect_metric_unbacked",
        "structural_expected_effect_metric_unbacked",
    ]
    assert failures[1]["evidence"] == {"metric": "made_up_metric_one"}
    assert failures[2]["evidence"] == {"metric": "made_up_metric_two"}


def test_mechanical_batch_includes_underexplored_dimensions_when_cluster_missing() -> None:
    thesis = _thesis(
        causal_cluster="",
        underexplored_dimensions_considered=[],
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=[_prior("ema-prior-a")],
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "structural_mechanical_batch_failures"
    failures = exc_info.value.evidence["failures"]
    assert [failure["code"] for failure in failures] == [
        "structural_missing_causal_cluster",
        "structural_underexplored_dimensions_invalid",
    ]


def test_mechanical_batch_does_not_escape_on_unknown_family_without_base_path() -> None:
    thesis = _thesis(
        strategy_family="unknown-family",
        thesis_id="",
        base_contract_id="prior-contract-abc123",
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "structural_mechanical_batch_failures"
    failures = exc_info.value.evidence["failures"]
    assert [failure["code"] for failure in failures] == [
        "structural_missing_thesis_id",
        "config_validity_base_contract_id_not_allowed",
        "unspecified_validation_error",
    ]


def test_mechanical_batch_with_single_failure_raises_original_code() -> None:
    thesis = _thesis(thesis_id="")

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(thesis, tools_called={"list_experiment_results", "web_search"})

    assert exc_info.value.rejection_code == "structural_missing_thesis_id"
    assert "failures" not in exc_info.value.evidence


def test_behavioral_pass_fires_before_mechanical_when_both_present() -> None:
    thesis = _thesis(thesis_id="", theme_keywords=["opening_noise"])

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=_cluster_priors(),
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "thesis_quality_theme_cluster_fixation"


def test_behavioral_pass_fires_first_signal_only_when_multiple_signals() -> None:
    thesis = _thesis(
        theme_keywords=["opening_noise"],
        requires_code_change=True,
        requested_primitives=["new_opening_filter"],
    )
    priors = [
        _prior(
            "ema-code-a",
            theme_keywords=["opening_noise"],
            requires_code_change=True,
            outcome="needs_code",
        ),
        _prior(
            "ema-code-b",
            theme_keywords=["opening_noise"],
            requires_code_change=True,
            outcome="needs_code",
        ),
        _prior(
            "ema-code-c",
            theme_keywords=["opening_noise"],
            requires_code_change=True,
            outcome="needs_code",
        ),
    ]

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=priors,
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "thesis_quality_theme_cluster_fixation"
    assert "needs_code_starvation" not in str(exc_info.value)


def test_rethink_class_1c_config_overlap_fires_before_mechanical() -> None:
    thesis = _thesis(thesis_id="", config_changes={"opening_session_filter": "late"})
    priors = [_prior("ema-prior-same-key", config_changes={"opening_session_filter": "early"})]

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=priors,
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "config_validity_config_key_overlap_real"


def test_neighboring_threshold_is_prioritized_over_config_overlap() -> None:
    thesis = _thesis(thesis_id="", config_changes={"opening_skip_minutes": 8})
    priors = [_prior("ema-prior-overlap-and-neighbor", config_changes={"opening_skip_minutes": 5})]

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=priors,
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "config_validity_neighboring_threshold"


def test_rethink_class_1c_neighboring_threshold_fires_before_mechanical() -> None:
    thesis = _thesis(
        thesis_id="",
        config_changes={
            "opening_skip_minutes": 8,
            "unrelated_filter_one": True,
            "unrelated_filter_two": "enabled",
        },
    )
    priors = [_prior("ema-prior-neighbor", config_changes={"opening_skip_minutes": 5})]

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=priors,
            tools_called={"list_experiment_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "config_validity_neighboring_threshold"


def test_mechanical_runs_when_behavioral_silent() -> None:
    thesis = _thesis(falsification_or_alternative="too short")

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(thesis, tools_called={"list_experiment_results", "web_search"})

    assert exc_info.value.rejection_code == "structural_falsification_invalid"


def test_stage_2_misalignment_routes_through_behavior_signal_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_codes: list[str] = []
    original_decide = thesis_validator._policy_decide

    def spy_decide(signals: list[Any]) -> Any:
        captured_codes.extend(signal.code for signal in signals)
        return original_decide(signals)

    monkeypatch.setattr(thesis_validator, "_policy_decide", spy_decide)
    contract = SimpleNamespace(
        runtime_config={
            "entry_cutoff_time": "10:00",
            "rr_ratio": 2.5,
            "gap_filter": True,
            "gap_pct": 0.01,
            "direction_bias": "long_only",
        },
        hypothesis="Filter setups by minimum opening volatility to avoid noise.",
        mechanism="Low-volatility opens have weaker microstructure signals.",
        strategy_family="ema",
        required_diagnostics=[],
        required_diagnostic_specs=[],
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_stage_2(contract)

    assert exc_info.value.rejection_code == "hypothesis_config_misalignment"
    assert captured_codes == ["hypothesis_config_misalignment"]


def test_stage_2_required_diagnostic_routes_through_mechanical_batch_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_codes: list[str] = []
    original_raise = thesis_validator._raise_mechanical_batch

    def spy_raise(failures: list[Any]) -> None:
        captured_codes.extend(failure.code for failure in failures)
        original_raise(failures)

    monkeypatch.setattr(thesis_validator, "_raise_mechanical_batch", spy_raise)
    contract = SimpleNamespace(
        runtime_config={
            "entry_cutoff_time": "10:00",
            "max_trades_per_day": 1,
        },
        hypothesis="Restrict entries to the morning entry window and keep only the first trade per day.",
        mechanism="Capture the opening dislocation only.",
        strategy_family="ema",
        required_diagnostics=["volatility_quintile_pf_spread"],
        required_diagnostic_specs=[],
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_stage_2(contract)

    assert exc_info.value.rejection_code == "required_diagnostic_missing_post_compile"
    assert captured_codes == ["required_diagnostic_missing_post_compile"]
