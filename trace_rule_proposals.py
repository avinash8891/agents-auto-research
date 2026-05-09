from __future__ import annotations

from itertools import count
from typing import Any

from trace_sdk import record_event


class RuleProposalRegistry:
    def __init__(self) -> None:
        self._proposal_ids = count(1)

    def create_proposal(
        self,
        *,
        title: str,
        rationale: str,
        evidence_event_ids: list[str],
        expected_impact: str,
        proposed_rule: str,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        proposal_id = f"proposal-{next(self._proposal_ids):04d}"
        payload = {
            "proposal_id": proposal_id,
            "title": title,
            "rationale": rationale,
            "evidence_event_ids": list(evidence_event_ids),
            "expected_impact": expected_impact,
            "proposed_rule": proposed_rule,
            "status": "proposed",
        }
        record_event(
            source_module="trace_rule_proposals",
            category="rule_proposal",
            action="create",
            summary=title,
            payload=payload,
            artifact_paths=artifact_paths,
        )
        return payload

    def update_status(
        self,
        *,
        proposal_id: str,
        status: str,
        resolution_note: str = "",
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "proposal_id": proposal_id,
            "status": status,
            "resolution_note": resolution_note,
        }
        record_event(
            source_module="trace_rule_proposals",
            category="rule_proposal",
            action="status_update",
            summary=f"{proposal_id} -> {status}",
            payload=payload,
            artifact_paths=artifact_paths,
        )
        return payload
