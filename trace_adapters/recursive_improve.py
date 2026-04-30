from __future__ import annotations

from copy import deepcopy
from typing import Any

from trace_logger import record_event


def build_recursive_improve_payload(
    *,
    research_round: int,
    thesis_id: str,
    outcome: str,
    family: str,
    reasoning: str = "",
    rejection_reason: str = "",
    quality: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "system": "recursive_improve",
        "iteration_context": {
            "round": research_round,
            "family": family,
            "candidate_id": thesis_id,
            "status": outcome,
        },
        "feedback": {
            "reasoning": reasoning,
            "rejection_reason": rejection_reason,
            "quality": deepcopy(quality or {}),
        },
        "usage": deepcopy(usage or {}),
    }


def build_recursive_improve_export_package(**kwargs: Any) -> dict[str, Any]:
    payload = build_recursive_improve_payload(**kwargs)
    return {
        "target": "recursive_improve",
        "schema_version": 1,
        "files": {
            "recursive-improve-event.json": payload,
            "recursive-improve-summary.json": {
                "candidate_id": payload["iteration_context"]["candidate_id"],
                "status": payload["iteration_context"]["status"],
                "round": payload["iteration_context"]["round"],
            },
        },
    }


def emit_recursive_improve_event(
    *,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
):
    return record_event(
        source_module="trace_adapters.recursive_improve",
        category="recursive_improve",
        action=action,
        summary=summary,
        payload=payload,
        artifact_paths=artifact_paths,
    )
