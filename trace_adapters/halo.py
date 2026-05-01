from __future__ import annotations

from copy import deepcopy
from typing import Any

from trace_sdk import record_event


def build_halo_payload(
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
        "system": "halo",
        "loop_state": {
            "research_round": research_round,
            "strategy_family": family,
            "thesis_id": thesis_id,
            "outcome": outcome,
        },
        "evaluation": {
            "reasoning": reasoning,
            "rejection_reason": rejection_reason,
            "quality": deepcopy(quality or {}),
        },
        "resources": {"usage": deepcopy(usage or {})},
    }


def build_halo_export_package(**kwargs: Any) -> dict[str, Any]:
    payload = build_halo_payload(**kwargs)
    return {
        "target": "halo",
        "schema_version": 1,
        "files": {
            "halo-event.json": payload,
            "halo-summary.json": {
                "thesis_id": payload["loop_state"]["thesis_id"],
                "outcome": payload["loop_state"]["outcome"],
                "research_round": payload["loop_state"]["research_round"],
            },
        },
    }


def emit_halo_event(
    *,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
):
    return record_event(
        source_module="trace_adapters.halo",
        category="halo",
        action=action,
        summary=summary,
        payload=payload,
        artifact_paths=artifact_paths,
    )
