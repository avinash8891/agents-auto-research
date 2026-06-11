from __future__ import annotations

from typing import Any


def build_agent_reflections(
    trajectory: list[dict[str, Any]],
    *,
    round_facts: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Create compact, deterministic Reflexion memory per agent.

    This module is intentionally independent from trace SDK wiring so both the
    Reflexio exporter and the live feedback reader use the same derivation.
    """
    by_agent: dict[str, list[dict[str, Any]]] = {}
    facts = _merge_round_facts(round_facts, trajectory)
    for item in trajectory:
        if not isinstance(item, dict):
            continue
        agent = _canonical_agent(str(item.get("agent") or ""))
        if not agent:
            continue
        by_agent.setdefault(agent, []).append(item)
    if not by_agent and _round_fact_evidence(facts):
        by_agent["conductor"] = []

    return {
        agent: _build_agent_reflection(agent, items, facts)
        for agent, items in sorted(by_agent.items())
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


def _build_agent_reflection(
    agent: str,
    items: list[dict[str, Any]],
    round_facts: dict[str, Any],
) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    evidence: list[str] = []
    errors: list[str] = []
    tools: list[str] = []
    evidence.extend(_round_fact_evidence(round_facts))
    fact_lesson = _round_fact_lesson(round_facts)

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
        if _has_structured_error(item):
            errors.append(_redact_text(combined[:300]))

    if agent == "analyst":
        lesson = fact_lesson or _analyst_lesson(action_counts, errors)
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
        lesson = fact_lesson or (
            "Use prior theses, round results, web evidence, and analyst data before "
            "proposing one next mechanism."
        )
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


def _has_structured_error(item: dict[str, Any]) -> bool:
    action = str(item.get("action") or "")
    return (
        action in {"builder_error"}
        or str(item.get("status") or "").lower() == "error"
        or bool(str(item.get("error_type") or "").strip())
        or bool(str(item.get("error_code") or "").strip())
    )


def _merge_round_facts(
    explicit: dict[str, Any] | None, trajectory: list[dict[str, Any]]
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    prediction_gaps: list[dict[str, Any]] = []
    harvest_lessons: list[dict[str, Any]] = []
    for source in [explicit or {}, *trajectory]:
        if not isinstance(source, dict):
            continue
        nested = source.get("round_facts")
        if isinstance(nested, dict):
            source = {**nested, **source}
        raw_counts = source.get("screening_verdict_counts")
        if isinstance(raw_counts, dict):
            for verdict, count in raw_counts.items():
                try:
                    parsed_count = int(count)
                except (TypeError, ValueError):
                    continue
                if parsed_count:
                    key = str(verdict)
                    counts[key] = counts.get(key, 0) + parsed_count
        prediction_gaps.extend(_prediction_gap_items(source.get("prediction_gaps")))
        prediction_gaps.extend(_prediction_gap_items(source.get("prediction_results")))
        harvest_lessons.extend(_harvest_lesson_items(source.get("harvest_lessons")))
    return {
        "screening_verdict_counts": counts,
        "prediction_gaps": prediction_gaps,
        "harvest_lessons": harvest_lessons,
    }


def _prediction_gap_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric") or "").strip()
        if not metric:
            continue
        gap = item.get("magnitude_gap", item.get("gap"))
        if gap is None:
            continue
        try:
            parsed_gap = float(gap)
        except (TypeError, ValueError):
            continue
        direction_passed = item.get("direction_passed")
        items.append(
            {
                "metric": metric,
                "gap": parsed_gap,
                "direction_passed": (
                    direction_passed if isinstance(direction_passed, bool) else None
                ),
            }
        )
    return items


def _harvest_lesson_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        lesson = str(item.get("lesson") or "").strip()
        if not lesson:
            continue
        items.append(
            {
                "round": item.get("round"),
                "thesis_id": str(item.get("thesis_id") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "lesson": lesson,
            }
        )
    return items


def _round_fact_evidence(round_facts: dict[str, Any]) -> list[str]:
    evidence: list[str] = []
    counts = round_facts.get("screening_verdict_counts")
    if isinstance(counts, dict) and counts:
        rendered_counts = ", ".join(
            f"{verdict}={count}" for verdict, count in sorted(counts.items())
        )
        evidence.append(f"screening verdicts: {rendered_counts}")
    gaps = round_facts.get("prediction_gaps")
    if isinstance(gaps, list) and gaps:
        rendered_gaps = "; ".join(_render_prediction_gap(item) for item in gaps[:5])
        evidence.append(f"prediction gaps: {rendered_gaps}")
    lessons = round_facts.get("harvest_lessons")
    if isinstance(lessons, list) and lessons:
        rendered_lessons = "; ".join(_render_harvest_lesson(item) for item in lessons[:5])
        evidence.append(f"harvest lessons: {rendered_lessons}")
    return evidence


def _round_fact_lesson(round_facts: dict[str, Any]) -> str:
    evidence = _round_fact_evidence(round_facts)
    if not evidence:
        return ""
    return "Use computed round facts before proposing the next mechanism: " + " | ".join(evidence)


def _render_prediction_gap(item: dict[str, Any]) -> str:
    metric = str(item.get("metric") or "unknown")
    gap = item.get("gap")
    try:
        rendered_gap = f"{float(gap):.6g}"
    except (TypeError, ValueError):
        rendered_gap = str(gap)
    direction_passed = item.get("direction_passed")
    if direction_passed is True:
        direction = "pass"
    elif direction_passed is False:
        direction = "fail"
    else:
        direction = "unknown"
    return f"{metric} gap={rendered_gap} direction={direction}"


def _render_harvest_lesson(item: dict[str, Any]) -> str:
    thesis_id = str(item.get("thesis_id") or "unknown")
    status = str(item.get("status") or "unknown")
    lesson = _redact_text(str(item.get("lesson") or "").strip())[:180]
    return f"{thesis_id} status={status}: {lesson}"


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
