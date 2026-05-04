from __future__ import annotations

from copy import deepcopy
from typing import Any

from trace_adapters import _emit_adapter_event


def build_reflexio_payload(
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
        "system": "reflexio",
        "episode": {
            "round": research_round,
            "family": family,
            "thesis_id": thesis_id,
            "outcome": outcome,
        },
        "reflection": {
            "reasoning": reasoning,
            "rejection_reason": rejection_reason,
            "quality": deepcopy(quality or {}),
        },
        "resources": {"usage": deepcopy(usage or {})},
    }


def build_reflexio_export_package(**kwargs: Any) -> dict[str, Any]:
    payload = build_reflexio_payload(**kwargs)
    return {
        "target": "reflexio",
        "schema_version": 1,
        "files": {
            "reflexio-event.json": payload,
            "reflexio-summary.json": {
                "thesis_id": payload["episode"]["thesis_id"],
                "outcome": payload["episode"]["outcome"],
                "round": payload["episode"]["round"],
            },
        },
    }


def emit_reflexio_event(
    *,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
) -> dict[str, Any]:
    return _emit_adapter_event(
        "reflexio", action=action, summary=summary, payload=payload, artifact_paths=artifact_paths
    )
