from __future__ import annotations

from itertools import count
from typing import Any

from trace_sdk import record_event


class AutonomyLedger:
    def __init__(self) -> None:
        self._decision_ids = count(1)
        self._audit_ids = count(1)

    def record_decision(
        self,
        *,
        summary: str,
        decision_type: str,
        score: float | None,
        graduation_status: str,
        outcome: str,
        rationale: str,
        constraints: list[str] | None = None,
        evidence_event_ids: list[str] | None = None,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        decision_id = f"decision-{next(self._decision_ids):04d}"
        payload = {
            "decision_id": decision_id,
            "decision_type": decision_type,
            "score": score,
            "graduation_status": graduation_status,
            "outcome": outcome,
            "rationale": rationale,
            "constraints": list(constraints or []),
            "evidence_event_ids": list(evidence_event_ids or []),
        }
        record_event(
            source_module="trace_autonomy_ledger",
            category="autonomy",
            action="decision",
            summary=summary,
            payload=payload,
            artifact_paths=list(artifact_paths or []),
        )
        return payload

    def record_audit(
        self,
        *,
        summary: str,
        action: str,
        approval_status: str,
        actor: str,
        related_decision_id: str | None = None,
        notes: str = "",
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        audit_id = f"audit-{next(self._audit_ids):04d}"
        payload = {
            "audit_id": audit_id,
            "action": action,
            "approval_status": approval_status,
            "actor": actor,
            "related_decision_id": related_decision_id,
            "notes": notes,
        }
        record_event(
            source_module="trace_autonomy_ledger",
            category="autonomy",
            action="audit",
            summary=summary,
            payload=payload,
            artifact_paths=list(artifact_paths or []),
        )
        return payload
