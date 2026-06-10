from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trace_adapters import _emit_adapter_event
from trace_adapters.artifacts import content_from_artifacts, portable_path, redact_text
from trace_adapters.halo import read_canonical_trace


def build_recursive_improve_payload(
    *,
    research_round: int,
    thesis_id: str,
    outcome: str,
    family: str,
    reasoning: str = "",
    validation_failure_reason: str = "",
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
            "validation_failure_reason": validation_failure_reason,
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
    trace_path = Path(canonical_trace_path)
    events = _read_canonical_trace(trace_path)
    first = events[0] if events else {}
    iteration = payload.get("iteration_context") or {}
    feedback = payload.get("feedback") or {}
    status = str(iteration.get("status") or "")
    success = status in {"compiled", "stopped"}
    usage = _total_usage(events)
    messages = _messages_from_events(events, trace_path=trace_path)
    return {
        "session_id": str(first.get("run_id") or first.get("session_id") or ""),
        "timestamp": str(first.get("timestamp") or ""),
        "duration_s": _duration_seconds(events),
        "success": success,
        "error": "" if success else str(feedback.get("validation_failure_reason") or status),
        "output": str(feedback.get("reasoning") or ""),
        "feedback": str(feedback.get("validation_failure_reason") or ""),
        "git_branch": "",
        "git_commit": "",
        "metadata": {
            "source": "agents-auto-research",
            "family": str(iteration.get("family") or first.get("family") or ""),
            "job": int(first.get("job") or 0),
            "research_round": int(iteration.get("round") or 0),
            "candidate_id": str(iteration.get("candidate_id") or ""),
            "run_id": str(first.get("run_id") or ""),
            "canonical_trace": portable_path(canonical_trace_path),
            "usage_summary": _usage_metrics(usage, _assistant_usage_totals({"messages": messages})),
        },
        "usage": usage,
        "messages": messages,
    }


def build_recursive_improve_usage_metrics(trace: dict[str, Any]) -> dict[str, int | str]:
    """Report accurate trace-level token usage separately from assistant-attached usage.

    recursive-improve's built-in detector only reads usage attached to assistant
    messages. Canonical traces can contain valid usage events that cannot be
    attached to an assistant response, so keep the full trace total explicit
    instead of inventing empty assistant messages.
    """
    trace_usage = trace.get("usage") if isinstance(trace.get("usage"), dict) else {}
    assistant_usage = _assistant_usage_totals(trace)
    return _usage_metrics(trace_usage, assistant_usage)


def _usage_metrics(
    trace_usage: dict[str, Any], assistant_usage: dict[str, int]
) -> dict[str, int | str]:
    trace_total = {
        "prompt_tokens": int(trace_usage.get("prompt_tokens") or 0),
        "completion_tokens": int(trace_usage.get("completion_tokens") or 0),
        "total_tokens": int(trace_usage.get("total_tokens") or 0),
        "cached_input_tokens": int(trace_usage.get("cached_input_tokens") or 0),
        "estimated_total_tokens": int(trace_usage.get("estimated_total_tokens") or 0),
    }
    return {
        "source": "recursive_improve_adapter_trace_usage",
        "trace_total_tokens": trace_total["total_tokens"],
        "trace_prompt_tokens": trace_total["prompt_tokens"],
        "trace_completion_tokens": trace_total["completion_tokens"],
        "trace_cached_input_tokens": trace_total["cached_input_tokens"],
        "trace_estimated_total_tokens": trace_total["estimated_total_tokens"],
        "assistant_matched_total_tokens": assistant_usage["total_tokens"],
        "assistant_matched_prompt_tokens": assistant_usage["prompt_tokens"],
        "assistant_matched_completion_tokens": assistant_usage["completion_tokens"],
        "assistant_usage_message_count": assistant_usage["message_count"],
        "unmatched_total_tokens": max(
            0, trace_total["total_tokens"] - assistant_usage["total_tokens"]
        ),
        "unmatched_prompt_tokens": max(
            0, trace_total["prompt_tokens"] - assistant_usage["prompt_tokens"]
        ),
        "unmatched_completion_tokens": max(
            0, trace_total["completion_tokens"] - assistant_usage["completion_tokens"]
        ),
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
    if not path.exists():
        return []
    return read_canonical_trace(path)


def _messages_from_events(
    events: list[dict[str, Any]], *, trace_path: Path | None = None
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    pending_usage_events: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        action = str(event.get("action") or "")
        if action == "prompt":
            system_prompt = content_from_artifacts(event, "SYSTEM PROMPT", trace_path=trace_path)
            if system_prompt:
                messages.append(_message(event, "system", system_prompt, payload, kind="prompt"))
            messages.append(
                _message(
                    event,
                    "user",
                    content_from_artifacts(event, "USER PROMPT", trace_path=trace_path),
                    payload,
                    kind="prompt",
                )
            )
        elif action == "response":
            messages.append(
                _message(
                    event,
                    "assistant",
                    content_from_artifacts(event, "RAW RESPONSE", trace_path=trace_path),
                    payload,
                    kind="response",
                )
            )
            pending_usage_events = [
                usage_event
                for usage_event in pending_usage_events
                if not _attach_usage_to_assistant(messages, usage_event)
            ]
        elif action == "tool_call":
            messages.append(
                _message(
                    event,
                    "tool",
                    redact_text(str(payload.get("tool_input_preview") or "")),
                    payload,
                    kind="tool_call",
                )
            )
        elif action == "tool_result":
            messages.append(
                _message(
                    event,
                    "tool",
                    redact_text(str(payload.get("tool_output_preview") or "")),
                    payload,
                    kind="tool_result",
                )
            )
        elif event.get("category") == "usage":
            if not _attach_usage_to_assistant(messages, event):
                pending_usage_events.append(event)
            messages.append(
                _message(
                    event, "metadata", json.dumps(payload, sort_keys=True), payload, kind="usage"
                )
            )
    return messages


def _attach_usage_to_assistant(messages: list[dict[str, Any]], event: dict[str, Any]) -> bool:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    usage = _recursive_improve_usage(payload)
    if not usage:
        return True
    agent = str(payload.get("agent") or payload.get("agent_name") or "")
    trace_id = str(payload.get("trace_id") or "")
    model = str(event.get("model_name") or "")
    for message in reversed(messages):
        if message.get("role") != "assistant" or message.get("usage"):
            continue
        if trace_id:
            if message.get("trace_id") != trace_id:
                continue
            message["usage"] = usage
            return True
        if agent and _agent_key(str(message.get("agent") or "")) != _agent_key(agent):
            continue
        if model and message.get("model") != model:
            continue
        message["usage"] = usage
        return True
    return False


def _agent_key(agent: str) -> str:
    return agent.replace("-", "_").lower()


def _recursive_improve_usage(payload: dict[str, Any]) -> dict[str, int]:
    input_tokens = int(payload.get("input_tokens") or 0)
    output_tokens = int(payload.get("output_tokens") or 0)
    total_tokens = int(payload.get("total_tokens") or input_tokens + output_tokens)
    estimated_input_tokens = int(payload.get("estimated_input_tokens") or 0)
    estimated_output_tokens = int(payload.get("estimated_output_tokens") or 0)
    estimated_total_tokens = int(
        payload.get("estimated_total_tokens") or estimated_input_tokens + estimated_output_tokens
    )
    if (
        max(
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_input_tokens,
            estimated_output_tokens,
            estimated_total_tokens,
        )
        <= 0
    ):
        return {}
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": int(payload.get("cached_input_tokens") or 0),
        "estimated_prompt_tokens": estimated_input_tokens,
        "estimated_completion_tokens": estimated_output_tokens,
        "estimated_total_tokens": estimated_total_tokens,
    }


def _total_usage(events: list[dict[str, Any]]) -> dict[str, int]:
    total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "estimated_prompt_tokens": 0,
        "estimated_completion_tokens": 0,
        "estimated_total_tokens": 0,
    }
    for event in events:
        if event.get("category") != "usage":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        usage = _recursive_improve_usage(payload)
        for key in total:
            total[key] += int(usage.get(key) or 0)
    return total


def _assistant_usage_totals(trace: dict[str, Any]) -> dict[str, int]:
    total = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "message_count": 0,
    }
    messages = trace.get("messages") if isinstance(trace.get("messages"), list) else []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        if not usage:
            continue
        total["message_count"] += 1
        total["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        total["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        total["total_tokens"] += int(usage.get("total_tokens") or 0)
    return total


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
    timestamps = [
        parsed
        for event in events
        for parsed in [_parse_timestamp(event.get("span_start_time") or event.get("timestamp"))]
        if parsed is not None
    ] + [
        parsed
        for event in events
        for parsed in [_parse_timestamp(event.get("span_end_time"))]
        if parsed is not None
    ]
    if len(timestamps) < 2:
        return 0.0
    return max(0.0, (max(timestamps) - min(timestamps)).total_seconds())


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "")
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        prefix, suffix = text.split(".", 1)
        fraction = suffix
        timezone_suffix = ""
        for marker in ("+", "-"):
            if marker in suffix:
                fraction, timezone_suffix = suffix.split(marker, 1)
                timezone_suffix = marker + timezone_suffix
                break
        text = f"{prefix}.{fraction[:6]}{timezone_suffix}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
