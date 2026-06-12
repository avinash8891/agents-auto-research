"""Tests for the behavior signal + policy abstraction.

The module is a pure data + decision layer with no dependencies on
the validator or any external state. Tests cover signal construction
and policy decision-making in isolation.
"""

from __future__ import annotations

from typing import Any

from behavior_signals import BehaviorSignal, PolicyDecision, decide
from research_types import Disqualifier, ExpectedEffect, ResearchThesis
from thesis_validator import validate_research_thesis


def _make_thesis(**overrides: Any) -> ResearchThesis:
    """Construct a minimal valid ResearchThesis with overrides for the
    field(s) under test. Reduces duplication across detector tests.

    Defaults are chosen to satisfy all structural validators so the
    behavioral detectors (which are what these tests exercise) can be
    isolated.
    """
    defaults: dict[str, Any] = {
        "thesis_id": "ema-x",
        "strategy_family": "ema",
        "hypothesis": "Hypothesis text long enough to satisfy any future length check.",
        "mechanism": "Mechanism text long enough to satisfy any future length check.",
        "mechanism_dimension": "entry_timing",
        "dimension_novelty": "x" * 50,
        "config_changes": {"some_key": 1},
        "expected_effects": [
            ExpectedEffect(
                metric="profit_factor",
                direction="increase",
                rationale="The mechanism should improve realized edge.",
            ),
            ExpectedEffect(
                metric="trade_count",
                direction="decrease_or_same",
                rationale="The filter should reduce but not collapse trade activity.",
            ),
        ],
        "disqualifiers": [Disqualifier(name="x", condition="y" * 50, kind="mechanism_evidence")],
        "falsification_or_alternative": "z" * 100,
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "alternative one",
                "why_rejected": "This alternative is less directly tied to the tested mechanism.",
            },
            {
                "mechanism": "alternative two",
                "why_rejected": "This alternative is useful but weaker for this specific test.",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "external mechanism context"},
            {"source": "analyst", "citation": "trade-level analyst context"},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame builds EMA entry signals."
        ),
    }
    defaults.update(overrides)
    return ResearchThesis(**defaults)


def test_behavior_signal_is_frozen_and_carries_all_required_fields() -> None:
    """BehaviorSignal must be immutable so it can flow safely through the
    policy layer without surprise mutation."""
    sig = BehaviorSignal(
        code="validator_block",
        confidence=0.83,
        severity="block",
        summary="4 of last 7 share keywords",
        evidence={"overlap_count": 4},
        remediation=("Propose from a different mechanism dimension",),
    )
    assert sig.code == "validator_block"
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
        code="validator_block",
        confidence=0.71,
        severity="block",
        summary="x",
    )
    decision = decide([sig])
    assert decision.action == "reject"
    assert decision.rejection_code == "validator_block"
    assert decision.signals == (sig,)


def test_decide_rejects_with_first_signal_code_when_multiple_present() -> None:
    """Default policy: first signal wins for the rejection_code so the
    behavior matches the pre-refactor validator (which raised on the first
    check that fired)."""
    first = BehaviorSignal(
        code="first_validator_block", confidence=1.0, severity="block", summary=""
    )
    second = BehaviorSignal(
        code="second_validator_block", confidence=1.0, severity="block", summary=""
    )
    decision = decide([first, second])
    assert decision.action == "reject"
    assert decision.rejection_code == "first_validator_block"
    assert decision.triggering == first
    assert decision.signals == (first, second)


def test_decide_accepts_with_warning_when_only_warn_signals_present() -> None:
    sig = BehaviorSignal(
        code="thesis_quality_soft_overlap",
        confidence=0.55,
        severity="warn",
        summary="soft overlap",
    )

    decision = decide([sig])

    assert decision.action == "accept_with_warning"
    assert decision.rejection_code == ""
    assert decision.triggering is None
    assert decision.signals == (sig,)
    assert decision.warnings == (sig,)


def test_decide_accepts_when_only_info_signals_present() -> None:
    sig = BehaviorSignal(
        code="thesis_quality_note",
        confidence=0.25,
        severity="info",
        summary="informational note",
    )

    decision = decide([sig])

    assert decision.action == "accept"
    assert decision.signals == (sig,)
    assert decision.warnings == ()


def test_policy_decision_action_is_typed_literal() -> None:
    """A decision's action must be one of the three documented values."""
    decision = PolicyDecision(action="accept")
    assert decision.action in ("accept", "accept_with_warning", "reject")


def test_live_behavioral_pass_no_longer_raises_for_missing_mechanism_evidence() -> None:
    proposal = _make_thesis(
        config_changes={"k": 1},
        disqualifiers=[Disqualifier(name="x", condition="y" * 100, kind="metric_threshold")],
    )
    validated = validate_research_thesis(proposal, prior_theses=[])

    assert validated.thesis_id == proposal.thesis_id


def test_run_behavioral_pass_does_not_raise_when_no_signals_fire() -> None:
    """End-to-end: with no signals, the validator returns without raising."""
    proposal = _make_thesis(
        config_changes={"k": 1},
        disqualifiers=[Disqualifier(name="x", condition="z" * 50, kind="mechanism_evidence")],
    )
    validate_research_thesis(proposal, prior_theses=[])  # no raise expected
