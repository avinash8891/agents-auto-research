from __future__ import annotations

from typing import Any


def format_result_history(results: list[dict[str, Any]]) -> str:
    """Format experiment results into a readable history for prompts."""
    if not results:
        return "No experiments run yet."
    lines: list[str] = []
    for r in results:
        config = r.get("config", "unknown")
        metric = r.get("metric", "?")
        status = r.get("status", "?")
        label = config
        thesis_id = r.get("thesis_id", "")
        config_changes = r.get("config_changes", {})
        if thesis_id:
            if config_changes:
                changes_str = ", ".join(f"{k}={v}" for k, v in config_changes.items())
                label = f"{thesis_id} ({changes_str})"
            else:
                label = thesis_id
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
        lines.append(f"  - {label}: {' | '.join(parts)}")
        if r.get("why"):
            lines.append(f"    WHY: {r['why']}")
        if r.get("regime_insight"):
            lines.append(f"    REGIME: {r['regime_insight']}")
        if r.get("next_thesis_suggestion"):
            lines.append(f"    NEXT: {r['next_thesis_suggestion']}")
        if r.get("insight_brief"):
            lines.append("    ANALYST INSIGHTS:")
            for bl in r["insight_brief"].split("\n")[:8]:
                lines.append(f"      {bl}")
    return "\n".join(lines)


def format_insight_brief(analysis: dict[str, Any]) -> str:
    """Compress analyst findings into compact brief for research agent."""
    if not analysis:
        return "(diagnostic analysis unavailable)"

    lines: list[str] = []
    overall = analysis.get("overall_diagnosis", "")
    if overall:
        lines.append(f"DIAGNOSIS: {overall}")
        lines.append("")

    anomalies = analysis.get("key_anomalies", [])
    if anomalies:
        lines.append("ANOMALIES:")
        for a in anomalies:
            conf = a.get("confidence", "low")
            if conf == "low":
                continue
            lines.append(f"  [{conf.upper()}] {a.get('pattern', '')}")
            lines.append(f"    Data: {a.get('numbers', '')} (n={a.get('sample_size', '')})")
            lines.append(f"    Exploit: {a.get('suggested_exploit', '')}")

    questions = analysis.get("discovery_questions", [])
    if questions:
        lines.append("")
        lines.append("OPEN QUESTIONS:")
        for q in questions[:3]:
            lines.append(f"  - {q}")

    return "\n".join(lines)


def format_web_findings(research: dict[str, Any]) -> str:
    """Compress web research into compact block for research agent."""
    if not research:
        return "(no web research available)"

    lines: list[str] = []
    summary = research.get("summary", "")
    if summary:
        lines.append(f"WEB RESEARCH SUMMARY: {summary}")
        lines.append("")

    findings = research.get("findings", [])
    if findings:
        lines.append("EXTERNAL FINDINGS:")
        for f in findings:
            label = f.get("label", "")
            quality = f.get("source_quality", "")
            topic = f.get("topic", "")
            finding = f.get("finding", "")
            source = f.get("source", "")
            idea = f.get("actionable_idea", "")
            tag = f"[{label}" + (f"/{quality}" if quality else "") + "]"
            lines.append(f"  {tag} {topic}: {finding}")
            if source:
                lines.append(f"    Source: {source}")
            if idea:
                lines.append(f"    Idea: {idea}")

    gaps = research.get("confidence_and_gaps", "")
    if gaps:
        lines.append("")
        lines.append(f"CONFIDENCE & GAPS: {gaps}")

    return "\n".join(lines)
