from __future__ import annotations

from typing import Any


def build_agent_reflections(trajectory: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Create compact, deterministic Reflexion memory per agent.

    This module is intentionally independent from trace SDK wiring so both the
    Reflexio exporter and the live feedback reader use the same derivation.
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
            evidence.append(_redact_text(combined[:300]))
        if _looks_like_error(item, combined):
            errors.append(_redact_text(combined[:300]))

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
        lesson = "Use prior theses, round results, web evidence, and analyst data before proposing one next mechanism."
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


def _redact_text(text: str) -> str:
    # Keep this module trace-SDK independent. The full artifact redactor still
    # runs before trajectory content reaches this layer.
    return text.replace("\x00", "")
