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
