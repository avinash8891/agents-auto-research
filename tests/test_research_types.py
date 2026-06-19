from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_types import MechanismProposal


def _prediction(metric: str, value: float) -> dict[str, object]:
    return {
        "metric": metric,
        "direction": "increase",
        "predicted": value,
        "rationale": f"{metric} should improve",
    }


def _actionable_payload() -> dict[str, object]:
    return {
        "story": "Signed volume separates informed entries.",
        "rule": "signed_volume_z > 1.5",
        "competitor_rule": "signed_volume_z <= 1.5",
        "competitor_story": "Signed volume does not matter.",
        "actionable": True,
        "proposed_change": None,
        "predictions": [
            _prediction("profit_factor", 1.4),
            _prediction("trade_count", 20),
        ],
    }


def test_actionable_mechanism_allows_requested_primitive_without_proposed_change() -> None:
    payload = _actionable_payload()
    payload["requested_primitive"] = {
        "name": "signed_volume_z",
        "kind": "entry_feature",
        "description": "Entry-time z-score of signed volume.",
        "required_data": ["trade_signed_volume"],
    }

    proposal = MechanismProposal.model_validate(payload)

    assert proposal.requested_primitive is not None
    assert proposal.requested_primitive.name == "signed_volume_z"
    assert proposal.proposed_change is None


def test_actionable_mechanism_rejects_without_change_or_requested_primitive() -> None:
    payload = _actionable_payload()

    with pytest.raises(ValidationError, match="proposed_change or requested_primitive"):
        MechanismProposal.model_validate(payload)


def test_requested_primitive_requires_snake_case_name() -> None:
    payload = _actionable_payload()
    payload["requested_primitive"] = {
        "name": "Signed Volume",
        "kind": "entry_feature",
        "description": "Entry-time z-score of signed volume.",
        "required_data": ["trade_signed_volume"],
    }

    with pytest.raises(ValidationError, match="snake_case"):
        MechanismProposal.model_validate(payload)
