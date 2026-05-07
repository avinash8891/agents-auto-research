from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from trace_adapters import _emit_adapter_event
from trace_adapters.artifacts import content_from_artifacts

HALO_PROJECT_ID = "agents-auto-research"
HALO_SERVICE_NAME = "agents-auto-research"
HALO_SCHEMA_VERSION = 1

_REQUIRED_HALO_KEYS = {
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "kind",
    "start_time",
    "end_time",
    "status",
    "attributes",
    "resource",
    "scope",
}
_REQUIRED_HALO_ATTRIBUTES = {
    "inference.export.schema_version",
    "inference.project_id",
    "inference.observation_kind",
    "openinference.span.kind",
}


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
) -> dict[str, Any]:
    return _emit_adapter_event(
        "halo", action=action, summary=summary, payload=payload, artifact_paths=artifact_paths
    )


def read_canonical_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid canonical trace JSONL: {exc}"
                ) from exc
            if not isinstance(event, dict):
                raise ValueError(f"{path}:{line_number}: canonical trace event must be an object")
            events.append(event)
    return events


def export_halo_trace_jsonl(canonical_trace_path: Path, output_path: Path) -> Path:
    events = read_canonical_trace(canonical_trace_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for event in events:
            span = canonical_event_to_halo_span(event)
            handle.write(json.dumps(span, sort_keys=True, default=str) + "\n")
    return output_path


def verify_halo_trace_jsonl(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"HALO trace JSONL missing: {path}")
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            count += 1
            try:
                span = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid HALO JSONL: {exc}") from exc
            if not isinstance(span, dict):
                raise ValueError(f"{path}:{line_number}: HALO span must be an object")
            missing = _REQUIRED_HALO_KEYS - set(span)
            if missing:
                raise ValueError(f"{path}:{line_number}: missing required keys: {sorted(missing)}")
            attributes = span.get("attributes")
            if not isinstance(attributes, dict):
                raise ValueError(f"{path}:{line_number}: attributes must be an object")
            missing_attrs = _REQUIRED_HALO_ATTRIBUTES - set(attributes)
            if missing_attrs:
                raise ValueError(
                    f"{path}:{line_number}: missing required attributes: {sorted(missing_attrs)}"
                )
            resource = span.get("resource")
            if not isinstance(resource, dict) or not isinstance(resource.get("attributes"), dict):
                raise ValueError(f"{path}:{line_number}: resource.attributes must be an object")
            scope = span.get("scope")
            if not isinstance(scope, dict) or not isinstance(scope.get("name"), str):
                raise ValueError(f"{path}:{line_number}: scope.name must be a string")
            if not isinstance(span.get("parent_span_id"), str):
                raise ValueError(f"{path}:{line_number}: parent_span_id must be a string")
    if count == 0:
        raise ValueError(f"HALO trace JSONL has no spans: {path}")


def canonical_event_to_halo_span(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    observation_kind = _observation_kind(event)
    span_id = _real_or_stable_id(
        event.get("span_id"),
        "span",
        event.get("run_id"),
        event.get("event_id"),
        event.get("seq"),
        length=16,
    )
    trace_id = _real_or_stable_id(
        event.get("otel_trace_id"),
        "trace",
        event.get("run_id"),
        event.get("session_id"),
        length=32,
    )
    parent_span_id = str(event.get("parent_span_id") or "")
    start_time = str(event.get("span_start_time") or _timestamp(event))
    end_time = str(event.get("span_end_time") or start_time)

    attributes = _base_attributes(event, observation_kind)
    attributes.update(_event_specific_attributes(event, payload, observation_kind))
    resource_attributes = _resource_attributes(event)
    scope = _scope(event)

    span: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": str(event.get("span_name") or _span_name(event, payload)),
        "kind": str(event.get("span_kind") or "SPAN_KIND_INTERNAL"),
        "start_time": start_time,
        "end_time": end_time,
        "status": _status(event, payload),
        "attributes": attributes,
        "resource": {"attributes": resource_attributes},
        "scope": scope,
    }
    return span


def _base_attributes(event: dict[str, Any], observation_kind: str) -> dict[str, Any]:
    return {
        "inference.export.schema_version": HALO_SCHEMA_VERSION,
        "inference.project_id": HALO_PROJECT_ID,
        "inference.observation_kind": observation_kind,
        "openinference.span.kind": observation_kind,
        "autoresearch.event_id": str(event.get("event_id") or ""),
        "autoresearch.run_id": str(event.get("run_id") or ""),
        "autoresearch.session_id": str(event.get("session_id") or ""),
        "autoresearch.family": str(event.get("family") or ""),
        "autoresearch.job": int(event.get("job") or 0),
        "autoresearch.category": str(event.get("category") or ""),
        "autoresearch.action": str(event.get("action") or ""),
        "autoresearch.summary": str(event.get("summary") or ""),
        "autoresearch.hypothesis_id": str(event.get("hypothesis_id") or ""),
        "autoresearch.hypothesis_name": str(event.get("hypothesis_name") or ""),
    }


def _event_specific_attributes(
    event: dict[str, Any], payload: dict[str, Any], observation_kind: str
) -> dict[str, Any]:
    action = str(event.get("action") or "")
    attributes: dict[str, Any] = {}
    agent = payload.get("agent_name") or payload.get("agent")
    if agent:
        attributes["agent.name"] = str(agent)

    model_name = event.get("model_name")
    if model_name:
        attributes["llm.model_name"] = str(model_name)
        attributes["inference.llm.model_name"] = str(model_name)

    if observation_kind == "TOOL":
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "unknown_tool")
        attributes["tool.name"] = tool_name
        if action == "tool_call":
            attributes["input.value"] = str(payload.get("tool_input_preview") or "")
            attributes["input.mime_type"] = "text/plain"
        if action == "tool_result":
            attributes["output.value"] = str(payload.get("tool_output_preview") or "")
            attributes["output.mime_type"] = "text/plain"
            attributes["tool.output_length"] = int(payload.get("tool_output_length") or 0)
            if payload.get("error_type"):
                attributes["error.type"] = str(payload["error_type"])

    if observation_kind == "LLM":
        if action == "prompt":
            messages = []
            system_prompt = content_from_artifacts(event, "SYSTEM PROMPT")
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append(
                {"role": "user", "content": content_from_artifacts(event, "USER PROMPT")}
            )
            attributes["llm.input_messages"] = json.dumps(messages)
            _add_flat_messages(attributes, "llm.input_messages", messages)
        elif action == "response":
            messages = [
                {
                    "role": "assistant",
                    "content": content_from_artifacts(event, "RAW RESPONSE"),
                }
            ]
            attributes["llm.output_messages"] = json.dumps(messages)
            _add_flat_messages(attributes, "llm.output_messages", messages)
        if event.get("artifact_paths"):
            attributes["autoresearch.artifact_paths"] = json.dumps(
                event.get("artifact_paths") or []
            )
        for source_key, target_key in [
            ("input_tokens", "inference.llm.input_tokens"),
            ("output_tokens", "inference.llm.output_tokens"),
            ("total_tokens", "inference.llm.total_tokens"),
            ("cached_input_tokens", "inference.llm.cached_input_tokens"),
            ("reasoning_output_tokens", "inference.llm.reasoning_output_tokens"),
        ]:
            if source_key in payload:
                attributes[target_key] = int(payload.get(source_key) or 0)
        if "input_tokens" in payload:
            attributes["llm.token_count.prompt"] = int(payload.get("input_tokens") or 0)
        if "output_tokens" in payload:
            attributes["llm.token_count.completion"] = int(payload.get("output_tokens") or 0)

    if observation_kind in {"CHAIN", "AGENT", "SPAN"}:
        attributes["input.value"] = str(event.get("summary") or "")

    return attributes


def _add_flat_messages(
    attributes: dict[str, Any], prefix: str, messages: list[dict[str, str]]
) -> None:
    for index, message in enumerate(messages):
        attributes[f"{prefix}.{index}.message.role"] = message["role"]
        attributes[f"{prefix}.{index}.message.content"] = message["content"]


def _observation_kind(event: dict[str, Any]) -> str:
    category = str(event.get("category") or "")
    action = str(event.get("action") or "")
    if action in {"tool_call", "tool_result"}:
        return "TOOL"
    if category == "usage" or action in {"prompt", "response"}:
        return "LLM"
    if category == "agent":
        return "AGENT"
    if category in {"state", "quality", "autonomy", "rule_proposal", "refinement"}:
        return "CHAIN"
    return "SPAN"


def _span_name(event: dict[str, Any], payload: dict[str, Any]) -> str:
    action = str(event.get("action") or "event")
    category = str(event.get("category") or "trace")
    if action in {"tool_call", "tool_result"}:
        return (
            f"{payload.get('agent_name') or 'agent'}.{payload.get('tool_name') or 'tool'}.{action}"
        )
    if action in {"prompt", "response"}:
        return f"{payload.get('agent_name') or 'agent'}.{action}"
    return f"{category}.{action}"


def _status(event: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    status_code = str(event.get("span_status_code") or "")
    if status_code.startswith("STATUS_CODE_"):
        return {"code": status_code, "message": str(event.get("span_status_message") or "")}
    status = str(payload.get("status") or "").lower()
    if status in {"error", "failed", "failure"}:
        return {
            "code": "STATUS_CODE_ERROR",
            "message": str(payload.get("error_type") or payload.get("tool_output_preview") or ""),
        }
    return {"code": "STATUS_CODE_OK"}


def _timestamp(event: dict[str, Any]) -> str:
    timestamp = str(event.get("timestamp") or "")
    return timestamp or "1970-01-01T00:00:00.000Z"


def _resource_attributes(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("resource_attributes")
    if isinstance(raw, dict):
        resource = {str(k): v for k, v in raw.items()}
    else:
        resource = {}
    resource.setdefault("service.name", HALO_SERVICE_NAME)
    resource.setdefault("service.namespace", "autoresearch")
    resource.setdefault("service.instance.id", str(event.get("session_id") or ""))
    resource.setdefault("inference.project_id", HALO_PROJECT_ID)
    return resource


def _scope(event: dict[str, Any]) -> dict[str, str]:
    raw = event.get("scope")
    if isinstance(raw, dict):
        name = str(raw.get("name") or "")
        version = str(raw.get("version", ""))
    else:
        name = ""
        version = str(HALO_SCHEMA_VERSION)
    return {
        "name": name or "autoresearch.trace_adapters.halo",
        "version": version,
    }


def _real_or_stable_id(value: Any, *stable_parts: Any, length: int) -> str:
    text = str(value or "")
    if text and len(text) == length:
        return text
    return _stable_hex(*stable_parts, length=length)


def _stable_hex(*parts: Any, length: int) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]
