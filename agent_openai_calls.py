from __future__ import annotations

import subprocess
from time import monotonic
from typing import Any

import agent_infra
import agent_prompts
from agent_runners import _validate_output
from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from agent_token_usage import _accumulate_usage
from autoresearch_constants import DEFAULT_AGENT_MODEL as _OPENAI_AGENT_MODEL
from autoresearch_logging import get_logger
from trace_sdk import (
    trace,
    trace_agent_prompt,
    trace_agent_response,
    trace_agent_tool_call,
    trace_agent_tool_result,
)
from web_research_cli import WebResearchCliError, run_codex_web_research

log = get_logger(__name__)

_PROVIDER = "openai"
_MODEL = _OPENAI_AGENT_MODEL


def _trace(component: str, message: str, data: dict | None = None) -> None:
    trace(component, message, data, model_provider=_PROVIDER, model_name=_MODEL)


def _trace_prompt(agent_name: str, prompt: str, system_prompt: str = "") -> str:
    return trace_agent_prompt(
        agent_name, prompt, system_prompt, model_provider=_PROVIDER, model_name=_MODEL
    )


def _trace_response(
    agent_name: str, trace_id: str, raw_text: str, parsed: dict | None = None
) -> None:
    trace_agent_response(
        agent_name, trace_id, raw_text, parsed, model_provider=_PROVIDER, model_name=_MODEL
    )


def _trace_tool(agent_name: str, trace_id: str, tool_name: str, tool_input: str = "") -> None:
    trace_agent_tool_call(
        agent_name, trace_id, tool_name, tool_input, model_provider=_PROVIDER, model_name=_MODEL
    )


def _trace_tool_result(
    agent_name: str,
    trace_id: str,
    tool_name: str,
    tool_output: str,
    *,
    status: str = "ok",
    error_type: str = "",
    truncated: bool = False,
    duration_ms: int | None = None,
) -> None:
    trace_agent_tool_result(
        agent_name,
        trace_id,
        tool_name,
        tool_output,
        status=status,
        error_type=error_type,
        truncated=truncated,
        duration_ms=duration_ms,
        model_provider=_PROVIDER,
        model_name=_MODEL,
    )


async def _run_web_research_openai(
    prompt: str,
    retries: int = agent_prompts.MAX_RETRIES,
) -> dict[str, Any] | None:
    """Run web research through Codex CLI with OpenAI live web search enabled."""
    for attempt in range(1, retries + 1):
        trace_id = _trace_prompt(
            "openai-web-researcher",
            prompt,
            agent_prompts.WEB_RESEARCHER_SYSTEM_PROMPT,
        )
        _trace(
            "OPENAI_AGENT",
            f"web-researcher attempt={attempt}/{retries} model={_MODEL} api=codex_cli_web_search",
        )
        try:
            output, metadata = run_codex_web_research(
                prompt,
                instructions=agent_prompts.WEB_RESEARCHER_SYSTEM_PROMPT,
                model=_MODEL,
            )
            _trace("OPENAI_AGENT", "web-researcher codex_cli completed", metadata)
            usage = metadata.get("usage")
            if isinstance(usage, dict):
                usage = {**usage, "usage_source": metadata.get("usage_source", "")}
                _accumulate_usage("web-researcher", usage, provider=_PROVIDER, model=_MODEL)
            parsed_result = agent_infra._parse_json_detailed(output)
            parsed = parsed_result.get("parsed") if parsed_result.get("status") == "ok" else None
            _trace_response("openai-web-researcher", trace_id, output, parsed)
            if parsed is not None and _validate_output("web-researcher", parsed):
                _trace("OPENAI_AGENT", "web-researcher VALIDATED OK")
                return parsed
            _trace("OPENAI_AGENT", "web-researcher validate FAILED")
            error = agent_infra._structured_error(
                "web-researcher",
                "validation" if parsed is not None else parsed_result.get("kind", "parse"),
                (
                    "Web research response failed validation"
                    if parsed is not None
                    else "Web research response could not be parsed"
                ),
                attempt=attempt,
                details=parsed_result.get("message"),
                excerpt=parsed_result.get("excerpt") or (output[:200] if output else None),
            )
        except WebResearchCliError as exc:
            _trace("OPENAI_AGENT", f"web-researcher ERROR: {exc.__class__.__name__}: {exc}")
            log.warning(
                "web-researcher attempt=%d failed: %s: %s", attempt, exc.__class__.__name__, exc
            )
            accumulate_agents_sdk_result_usage(
                "web-researcher", None, provider=_PROVIDER, model=_MODEL
            )
            error = agent_infra._structured_error(
                "web-researcher",
                "transport",
                "Web research execution failed",
                attempt=attempt,
                details=f"{exc.__class__.__name__}: {exc}",
            )

        if attempt < retries:
            prompt = f"RETRY: Return valid JSON with 'findings' array and 'summary'. {prompt}"
            continue

        return error

    return agent_infra._structured_error(
        "web-researcher",
        "exhausted",
        "Web research retries exhausted",
        attempt=retries,
    )


DIAGNOSTIC_ANALYST_PROMPT = agent_prompts.DIAGNOSTIC_ANALYST_SYSTEM_PROMPT


async def _run_diagnostic_analyst_openai(
    prompt: str,
    retries: int = agent_prompts.MAX_RETRIES,
) -> dict[str, Any] | None:
    """Run diagnostic analyst using OpenAI Agents SDK with local function tools."""
    import sys as _sys

    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import function_tool
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

    agent_infra._ensure_oauth_proxy()
    current_trace_id = "active"

    @function_tool
    def read_file(file_path: str) -> str:
        """Read a file from the local filesystem and return its contents."""
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        _trace_tool("codex-analyst", current_trace_id, "read_file", file_path)
        try:
            with open(file_path) as f:
                content = f.read()
            if len(content) > 50000:
                truncated = True
                output = content[:50000] + f"\n... (truncated, {len(content)} total chars)"
            else:
                output = content
        except Exception as e:
            log.warning("read_file tool failed for path=%s: %s", file_path, e)
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        _trace_tool_result(
            "codex-analyst",
            current_trace_id,
            "read_file",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
        )
        return output

    @function_tool
    def run_python(code: str) -> str:
        """Execute Python code locally and return stdout + stderr."""
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        _trace_tool("codex-analyst", current_trace_id, "run_python", code)
        try:
            result = subprocess.run(
                [_sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            if result.returncode != 0:
                output += f"\nEXIT CODE: {result.returncode}"
            if len(output) > 30000:
                truncated = True
                output = output[:30000] + "\n... (truncated)"
        except subprocess.TimeoutExpired:
            status = "error"
            error_type = "TimeoutExpired"
            output = "ERROR: Code execution timed out (60s limit)"
        except Exception as e:
            log.warning("run_python tool failed: %s", e)
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        _trace_tool_result(
            "codex-analyst",
            current_trace_id,
            "run_python",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
        )
        return output

    client = agent_infra._get_openai_client(agent_infra._OAUTH_PROXY_URL)
    model = OpenAIChatCompletionsModel(model=_OPENAI_AGENT_MODEL, openai_client=client)

    agent = OAIAgent(
        name="codex-diagnostic-analyst",
        instructions=DIAGNOSTIC_ANALYST_PROMPT,
        tools=[read_file, run_python],
        model=model,
    )

    for attempt in range(1, retries + 1):
        trace_id = _trace_prompt("codex-analyst", prompt, DIAGNOSTIC_ANALYST_PROMPT)
        current_trace_id = trace_id
        _trace(
            "OPENAI_AGENT", f"codex-analyst attempt={attempt}/{retries} model={_MODEL} api=chatcmpl"
        )
        try:
            result = OAIRunner.run_streamed(
                agent,
                prompt,
                run_config=OAIRunConfig(
                    tracing_disabled=True,
                ),
            )
            async for _event in result.stream_events():
                pass

            output = result.final_output or ""
            accumulate_agents_sdk_result_usage(
                "codex-analyst",
                result,
                provider=_PROVIDER,
                model=_MODEL,
                input_text=f"{DIAGNOSTIC_ANALYST_PROMPT}\n\n{prompt}",
                output_text=output,
            )
            parsed_result = agent_infra._parse_json_detailed(output)
            parsed = parsed_result.get("parsed") if parsed_result.get("status") == "ok" else None
            _trace_response("codex-analyst", trace_id, output, parsed)
            if parsed is not None and _validate_output("diagnostic-analyst", parsed):
                _trace("OPENAI_AGENT", "codex-analyst VALIDATED OK")
                return parsed
            _trace("OPENAI_AGENT", "codex-analyst validate FAILED")
            error = agent_infra._structured_error(
                "diagnostic-analyst",
                "validation" if parsed is not None else parsed_result.get("kind", "parse"),
                (
                    "Diagnostic analyst response failed validation"
                    if parsed is not None
                    else "Diagnostic analyst response could not be parsed"
                ),
                attempt=attempt,
                details=parsed_result.get("message"),
                excerpt=parsed_result.get("excerpt") or (output[:200] if output else None),
            )
        except Exception as exc:
            _trace("OPENAI_AGENT", f"codex-analyst ERROR: {exc.__class__.__name__}: {exc}")
            log.warning(
                "codex-analyst attempt=%d failed: %s: %s", attempt, exc.__class__.__name__, exc
            )
            accumulate_agents_sdk_result_usage(
                "codex-analyst", None, provider=_PROVIDER, model=_MODEL
            )
            error = agent_infra._structured_error(
                "diagnostic-analyst",
                "transport",
                "Diagnostic analyst execution failed",
                attempt=attempt,
                details=exc.__class__.__name__,
            )

        if attempt < retries:
            prompt = (
                f"RETRY: Return valid JSON with key_anomalies array and overall_diagnosis. {prompt}"
            )
            continue

        return error

    return agent_infra._structured_error(
        "diagnostic-analyst",
        "exhausted",
        "Diagnostic analyst retries exhausted",
        attempt=retries,
    )
