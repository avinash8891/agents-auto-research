from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from agents import Agent as OAIAgent
from agents import ModelSettings as OAIModelSettings
from agents import RunConfig as OAIRunConfig
from agents import Runner as OAIRunner
from agents import function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from agent_infra import _run_coroutine_sync
from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from agent_token_usage import get_round_usage, reset_round_usage
from autoresearch_logging import get_logger
from research_memory import _palace_status
from research_memory import get_experiment_result as get_experiment_result_for_root
from research_memory import get_past_thesis as get_past_thesis_for_root
from research_memory import list_experiment_results as list_experiment_results_for_root
from research_memory import list_past_theses as list_past_theses_for_root
from research_memory import save_research_finding, search_research_findings
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
from trace_sdk import (
    trace,
    trace_agent_prompt,
    trace_agent_response,
    trace_agent_tool_call,
    trace_agent_tool_result,
)

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
    current_job: int | None = None,
) -> dict[str, Any] | None:
    strategy_desc = _strategy_description_for(family_name)

    system_prompt = _build_conductor_system_prompt(strategy_desc)

    outcome_lines = json.dumps(latest_outcome, indent=2) if latest_outcome else "(no results yet)"
    base_prompt = (
        f"Research round: {research_round}\n\n"
        f"LATEST EXPERIMENT OUTCOME:\n{outcome_lines}\n\n"
        f"EXPERIMENT RESULTS SUMMARY:\n{experiment_results}\n\n"
    )

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
            base_prompt + f"{evidence_lines}\n\n"
            f"Analyze the trades, check your data-fact memory, and propose your next thesis."
        )
    else:
        if latest_outcome:
            no_trades_instruction = (
                "No trades file is available for the latest/current experiment. "
                "This is not a cold start: use the latest outcome and experiment-result "
                "tools to understand what happened. Do not call analyze_trades this round; "
                "use web research, past theses, experiment-result tools, memory, and source-code "
                "reasoning to propose the next thesis only if the evidence is sufficient."
            )
        else:
            no_trades_instruction = (
                "No current-job experiments have completed yet. No trades file is available. "
                "Check memory for data facts, do web research on the strategy, and propose "
                "the first thesis."
            )
        user_prompt = base_prompt + no_trades_instruction

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
            started = monotonic()
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "analyze_trades",
                focus_question,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if not trades_file:
                output = "ERROR: No trades file available for this round."
            else:
                output = await _call_analyst(
                    trades_file,
                    focus_question,
                    strategy_events_file=strategy_events_file,
                    diagnostics_file=diagnostics_file,
                )
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "analyze_trades",
                output,
                status="error" if output.startswith("ERROR:") else "ok",
                error_type="NoTradesFile" if output.startswith("ERROR:") else "",
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def web_search(query: str, context: str = "") -> str:
            started = monotonic()
            tool_input = json.dumps({"query": query, "context": context}, default=str)
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "web_search",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            output = await _call_web_researcher(query, context)
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "web_search",
                output,
                status="error" if output.startswith("WEB_SEARCH ERROR:") else "ok",
                error_type="WebResearchError" if output.startswith("WEB_SEARCH ERROR:") else "",
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def save_finding(
            finding: str,
            finding_type: str,
            status: str,
            evidence: str,
            scope: str,
            expires_if: str,
        ) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {
                    "finding": finding,
                    "finding_type": finding_type,
                    "status": status,
                    "evidence": evidence,
                    "scope": scope,
                    "expires_if": expires_if,
                },
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "save_finding",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
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
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "save_finding",
                result,
                status="error" if result.startswith("ERROR:") else "ok",
                error_type="SaveFindingError" if result.startswith("ERROR:") else "",
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return result

        @function_tool
        async def search_findings(query: str, finding_type: str = "") -> str:
            started = monotonic()
            tool_input = json.dumps({"query": query, "finding_type": finding_type}, default=str)
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "search_findings",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            results = search_research_findings(
                query=query,
                finding_type=finding_type,
                n_results=10,
            )
            if not results:
                output = "No findings found."
                trace_agent_tool_result(
                    "research-conductor",
                    trace_id,
                    "search_findings",
                    output,
                    duration_ms=int((monotonic() - started) * 1000),
                    model_provider="openai",
                    model_name=_CONDUCTOR_MODEL,
                )
                return output
            if len(results) == 1 and "error" in results[0]:
                output = f"SEARCH ERROR: {results[0]['error']}"
                trace_agent_tool_result(
                    "research-conductor",
                    trace_id,
                    "search_findings",
                    output,
                    status="error",
                    error_type="PalaceSearchError",
                    duration_ms=int((monotonic() - started) * 1000),
                    model_provider="openai",
                    model_name=_CONDUCTOR_MODEL,
                )
                return output
            lines = []
            for r in results:
                text = r.get("text", "")[:300]
                room_name = r.get("room", "")
                dist = r.get("distance", "?")
                lines.append(f"[{room_name}] (dist={dist}) {text}")
            output = "\n---\n".join(lines)
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "search_findings",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def memory_status() -> str:
            started = monotonic()
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "memory_status",
                "",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            info = _palace_status()
            if "error" in info:
                output = f"STATUS ERROR: {info['error']}"
            else:
                output = json.dumps(info, indent=2, default=str)
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "memory_status",
                output,
                status="error" if output.startswith("STATUS ERROR:") else "ok",
                error_type="MemoryStatusError" if output.startswith("STATUS ERROR:") else "",
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def list_past_theses(offset: int = 0, limit: int = 25) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {"root": str(_ROOT), "job_id": current_job, "offset": offset, "limit": limit},
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "list_past_theses",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            output = list_past_theses_for_root(
                _ROOT, job_id=current_job, offset=offset, limit=limit
            )
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "list_past_theses",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def get_past_thesis(thesis_id: str) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {"root": str(_ROOT), "job_id": current_job, "thesis_id": thesis_id},
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "get_past_thesis",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            output = get_past_thesis_for_root(_ROOT, thesis_id, job_id=current_job)
            parsed_status = "ok"
            error_type = ""
            try:
                parsed_output = json.loads(output)
                if parsed_output.get("status") in {"error", "not_found"}:
                    parsed_status = "error"
                    error_type = str(
                        parsed_output.get("error") or parsed_output.get("status") or ""
                    )
            except Exception:
                parsed_status = "error"
                error_type = "InvalidToolOutput"
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "get_past_thesis",
                output,
                status=parsed_status,
                error_type=error_type,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def list_experiment_results(
            order: str = "latest", offset: int = 0, limit: int = 10
        ) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {
                    "root": str(_ROOT),
                    "job_id": current_job,
                    "order": order,
                    "offset": offset,
                    "limit": limit,
                },
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "list_experiment_results",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            output = list_experiment_results_for_root(
                _ROOT, job_id=current_job, order=order, offset=offset, limit=limit
            )
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "list_experiment_results",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def get_experiment_result(thesis_id: str) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {"root": str(_ROOT), "job_id": current_job, "thesis_id": thesis_id},
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "get_experiment_result",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            output = get_experiment_result_for_root(_ROOT, thesis_id, job_id=current_job)
            parsed_status = "ok"
            error_type = ""
            try:
                parsed_output = json.loads(output)
                if parsed_output.get("status") in {"error", "not_found"}:
                    parsed_status = "error"
                    error_type = str(
                        parsed_output.get("error") or parsed_output.get("status") or ""
                    )
            except Exception:
                parsed_status = "error"
                error_type = "InvalidToolOutput"
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "get_experiment_result",
                output,
                status=parsed_status,
                error_type=error_type,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

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
                get_past_thesis,
                list_experiment_results,
                get_experiment_result,
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

        accumulate_agents_sdk_result_usage(
            "conductor",
            result,
            provider="openai",
            model=_CONDUCTOR_MODEL,
            input_text=f"{system_prompt}\n\n{user_prompt}",
            output_text=result_text,
        )
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
        validation_reason = ""
        if not isinstance(theses, list):
            validation_reason = "suggested_theses must be a list"
        elif len(theses) != 1:
            validation_reason = f"expected exactly one thesis, got {len(theses)}"
        elif not isinstance(theses[0], dict):
            validation_reason = "suggested_theses[0] must be an object"
        if validation_reason:
            trace(
                "CONDUCTOR",
                f"validate failed: {validation_reason}",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
        elif theses:
            t = theses[0]
            candidate = dict(t)
            candidate["strategy_family"] = family_name
            try:
                validate_thesis_dict(candidate)
            except Exception as exc:
                validation_reason = str(exc)
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
            "validation_reason": validation_reason,
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
    current_job: int | None = None,
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
            current_job=current_job,
        )
    )
