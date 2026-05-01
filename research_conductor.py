from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_token_usage import _accumulate_usage, get_round_usage, reset_round_usage
from research_memory import _palace_search, _palace_status
from research_memory import list_past_theses as list_past_theses_for_root
from research_memory import save_research_finding
from research_paths import _ROOT, _ensure_oauth_proxy, _parse_json
from research_prompts import _build_conductor_system_prompt
from research_subagents import _call_analyst, _call_web_researcher
from research_tools_mcp import _build_research_tools_mcp
from research_types import ResearchThesis
from strategy_family import load_family
from trace_sdk import trace, trace_agent_prompt, trace_agent_response
from trace_refinement import RefinementRecorder

__all__ = [
    "run_research_conductor",
    "run_research_conductor_sync",
    "reset_round_usage",
    "get_round_usage",
]

_REFINEMENT_RECORDER = RefinementRecorder()


def _strategy_description_for(family_name: str) -> str:
    try:
        description = load_family(family_name).description_for_research
    except ValueError:
        description = ""
    return description or f"Strategy family: {family_name}"


async def run_research_conductor(
    trades_file: str,
    experiment_results: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
) -> dict[str, Any] | None:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    strategy_desc = _strategy_description_for(family_name)

    # Build in-process MCP server with research tools
    _ensure_oauth_proxy()
    research_mcp = _build_research_tools_mcp(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        call_analyst=_call_analyst,
        call_web_researcher=_call_web_researcher,
        save_research_finding=save_research_finding,
        palace_search=_palace_search,
        palace_status=_palace_status,
        root=_ROOT,
        list_past_theses_for_root=list_past_theses_for_root,
    )

    # Single MCP server with all tools (no external mempalace process)
    mcp_servers: dict[str, Any] = {
        "research-tools": {
            "type": "sdk",
            "name": "research-tools",
            "instance": research_mcp._mcp_server,
        },
    }

    system_prompt = _build_conductor_system_prompt(strategy_desc)

    # Build user prompt with experiment results table
    outcome_lines = json.dumps(latest_outcome, indent=2) if latest_outcome else "(no results yet)"

    if trades_file:
        evidence_lines = f"Trades file for analysis: {trades_file}"
        if strategy_events_file:
            evidence_lines += (
                f"\nStrategy events file: {strategy_events_file}"
                "\n  (Contains EVERY setup the strategy considered — accepted AND rejected."
                "  Use this to understand WHY signals were filtered out.)"
            )
        if diagnostics_file:
            evidence_lines += (
                f"\nDiagnostics file: {diagnostics_file}"
                "\n  (Quick summary of event counts and rejection breakdown. Read this FIRST.)"
            )
        user_prompt = (
            f"Research round: {research_round}\n\n"
            f"LATEST EXPERIMENT OUTCOME:\n{outcome_lines}\n\n"
            f"FULL EXPERIMENT RESULTS TABLE:\n{experiment_results}\n\n"
            f"{evidence_lines}\n\n"
            f"Analyze the trades, check your data-fact memory, and propose your next thesis."
        )
    else:
        user_prompt = (
            f"Research round: {research_round}\n\n"
            f"No experiments have been run yet. No trades file available.\n\n"
            f"FULL EXPERIMENT RESULTS TABLE:\n{experiment_results}\n\n"
            f"Check memory for data facts, do web research on the strategy, "
            f"and propose your first thesis."
        )

    if rejection_feedback:
        user_prompt += (
            f"\n\nYOUR PREVIOUS THESIS WAS REJECTED BY THE VALIDATOR:\n"
            f"{rejection_feedback}\n\n"
            f"Propose a DIFFERENT thesis that avoids this issue. "
            f"Read the source code to understand what the strategy does."
        )

    trace(
        "CONDUCTOR",
        f"START round={research_round} trades={'YES' if trades_file else 'NO'}",
    )
    refinement_session = _REFINEMENT_RECORDER.start_session(
        summary=f"research round {research_round}",
        objective="produce the next thesis proposal",
        initial_context={
            "research_round": research_round,
            "family_name": family_name,
            "has_trades_file": bool(trades_file),
            "rejection_feedback": rejection_feedback,
        },
    )
    trace_id = trace_agent_prompt("research-conductor", user_prompt, system_prompt)

    result_text = ""
    got_assistant_text = False
    session_finished = False
    try:
        async for message in query(
            prompt=user_prompt,
            options=ClaudeAgentOptions(
                system_prompt=system_prompt,
                model="claude-opus-4-6",
                mcp_servers=mcp_servers,
                allowed_tools=[
                    "mcp__research-tools__analyze_trades",
                    "mcp__research-tools__web_search",
                    "mcp__research-tools__save_finding",
                    "mcp__research-tools__search_findings",
                    "mcp__research-tools__memory_status",
                    "mcp__research-tools__list_past_theses",
                ],
                permission_mode="bypassPermissions",
                max_turns=50,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        got_assistant_text = True
                        result_text += block.text
            elif isinstance(message, ResultMessage):
                if not got_assistant_text and hasattr(message, "result") and message.result:
                    result_text = str(message.result)
                # Capture conductor token usage
                if message.usage:
                    trace(
                        "CONDUCTOR",
                        f"USAGE conductor raw_keys={list(message.usage.keys())} usage={message.usage}",
                    )
                    _accumulate_usage(
                        "conductor",
                        message.usage,
                        message.total_cost_usd,
                        dedupe_key=f"conductor-message-{id(message)}-usage",
                    )
                elif message.model_usage:
                    trace(
                        "CONDUCTOR",
                        f"USAGE conductor model_usage_keys={list(message.model_usage.keys())} model_usage={message.model_usage}",
                    )
                    _accumulate_usage(
                        "conductor",
                        message.model_usage,
                        dedupe_key=f"conductor-message-{id(message)}-model",
                    )
    except asyncio.TimeoutError:
        trace("CONDUCTOR", "TIMEOUT")
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="timeout",
            final_outcome="conductor_error",
        )
        session_finished = True
        return {
            "status": "conductor_error",
            "error": "timeout",
            "suggested_theses": [],
            "should_stop": False,
        }
    except Exception as exc:
        trace("CONDUCTOR", f"ERROR: {exc}")
        print(f"CONDUCTOR error: {exc}")
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="exception",
            final_outcome="conductor_error",
        )
        session_finished = True
        return {
            "status": "conductor_error",
            "error": str(exc),
            "suggested_theses": [],
            "should_stop": False,
        }

    parsed = _parse_json(result_text)
    trace_agent_response("research-conductor", trace_id, result_text, parsed)
    _REFINEMENT_RECORDER.record_iteration(
        session_id=refinement_session["session_id"],
        iteration=1,
        generate={
            "trace_id": trace_id,
            "prompt_length": len(user_prompt),
            "system_prompt_length": len(system_prompt),
        },
        critique={"rejection_feedback": rejection_feedback},
        revise={"used_feedback_retry": bool(rejection_feedback)},
        evaluate={
            "parsed": bool(parsed),
            "suggested_theses": len(parsed.get("suggested_theses", [])) if parsed else 0,
            "should_stop": bool(parsed.get("should_stop")) if parsed else False,
        },
    )

    if parsed:
        theses = parsed.get("suggested_theses", [])
        if parsed.get("should_stop"):
            trace("CONDUCTOR", "recommends STOP")
            _REFINEMENT_RECORDER.finish_session(
                session_id=refinement_session["session_id"],
                stopping_reason="should_stop",
                final_outcome="stop",
            )
            session_finished = True
            return parsed
        if theses and isinstance(theses[0], dict):
            t = theses[0]
            candidate = dict(t)
            candidate["strategy_family"] = family_name
            try:
                ResearchThesis.model_validate(candidate)
            except Exception as exc:
                trace("CONDUCTOR", f"validate failed thesis={t.get('thesis_id', 'unknown')}: {exc}")
            else:
                trace("CONDUCTOR", f"OK thesis={t['thesis_id']}")
                _REFINEMENT_RECORDER.finish_session(
                    session_id=refinement_session["session_id"],
                    stopping_reason="valid_thesis",
                    final_outcome="accepted",
                )
                session_finished = True
                return parsed
        trace("CONDUCTOR", f"validate failed: {result_text[:200]}")
    else:
        trace("CONDUCTOR", f"parse failed: {result_text[:200]}")

    if not session_finished:
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="invalid_output",
            final_outcome="retry_required",
        )

    return None


def run_research_conductor_sync(
    trades_file: str,
    experiment_results: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
) -> dict[str, Any] | None:
    return asyncio.run(
        run_research_conductor(
            trades_file,
            experiment_results,
            latest_outcome,
            research_round,
            family_name,
            strategy_events_file=strategy_events_file,
            diagnostics_file=diagnostics_file,
            rejection_feedback=rejection_feedback,
        )
    )
