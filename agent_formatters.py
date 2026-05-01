from __future__ import annotations

from typing import Any

MAX_RESULT_HISTORY_CHARS = 4000
MAX_INSIGHT_BRIEF_CHARS = 3000
MAX_WEB_FINDINGS_CHARS = 3000


def _clip(text: Any, limit: int = 240) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _append_bounded(lines: list[str], line: str, *, budget: int, emitted: int) -> tuple[bool, int]:
    next_len = emitted + len(line) + (1 if lines else 0)
    if next_len > budget:
        if not lines or lines[-1] != "... (truncated)":
            lines.append("... (truncated)")
        return False, emitted
    lines.append(line)
    return True, next_len


def format_result_history(results: list[dict[str, Any]]) -> str:
    """Format experiment results into a readable history for prompts."""
    if not results:
        return "No experiments run yet."
    lines: list[str] = []
    emitted = 0
    for idx, r in enumerate(results):
        if idx >= 12:
            lines.append("... (truncated)")
            break
        config = _clip(r.get("config", "unknown"), 120)
        metric = _clip(r.get("metric", "?"), 80)
        status = _clip(r.get("status", "?"), 40)
        label = config
        thesis_id = r.get("thesis_id", "")
        config_changes = r.get("config_changes", {})
        if thesis_id:
            if config_changes:
                changes_str = ", ".join(f"{k}={_clip(v, 80)}" for k, v in config_changes.items())
                label = f"{_clip(thesis_id, 120)} ({changes_str})"
            else:
                label = _clip(thesis_id, 120)
        parts = [f"metric={metric}", f"status={status}"]
        if r.get("trade_count"):
            parts.append(f"trades={r['trade_count']}")
        if r.get("profit_factor"):
            parts.append(f"PF={r['profit_factor']}")
        if r.get("max_drawdown"):
            parts.append(f"maxDD={r['max_drawdown']}")
        if r.get("avg_sharpe_across_windows"):
            parts.append(f"sharpe={r['avg_sharpe_across_windows']}")
        if r.get("exit_mix"):
            parts.append(f"exit_mix={r['exit_mix']}")
        if r.get("regime_expectancy"):
            parts.append(f"regime={r['regime_expectancy']}")
        ok, emitted = _append_bounded(
            lines,
            f"  - {label}: {' | '.join(parts)}",
            budget=MAX_RESULT_HISTORY_CHARS,
            emitted=emitted,
        )
        if not ok:
            break
        if r.get("why"):
            ok, emitted = _append_bounded(
                lines,
                f"    WHY: {_clip(r['why'], 200)}",
                budget=MAX_RESULT_HISTORY_CHARS,
                emitted=emitted,
            )
            if not ok:
                break
        if r.get("regime_insight"):
            ok, emitted = _append_bounded(
                lines,
                f"    REGIME: {_clip(r['regime_insight'], 200)}",
                budget=MAX_RESULT_HISTORY_CHARS,
                emitted=emitted,
            )
            if not ok:
                break
        if r.get("next_thesis_suggestion"):
            ok, emitted = _append_bounded(
                lines,
                f"    NEXT: {_clip(r['next_thesis_suggestion'], 200)}",
                budget=MAX_RESULT_HISTORY_CHARS,
                emitted=emitted,
            )
            if not ok:
                break
        if r.get("insight_brief"):
            ok, emitted = _append_bounded(
                lines,
                "    ANALYST INSIGHTS:",
                budget=MAX_RESULT_HISTORY_CHARS,
                emitted=emitted,
            )
            if not ok:
                break
            for bl in r["insight_brief"].split("\n")[:8]:
                ok, emitted = _append_bounded(
                    lines,
                    f"      {_clip(bl, 180)}",
                    budget=MAX_RESULT_HISTORY_CHARS,
                    emitted=emitted,
                )
                if not ok:
                    break
            if not ok:
                break
    return "\n".join(lines)


def format_insight_brief(analysis: dict[str, Any]) -> str:
    """Compress analyst findings into compact brief for research agent."""
    if not analysis:
        return "(diagnostic analysis unavailable)"

    lines: list[str] = []
    emitted = 0
    overall = analysis.get("overall_diagnosis", "")
    if overall:
        ok, emitted = _append_bounded(
            lines,
            f"DIAGNOSIS: {_clip(overall, 500)}",
            budget=MAX_INSIGHT_BRIEF_CHARS,
            emitted=emitted,
        )
        if ok:
            ok, emitted = _append_bounded(
                lines, "", budget=MAX_INSIGHT_BRIEF_CHARS, emitted=emitted
            )
        if not ok:
            return "\n".join(lines)

    anomalies = analysis.get("key_anomalies", [])
    if anomalies:
        ok, emitted = _append_bounded(
            lines, "ANOMALIES:", budget=MAX_INSIGHT_BRIEF_CHARS, emitted=emitted
        )
        if not ok:
            return "\n".join(lines)
        for a in anomalies:
            conf = a.get("confidence", "low")
            if conf == "low":
                continue
            ok, emitted = _append_bounded(
                lines,
                f"  [{conf.upper()}] {_clip(a.get('pattern', ''), 220)}",
                budget=MAX_INSIGHT_BRIEF_CHARS,
                emitted=emitted,
            )
            if not ok:
                return "\n".join(lines)
            ok, emitted = _append_bounded(
                lines,
                f"    Data: {_clip(a.get('numbers', ''), 160)} (n={_clip(a.get('sample_size', ''), 40)})",
                budget=MAX_INSIGHT_BRIEF_CHARS,
                emitted=emitted,
            )
            if not ok:
                return "\n".join(lines)
            ok, emitted = _append_bounded(
                lines,
                f"    Exploit: {_clip(a.get('suggested_exploit', ''), 220)}",
                budget=MAX_INSIGHT_BRIEF_CHARS,
                emitted=emitted,
            )
            if not ok:
                return "\n".join(lines)

    questions = analysis.get("discovery_questions", [])
    if questions:
        ok, emitted = _append_bounded(lines, "", budget=MAX_INSIGHT_BRIEF_CHARS, emitted=emitted)
        if not ok:
            return "\n".join(lines)
        ok, emitted = _append_bounded(
            lines, "OPEN QUESTIONS:", budget=MAX_INSIGHT_BRIEF_CHARS, emitted=emitted
        )
        if not ok:
            return "\n".join(lines)
        for q in questions[:3]:
            ok, emitted = _append_bounded(
                lines,
                f"  - {_clip(q, 220)}",
                budget=MAX_INSIGHT_BRIEF_CHARS,
                emitted=emitted,
            )
            if not ok:
                return "\n".join(lines)

    return "\n".join(lines)


def format_web_findings(research: dict[str, Any]) -> str:
    """Compress web research into compact block for research agent."""
    if not research:
        return "(no web research available)"

    lines: list[str] = []
    emitted = 0
    summary = research.get("summary", "")
    if summary:
        ok, emitted = _append_bounded(
            lines,
            f"WEB RESEARCH SUMMARY: {_clip(summary, 500)}",
            budget=MAX_WEB_FINDINGS_CHARS,
            emitted=emitted,
        )
        if ok:
            ok, emitted = _append_bounded(lines, "", budget=MAX_WEB_FINDINGS_CHARS, emitted=emitted)
        if not ok:
            return "\n".join(lines)

    findings = research.get("findings", [])
    if findings:
        ok, emitted = _append_bounded(
            lines, "EXTERNAL FINDINGS:", budget=MAX_WEB_FINDINGS_CHARS, emitted=emitted
        )
        if not ok:
            return "\n".join(lines)
        for f in findings:
            label = _clip(f.get("label", ""), 60)
            quality = _clip(f.get("source_quality", ""), 40)
            topic = _clip(f.get("topic", ""), 120)
            finding = _clip(f.get("finding", ""), 240)
            source = _clip(f.get("source", ""), 220)
            idea = _clip(f.get("actionable_idea", ""), 220)
            tag = f"[{label}" + (f"/{quality}" if quality else "") + "]"
            ok, emitted = _append_bounded(
                lines,
                f"  {tag} {topic}: {finding}",
                budget=MAX_WEB_FINDINGS_CHARS,
                emitted=emitted,
            )
            if not ok:
                return "\n".join(lines)
            if source:
                ok, emitted = _append_bounded(
                    lines,
                    f"    Source: {source}",
                    budget=MAX_WEB_FINDINGS_CHARS,
                    emitted=emitted,
                )
                if not ok:
                    return "\n".join(lines)
            if idea:
                ok, emitted = _append_bounded(
                    lines,
                    f"    Idea: {idea}",
                    budget=MAX_WEB_FINDINGS_CHARS,
                    emitted=emitted,
                )
                if not ok:
                    return "\n".join(lines)

    gaps = research.get("confidence_and_gaps", "")
    if gaps:
        ok, emitted = _append_bounded(lines, "", budget=MAX_WEB_FINDINGS_CHARS, emitted=emitted)
        if ok:
            ok, emitted = _append_bounded(
                lines,
                f"CONFIDENCE & GAPS: {_clip(gaps, 500)}",
                budget=MAX_WEB_FINDINGS_CHARS,
                emitted=emitted,
            )
            if not ok:
                return "\n".join(lines)

    return "\n".join(lines)
