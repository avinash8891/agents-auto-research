from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from trace_adapters import _emit_adapter_event


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
    canonical_trace_path = kwargs.pop("canonical_trace_path", None)
    payload = build_recursive_improve_payload(**kwargs)
    trace = (
        build_recursive_improve_trace(canonical_trace_path, payload)
        if canonical_trace_path is not None
        else None
    )
    files: dict[str, Any] = {
        "recursive-improve-event.json": payload,
        "recursive-improve-summary.json": {
            "candidate_id": payload["iteration_context"]["candidate_id"],
            "status": payload["iteration_context"]["status"],
            "round": payload["iteration_context"]["round"],
        },
    }
    if trace is not None:
        files["recursive-improve-trace.json"] = trace
    return {
        "target": "recursive_improve",
        "schema_version": 1,
        "files": files,
    }


def build_recursive_improve_trace(
    canonical_trace_path: str | Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build recursive-improve's eval/traces-style session JSON from canonical trace."""
    events = _read_canonical_trace(Path(canonical_trace_path))
    first = events[0] if events else {}
    iteration = payload.get("iteration_context") or {}
    feedback = payload.get("feedback") or {}
    status = str(iteration.get("status") or "")
    success = status in {"compiled", "stopped"}
    return {
        "session_id": str(first.get("run_id") or first.get("session_id") or ""),
        "timestamp": str(first.get("timestamp") or ""),
        "duration_s": _duration_seconds(events),
        "success": success,
        "error": "" if success else str(feedback.get("rejection_reason") or status),
        "output": str(feedback.get("reasoning") or ""),
        "feedback": str(feedback.get("rejection_reason") or ""),
        "git_branch": "",
        "git_commit": "",
        "metadata": {
            "source": "agents-auto-research",
            "family": str(iteration.get("family") or first.get("family") or ""),
            "job": int(first.get("job") or 0),
            "research_round": int(iteration.get("round") or 0),
            "candidate_id": str(iteration.get("candidate_id") or ""),
            "run_id": str(first.get("run_id") or ""),
            "canonical_trace": str(canonical_trace_path),
        },
        "messages": _messages_from_events(events),
    }


def emit_recursive_improve_event(
    *,
    action: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
) -> dict[str, Any]:
    return _emit_adapter_event(
        "recursive_improve",
        action=action,
        summary=summary,
        payload=payload,
        artifact_paths=artifact_paths,
    )


def _read_canonical_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
    return events


def _messages_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        action = str(event.get("action") or "")
        if action == "prompt":
            system_prompt = str(payload.get("system_prompt_preview") or "")
            if system_prompt:
                messages.append(_message(event, "system", system_prompt, payload, kind="prompt"))
            messages.append(
                _message(
                    event, "user", str(payload.get("prompt_preview") or ""), payload, kind="prompt"
                )
            )
        elif action == "response":
            messages.append(
                _message(
                    event,
                    "assistant",
                    str(payload.get("response_preview") or ""),
                    payload,
                    kind="response",
                )
            )
        elif action == "tool_call":
            messages.append(
                _message(
                    event,
                    "tool",
                    str(payload.get("tool_input_preview") or ""),
                    payload,
                    kind="tool_call",
                )
            )
        elif action == "tool_result":
            messages.append(
                _message(
                    event,
                    "tool",
                    str(payload.get("tool_output_preview") or ""),
                    payload,
                    kind="tool_result",
                )
            )
        elif event.get("category") == "usage":
            messages.append(
                _message(
                    event, "metadata", json.dumps(payload, sort_keys=True), payload, kind="usage"
                )
            )
    return messages


def _message(
    event: dict[str, Any],
    role: str,
    content: str,
    payload: dict[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "timestamp": str(event.get("timestamp") or ""),
        "kind": kind,
        "agent": str(payload.get("agent_name") or payload.get("agent") or ""),
        "model": str(event.get("model_name") or ""),
        "tool_name": str(payload.get("tool_name") or payload.get("tool") or ""),
        "event_id": str(event.get("event_id") or ""),
        "trace_id": str(payload.get("trace_id") or event.get("otel_trace_id") or ""),
    }


def _duration_seconds(events: list[dict[str, Any]]) -> float:
    if len(events) < 2:
        return 0.0
    return 0.0
