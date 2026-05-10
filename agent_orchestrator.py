"""Public orchestrator API for diagnostics, web research, and thesis generation."""

from __future__ import annotations

from typing import Any

import agent_formatters
import agent_infra
import agent_openai_calls
import agent_prompts
import agent_runners
from agent_orchestrator_helpers import (
    _build_diagnostic_prompt,
    _build_research_prompt,
    _build_web_research_prompt,
    _persist_diagnostic_result,
    _persist_research_thesis,
    _persist_web_research_result,
)
from trace_sdk import trace

format_result_history = agent_formatters.format_result_history
_run_single_agent = agent_runners._run_single_agent
_is_error_result = agent_infra._is_error_result


_run_coroutine_sync = agent_infra._run_coroutine_sync


async def run_diagnostic_analysis(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None = None,
    baseline_results: dict[str, Any] | None = None,
    *,
    family: str,
) -> dict[str, Any] | None:
    """Run the diagnostic analyst on raw trades. Orchestrator manages memory."""
    trace(
        "ORCHESTRATOR",
        f"run_diagnostic_analysis config={config} metric={metric} trades={trades_file}",
    )
    prompt = _build_diagnostic_prompt(
        trades_file, config, metric, config_contents, baseline_results, family
    )
    result = await agent_openai_calls._run_diagnostic_analyst_openai(prompt)
    if _is_error_result(result):
        return result

    if result:
        result = _persist_diagnostic_result(result, config, metric, family)

    return result


async def run_web_research(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int = 1,
    *,
    family: str,
    job: int | None = None,
) -> dict[str, Any] | None:
    """Run the web researcher via the OpenAI Agents SDK (builds prompt, delegates to search)."""
    trace(
        "ORCHESTRATOR",
        f"run_web_research round={research_round} family={family} strategy={strategy_label}",
    )
    user_prompt = _build_web_research_prompt(
        strategy_label, analyst_brief, result_summary, research_round, family
    )
    result = await agent_openai_calls._run_web_research_openai(user_prompt)
    if _is_error_result(result):
        return result

    if result:
        result = _persist_web_research_result(
            result,
            research_round,
            family,
            job=job,
        )

    return result


async def run_research_agent(
    context: dict[str, Any],
    family_name: str,
) -> dict[str, Any] | None:
    """Run the research agent. Orchestrator manages memory."""
    trace(
        "ORCHESTRATOR",
        f"run_research_agent family={family_name} round={context.get('research_round')}",
    )
    from family_research_spec import get_family_research_spec

    spec = get_family_research_spec(family_name)
    research_round = context.get("research_round", 1)
    prompt = _build_research_prompt(context, family_name, spec)

    research_def = agent_prompts._research_agent(
        strategy_label=spec.strategy_label,
        family_name=family_name,
        config_rules=spec.config_rules,
        config_schema=spec.config_schema,
        thesis_json_hint=spec.thesis_json_hint,
    )

    parsed = await agent_runners._run_single_agent(
        "research-agent",
        prompt,
        research_def,
        retries=agent_prompts.MAX_RETRIES,
        timeout=agent_infra.SDK_TIMEOUT_SECONDS,
    )
    if _is_error_result(parsed):
        return parsed
    if not parsed:
        return None

    return _persist_research_thesis(parsed, family_name, research_round)


def analyze_diagnostics_sync(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None = None,
    baseline_results: dict[str, Any] | None = None,
    *,
    family: str,
) -> dict[str, Any] | None:
    return _run_coroutine_sync(
        run_diagnostic_analysis(
            trades_file,
            config,
            metric,
            config_contents,
            baseline_results,
            family=family,
        )
    )


def run_web_research_sync(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int = 1,
    *,
    family: str,
    job: int | None = None,
) -> dict[str, Any] | None:
    return _run_coroutine_sync(
        run_web_research(
            strategy_label,
            analyst_brief,
            result_summary,
            research_round,
            family=family,
            job=job,
        )
    )


def run_research_agent_sync(
    context: dict[str, Any],
    family_name: str,
) -> dict[str, Any] | None:
    return _run_coroutine_sync(run_research_agent(context, family_name))
