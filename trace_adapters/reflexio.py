from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

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
            "rejection_reason": rejection_reason,
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


def build_agent_reflections(trajectory: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Create compact, deterministic Reflexion memory per agent.

    Reflexion's useful unit is "what this actor should remember next time".
    The round-level reflection remains the global lesson; this creates the
    agent-specific memory from that agent's own trace slice without adding a
    second LLM loop or a separate memory system.
    """
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for item in trajectory:
        if not isinstance(item, dict):
            continue
        agent = _canonical_agent(str(item.get("agent") or ""))
        if not agent:
            continue
        by_agent.setdefault(agent, []).append(item)

    return {
        agent: _build_agent_reflection(agent, items)
        for agent, items in sorted(by_agent.items())
        if items
    }


def _canonical_agent(agent: str) -> str:
    normalized = agent.strip().lower().replace("_", "-")
    aliases = {
        "research-conductor": "conductor",
        "codex-diagnostic-analyst": "analyst",
        "openai-web-researcher": "web-researcher",
        "research-agent": "web-researcher",
        "compiler-builder": "builder",
        "codex-builder": "builder",
    }
    return aliases.get(normalized, normalized)


def _build_agent_reflection(agent: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    evidence: list[str] = []
    errors: list[str] = []
    tools: list[str] = []

    for item in items:
        action = str(item.get("action") or "")
        action_counts[action] = action_counts.get(action, 0) + 1
        tool = str(item.get("tool_name") or "")
        if tool and tool not in tools:
            tools.append(tool)
        summary = str(item.get("summary") or "").strip()
        content = str(item.get("content") or "").strip()
        combined = " ".join(part for part in (summary, content) if part).strip()
        if combined:
            evidence.append(redact_text(combined[:300]))
        if _looks_like_error(item, combined):
            errors.append(redact_text(combined[:300]))

    if agent == "analyst":
        lesson = _analyst_lesson(action_counts, errors)
        avoid = _analyst_avoid(errors)
        repeat = ["answer the conductor focus question with compact data evidence"]
        if "read_artifact" in tools:
            repeat.append("read typed artifacts before broader analysis")
        if "run_python" in tools:
            repeat.append("use bounded Python summaries instead of raw row dumps")
    elif agent == "web-researcher":
        lesson = "Use external sources that directly support or falsify the conductor's mechanism."
        avoid = ["generic market commentary without actionable mechanism evidence"]
        repeat = ["prefer academic or primary sources", "return source-specific actionable ideas"]
    elif agent == "builder":
        lesson = _builder_lesson(errors or evidence)
        avoid = [
            "inventing runtime config keys that are not accepted by contract/schema",
            "reporting success before deterministic verifier passes",
        ]
        repeat = [
            "read thesis and contract artifacts first",
            "run narrow verifier/tests before success",
        ]
    elif agent == "conductor":
        lesson = "Use prior theses, experiment results, web evidence, and analyst data before proposing one next mechanism."
        avoid = ["repeating prior mechanisms without a new data-backed reason"]
        repeat = ["fetch exact past thesis/result details before relying on them"]
    else:
        lesson = f"Use the prior {agent} trajectory to avoid repeated failure modes."
        avoid = ["repeating prior errors"]
        repeat = ["reuse successful prior actions"]

    return {
        "lesson": lesson,
        "avoid": avoid,
        "repeat": repeat,
        "evidence": _dedupe_keep_order(evidence)[-8:],
        "error_evidence": _dedupe_keep_order(errors)[-5:],
        "action_counts": action_counts,
        "tools": tools,
    }


def _looks_like_error(item: dict[str, Any], combined: str) -> bool:
    action = str(item.get("action") or "")
    text = combined.lower()
    return (
        action in {"builder_error"}
        or " status=error" in text
        or " result error" in text
        or "error" in text
        or "failed" in text
        or "exception" in text
        or "traceback" in text
    )


def _analyst_lesson(action_counts: dict[str, int], errors: list[str]) -> str:
    if errors:
        return "Keep analyst work focused and recover from tool failures with smaller, typed data checks."
    if action_counts.get("tool_result", 0):
        return "Continue grounding analysis in concrete tool results and compact computed metrics."
    return "Answer the assigned focus question with direct evidence."


def _analyst_avoid(errors: list[str]) -> list[str]:
    avoid = ["broad fishing beyond the focus question"]
    if errors:
        avoid.append("repeating failed Python/path probes")
    return avoid


def _builder_lesson(evidence: list[str]) -> str:
    text = " ".join(evidence).lower()
    if "unexpected_config_key" in text or "unsupported" in text or "contract" in text:
        return "Map thesis requirements through the contract/schema and verifier before writing runtime config."
    if "diagnostic" in text:
        return "Implement required diagnostics in runtime code before reporting builder success."
    return "Use verifier failures as the source of truth for the next builder attempt."


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


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
