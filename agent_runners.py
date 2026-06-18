from __future__ import annotations

import asyncio
from typing import Any

from agents import Agent as OAIAgent
from agents import ModelSettings as OAIModelSettings
from agents import RunConfig as OAIRunConfig
from agents import Runner as OAIRunner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

import agent_infra
from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from autoresearch_constants import DEFAULT_AGENT_MODEL
from autoresearch_logging import get_logger
from research_paths import _OAUTH_PROXY_URL, _extract_runner_output_text

log = get_logger(__name__)

DEFAULT_AGENT_RETRIES = 2


def _validate_output(agent_name: str, parsed: dict[str, Any]) -> bool:
    """Generic SDK runner accepts any parsed JSON object.

    Domain-specific schemas are enforced at the live call sites. Keeping legacy
    agent-name branches here made retired v1 prompts look behaviorally relevant.
    """
    del agent_name
    return isinstance(parsed, dict)


async def _drain_streamed(streamed_run: Any) -> None:
    stream = streamed_run.stream_events()
    try:
        async for _ in stream:
            pass
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass


async def _run_single_agent(
    name: str,
    prompt: str,
    agent_def: Any,
    retries: int = DEFAULT_AGENT_RETRIES,
    timeout: int = agent_infra.SDK_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Run a single agent directly with the OpenAI Agents SDK."""
    from trace_sdk import trace, trace_agent_prompt, trace_agent_response

    client = agent_infra._get_openai_client(_OAUTH_PROXY_URL)
    model_name = getattr(agent_def, "model", DEFAULT_AGENT_MODEL)
    model_provider = getattr(agent_def, "provider", "openai")
    model = OpenAIChatCompletionsModel(model=model_name, openai_client=client)
    model_settings = OAIModelSettings(store=False)
    agent = OAIAgent(
        name=name,
        instructions=agent_def.prompt,
        tools=list(agent_def.tools or []),
        model=model,
    )

    original_prompt = prompt
    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(
            f"sdk-{name}",
            prompt,
            agent_def.prompt,
            model_provider=model_provider,
            model_name=model_name,
        )
        trace(
            "AGENT_SDK",
            f"{name} attempt={attempt}/{retries} model={model_name} tools={agent_def.tools}",
            model_provider=model_provider,
            model_name=model_name,
        )
        result = None
        try:
            result = OAIRunner.run_streamed(
                agent,
                prompt,
                max_turns=agent_def.maxTurns or 10,
                run_config=OAIRunConfig(
                    model_settings=model_settings,
                    tracing_disabled=True,
                ),
            )
            await asyncio.wait_for(_drain_streamed(result), timeout)
        except asyncio.TimeoutError:
            trace(
                "AGENT_SDK",
                f"{name} TIMEOUT after {timeout}s",
                model_provider=model_provider,
                model_name=model_name,
            )
            accumulate_agents_sdk_result_usage(
                name,
                result,
                provider=model_provider,
                model=model_name,
                input_text=f"{agent_def.prompt}\n\n{prompt}",
                trace_id=trace_id,
            )
            error = agent_infra._structured_error(
                name,
                "timeout",
                f"Timed out after {timeout}s",
                attempt=attempt,
            )
            if attempt < retries:
                continue
            return error
        except Exception as exc:
            trace(
                "AGENT_SDK",
                f"{name} ERROR: {exc.__class__.__name__}",
                model_provider=model_provider,
                model_name=model_name,
            )
            accumulate_agents_sdk_result_usage(
                name, None, provider=model_provider, model=model_name, trace_id=trace_id
            )
            error = agent_infra._structured_error(
                name,
                "transport",
                "Agent execution failed",
                attempt=attempt,
                details=exc.__class__.__name__,
            )
            if attempt < retries:
                continue
            return error

        result_text = _extract_runner_output_text(result)
        accumulate_agents_sdk_result_usage(
            name,
            result,
            provider=model_provider,
            model=model_name,
            input_text=f"{agent_def.prompt}\n\n{prompt}",
            output_text=result_text,
            trace_id=trace_id,
        )

        parsed_result = agent_infra._parse_json_detailed(result_text)
        parsed = parsed_result.get("parsed") if parsed_result.get("status") == "ok" else None
        trace_agent_response(
            f"sdk-{name}",
            trace_id,
            result_text,
            parsed,
            model_provider=model_provider,
            model_name=model_name,
        )
        if parsed is not None:
            if _validate_output(name, parsed):
                trace(
                    "AGENT_SDK",
                    f"{name} VALIDATED OK",
                    model_provider=model_provider,
                    model_name=model_name,
                )
                return parsed
            trace(
                "AGENT_SDK",
                f"{name} quality check FAILED",
                model_provider=model_provider,
                model_name=model_name,
            )
            error = agent_infra._structured_error(
                name,
                "validation",
                "Agent response failed validation",
                attempt=attempt,
            )
        else:
            trace(
                "AGENT_SDK",
                f"{name} parse FAILED",
                model_provider=model_provider,
                model_name=model_name,
            )
            error = agent_infra._structured_error(
                name,
                parsed_result.get("kind", "parse"),
                "Agent response could not be parsed",
                attempt=attempt,
                details=parsed_result.get("message"),
                excerpt=parsed_result.get("excerpt")
                or (result_text[:200] if result_text else None),
            )

        if attempt < retries:
            retry_reason = (
                "Your previous response failed semantic validation."
                if error.get("kind") == "validation"
                else "Your previous response could not be parsed as valid JSON."
            )
            prompt = (
                f"RETRY: {retry_reason} Return the required schema.\n\n"
                f"Original request:\n{original_prompt}"
            )
            continue

        return error

    return agent_infra._structured_error(
        name,
        "exhausted",
        "Agent retries exhausted",
        attempt=retries,
    )
