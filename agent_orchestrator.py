"""Public orchestrator API for diagnostics, web research, and thesis generation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import agent_codex_calls
import agent_definitions
import agent_infra
import agent_memory
from agent_runners import _run_single_agent
from trace_logger import trace


async def run_diagnostic_analysis(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None = None,
    baseline_results: dict[str, Any] | None = None,
    family: str = "ema",
) -> dict[str, Any] | None:
    """Run the diagnostic analyst on raw trades. Orchestrator manages memory."""
    trace(
        "ORCHESTRATOR",
        f"run_diagnostic_analysis config={config} metric={metric} trades={trades_file}",
    )
    # READ: get prior diagnostics from mempalace
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

    prompt = (
        f"Analyze the raw trades for config '{config}' "
        f"(strategy family: {family}).\n\n"
        f"STRATEGY CONFIG (what settings are applied):\n{config_str}\n\n"
        f"BACKTEST RESULTS:\n{results_str}\n\n"
        f"RAW TRADES FILE: {trades_file}\n\n"
        f"PRIOR DIAGNOSTIC FINDINGS (from earlier runs):\n{prior}\n\n"
        f"Load the CSV file and perform your analysis. "
        f"The file contains one row per trade with the schema described in your instructions."
    )
    # Use Codex SDK analyst (gpt-5.5 + local FunctionTools) instead of
    # Claude SDK analyst (which spawns a crash-prone CLI subprocess).
    result = await agent_codex_calls._run_diagnostic_analyst_openai(prompt)

    # WRITE: persist validated result to mempalace
    if result:
        summary = result.get("overall_diagnosis", "")
        anomalies = result.get("key_anomalies", [])
        content = (
            f"DIAGNOSTIC ANALYSIS: {config} PF={metric}\n"
            f"DIAGNOSIS: {summary}\n"
            f"ANOMALIES ({len(anomalies)}):\n"
            + "\n".join(
                f"  [{a.get('confidence', '?')}] {a.get('pattern', '')} "
                f"-> {a.get('suggested_exploit', '')}"
                for a in anomalies
            )
        )
        agent_memory._mempalace_write("autoresearch", f"{family}-diagnostics", content)
        agent_memory._mempalace_diary(
            "diagnostic-analyst",
            f"{family}-analysis",
            f"CONFIG:{config}|PF:{metric}|ANOMALIES:{len(anomalies)}|{summary[:100]}",
        )

    return result


async def run_web_research(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int = 1,
    family: str = "ema",
) -> dict[str, Any] | None:
    """Run the web researcher via Claude SDK (builds prompt, delegates to OpenAI for search)."""
    trace(
        "ORCHESTRATOR",
        f"run_web_research round={research_round} family={family} strategy={strategy_label}",
    )
    # READ: get prior web findings
    prior = agent_memory._mempalace_search("web research findings strategy", wing="autoresearch")

    user_prompt = (
        f"Strategy: {strategy_label}, round {research_round}\n\n"
        f"DIAGNOSTIC INSIGHTS:\n{analyst_brief}\n\n"
        f"RESULTS SUMMARY:\n{result_summary}\n\n"
        f"PRIOR WEB RESEARCH (do not repeat):\n{prior}\n\n"
        f"Search for external evidence and ideas relevant to these findings."
    )
    result = await agent_codex_calls._run_web_research_openai(user_prompt)

    # WRITE: persist validated result
    if result:
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
        agent_memory._mempalace_write("autoresearch", f"{family}-web-research", content)
        agent_memory._mempalace_diary(
            "web-researcher",
            f"{family}-research",
            f"ROUND:{research_round}|FINDINGS:{len(findings)}|{summary[:100]}",
        )

    return result


async def run_research_agent(
    context: dict[str, Any],
    family_name: str = "orb",
) -> dict[str, Any] | None:
    """Run the research agent. Orchestrator manages memory."""
    trace(
        "ORCHESTRATOR",
        f"run_research_agent family={family_name} round={context.get('research_round')}",
    )
    from family_research import get_family_research_spec

    spec = get_family_research_spec(family_name)
    best = context.get("current_best", {})
    history = context.get("result_history", "")
    research_round = context.get("research_round", 1)
    analyst_brief = context.get("analyst_brief", "")
    web_findings = context.get("web_findings", "")

    # READ: get prior theses from mempalace
    prior_theses = agent_memory._mempalace_search("research thesis proposed", wing="autoresearch")

    best_config = best.get("config_contents", {})
    best_config_str = json.dumps(best_config, indent=2) if best_config else "unknown"

    prompt = (
        f"Research round: {research_round}\n"
        f"Current best: {best.get('config', 'none')} at metric={best.get('metric', 'unknown')}\n\n"
        f"CURRENT BEST CONFIG (these settings are ALREADY applied — do NOT re-propose them):\n{best_config_str}\n\n"
        f"FULL EXPERIMENT HISTORY:\n{history}\n\n"
        f"DIAGNOSTIC ANALYST INSIGHTS:\n{analyst_brief}\n\n"
        f"WEB RESEARCH FINDINGS:\n{web_findings}\n\n"
        f"PRIOR THESES (from memory):\n{prior_theses}\n\n"
        f"Based on all the above, propose exactly ONE next thesis that CHANGES something from the current best config."
    )

    research_def = agent_definitions._research_agent(
        strategy_label=spec.strategy_label,
        config_rules=spec.config_rules,
        config_schema=spec.config_schema,
        thesis_json_hint=spec.thesis_json_hint,
    )

    parsed = await _run_single_agent(
        "research-agent",
        prompt,
        research_def,
        retries=agent_definitions.MAX_RETRIES,
        timeout=agent_infra.SDK_TIMEOUT_SECONDS,
    )
    if not parsed:
        return None

    theses = parsed.get("suggested_theses", [])
    if theses:
        thesis = theses[0]
        content = (
            f"THESIS round={research_round}: {thesis.get('thesis_id', '?')}\n"
            f"HYPOTHESIS: {thesis.get('hypothesis', '')}\n"
            f"MECHANISM: {thesis.get('mechanism', '')}\n"
            f"REASONING: {parsed.get('reasoning', '')}"
        )
        agent_memory._mempalace_write("autoresearch", f"{family_name}-theses", content)
        agent_memory._mempalace_diary(
            "research-agent",
            f"{family_name}-thesis",
            f"ROUND:{research_round}|THESIS:{thesis.get('thesis_id', '?')}|"
            f"{parsed.get('reasoning', '')[:100]}",
        )
    return parsed


def analyze_diagnostics_sync(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None = None,
    baseline_results: dict[str, Any] | None = None,
    family: str = "ema",
) -> dict[str, Any] | None:
    return asyncio.run(
        run_diagnostic_analysis(
            trades_file,
            config,
            metric,
            config_contents,
            baseline_results,
            family,
        )
    )


def run_web_research_sync(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int = 1,
    family: str = "ema",
) -> dict[str, Any] | None:
    return asyncio.run(
        run_web_research(
            strategy_label,
            analyst_brief,
            result_summary,
            research_round,
            family,
        )
    )


def run_research_agent_sync(
    context: dict[str, Any],
    family_name: str = "orb",
) -> dict[str, Any] | None:
    return asyncio.run(run_research_agent(context, family_name))
