"""Exhaustive gate coverage for thesis_validator.

For every validator gate, construct the minimal thesis that should trip ONLY
that gate, validate it, and assert the expected ``rejection_code``.

Purpose:
  * Prove every gate is actually reachable (no dead code).
  * Catch gates that fail silently or fire under the wrong code.
  * Catch gate-ordering bugs where one gate shadows another and the shadowed
    gate can never fire in practice.
  * Document each gate's trigger condition by example (the test body is the
    spec).

Tests are organized to mirror the validator's three sub-sections plus
Stage 2 (post-compile). Each test mutates ONE field of a known-valid
baseline thesis to isolate the gate under test.

If a gate is dead (unreachable) or its rejection_code drifts, the
corresponding test fails loudly with a precise diagnostic.
"""

from __future__ import annotations

from typing import Any

import pytest

from research_types import Disqualifier, EvidenceCitation, ExpectedEffect, ResearchThesis
from thesis_validator import (
    ThesisValidationError,
)
from thesis_validator import validate_research_thesis as _validate_research_thesis

# ---------------------------------------------------------------------------
# Baselines: production-grade names (ema family), realistic values throughout.
# ---------------------------------------------------------------------------

STRATEGY_FAMILY = "ema"
SAMPLE_THESIS_ID = "ema-opening-noise-skip-v1"
SAMPLE_MECHANISM_DIMENSION = "entry_timing"
from thesis_validator import VALID_PROCESS_TOOLS as _VALID_PROCESS_TOOLS


def validate_research_thesis(*args: Any, **kwargs: Any) -> ResearchThesis:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    return _validate_research_thesis(*args, **kwargs)


_VALID_FALSIFICATION_TEXT = (
    "If opening-minute losses do not decrease after the skip, the auction-noise "
    "hypothesis is wrong; the real cause may be position sizing."
)


def _minimal_valid_thesis(**overrides: Any) -> ResearchThesis:
    """Return a ResearchThesis that passes every gate (assuming no priors)."""
    base = {
        "thesis_id": SAMPLE_THESIS_ID,
        "strategy_family": STRATEGY_FAMILY,
        "hypothesis": "Skipping the first 5 minutes avoids opening-auction noise.",
        "mechanism": "Opening liquidity is thin; signals there are mostly noise.",
        "mechanism_dimension": SAMPLE_MECHANISM_DIMENSION,
        "dimension_novelty": "Tests session-window conditioning, not threshold tuning.",
        "config_changes": {"opening_skip_minutes": 5},
        "expected_effects": [
            ExpectedEffect(
                metric="profit_factor",
                direction="increase",
                rationale="Avoiding opening-auction noise should improve realized edge.",
            ),
            ExpectedEffect(
                metric="trade_count",
                direction="decrease_or_same",
                rationale="The timing filter should remove noisy trades without collapsing activity.",
            ),
        ],
        "disqualifiers": [
            Disqualifier(
                name="opening_loss_pattern",
                condition=(
                    "first-5min loss rate not concentrated in the opening " "auction window"
                ),  # ≥40 chars to satisfy mechanism_evidence substantiveness gate
                kind="mechanism_evidence",
            )
        ],
        # falsification_or_alternative is unconditionally required now —
        # used to be optional, which let the doctrine ("every thesis needs a
        # disconfirmer") go unenforced. The valid baseline must include it.
        "falsification_or_alternative": _VALID_FALSIFICATION_TEXT,
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": (
                    "Would retune risk after entry instead of testing whether entry timing "
                    "is the root cause of noisy opening losses."
                ),
            },
            {
                "mechanism": "trend-strength filter",
                "why_rejected": (
                    "Would filter for directional continuation but would not isolate the "
                    "opening-auction liquidity mechanism."
                ),
            },
        ],
        "evidence_citations": [
            EvidenceCitation(
                source="web_search",
                citation="Market microstructure literature links opening auctions with wider spreads.",
            ),
            EvidenceCitation(
                source="analyst",
                citation="round-1 analyst: losses cluster in the first five regular-session minutes.",
            ),
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame builds the EMA entry "
            "signals that the opening-skip thesis modifies."
        ),
    }
    base.update(overrides)
    return ResearchThesis(**base)


def _prior(
    thesis_id: str,
    *,
    config_changes: dict[str, Any] | None = None,
    mechanism_dimension: str = SAMPLE_MECHANISM_DIMENSION,
    theme_keywords: list[str] | None = None,
    requires_code_change: bool = False,
    outcome: str = "completed",
    validator_status: str = "compiled",
) -> dict[str, Any]:
    """Return a prior-thesis dict shaped like rows from BacktestRunDB."""
    return {
        "thesis_id": thesis_id,
        "config_changes": config_changes or {},
        "mechanism_dimension": mechanism_dimension,
        "outcome": outcome,
        "validator_status": validator_status,
        "thesis_details": {
            "theme_keywords": theme_keywords or [],
            "requires_code_change": requires_code_change,
        },
    }


def _assert_baseline_passes() -> None:
    """Sanity: the unmutated baseline must validate cleanly (no priors)."""
    validate_research_thesis(_minimal_valid_thesis())


def test_baseline_thesis_passes_all_gates_with_no_priors() -> None:
    """Anchors every other test — if this fails, the mutations below are
    meaningless."""
    _assert_baseline_passes()


# ---------------------------------------------------------------------------
# Helper to extract the rejection_code from a raised ThesisValidationError.
# ---------------------------------------------------------------------------


def _expect_rejection(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None,
    expected_code: str,
) -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(thesis, prior_theses=prior_theses)
    actual = exc_info.value.rejection_code
    assert actual == expected_code, (
        f"Expected rejection_code='{expected_code}' but got '{actual}'. "
        f"Message: {exc_info.value!s}"
    )


def _expect_acceptance(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> None:
    validate_research_thesis(thesis, prior_theses=prior_theses, **kwargs)


# ===========================================================================
# Stage 1A — Structural (18 gates)
# ===========================================================================


def test_gate_structural_missing_thesis_id() -> None:
    thesis = _minimal_valid_thesis(thesis_id="   ")
    _expect_rejection(thesis, None, "structural_missing_thesis_id")


def test_gate_structural_missing_hypothesis() -> None:
    thesis = _minimal_valid_thesis(hypothesis="")
    _expect_rejection(thesis, None, "structural_missing_hypothesis")


def test_gate_structural_missing_mechanism() -> None:
    thesis = _minimal_valid_thesis(mechanism="")
    _expect_rejection(thesis, None, "structural_missing_mechanism")


def test_gate_structural_missing_mechanism_dimension_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(mechanism_dimension="")
    _expect_acceptance(thesis)


def test_gate_structural_mechanism_dimension_invalid_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(mechanism_dimension="totally_not_a_dimension")
    _expect_acceptance(thesis)


def test_gate_structural_emergent_missing_new_dimension_name_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        mechanism_dimension="emergent",
        new_dimension_name="",
    )
    _expect_acceptance(thesis)


def test_gate_structural_emergent_duplicates_core_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        mechanism_dimension="emergent",
        new_dimension_name="entry_timing",  # a core dimension
    )
    _expect_acceptance(thesis)


def test_gate_structural_emergent_short_fields_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        mechanism_dimension="emergent",
        new_dimension_name="liquidity_regime_classifier",
        why_existing_dimensions_do_not_fit="short",  # <40 chars
        mechanism_family_definition="x" * 50,
        expected_reuse_across_future_theses="x" * 50,
    )
    _expect_acceptance(thesis)


def test_gate_structural_dimension_novelty_empty_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(dimension_novelty="")
    validate_research_thesis(thesis)


def test_gate_structural_dimension_novelty_too_short_without_priors_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(dimension_novelty="x")  # 1 char, no priors
    validate_research_thesis(thesis)


def test_gate_structural_missing_causal_cluster_when_priors_exist_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(causal_cluster="")
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_acceptance(thesis, priors)


def test_gate_structural_underexplored_dimensions_empty_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=[],
    )
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_acceptance(thesis, priors)


def test_gate_structural_underexplored_dimensions_garbage_value_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["not_a_real_dimension"],
    )
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_acceptance(thesis, priors)


def test_gate_structural_underexplored_dimensions_includes_chosen_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=[SAMPLE_MECHANISM_DIMENSION, "risk_structure"],
    )
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_acceptance(thesis, priors)


def test_gate_structural_novel_connection_too_short_when_overlap_high_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        theme_keywords=["opening", "stop_distance"],
        # LLM-self-reported field is ignored now; left empty intentionally.
        dominant_cluster_overlap="",
        novel_connection="too short",
    )
    priors = [
        _prior(
            "ema-prior-a",
            config_changes={"some_other_key": 10},
            theme_keywords=["opening", "alpha"],
        ),
        _prior(
            "ema-prior-b",
            config_changes={"different_key": 20},
            theme_keywords=["stop_distance", "beta"],
        ),
    ]
    _expect_acceptance(thesis, priors)


def test_gate_structural_missing_config_or_code_change() -> None:
    thesis = _minimal_valid_thesis(
        config_changes={},
        requires_code_change=False,
    )
    _expect_rejection(thesis, None, "structural_missing_config_or_code_change")


def test_gate_structural_missing_requested_primitives() -> None:
    thesis = _minimal_valid_thesis(
        config_changes={},
        requires_code_change=True,
        requested_primitives=[],
    )
    _expect_rejection(thesis, None, "structural_missing_requested_primitives")


def test_gate_structural_missing_expected_effects() -> None:
    thesis = _minimal_valid_thesis(expected_effects=[])
    _expect_rejection(thesis, None, "structural_missing_expected_effects")


def test_gate_structural_falsification_empty_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(falsification_or_alternative="")
    validate_research_thesis(thesis)


def test_gate_structural_falsification_too_short_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        falsification_or_alternative="short text",  # set but <80 chars
    )
    validate_research_thesis(thesis)


def test_gate_structural_missing_disqualifiers() -> None:
    thesis = _minimal_valid_thesis(disqualifiers=[])
    _expect_rejection(thesis, None, "structural_missing_disqualifiers")


def test_gate_structural_expected_effect_metric_unbacked() -> None:
    thesis = _minimal_valid_thesis(
        expected_effects=[ExpectedEffect(metric="totally_custom_metric", direction="increase")],
        required_diagnostics=[],  # custom metric not declared
    )
    _expect_rejection(thesis, None, "structural_expected_effect_metric_unbacked")


def test_gate_structural_missing_evidence_strength_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(evidence_strength="")
    validate_research_thesis(thesis)


def test_gate_structural_missing_alternatives_considered_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(alternatives_considered=[])
    validate_research_thesis(thesis)


def test_gate_structural_alternatives_considered_blank_mechanisms_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        alternatives_considered=[
            {
                "mechanism": " ",
                "why_rejected": (
                    "This is not a real named alternative mechanism and should not "
                    "satisfy the multi-candidate gate."
                ),
            },
            {
                "mechanism": "",
                "why_rejected": (
                    "This also omits the rejected mechanism name while padding the "
                    "rejection explanation enough to pass schema length checks."
                ),
            },
        ]
    )

    _expect_acceptance(thesis)


def test_gate_structural_missing_evidence_citations_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(evidence_citations=[])
    _expect_acceptance(thesis)


def test_gate_structural_evidence_citations_missing_analyst_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        evidence_citations=[
            EvidenceCitation(
                source="web_search",
                citation="Market microstructure literature links opening auctions with wider spreads.",
            )
        ]
    )
    _expect_acceptance(thesis)


def test_gate_structural_evidence_citations_allow_experiment_result_when_no_trades() -> None:
    thesis = _minimal_valid_thesis(
        evidence_citations=[
            EvidenceCitation(
                source="web_search",
                citation="Market microstructure literature links opening auctions with wider spreads.",
            ),
            EvidenceCitation(
                source="round_result",
                citation="latest experiment rejected before writing a trades artifact.",
            ),
        ]
    )

    validate_research_thesis(thesis, evidence_context="no_trades")


def test_gate_structural_evidence_citations_no_trades_no_longer_requires_experiment_result() -> (
    None
):
    thesis = _minimal_valid_thesis(
        evidence_citations=[
            EvidenceCitation(
                source="web_search",
                citation="Market microstructure literature links opening auctions with wider spreads.",
            )
        ]
    )

    _expect_acceptance(thesis, evidence_context="no_trades")


def test_gate_structural_evidence_citations_cold_start_allows_web_only() -> None:
    thesis = _minimal_valid_thesis(
        evidence_citations=[
            EvidenceCitation(
                source="web_search",
                citation="Market microstructure literature links opening auctions with wider spreads.",
            )
        ]
    )

    validate_research_thesis(thesis, evidence_context="cold_start")


def test_gate_structural_source_code_verification_requires_real_file_and_symbol() -> None:
    thesis = _minimal_valid_thesis(
        source_code_verification=(
            "strategies/ema/signals.py:not_a_real_symbol allegedly contains the entry filter."
        )
    )
    _expect_rejection(thesis, None, "structural_source_code_verification_invalid")


def test_gate_structural_source_code_verification_requires_strategy_family_file() -> None:
    thesis = _minimal_valid_thesis(
        source_code_verification=(
            "strategies/orb/signals.py:generate_orb_signals belongs to a different family."
        )
    )
    _expect_rejection(thesis, None, "structural_source_code_verification_invalid")


def test_gate_structural_expected_effects_need_coupled_predictions() -> None:
    thesis = _minimal_valid_thesis(
        expected_effects=[
            ExpectedEffect(
                metric="profit_factor",
                direction="increase",
                rationale="Avoiding opening-auction noise should improve realized edge.",
            )
        ]
    )
    _expect_rejection(thesis, None, "structural_expected_effects_not_coupled")


def test_disqualifiers_fire_before_expected_effects_metric_unbacked() -> None:
    """Regression: a thesis missing disqualifiers AND with an unbacked
    expected_effects metric must report both mechanical failures.

    The current validator batches mechanical failures, so this pins the
    non-process assertions inside the batch evidence rather than expecting a
    process or fail-fast rejection code.
    """
    thesis = _minimal_valid_thesis(
        expected_effects=[
            ExpectedEffect(metric="non_builtin_made_up_metric", direction="increase"),
        ],
        required_diagnostics=[],  # metric NOT backed
        disqualifiers=[],  # also missing
    )
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_research_thesis(thesis)

    assert exc_info.value.rejection_code == "structural_mechanical_batch_failures"
    failure_codes = {failure["code"] for failure in exc_info.value.evidence["failures"]}
    assert failure_codes == {
        "structural_missing_disqualifiers",
        "structural_expected_effect_metric_unbacked",
    }


# ===========================================================================
# Stage 1B — Thesis quality (7 gates)
# ===========================================================================


def test_prior_winner_language_no_longer_a_gate() -> None:
    """The prior-winner language regex gate was removed.

    Reason: behavioral text detection had high false-negative rate (paraphrasing
    around the patterns trivially evaded it), and actual inheritance is caught
    structurally by gates `config_validity_base_contract_id_not_allowed` and
    `config_validity_base_config_path_inheritance_blocked`. This test pins the
    removal so the gate isn't accidentally reintroduced.
    """
    # A thesis using formerly-banned language should now pass on the language
    # check alone (other gates still apply, but the regex gate is gone).
    thesis = _minimal_valid_thesis(
        hypothesis=(
            "Building on the current best config, tighten the stops based on "
            "the winning configuration from the prior experiment."
        ),
    )
    # No exception — gate 19 is gone.
    validate_research_thesis(thesis)


def test_gate_thesis_quality_theme_cluster_fixation_no_longer_rejects() -> None:
    """Theme repetition is no longer a validator gate.

    This setup has high computed theme overlap (3 of 4 priors share keywords)
    but the v2 objective and population dedupe handle fixation instead of
    rejecting on keyword sets.
    """
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        theme_keywords=["opening", "stop_distance"],
        novel_connection=(
            "Reframes opening-session as a regime-conditioning question instead of a "
            "threshold-tuning one, which prior theses did not consider."
        ),
    )
    priors = [
        _prior("ema-p1", config_changes={"k1": 1}, theme_keywords=["opening", "alpha"]),
        _prior("ema-p2", config_changes={"k2": 1}, theme_keywords=["opening", "beta"]),
        _prior("ema-p3", config_changes={"k3": 1}, theme_keywords=["opening", "gamma"]),
        _prior("ema-p4", config_changes={"k4": 1}, theme_keywords=["delta"]),
    ]
    _expect_acceptance(thesis, priors)


def test_gate_thesis_quality_needs_code_starvation() -> None:
    """3 consecutive priors with requires_code_change=true and no run."""
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        config_changes={},
        requires_code_change=True,
        requested_primitives=["new_primitive_x"],
    )
    priors = [
        _prior(
            "ema-cp1",
            config_changes={"k1": 1},
            requires_code_change=True,
            outcome="needs_code",
        ),
        _prior(
            "ema-cp2",
            config_changes={"k2": 1},
            requires_code_change=True,
            outcome="needs_code",
        ),
        _prior(
            "ema-cp3",
            config_changes={"k3": 1},
            requires_code_change=True,
            outcome="needs_code",
        ),
    ]
    _expect_rejection(thesis, priors, "thesis_quality_needs_code_starvation")


def test_gate_thesis_quality_direction_whipsaw_no_longer_rejects() -> None:
    """Direction-token whipsaw is prompt guidance, not a validator gate.

    The old heuristic inferred tighten/widen intent from thesis IDs. The v2
    path removes that text gate and lets the causal objective decide whether
    the mechanism adds predictive skill.
    """
    thesis = _minimal_valid_thesis(
        thesis_id="ema-loosen-stops-v1",  # 'loosen' only (widen token)
        causal_cluster="stop-distance",
        underexplored_dimensions_considered=["risk_structure"],
        theme_keywords=["stop_distance"],
        prior_lever_outcomes=[],  # no citation
        config_changes={"different_key": 0.01},
        # The single prior shares the theme keyword → 100% computed overlap →
        # structural novel_connection gate would fire first. Satisfy it so the
        # later whipsaw quality gate is the one that trips.
        novel_connection=(
            "Stop-distance lever is being approached as a regime-dependent floor "
            "rather than the absolute threshold tested previously."
        ),
    )
    priors = [
        _prior(
            "ema-tighten-stops-v0",  # 'tighten' only (tighten token)
            config_changes={"some_other_key": 5},
            theme_keywords=["stop_distance"],
        ),
    ]
    _expect_acceptance(thesis, priors)


def test_gate_thesis_quality_missing_mechanism_evidence_disqualifier_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        disqualifiers=[
            Disqualifier(
                name="metric_only",
                condition="profit_factor < 1.0",
                kind="metric_threshold",  # not mechanism_evidence
            )
        ],
    )
    validate_research_thesis(thesis)


def test_gate_thesis_quality_mechanism_evidence_disqualifier_too_short_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        disqualifiers=[
            Disqualifier(
                name="trivial",
                condition="x",  # too short
                kind="mechanism_evidence",
            )
        ],
    )
    validate_research_thesis(thesis)


def test_gate_structural_repeated_llm_thesis_id_no_longer_rejects() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
    )
    priors = [
        _prior(
            SAMPLE_THESIS_ID,  # same as current
            config_changes={"some_other_key": 10},
        ),
    ]
    validated = validate_research_thesis(thesis, priors)
    assert validated.thesis_id == SAMPLE_THESIS_ID


def test_gate_structural_dimension_novelty_too_short_with_same_dim_priors_no_longer_rejects() -> (
    None
):
    thesis = _minimal_valid_thesis(
        thesis_id="ema-new-v1",
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        dimension_novelty="too short",  # <30 chars
        config_changes={"different_key": 7},
    )
    priors = [
        _prior(
            "ema-prior-same-dim",
            config_changes={"some_other_key": 10},
            mechanism_dimension=SAMPLE_MECHANISM_DIMENSION,
        ),
    ]
    _expect_acceptance(thesis, priors)


# ===========================================================================
# Stage 1C — Config validity (8 gates)
# ===========================================================================


def test_gate_config_validity_base_config_path_invalid_extension() -> None:
    thesis = _minimal_valid_thesis(base_config_path="configs/foo.txt")
    _expect_rejection(thesis, None, "config_validity_base_config_path_invalid")


def test_gate_config_validity_base_config_path_invalid_absolute() -> None:
    thesis = _minimal_valid_thesis(base_config_path="/abs/path/foo.json")
    _expect_rejection(thesis, None, "config_validity_base_config_path_invalid")


def test_gate_config_validity_base_config_path_runtime_construction() -> None:
    thesis = _minimal_valid_thesis(base_config_path="runtime/jobs/job-1/foo.json")
    _expect_rejection(thesis, None, "config_validity_base_config_path_runtime_construction")


def test_gate_config_validity_experiments_path_routes_to_inheritance_blocked() -> None:
    """The `experiments/` path is no longer special-cased. It falls through
    to the general inheritance_blocked gate that rejects ANY non-baseline path."""
    thesis = _minimal_valid_thesis(base_config_path="experiments/foo.json")
    _expect_rejection(thesis, None, "config_validity_base_config_path_inheritance_blocked")


def test_gate_config_validity_base_contract_id_not_allowed() -> None:
    thesis = _minimal_valid_thesis(base_contract_id="prior-contract-abc123")
    _expect_rejection(thesis, None, "config_validity_base_contract_id_not_allowed")


def test_gate_config_validity_base_config_path_inheritance_blocked() -> None:
    # A configs/-prefixed path that ISN'T the family baseline.
    thesis = _minimal_valid_thesis(base_config_path="configs/some_other_config.json")
    _expect_rejection(thesis, None, "config_validity_base_config_path_inheritance_blocked")


def test_gate_config_validity_config_changes_metadata_leak() -> None:
    thesis = _minimal_valid_thesis(
        config_changes={"requires_code_change": True, "real_key": 5},
    )
    _expect_rejection(thesis, None, "config_validity_config_changes_metadata_leak")


def test_gate_config_validity_config_key_overlap_real() -> None:
    """Same keys as a prior → Jaccard ≥50%."""
    thesis = _minimal_valid_thesis(
        thesis_id="ema-overlap-v1",
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        dimension_novelty="Materially different mechanism, not a parameter shift here.",
        config_changes={"key_a": 99, "key_b": 88},  # 100% overlap with prior
    )
    priors = [
        _prior("ema-prior-overlap", config_changes={"key_a": 1, "key_b": 2}),
    ]
    _expect_rejection(thesis, priors, "config_validity_config_key_overlap_real")


def test_gate_config_validity_neighboring_threshold() -> None:
    """Same key, value within 2x of prior.

    FINDING — gate ordering: the Jaccard config-key overlap gate (#31) runs
    BEFORE the neighboring-threshold gate (#32) in the live config-validity collector.
    For single-key theses, 100% key overlap fires gate #31 every time, so
    gate #32 is only reachable when multiple keys span both theses AND the
    overall overlap stays under 50%. Below: current has 3 keys, prior has 3
    different keys, one shared → Jaccard = 1/5 = 20% < 50% → gate #31 passes,
    gate #32 then trips on the shared numeric key being within 2x.
    """
    thesis = _minimal_valid_thesis(
        thesis_id="ema-neighbor-v1",
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        dimension_novelty=(
            "Same key but justified by tick-size structural boundary at this value."
        ),
        config_changes={
            "min_stop_distance_pct": 0.01,  # prior was 0.005 → ratio 2.0x (within band)
            "completely_different_key_a": 7,
            "completely_different_key_b": 11,
        },
    )
    priors = [
        _prior(
            "ema-prior-neighbor",
            config_changes={
                "min_stop_distance_pct": 0.005,
                "unrelated_key_x": 1,
                "unrelated_key_y": 2,
            },
        ),
    ]
    _expect_rejection(thesis, priors, "config_validity_neighboring_threshold")


# ===========================================================================
# Stage 2 — Post-compile (covered separately; needs BacktestContract not thesis)
# ===========================================================================
#
# Stage 2 gates take a BacktestContract, not a ResearchThesis. They are tested
# in tests/test_validator_stages.py (existing). This module focuses on Stage 1.
