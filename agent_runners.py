from __future__ import annotations

import asyncio
from typing import Any

from agents import Agent as OAIAgent
from agents import ModelSettings as OAIModelSettings
from agents import RunConfig as OAIRunConfig
from agents import Runner as OAIRunner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

import agent_infra
import agent_prompts
from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from autoresearch_constants import DEFAULT_AGENT_MODEL
from autoresearch_logging import get_logger
from research_paths import _OAUTH_PROXY_URL, _extract_runner_output_text
from thesis_validator import normalize_thesis_payload

log = get_logger(__name__)


def _validate_output(agent_name: str, parsed: dict[str, Any]) -> bool:
    """Quality check: ensure agent output has required fields and structure."""
    if agent_name == "diagnostic-analyst":
        anomalies = parsed.get("key_anomalies")
        diagnosis = parsed.get("overall_diagnosis")
        if not isinstance(anomalies, list) or not isinstance(diagnosis, str):
            return False
        if not anomalies or not diagnosis:
            return False
        for a in anomalies:
            if not isinstance(a, dict):
                return False
            if not a.get("pattern") or not a.get("numbers"):
                return False
        return True

    if agent_name == "web-researcher":
        findings = parsed.get("findings")
        if not isinstance(findings, list) or not findings:
            return False
        for f in findings:
            if not isinstance(f, dict):
                return False
            if not f.get("topic") or not f.get("finding"):
                return False
        return bool(parsed.get("summary"))

    if agent_name == "research-agent":
        if parsed.get("should_stop") is True:
            return True
        theses = parsed.get("suggested_theses")
        if not isinstance(theses, list) or not theses:
            return False
        t = theses[0]
        if not isinstance(t, dict):
            return False
        candidate = normalize_thesis_payload(dict(t))
        required_scalar_fields = (
            "hypothesis",
            "mechanism",
            "mechanism_dimension",
            "dimension_novelty",
        )
        if any(not str(candidate.get(field) or "").strip() for field in required_scalar_fields):
            return False
        config_changes = candidate.get("config_changes")
        requires_code_change = bool(candidate.get("requires_code_change"))
        if not isinstance(config_changes, dict):
            return False
        if not config_changes and not requires_code_change:
            return False
        expected_effects = candidate.get("expected_effects")
        if not isinstance(expected_effects, list) or not expected_effects:
            return False
        for effect in expected_effects:
            if not isinstance(effect, dict):
                return False
            if not str(effect.get("metric") or "").strip():
                return False
            if not str(effect.get("direction") or "").strip():
                return False
        disqualifiers = candidate.get("disqualifiers")
        if not isinstance(disqualifiers, list) or not disqualifiers:
            return False
        for disqualifier in disqualifiers:
            if not isinstance(disqualifier, dict):
                return False
            if not str(disqualifier.get("name") or "").strip():
                return False
            if not str(disqualifier.get("condition") or "").strip():
                return False
        if requires_code_change:
            requested_primitives = candidate.get("requested_primitives")
            if not isinstance(requested_primitives, list) or not requested_primitives:
                return False
        return True

    return True


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
    retries: int = agent_prompts.MAX_RETRIES,
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
                name, None, provider=model_provider, model=model_name, trace_id=trace_id
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
            prompt = (
                f"RETRY: Your previous response could not be parsed as valid JSON "
                f"or was missing required fields. {prompt}"
            )
            continue

        return error

    return agent_infra._structured_error(
        name,
        "exhausted",
        "Agent retries exhausted",
        attempt=retries,
    )
