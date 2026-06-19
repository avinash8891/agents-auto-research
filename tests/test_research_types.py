"""Contract tests for the conductor's MechanismProposal output schema.

Three legitimate outcomes must validate (and only those):
- actionable=true  -> rule + proposed_change + >=2 predictions (test a change)
- actionable=false + rule  -> record an insight to the causal model
- actionable=false, rule=None -> decline: no new testable rule this round
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_types import MechanismProposal, Prediction


def _predictions() -> list[Prediction]:
    return [
        Prediction(metric="profit_factor", direction="increase", predicted=2.0),
        Prediction(metric="max_drawdown", direction="decrease", predicted=0.05),
    ]


def test_decline_proposal_allows_null_rule() -> None:
    proposal = MechanismProposal(
        story="No new testable rule is supported beyond the already screened one.",
        rule=None,
        competitor_rule=None,
        competitor_story=None,
        actionable=False,
    )
    assert proposal.actionable is False
    assert proposal.rule is None


def test_non_actionable_insight_keeps_its_rule() -> None:
    proposal = MechanismProposal(
        story="Worth recording, not acting on yet.",
        rule="gap_pct < 0",
        competitor_rule="gap_pct > 0",
        competitor_story="competing read",
        actionable=False,
    )
    assert proposal.actionable is False
    assert proposal.rule == "gap_pct < 0"


def test_actionable_requires_rule() -> None:
    with pytest.raises(ValidationError, match="rule is required when actionable"):
        MechanismProposal(
            story="s",
            rule=None,
            competitor_rule=None,
            competitor_story=None,
            actionable=True,
            proposed_change={"gap_filter": True},
            predictions=_predictions(),
        )


def test_actionable_with_rule_change_and_predictions_validates() -> None:
    proposal = MechanismProposal(
        story="Gap-down opens snap back.",
        rule="side == 'short' and gap_pct < 0",
        competitor_rule="side == 'short' and gap_pct > 0",
        competitor_story="competing read",
        actionable=True,
        proposed_change={"gap_filter": True},
        predictions=_predictions(),
    )
    assert proposal.actionable is True
    assert proposal.proposed_change == {"gap_filter": True}


def test_actionable_with_requested_primitive_and_no_proposed_change_validates() -> None:
    """A thesis whose rule needs a NEW strategy capability sets requested_primitive
    and leaves proposed_change null; the builder implements the rule. This must
    validate (route-to-builder path)."""
    proposal = MechanismProposal(
        story="Needs a conjunctive exclusion no existing lever expresses.",
        rule="side == 'short' and bars_since_open == 0 and gap_pct < 0",
        competitor_rule="side == 'short' and gap_pct > 0",
        competitor_story="competing read",
        actionable=True,
        requested_primitive="gap_down_early_short_exclusion",
        proposed_change=None,
        predictions=_predictions(),
    )
    assert proposal.requested_primitive == "gap_down_early_short_exclusion"
    assert proposal.proposed_change is None


def test_actionable_requires_change_or_primitive() -> None:
    with pytest.raises(ValidationError, match="proposed_change or requested_primitive"):
        MechanismProposal(
            story="s",
            rule="side == 'short'",
            competitor_rule="side == 'long'",
            competitor_story="c",
            actionable=True,
            predictions=_predictions(),
        )


def test_research_thesis_carries_mechanism_rule_across_boundary() -> None:
    """ResearchThesis ignores extras, so the conductor's df.query rule must live
    in a dedicated field to survive into builder_request/thesis.json (where the
    builder reads it). Without this the builder only saw prose, not the rule."""
    from research_types import ResearchThesis

    thesis = ResearchThesis.model_validate(
        {
            "thesis_id": "t1",
            "strategy_family": "ema",
            "hypothesis": "h",
            "mechanism": "m",
            "requires_code_change": True,
            "requested_primitives": ["gap_down_early_short_exclusion"],
            "mechanism_rule": "side == 'short' and bars_since_open == 0 and gap_pct < 0",
            "rule": "DROPPED-EXTRA",  # extra field is ignored by the model
        }
    )
    assert thesis.mechanism_rule == "side == 'short' and bars_since_open == 0 and gap_pct < 0"
    assert "mechanism_rule" in thesis.model_dump()  # persists into thesis.json
