from __future__ import annotations

import asyncio
import json
from typing import Any

from agents import Agent as OAIAgent
from agents import ModelSettings as OAIModelSettings
from agents import RunConfig as OAIRunConfig
from agents import Runner as OAIRunner
from agents import function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from agent_infra import _run_coroutine_sync
from agent_token_usage import _accumulate_result_usage, get_round_usage, reset_round_usage
from autoresearch_logging import get_logger
from research_memory import _palace_search, _palace_status
from research_memory import list_past_theses as list_past_theses_for_root
from research_memory import save_research_finding
from research_paths import (
    _CONDUCTOR_MODEL,
    _OAUTH_PROXY_URL,
    _ROOT,
    _ensure_oauth_proxy,
    _get_openai_client,
    _parse_json,
)
from research_prompts import _build_conductor_system_prompt
from research_subagents import _call_analyst, _call_web_researcher
from strategy_family import load_family
from thesis_validator import validate_thesis_dict
from trace_refinement import RefinementRecorder
from trace_sdk import trace, trace_agent_prompt, trace_agent_response

log = get_logger(__name__)

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
    strategy_desc = _strategy_description_for(family_name)

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
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
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
    trace_id = trace_agent_prompt(
        "research-conductor",
        user_prompt,
        system_prompt,
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
    result_text = ""
    session_finished = False
    try:
        _ensure_oauth_proxy()
        client = _get_openai_client(_OAUTH_PROXY_URL)
        model = OpenAIChatCompletionsModel(model=_CONDUCTOR_MODEL, openai_client=client)

        @function_tool
        async def analyze_trades(focus_question: str) -> str:
            if not trades_file:
                return "ERROR: No trades file available for this round."
            return await _call_analyst(
                trades_file,
                focus_question,
                strategy_events_file=strategy_events_file,
                diagnostics_file=diagnostics_file,
            )

        @function_tool
        async def web_search(query: str, context: str = "") -> str:
            return await _call_web_researcher(query, context)

        @function_tool
        async def save_finding(
            finding: str,
            finding_type: str,
            status: str,
            evidence: str,
            scope: str,
            expires_if: str,
        ) -> str:
            trace(
                "CONDUCTOR",
                f"save_finding type={finding_type} status={status} finding='{finding[:80]}'",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            result = save_research_finding(
                finding=finding,
                finding_type=finding_type,
                status=status,
                evidence=evidence,
                scope=scope,
                expires_if=expires_if,
            )
            return result

        @function_tool
        async def search_findings(query: str, finding_type: str = "") -> str:
            room = finding_type if finding_type else None
            results = _palace_search(
                query=query,
                wing="research_findings",
                room=room,
                n_results=10,
            )
            if not results:
                return "No findings found."
            if len(results) == 1 and "error" in results[0]:
                return f"SEARCH ERROR: {results[0]['error']}"
            lines = []
            for r in results:
                text = r.get("text", "")[:300]
                room_name = r.get("room", "")
                dist = r.get("distance", "?")
                lines.append(f"[{room_name}] (dist={dist}) {text}")
            return "\n---\n".join(lines)

        @function_tool
        async def memory_status() -> str:
            info = _palace_status()
            if "error" in info:
                return f"STATUS ERROR: {info['error']}"
            return json.dumps(info, indent=2, default=str)

        @function_tool
        async def list_past_theses() -> str:
            return list_past_theses_for_root(_ROOT)

        agent = OAIAgent(
            name="research-conductor",
            instructions=system_prompt,
            tools=[
                analyze_trades,
                web_search,
                save_finding,
                search_findings,
                memory_status,
                list_past_theses,
            ],
            model=model,
        )

        result = OAIRunner.run_streamed(
            agent,
            user_prompt,
            max_turns=50,
            run_config=OAIRunConfig(
                model_settings=OAIModelSettings(store=False),
                tracing_disabled=True,
            ),
        )
        async for _ in result.stream_events():
            pass
        if hasattr(result, "final_output_as"):
            try:
                result_text = result.final_output_as(str) or ""
            except Exception as exc:
                log.warning("final_output_as failed for conductor: %s", exc)
                result_text = ""
        if not result_text:
            final_output = getattr(result, "final_output", None)
            if isinstance(final_output, str):
                result_text = final_output
            elif final_output is not None:
                result_text = json.dumps(final_output, default=str)

        _accumulate_result_usage("conductor", result, provider="openai", model=_CONDUCTOR_MODEL)
    except asyncio.TimeoutError:
        trace(
            "CONDUCTOR",
            "TIMEOUT",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
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
        error_text = str(exc)
        error_kind = "proxy_unavailable" if "openai-oauth proxy" in error_text else "exception"
        trace(
            "CONDUCTOR",
            f"ERROR: {error_kind}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="exception",
            final_outcome="conductor_error",
        )
        session_finished = True
        return {
            "status": "conductor_error",
            "error": error_kind,
            "details": exc.__class__.__name__,
            "suggested_theses": [],
            "should_stop": False,
        }

    parsed = _parse_json(result_text)
    trace_agent_response(
        "research-conductor",
        trace_id,
        result_text,
        parsed,
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
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
            trace(
                "CONDUCTOR",
                "recommends STOP",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
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
                validate_thesis_dict(candidate)
            except Exception as exc:
                trace(
                    "CONDUCTOR",
                    f"validate failed thesis={t.get('thesis_id', 'unknown')}: {exc}",
                    model_provider="openai",
                    model_name=_CONDUCTOR_MODEL,
                )
            else:
                trace(
                    "CONDUCTOR",
                    f"OK thesis={t['thesis_id']}",
                    model_provider="openai",
                    model_name=_CONDUCTOR_MODEL,
                )
                _REFINEMENT_RECORDER.finish_session(
                    session_id=refinement_session["session_id"],
                    stopping_reason="valid_thesis",
                    final_outcome="accepted",
                )
                session_finished = True
                return parsed
        trace(
            "CONDUCTOR",
            f"validate failed (len={len(result_text)})",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        failure = {
            "status": "conductor_error",
            "error": "validation_failed",
            "reasoning": parsed.get("reasoning", ""),
            "suggested_theses": [],
            "should_stop": False,
        }
    else:
        trace(
            "CONDUCTOR",
            f"parse failed (len={len(result_text)})",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        failure = {
            "status": "conductor_error",
            "error": "parse_failed",
            "reasoning": "",
            "suggested_theses": [],
            "should_stop": False,
        }

    if not session_finished:
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="invalid_output",
            final_outcome="retry_required",
        )

    return failure


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
    return _run_coroutine_sync(
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
