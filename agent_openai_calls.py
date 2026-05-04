from __future__ import annotations

import subprocess
from typing import Any

import agent_infra
import agent_prompts
from agent_runners import _validate_output
from agent_token_usage import _accumulate_result_usage
from trace_sdk import trace, trace_agent_prompt, trace_agent_response, trace_agent_tool_call


async def _run_web_research_openai(
    prompt: str,
    retries: int = agent_prompts.MAX_RETRIES,
) -> dict[str, Any] | None:
    """Run web research using OpenAI Agents SDK via openai-oauth proxy."""
    from agents import Agent as OAIAgent
    from agents import ModelSettings as OAIModelSettings
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import WebSearchTool
    from agents.models.openai_responses import OpenAIResponsesModel
    from openai import AsyncOpenAI

    agent_infra._ensure_oauth_proxy()

    client = AsyncOpenAI(api_key="unused", base_url=agent_infra._OAUTH_PROXY_URL)
    model = OpenAIResponsesModel(model="gpt-5.5", openai_client=client)

    agent = OAIAgent(
        name="web-researcher",
        instructions=agent_prompts.WEB_RESEARCHER_SYSTEM_PROMPT,
        tools=[WebSearchTool()],
        model=model,
    )

    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(
            "openai-web-researcher",
            prompt,
            agent_prompts.WEB_RESEARCHER_SYSTEM_PROMPT,
            model_provider="openai",
            model_name="gpt-5.5",
        )
        trace(
            "OPENAI_AGENT",
            f"web-researcher attempt={attempt}/{retries} model=gpt-5.5 api=responses",
            model_provider="openai",
            model_name="gpt-5.5",
        )
        try:
            result = OAIRunner.run_streamed(
                agent,
                prompt,
                run_config=OAIRunConfig(
                    model_settings=OAIModelSettings(store=False),
                    tracing_disabled=True,
                ),
            )
            async for _event in result.stream_events():
                pass

            output = result.final_output or ""
            _accumulate_result_usage("web-researcher", result)
            parsed_result = agent_infra._parse_json_detailed(output)
            parsed = parsed_result.get("parsed") if parsed_result.get("status") == "ok" else None
            trace_agent_response(
                "openai-web-researcher",
                trace_id,
                output,
                parsed,
                model_provider="openai",
                model_name="gpt-5.5",
            )
            if parsed is not None and _validate_output("web-researcher", parsed):
                trace(
                    "OPENAI_AGENT",
                    "web-researcher VALIDATED OK",
                    model_provider="openai",
                    model_name="gpt-5.5",
                )
                return parsed
            trace(
                "OPENAI_AGENT",
                "web-researcher validate FAILED",
                model_provider="openai",
                model_name="gpt-5.5",
            )
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
        except Exception as exc:
            trace(
                "OPENAI_AGENT",
                f"web-researcher ERROR: {exc.__class__.__name__}",
                model_provider="openai",
                model_name="gpt-5.5",
            )
            _accumulate_result_usage("web-researcher", None)
            error = agent_infra._structured_error(
                "web-researcher",
                "transport",
                "Web research execution failed",
                attempt=attempt,
                details=exc.__class__.__name__,
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
    from openai import AsyncOpenAI

    agent_infra._ensure_oauth_proxy()
    current_trace_id = "active"

    @function_tool
    def read_file(file_path: str) -> str:
        """Read a file from the local filesystem and return its contents."""
        trace_agent_tool_call(
            "codex-analyst",
            current_trace_id,
            "read_file",
            file_path,
            model_provider="openai",
            model_name="gpt-5.5",
        )
        try:
            with open(file_path) as f:
                content = f.read()
            if len(content) > 50000:
                return content[:50000] + f"\n... (truncated, {len(content)} total chars)"
            return content
        except Exception as e:
            return f"ERROR: {e}"

    @function_tool
    def run_python(code: str) -> str:
        """Execute Python code locally and return stdout + stderr."""
        trace_agent_tool_call(
            "codex-analyst",
            current_trace_id,
            "run_python",
            code,
            model_provider="openai",
            model_name="gpt-5.5",
        )
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
                output = output[:30000] + "\n... (truncated)"
            return output
        except subprocess.TimeoutExpired:
            return "ERROR: Code execution timed out (60s limit)"
        except Exception as e:
            return f"ERROR: {e}"

    client = AsyncOpenAI(api_key="unused", base_url=agent_infra._OAUTH_PROXY_URL)
    model = OpenAIChatCompletionsModel(model="gpt-5.5", openai_client=client)

    agent = OAIAgent(
        name="codex-diagnostic-analyst",
        instructions=DIAGNOSTIC_ANALYST_PROMPT,
        tools=[read_file, run_python],
        model=model,
    )

    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(
            "codex-analyst",
            prompt,
            DIAGNOSTIC_ANALYST_PROMPT,
            model_provider="openai",
            model_name="gpt-5.5",
        )
        current_trace_id = trace_id
        trace(
            "OPENAI_AGENT",
            f"codex-analyst attempt={attempt}/{retries} model=gpt-5.5 api=chatcmpl",
            model_provider="openai",
            model_name="gpt-5.5",
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
            _accumulate_result_usage("codex-analyst", result)
            parsed_result = agent_infra._parse_json_detailed(output)
            parsed = parsed_result.get("parsed") if parsed_result.get("status") == "ok" else None
            trace_agent_response(
                "codex-analyst",
                trace_id,
                output,
                parsed,
                model_provider="openai",
                model_name="gpt-5.5",
            )
            if parsed is not None and _validate_output("diagnostic-analyst", parsed):
                trace(
                    "OPENAI_AGENT",
                    "codex-analyst VALIDATED OK",
                    model_provider="openai",
                    model_name="gpt-5.5",
                )
                return parsed
            trace(
                "OPENAI_AGENT",
                "codex-analyst validate FAILED",
                model_provider="openai",
                model_name="gpt-5.5",
            )
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
            trace(
                "OPENAI_AGENT",
                f"codex-analyst ERROR: {exc.__class__.__name__}",
                model_provider="openai",
                model_name="gpt-5.5",
            )
            _accumulate_result_usage("codex-analyst", None)
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
