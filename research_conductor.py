from __future__ import annotations

import asyncio
import json
import os
from time import monotonic
from typing import Any

from agents import Agent as OAIAgent
from agents import AgentOutputSchema as OAIAgentOutputSchema
from agents import ModelSettings as OAIModelSettings
from agents import RunConfig as OAIRunConfig
from agents import Runner as OAIRunner
from agents import function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from agent_infra import _run_coroutine_sync
from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from agent_token_usage import get_round_usage, reset_round_usage
from autoresearch_logging import get_logger
from research_memory import (
    _palace_status,
)
from research_memory import get_past_thesis as get_past_thesis_for_root
from research_memory import get_round_result as get_round_result_for_root
from research_memory import list_past_theses as list_past_theses_for_root
from research_memory import list_round_results as list_round_results_for_root
from research_memory import (
    round_result_not_found_envelope,
    save_research_finding,
    search_research_findings,
)
from research_paths import (
    _CONDUCTOR_MODEL,
    _OAUTH_PROXY_URL,
    _ROOT,
    _ensure_oauth_proxy,
    _get_openai_client,
    _parse_json,
)
from research_prompts import _build_mechanism_system_prompt
from research_subagents import _call_analyst, _call_web_researcher
from research_tools_schema import (
    AnalyzeTradesArgs,
    GetPastThesisArgs,
    GetRejectionArgs,
    GetRoundResultArgs,
    ListPastThesesArgs,
    ListRejectionsArgs,
    ListRoundResultsArgs,
    MemoryStatusArgs,
    RejectionPatternSummaryArgs,
    SaveFindingArgs,
    SearchFindingsArgs,
    WebSearchArgs,
)
from research_types import ConductorResult, MechanismProposal
from strategy_family import load_family
from trace_refinement import RefinementRecorder
from trace_sdk import (
    trace,
    trace_agent_prompt,
    trace_agent_response,
    trace_agent_tool_call,
    trace_agent_tool_result,
)

log = get_logger(__name__)


def _conductor_output_schema() -> OAIAgentOutputSchema:
    """SDK-safe output schema for the conductor agent.

    MechanismProposal.proposed_change is free-form (dict[str, Any]); the
    openai-agents SDK's strict JSON schema mode rejects the resulting
    additionalProperties. Disable strict so the SDK parses the model
    non-strictly (the conductor still validates via Pydantic downstream).
    """
    return OAIAgentOutputSchema(MechanismProposal, strict_json_schema=False)


__all__ = [
    "run_research_conductor",
    "run_research_conductor_sync",
    "reset_round_usage",
    "get_round_usage",
]

_REFINEMENT_RECORDER = RefinementRecorder()
CONDUCTOR_TIMEOUT_ENV = "AUTORESEARCH_CONDUCTOR_TIMEOUT_SECONDS"
DEFAULT_CONDUCTOR_TIMEOUT_SECONDS = 900.0


def _lenient_research_round_id(job_id: int | None, research_round: int) -> str:
    return f"job-{int(job_id or 0)}-round-{int(research_round)}"


def _conductor_timeout_seconds() -> float:
    raw = os.environ.get(CONDUCTOR_TIMEOUT_ENV, "")
    if not raw:
        return DEFAULT_CONDUCTOR_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        log.warning("%s=%r is invalid; using default timeout", CONDUCTOR_TIMEOUT_ENV, raw)
        return DEFAULT_CONDUCTOR_TIMEOUT_SECONDS
    if value <= 0:
        log.warning("%s=%r must be positive; using default timeout", CONDUCTOR_TIMEOUT_ENV, raw)
        return DEFAULT_CONDUCTOR_TIMEOUT_SECONDS
    return value


def _validate_tool_args(model_cls: type, kwargs: dict[str, Any]) -> str:
    try:
        model_cls(**kwargs)
        return ""
    except Exception as exc:  # noqa: BLE001
        errors = getattr(exc, "errors", lambda **_: [])(include_url=False)
        if not errors:
            return f"VALIDATION ERROR: {exc}"
        parts = []
        for error in errors:
            loc = ".".join(str(item) for item in error.get("loc", ())) or "input"
            parts.append(f"{loc}: {error.get('msg')}")
        return "VALIDATION ERROR: " + "; ".join(parts)


def _strategy_description_for(family_name: str) -> str:
    try:
        description = load_family(family_name).description_for_research
    except ValueError:
        description = ""
    return description or f"Strategy family: {family_name}"


def _backtest_contract_for(family_name: str):
    try:
        return load_family(family_name).backtest_contract
    except ValueError:
        return None


def _render_resolution_context(resolution_context: dict[str, Any] | None) -> str:
    context = resolution_context or {}
    keys = context.get("resolution_config_keys") or []
    resolved = context.get("resolved_minutes_by_key") or {}
    minimum = context.get("minimum_supported_time_bucket_minutes")
    lines = ["EXECUTION RESOLUTION CONTEXT:"]
    if keys:
        lines.append(f"- resolution_config_keys: {', '.join(str(key) for key in keys)}")
    else:
        lines.append("- resolution_config_keys: none declared")
    if resolved:
        for key, minutes in resolved.items():
            lines.append(f"- {key}: {minutes} minute bars")
    else:
        lines.append("- resolved_minutes_by_key: unavailable")
    if minimum:
        lines.append(f"- minimum_supported_time_bucket_minutes: {minimum}")
        lines.append(
            f"- Treat {minimum}-minute bars as the finest executable time granularity "
            "unless finer raw data is explicitly available."
        )
    else:
        lines.append("- minimum_supported_time_bucket_minutes: unknown")
    return "\n".join(lines)


async def run_research_conductor(
    trades_file: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
    agent_reflexions: dict[str, str] | None = None,
    current_job: int | None = None,
    rendered_corpus: str = "",
) -> ConductorResult:
    if not rendered_corpus:
        raise ValueError("mechanism conductor mode requires rendered_corpus")
    system_prompt = _build_mechanism_system_prompt()

    user_prompt = rendered_corpus

    if rejection_feedback:
        user_prompt += (
            f"\n\nFEEDBACK TO APPLY BEFORE PROPOSING:\n"
            f"{rejection_feedback}\n\n"
            "Propose a mechanism that addresses this feedback. Use only the rendered "
            "corpus and this feedback."
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
            "has_rendered_corpus": bool(rendered_corpus),
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
    resolution_context = latest_outcome.get("resolution_context")
    # Track which tools have been called so the thesis validator can enforce
    # per-thesis process requirements after the conductor proposes a thesis.
    tools_called_this_round: set[str] = set()
    try:
        _ensure_oauth_proxy()
        client = _get_openai_client(_OAUTH_PROXY_URL)
        model = OpenAIChatCompletionsModel(model=_CONDUCTOR_MODEL, openai_client=client)

        @function_tool
        async def analyze_trades(focus_question: str) -> str:
            """Ask the analyst for baseline-first or cross-round quantitative analysis.

            The analyst defaults to baseline-anchored mechanism analysis when
            baseline artifacts exist. It can also retrieve latest/current/best
            or specific round artifacts via its own tools when the question
            calls for longitudinal comparison or latest-run forensics.
            """
            started = monotonic()
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "analyze_trades",
                focus_question,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if err := _validate_tool_args(AnalyzeTradesArgs, {"focus_question": focus_question}):
                return err
            if not trades_file:
                output = "ERROR: No trades file available for this round."
            else:
                tools_called_this_round.add("analyze_trades")
                output = await _call_analyst(
                    trades_file,
                    focus_question,
                    strategy_events_file=strategy_events_file,
                    diagnostics_file=diagnostics_file,
                    family_name=family_name,
                    latest_outcome=latest_outcome,
                    resolution_context=resolution_context,
                    reflexion_feedback=(agent_reflexions or {}).get("analyst", ""),
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
            if err := _validate_tool_args(WebSearchArgs, {"query": query, "context": context}):
                return err
            tools_called_this_round.add("web_search")
            output = await _call_web_researcher(
                query,
                context,
                reflexion_feedback=(agent_reflexions or {}).get("web-researcher", ""),
            )
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
            """Persist one structured research finding to cross-round memory.

            SAVE: data facts, dataset properties, seasonal patterns, web research
            findings — things that will still be true next round.
            DO NOT SAVE: round outcomes, thesis evaluations, opinions like
            "I think X is better". Round results are in the results table;
            you see them fresh every round. Form your own conclusions; do not
            cache opinions (they poison future rounds).

            Required fields:
              finding         the actual insight ONLY — do not repeat
                              type/status/evidence/scope/expires_if inside it.
              finding_type    one of [observation, hypothesis, validated_finding,
                              rejected_finding, open_question, implementation_note]
              status          one of [unvalidated, validated, rejected, stale]
              evidence        which research_round_id produced this
                              (e.g. "round_003, thesis entry_window_test")
              scope           what data this applies to
                              (e.g. "train_period_only", "full_sample", "SPY_only")
              expires_if      condition that would invalidate this
                              (e.g. "fails on validation split", "baseline changes")

            Good save:
              finding_type="observation", status="unvalidated",
              evidence="round_003 analyst", scope="train_2023-2025",
              expires_if="baseline drift > 5%",
              finding="Tuesdays have PF=1.7 vs Friday PF=2.7 across 3017 trades."

            Bad save (will poison future rounds):
              "Parameter value X works better than Y" — opinion, no scope/expiry
              "Gap filter is too strict" — conclusion without evidence or scope

            When you READ a finding from search_findings later, check its STATUS
            and SCOPE before trusting it. STATUS=unvalidated from a different
            data period = hypothesis to verify, not a fact to build on.
            """
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
            if err := _validate_tool_args(
                SaveFindingArgs,
                {
                    "finding": finding,
                    "finding_type": finding_type,
                    "status": status,
                    "evidence": evidence,
                    "scope": scope,
                    "expires_if": expires_if,
                },
            ):
                return err
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
            if err := _validate_tool_args(
                SearchFindingsArgs, {"query": query, "finding_type": finding_type}
            ):
                return err
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
            if err := _validate_tool_args(MemoryStatusArgs, {}):
                return err
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
            """Return a bounded index of prior theses and their outcomes.

            Call this BEFORE proposing — the cumulative research ledger is the
            source of truth for what has been tried, what worked, what failed,
            what required code, and which mechanisms remain underexplored.

            Workflow: narrow first with this tool + search_findings, then fetch
            full details for relevant priors via get_past_thesis. Avoid
            re-fetching everything; pull only the priors you need to compare
            against your candidate.
            """
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
            if err := _validate_tool_args(ListPastThesesArgs, {"offset": offset, "limit": limit}):
                return err
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
            if err := _validate_tool_args(GetPastThesisArgs, {"thesis_id": thesis_id}):
                return err
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
        async def list_round_results(
            order: str = "latest",
            offset: int = 0,
            limit: int = 10,
            job_id: int | None = None,
            family: str | None = None,
        ) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {
                    "root": str(_ROOT),
                    "current_job": current_job,
                    "order": order,
                    "offset": offset,
                    "limit": limit,
                    "job_id": job_id,
                    "family": family,
                },
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "list_round_results",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if err := _validate_tool_args(
                ListRoundResultsArgs,
                {
                    "order": order,
                    "offset": offset,
                    "limit": limit,
                    "job_id": job_id,
                    "family": family,
                },
            ):
                return err
            tools_called_this_round.add("list_round_results")
            output = list_round_results_for_root(
                _ROOT,
                job_id=job_id if job_id is not None else current_job,
                family=family if family is not None else family_name,
                order=order,
                offset=offset,
                limit=limit,
            )
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "list_round_results",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool
        async def get_round_result(
            research_round_id: str,
            detail: bool = False,
            job_id: int | None = None,
            family: str | None = None,
        ) -> str:
            started = monotonic()
            tool_input = json.dumps(
                {
                    "root": str(_ROOT),
                    "current_job": current_job,
                    "research_round_id": research_round_id,
                    "detail": detail,
                    "job_id": job_id,
                    "family": family,
                },
                default=str,
            )
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "get_round_result",
                tool_input,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if err := _validate_tool_args(
                GetRoundResultArgs,
                {
                    "research_round_id": research_round_id,
                    "detail": detail,
                    "job_id": job_id,
                    "family": family,
                },
            ):
                return err
            tools_called_this_round.add("get_round_result")
            try:
                output = get_round_result_for_root(
                    _ROOT,
                    research_round_id=research_round_id,
                    detail=detail,
                    job_id=job_id if job_id is not None else current_job,
                    family=family if family is not None else family_name,
                )
            except KeyError as exc:
                output = round_result_not_found_envelope(research_round_id, exc)
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
                "get_round_result",
                output,
                status=parsed_status,
                error_type=error_type,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool(name_override="list_rejections")
        async def list_rejections_tool(
            round_number: int | None = None,
            rejection_code: str | None = None,
            limit: int = 25,
        ) -> str:
            """List validator/compile rejections for the current job.

            Optional filters: round_number to scope to one round; rejection_code
            to scope to one category (e.g. structural_missing_requested_primitives). Returns up
            to `limit` records, most-recent rounds first.
            """
            from rejection_artifact import list_rejections

            started = monotonic()
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "list_rejections",
                json.dumps(
                    {
                        "round_number": round_number,
                        "rejection_code": rejection_code,
                        "limit": limit,
                    },
                    default=str,
                ),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if err := _validate_tool_args(
                ListRejectionsArgs,
                {
                    "round_number": round_number,
                    "rejection_code": rejection_code,
                    "limit": limit,
                },
            ):
                return err
            if current_job is None:
                output = json.dumps({"status": "error", "error": "no current job"})
            else:
                items = list_rejections(
                    _ROOT,
                    job=current_job,
                    round_number=round_number,
                    rejection_code=rejection_code,
                    limit=limit,
                )
                output = json.dumps([it.model_dump() for it in items], default=str)
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "list_rejections",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool(name_override="get_rejection")
        async def get_rejection_tool(round_number: int, thesis_id: str) -> str:
            """Fetch the full rejection record for one (round, thesis_id)."""
            from rejection_artifact import get_rejection

            started = monotonic()
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "get_rejection",
                json.dumps({"round_number": round_number, "thesis_id": thesis_id}),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if err := _validate_tool_args(
                GetRejectionArgs, {"round_number": round_number, "thesis_id": thesis_id}
            ):
                return err
            if current_job is None:
                output = json.dumps({"status": "error", "error": "no current job"})
            else:
                obj = get_rejection(
                    _ROOT, job=current_job, round_number=round_number, thesis_id=thesis_id
                )
                if obj is None:
                    output = json.dumps({"status": "not_found"})
                else:
                    output = obj.model_dump_json()
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "get_rejection",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        @function_tool(name_override="rejection_pattern_summary")
        async def rejection_pattern_summary_tool(window_rounds: int = 10) -> str:
            """Group recent rejections by rejection_code and return counts.

            Use this to detect repeating failure modes (e.g. structural_missing_requested_primitives
            firing 4+ times). The summary covers the last `window_rounds` rounds.
            """
            from rejection_artifact import rejection_pattern_summary

            started = monotonic()
            trace_agent_tool_call(
                "research-conductor",
                trace_id,
                "rejection_pattern_summary",
                json.dumps({"window_rounds": window_rounds}),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            if err := _validate_tool_args(
                RejectionPatternSummaryArgs, {"window_rounds": window_rounds}
            ):
                return err
            if current_job is None:
                output = json.dumps({"status": "error", "error": "no current job"})
            else:
                summary = rejection_pattern_summary(
                    _ROOT, job=current_job, window_rounds=window_rounds
                )
                output = json.dumps(summary)
            trace_agent_tool_result(
                "research-conductor",
                trace_id,
                "rejection_pattern_summary",
                output,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output

        output_schema = _conductor_output_schema()
        agent = OAIAgent(
            name="research-conductor",
            instructions=system_prompt,
            tools=[analyze_trades] if trades_file else [],
            model=model,
            output_type=output_schema,
        )
        if not hasattr(agent, "output_type"):
            setattr(agent, "output_type", output_schema)

        async with asyncio.timeout(_conductor_timeout_seconds()):
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
                coerced_output = result.final_output_as(str)
            except Exception as exc:
                log.warning("final_output_as failed for conductor: %s", exc)
                result_text = ""
            else:
                if isinstance(coerced_output, MechanismProposal):
                    result_text = coerced_output.model_dump_json()
                elif isinstance(coerced_output, str):
                    result_text = coerced_output
                elif coerced_output is not None:
                    result_text = json.dumps(coerced_output, default=str)
        if not result_text:
            final_output = getattr(result, "final_output", None)
            if isinstance(final_output, MechanismProposal):
                result_text = final_output.model_dump_json()
            elif isinstance(final_output, str):
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
            trace_id=trace_id,
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
        return ConductorResult(status="conductor_error", error="timeout")
    except Exception as exc:
        error_text = str(exc)
        error_kind = "proxy_unavailable" if "openai-oauth proxy" in error_text else "exception"
        # rule H: surface the actual exception type + message + traceback so an
        # operator can act on it; a bare "ERROR: exception" is undiagnosable.
        log.exception(
            "CONDUCTOR agent run failed: kind=%s type=%s msg=%s",
            error_kind,
            type(exc).__name__,
            error_text,
        )
        trace(
            "CONDUCTOR",
            f"ERROR: {error_kind}: {type(exc).__name__}: {error_text}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="exception",
            final_outcome="conductor_error",
        )
        session_finished = True
        return ConductorResult(status="conductor_error", error=error_kind)

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
            "has_thesis": bool(parsed.get("rule")) if parsed else False,
        },
    )

    if parsed:
        try:
            proposal = MechanismProposal.model_validate(parsed)
        except Exception as exc:
            validation_reason = str(exc)
            trace(
                "CONDUCTOR",
                f"mechanism proposal validation failed (len={len(result_text)})",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            failure = ConductorResult(
                status="conductor_error",
                error="validation_failed",
                validation_reason=validation_reason,
                reasoning=str(parsed.get("story", "")),
                thesis=parsed if isinstance(parsed, dict) else None,
                tools_called=frozenset(tools_called_this_round),
            )
        else:
            _REFINEMENT_RECORDER.finish_session(
                session_id=refinement_session["session_id"],
                stopping_reason="mechanism_proposal",
                final_outcome="accepted",
            )
            session_finished = True
            return ConductorResult(
                status="ok",
                thesis=proposal.model_dump(mode="json"),
                reasoning=proposal.story,
                tools_called=frozenset(tools_called_this_round),
            )
    else:
        trace(
            "CONDUCTOR",
            f"parse failed (len={len(result_text)})",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        failure = ConductorResult(status="conductor_error", error="parse_failed")

    if not session_finished:
        _REFINEMENT_RECORDER.finish_session(
            session_id=refinement_session["session_id"],
            stopping_reason="invalid_output",
            final_outcome="retry_required",
        )

    return failure


def run_research_conductor_sync(
    trades_file: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
    agent_reflexions: dict[str, str] | None = None,
    current_job: int | None = None,
    rendered_corpus: str = "",
) -> ConductorResult | None:
    return _run_coroutine_sync(
        run_research_conductor(
            trades_file,
            latest_outcome,
            research_round,
            family_name,
            strategy_events_file=strategy_events_file,
            diagnostics_file=diagnostics_file,
            rejection_feedback=rejection_feedback,
            agent_reflexions=agent_reflexions,
            current_job=current_job,
            rendered_corpus=rendered_corpus,
        )
    )
