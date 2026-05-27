"""Tests for the behavior signal + policy abstraction.

The module is a pure data + decision layer with no dependencies on
the validator or any external state. Tests cover signal construction
and policy decision-making in isolation.
"""

from __future__ import annotations

from behavior_signals import BehaviorSignal, PolicyDecision, decide


def test_behavior_signal_is_frozen_and_carries_all_required_fields() -> None:
    """BehaviorSignal must be immutable so it can flow safely through the
    policy layer without surprise mutation."""
    sig = BehaviorSignal(
        code="thesis_quality_theme_cluster_fixation",
        confidence=0.83,
        severity="block",
        summary="4 of last 7 share keywords",
        evidence={"overlap_count": 4},
        remediation=("Propose from a different mechanism dimension",),
    )
    assert sig.code == "thesis_quality_theme_cluster_fixation"
    assert sig.confidence == 0.83
    assert sig.severity == "block"
    import dataclasses
    # frozen=True: attempting to set must raise.
    try:
        sig.code = "different"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("BehaviorSignal must be frozen")


def test_decide_accepts_when_no_signals_present() -> None:
    decision = decide([])
    assert decision.action == "accept"
    assert decision.rejection_code == ""
    assert decision.signals == ()


def test_decide_rejects_when_one_signal_present() -> None:
    sig = BehaviorSignal(
        code="thesis_quality_theme_cluster_fixation",
        confidence=0.71,
        severity="block",
        summary="x",
    )
    decision = decide([sig])
    assert decision.action == "reject"
    assert decision.rejection_code == "thesis_quality_theme_cluster_fixation"
    assert decision.signals == (sig,)


def test_decide_rejects_with_first_signal_code_when_multiple_present() -> None:
    """Default policy: first signal wins for the rejection_code so the
    behavior matches the pre-refactor validator (which raised on the first
    check that fired)."""
    first = BehaviorSignal(code="thesis_quality_theme_cluster_fixation", confidence=1.0, severity="block", summary="")
    second = BehaviorSignal(code="thesis_quality_direction_whipsaw", confidence=1.0, severity="block", summary="")
    decision = decide([first, second])
    assert decision.action == "reject"
    assert decision.rejection_code == "thesis_quality_theme_cluster_fixation"
    assert decision.triggering == first
    assert decision.signals == (first, second)


def test_policy_decision_action_is_typed_literal() -> None:
    """A decision's action must be one of the three documented values."""
    decision = PolicyDecision(action="accept")
    assert decision.action in ("accept", "accept_with_warning", "reject")


def test_theme_cluster_fixation_detector_returns_signal_when_pattern_fires() -> None:
    """When 4 of last 7 priors share keywords with the proposal, the detector
    returns a signal. Signal carries the same code that the pre-refactor
    raise produced."""
    from research_types import (
        ExpectedEffect, Disqualifier, ResearchThesis,
    )
    from thesis_validator import _detect_theme_cluster_fixation

    proposal = ResearchThesis(
        thesis_id="ema-new-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        theme_keywords=["opening", "stop_distance"],
        config_changes={"some_key": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    def prior(thesis_id: str, kw: list[str]) -> dict:
        return {
            "thesis_id": thesis_id,
            "config_changes": {f"k_{thesis_id}": 1},
            "outcome": "compiled",
            "thesis_details": {"theme_keywords": kw},
        }
    priors = [
        prior("p1", ["opening", "a"]),
        prior("p2", ["opening", "b"]),
        prior("p3", ["opening", "c"]),
        prior("p4", ["d"]),
    ]
    sig = _detect_theme_cluster_fixation(proposal, priors)
    assert sig is not None
    assert sig.code == "thesis_quality_theme_cluster_fixation"
    assert sig.severity == "block"
    assert sig.confidence > 0.5  # >=4/7 overlap
    assert "overlap_count" in sig.evidence
    assert sig.evidence["overlap_count"] == 4


def test_theme_cluster_fixation_detector_returns_none_when_pattern_absent() -> None:
    """When fewer than 4 priors share keywords, the detector returns None."""
    from research_types import (
        ExpectedEffect, Disqualifier, ResearchThesis,
    )
    from thesis_validator import _detect_theme_cluster_fixation

    proposal = ResearchThesis(
        thesis_id="ema-new-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        theme_keywords=["unique"],
        config_changes={"some_key": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    priors = [{"thesis_id": "p1", "config_changes": {}, "thesis_details": {"theme_keywords": ["other"]}}]
    assert _detect_theme_cluster_fixation(proposal, priors) is None


def test_needs_code_starvation_detector_returns_signal_at_streak_3() -> None:
    """Three consecutive priors with requires_code_change=true and no run
    in between → signal fired."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_needs_code_starvation

    proposal = ResearchThesis(
        thesis_id="ema-new-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={},
        requires_code_change=True,
        requested_primitives=["new_primitive"],
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    def code_prior(thesis_id: str) -> dict:
        return {
            "thesis_id": thesis_id,
            "config_changes": {f"k_{thesis_id}": 1},
            "outcome": "needs_code",
            "thesis_details": {"requires_code_change": True},
        }
    priors = [code_prior("p1"), code_prior("p2"), code_prior("p3")]
    sig = _detect_needs_code_starvation(proposal, priors)
    assert sig is not None
    assert sig.code == "thesis_quality_needs_code_starvation"
    assert sig.severity == "block"
    assert sig.confidence == 1.0


def test_direction_whipsaw_detector_returns_signal_on_flip() -> None:
    """Prior tightened a theme; this thesis loosens it without citation."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_direction_whipsaw

    proposal = ResearchThesis(
        thesis_id="ema-loosen-stops-v1",
        strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        theme_keywords=["stop_distance"],
        prior_lever_outcomes=[],
        config_changes={"different_key": 1},
        novel_connection=(
            "Stop-distance lever is approached as a regime-dependent floor "
            "rather than an absolute threshold tested previously."
        ),
        causal_cluster="stop-distance",
        underexplored_dimensions_considered=["risk_structure"],
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y", kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    prior = {
        "thesis_id": "ema-tighten-stops-v0",
        "config_changes": {"some_other_key": 5},
        "outcome": "compiled",
        "thesis_details": {"theme_keywords": ["stop_distance"]},
    }
    sig = _detect_direction_whipsaw(proposal, [prior])
    assert sig is not None
    assert sig.code == "thesis_quality_direction_whipsaw"
    assert sig.severity == "block"


def test_missing_mechanism_evidence_disqualifier_detector_fires_when_all_metric_threshold() -> None:
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_missing_mechanism_evidence_disqualifier

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="y" * 100, kind="metric_threshold")],
        falsification_or_alternative="z" * 100,
    )
    sig = _detect_missing_mechanism_evidence_disqualifier(proposal)
    assert sig is not None
    assert sig.code == "thesis_quality_missing_mechanism_evidence_disqualifier"
    assert sig.severity == "block"


def test_missing_mechanism_evidence_disqualifier_detector_returns_none_when_substantive_present() -> None:
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _detect_missing_mechanism_evidence_disqualifier

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="z" * 50, kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    assert _detect_missing_mechanism_evidence_disqualifier(proposal) is None


def test_validate_thesis_quality_translates_policy_reject_to_raise() -> None:
    """End-to-end: when a detector fires, the validator raises with the
    detector's code (mediated by the policy)."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import ThesisValidationError, _validate_thesis_quality

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        # Only metric_threshold → mechanism_evidence detector fires
        disqualifiers=[Disqualifier(name="x", condition="y" * 100, kind="metric_threshold")],
        falsification_or_alternative="z" * 100,
    )
    import pytest
    with pytest.raises(ThesisValidationError) as exc_info:
        _validate_thesis_quality(proposal, prior_theses=[])
    assert exc_info.value.rejection_code == "thesis_quality_missing_mechanism_evidence_disqualifier"


def test_validate_thesis_quality_does_not_raise_when_no_signals_fire() -> None:
    """End-to-end: with no signals, the validator returns without raising."""
    from research_types import ExpectedEffect, Disqualifier, ResearchThesis
    from thesis_validator import _validate_thesis_quality

    proposal = ResearchThesis(
        thesis_id="ema-x", strategy_family="ema",
        hypothesis="x", mechanism="x",
        mechanism_dimension="entry_timing",
        dimension_novelty="x" * 50,
        config_changes={"k": 1},
        expected_effects=[ExpectedEffect(metric="profit_factor", direction="increase")],
        disqualifiers=[Disqualifier(name="x", condition="z" * 50, kind="mechanism_evidence")],
        falsification_or_alternative="z" * 100,
    )
    _validate_thesis_quality(proposal, prior_theses=[])  # no raise expected
