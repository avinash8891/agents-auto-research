from __future__ import annotations

from typing import Any

import pytest

from research_types import Disqualifier, ExpectedEffect, ResearchThesis
from thesis_validator import (
    ThesisValidationError,
    validate_research_thesis,
    validate_stage_1,
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
        validate_research_thesis(thesis, tools_called={"list_round_results", "web_search"})

    assert exc_info.value.rejection_code == "structural_mechanical_batch_failures"
    failures = exc_info.value.evidence["failures"]
    assert [failure["code"] for failure in failures] == [
        "structural_missing_thesis_id",
        "structural_missing_hypothesis",
        "structural_missing_mechanism",
        "structural_missing_expected_effects",
    ]


def test_process_gate_skips_when_tools_called_unobserved() -> None:
    thesis = validate_research_thesis(_thesis(), tools_called=None)

    assert thesis.thesis_id == "ema-tier-batching-v1"


def test_process_gate_warns_when_tools_called_observed_empty() -> None:
    thesis = validate_research_thesis(_thesis(), tools_called=set())

    assert thesis.thesis_id == "ema-tier-batching-v1"


def test_validate_stage_1_accepts_process_tools() -> None:
    thesis = validate_stage_1(
        _thesis(),
        tools_called={"list_round_results", "web_search"},
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
            tools_called={"list_round_results", "web_search"},
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


def test_mechanical_batch_ignores_underexplored_dimensions_when_cluster_missing() -> None:
    thesis = _thesis(
        causal_cluster="",
        underexplored_dimensions_considered=[],
    )

    validated = validate_research_thesis(
        thesis,
        prior_theses=[_prior("ema-prior-a")],
        tools_called={"list_round_results", "web_search"},
    )

    assert validated.thesis_id == "ema-tier-batching-v1"


def test_mechanical_batch_does_not_escape_on_unknown_family_without_base_path() -> None:
    thesis = _thesis(
        strategy_family="unknown-family",
        thesis_id="",
        base_contract_id="prior-contract-abc123",
    )

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            tools_called={"list_round_results", "web_search"},
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
        validate_research_thesis(thesis, tools_called={"list_round_results", "web_search"})

    assert exc_info.value.rejection_code == "structural_missing_thesis_id"
    assert "failures" not in exc_info.value.evidence


def test_removed_theme_cluster_no_longer_preempts_mechanical_failure() -> None:
    thesis = _thesis(thesis_id="", theme_keywords=["opening_noise"])

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=_cluster_priors(),
            tools_called={"list_round_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "structural_missing_thesis_id"


def test_behavioral_pass_uses_first_remaining_signal_after_theme_cluster_removal() -> None:
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
            tools_called={"list_round_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "thesis_quality_needs_code_starvation"


def test_rethink_class_1c_config_overlap_fires_before_mechanical() -> None:
    thesis = _thesis(thesis_id="", config_changes={"opening_session_filter": "late"})
    priors = [_prior("ema-prior-same-key", config_changes={"opening_session_filter": "early"})]

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=priors,
            tools_called={"list_round_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "config_validity_config_key_overlap_real"


def test_neighboring_threshold_is_prioritized_over_config_overlap() -> None:
    thesis = _thesis(thesis_id="", config_changes={"opening_skip_minutes": 8})
    priors = [_prior("ema-prior-overlap-and-neighbor", config_changes={"opening_skip_minutes": 5})]

    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(
            thesis,
            prior_theses=priors,
            tools_called={"list_round_results", "web_search"},
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
            tools_called={"list_round_results", "web_search"},
        )

    assert exc_info.value.rejection_code == "config_validity_neighboring_threshold"


def test_mechanical_ignores_falsification_length_when_behavioral_silent() -> None:
    thesis = _thesis(falsification_or_alternative="too short")

    validated = validate_research_thesis(
        thesis,
        tools_called={"list_round_results", "web_search"},
    )

    assert validated.thesis_id == "ema-tier-batching-v1"
