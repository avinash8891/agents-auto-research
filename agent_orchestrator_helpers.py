from __future__ import annotations

import json
from typing import Any

import agent_memory


def _persist_to_memory(
    result: dict[str, Any],
    *,
    wing: str,
    room: str,
    content: str,
    agent_name: str,
    diary_key: str,
    diary_summary: str,
) -> dict[str, Any]:
    errors: list[str] = []
    if not agent_memory._mempalace_write(wing, room, content):
        errors.append("memory_write_failed")
    if not agent_memory._mempalace_diary(agent_name, diary_key, diary_summary):
        errors.append("diary_write_failed")
    if errors:
        result = dict(result)
        result["persistence_errors"] = errors
    return result


def _build_diagnostic_prompt(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None,
    baseline_results: dict[str, Any] | None,
    family: str,
) -> str:
    prior = agent_memory._mempalace_search("diagnostic anomalies patterns", wing="autoresearch")
    config_str = json.dumps(config_contents, indent=2) if config_contents else "unknown"
    results_str = json.dumps(
        {
            k: v
            for k, v in (baseline_results or {}).items()
            if k != "diagnostics" and k != "trades_file"
        },
        indent=2,
    )
    return (
        f"Analyze the raw trades for config '{config}' "
        f"(strategy family: {family}).\n\n"
        f"STRATEGY CONFIG (what settings are applied):\n{config_str}\n\n"
        f"BACKTEST RESULTS:\n{results_str}\n\n"
        f"RAW TRADES FILE: {trades_file}\n\n"
        f"PRIOR DIAGNOSTIC FINDINGS (from earlier runs):\n{prior}\n\n"
        f"Load the CSV file and perform your analysis. "
        f"The file contains one row per trade with the schema described in your instructions."
    )


def _persist_diagnostic_result(
    result: dict[str, Any], config: str, metric: float, family: str
) -> dict[str, Any]:
    summary = result.get("overall_diagnosis", "")
    anomalies = result.get("key_anomalies", [])
    content = (
        f"DIAGNOSTIC ANALYSIS: {config} PF={metric}\n"
        f"DIAGNOSIS: {summary}\n"
        f"ANOMALIES ({len(anomalies)}):\n"
        + "\n".join(
            f"  [{a.get('confidence', '?')}] {a.get('pattern', '')} -> {a.get('suggested_exploit', '')}"
            for a in anomalies
        )
    )
    return _persist_to_memory(
        result,
        wing="autoresearch",
        room=f"{family}-diagnostics",
        content=content,
        agent_name="diagnostic-analyst",
        diary_key=f"{family}-analysis",
        diary_summary=f"CONFIG:{config}|PF:{metric}|ANOMALIES:{len(anomalies)}|{summary[:100]}",
    )


def _build_web_research_prompt(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int,
    family: str,
) -> str:
    prior = agent_memory._mempalace_search("web research findings strategy", wing="autoresearch")
    return (
        f"Strategy: {strategy_label}, round {research_round}\n\n"
        f"DIAGNOSTIC INSIGHTS:\n{analyst_brief}\n\n"
        f"RESULTS SUMMARY:\n{result_summary}\n\n"
        f"PRIOR WEB RESEARCH (do not repeat):\n{prior}\n\n"
        f"Search for external evidence and ideas relevant to these findings."
    )


def _persist_web_research_result(
    result: dict[str, Any],
    research_round: int,
    family: str,
) -> dict[str, Any]:
    findings = result.get("findings", [])
    summary = result.get("summary", "")
    gaps = result.get("confidence_and_gaps", "")
    content = (
        f"WEB RESEARCH round={research_round}: {summary}\n"
        + "\n".join(
            f"  [{f.get('label', '?')}/{f.get('source_quality', '?')}] "
            f"{f.get('topic', '')}: {f.get('actionable_idea', '')}"
            for f in findings
        )
        + (f"\nGAPS: {gaps}" if gaps else "")
    )
    return _persist_to_memory(
        result,
        wing="autoresearch",
        room=f"{family}-web-research",
        content=content,
        agent_name="web-researcher",
        diary_key=f"{family}-research",
        diary_summary=f"ROUND:{research_round}|FINDINGS:{len(findings)}|{summary[:100]}",
    )


def _build_research_prompt(context: dict[str, Any], family_name: str, spec: Any) -> str:
    best = context.get("current_best", {})
    history = context.get("result_history", "")
    research_round = context.get("research_round", 1)
    analyst_brief = context.get("analyst_brief", "")
    web_findings = context.get("web_findings", "")

    prior_theses = agent_memory._mempalace_search("research thesis proposed", wing="autoresearch")
    best_config = best.get("config_contents", {})
    best_config_str = json.dumps(best_config, indent=2) if best_config else "unknown"

    return (
        f"Research round: {research_round}\n"
        f"Current best: {best.get('config', 'none')} at metric={best.get('metric', 'unknown')}\n\n"
        f"CURRENT BEST CONFIG (these settings are ALREADY applied):\n{best_config_str}\n\n"
        f"IMPORTANT: During compilation, your config_changes are merged over STRATEGY DEFAULTS "
        f"(not over the current-best config). Any key you OMIT from config_changes will revert "
        f"to the strategy default — NOT remain at the current-best value. You MUST include "
        f"every key from the current best that you want to preserve, plus your changed keys.\n\n"
        f"FULL EXPERIMENT HISTORY:\n{history}\n\n"
        f"DIAGNOSTIC ANALYST INSIGHTS:\n{analyst_brief}\n\n"
        f"WEB RESEARCH FINDINGS:\n{web_findings}\n\n"
        f"PRIOR THESES (from memory):\n{prior_theses}\n\n"
        f"Based on all the above, propose exactly ONE next thesis that CHANGES something from the current best config."
    )


def _persist_research_thesis(
    parsed: dict[str, Any],
    family_name: str,
    research_round: int,
) -> dict[str, Any]:
    theses = parsed.get("suggested_theses", [])
    if not theses:
        return parsed
    thesis = theses[0]
    content = (
        f"THESIS round={research_round}: {thesis.get('thesis_id', '?')}\n"
        f"HYPOTHESIS: {thesis.get('hypothesis', '')}\n"
        f"MECHANISM: {thesis.get('mechanism', '')}\n"
        f"REASONING: {parsed.get('reasoning', '')}"
    )
    return _persist_to_memory(
        parsed,
        wing="autoresearch",
        room=f"{family_name}-theses",
        content=content,
        agent_name="research-agent",
        diary_key=f"{family_name}-thesis",
        diary_summary=(
            f"ROUND:{research_round}|THESIS:{thesis.get('thesis_id', '?')}|"
            f"{parsed.get('reasoning', '')[:100]}"
        ),
    )
