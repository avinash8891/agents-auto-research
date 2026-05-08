from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from reflexio_agent_reflections import build_agent_reflections
from trace_adapters import _emit_adapter_event
from trace_adapters.artifacts import content_from_artifacts, redact_text


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
    trajectory: list[dict[str, Any]] | None = None,
    agent_reflections: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_trajectory = deepcopy(trajectory or [])
    return {
        "system": "reflexio",
        "episode": {
            "round": research_round,
            "family": family,
            "thesis_id": thesis_id,
            "outcome": outcome,
            "research_outcome": outcome,
            "scope": "research_agents",
        },
        "reflection": {
            "reasoning": reasoning,
            "rejection_reason": rejection_reason,
            "quality": deepcopy(quality or {}),
        },
        "agent_reflections": deepcopy(
            agent_reflections
            if agent_reflections is not None
            else build_agent_reflections(resolved_trajectory)
        ),
        "trajectory": resolved_trajectory,
        "feedback_signal": {
            "outcome": outcome,
            "research_outcome": outcome,
            "rejection_reason": rejection_reason,
            "scope": "research_agents",
            "quality": deepcopy(quality or {}),
        },
        "memory_key": f"{family}:round-{research_round}:thesis-{thesis_id}",
        "resources": {"usage": deepcopy(usage or {})},
    }


def build_reflexio_export_package(**kwargs: Any) -> dict[str, Any]:
    canonical_trace_path = kwargs.pop("canonical_trace_path", None)
    trajectory = (
        build_reflexio_trajectory(canonical_trace_path)
        if canonical_trace_path is not None
        else None
    )
    if trajectory is not None:
        kwargs["trajectory"] = trajectory
    payload = build_reflexio_payload(**kwargs)
    files: dict[str, Any] = {
        "reflexio-event.json": payload,
        "reflexio-summary.json": {
            "thesis_id": payload["episode"]["thesis_id"],
            "outcome": payload["episode"]["outcome"],
            "research_outcome": payload["episode"].get(
                "research_outcome", payload["episode"]["outcome"]
            ),
            "scope": payload["episode"].get("scope", "research_agents"),
            "round": payload["episode"]["round"],
        },
    }
    if trajectory is not None:
        files["reflexio-trajectory.json"] = trajectory
    return {
        "target": "reflexio",
        "schema_version": 1,
        "files": files,
    }


def build_reflexio_trajectory(canonical_trace_path: str | Path) -> list[dict[str, Any]]:
    """Build a compact prior-attempt trajectory for Reflexion memory."""
    trace_path = Path(canonical_trace_path)
    events = _read_canonical_trace(trace_path)
    trajectory: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        action = str(event.get("action") or "")
        category = str(event.get("category") or "")
        if action in {"prompt", "response", "tool_call", "tool_result"} or category in {
            "usage",
            "state",
            "quality",
            "trace",
            "builder",
        }:
            agent = str(payload.get("agent_name") or payload.get("agent") or "")
            if not agent and category == "builder":
                agent = "builder"
            trajectory.append(
                {
                    "event_id": str(event.get("event_id") or ""),
                    "timestamp": str(event.get("timestamp") or ""),
                    "category": category,
                    "action": action,
                    "summary": redact_text(str(event.get("summary") or "")),
                    "agent": agent,
                    "tool_name": str(payload.get("tool_name") or payload.get("tool") or ""),
                    "model": str(event.get("model_name") or ""),
                    "content": _trajectory_content(event, payload, trace_path=trace_path),
                }
            )
    return trajectory


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


def _read_canonical_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _trajectory_content(
    event: dict[str, Any], payload: dict[str, Any], *, trace_path: Path | None = None
) -> str:
    action = str(event.get("action") or "")
    if action == "prompt":
        return content_from_artifacts(event, "USER PROMPT", trace_path=trace_path)
    if action == "response":
        return content_from_artifacts(event, "RAW RESPONSE", trace_path=trace_path)
    if action == "tool_call":
        return redact_text(str(payload.get("tool_input_preview") or ""))
    if action == "tool_result":
        return redact_text(str(payload.get("tool_output_preview") or ""))
    if event.get("category") == "usage":
        return json.dumps(
            {
                "agent": payload.get("agent"),
                "input_tokens": payload.get("input_tokens"),
                "output_tokens": payload.get("output_tokens"),
                "total_tokens": payload.get("total_tokens"),
            },
            sort_keys=True,
        )
    return redact_text(str(event.get("summary") or ""))
