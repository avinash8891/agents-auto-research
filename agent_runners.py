from __future__ import annotations

import asyncio
import subprocess
from typing import Any

import agent_infra
import agent_prompts


async def _query_with_timeout(
    prompt: str,
    agent_def: Any,
    timeout: int,
):
    """Async generator: yield text chunks from the agent with a deadline."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query

    deadline = asyncio.get_event_loop().time() + timeout
    got_assistant_text = False
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=agent_def.prompt,
            model=agent_def.model,
            allowed_tools=list(agent_def.tools or []),
            permission_mode="bypassPermissions",
            max_turns=agent_def.maxTurns or 10,
        ),
    ):
        if asyncio.get_event_loop().time() > deadline:
            raise asyncio.TimeoutError()
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    got_assistant_text = True
                    yield block.text
        elif isinstance(message, ResultMessage):
            if not got_assistant_text and hasattr(message, "result") and message.result:
                yield str(message.result)


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
        if not t.get("thesis_id") or not t.get("hypothesis"):
            return False
        has_changes = bool(t.get("config_changes"))
        needs_code = t.get("requires_code_change", False)
        if not has_changes and not needs_code:
            return False
        return True

    return True


def _run_cli_agent(
    name: str,
    system_prompt: str,
    user_prompt: str,
    retries: int = 2,
    timeout: int = agent_infra.CLI_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Run an agent via `claude --print` CLI subprocess."""
    from trace_logger import trace, trace_agent_prompt, trace_agent_response

    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(f"cli-{name}", user_prompt, system_prompt)
        trace("CLI_AGENT", f"{name} attempt={attempt}/{retries} timeout={timeout}s")
        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    user_prompt,
                    "--model",
                    "sonnet",
                    "--system-prompt",
                    system_prompt,
                    "--max-turns",
                    "10",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                stderr = result.stderr.strip()[:200] if result.stderr else ""
                trace("CLI_AGENT", f"{name} exit={result.returncode}", {"stderr": stderr})
                print(f"CLI_AGENT {name} exit={result.returncode} (attempt {attempt}): {stderr}")
                if attempt < retries:
                    continue
                return None

            parsed = agent_infra._parse_json(output)
            trace_agent_response(f"cli-{name}", trace_id, output, parsed)
            if parsed is not None:
                if _validate_output(name, parsed):
                    trace("CLI_AGENT", f"{name} VALIDATED OK")
                    return parsed
                trace("CLI_AGENT", f"{name} quality check failed")
                print(f"CLI_AGENT {name} quality check failed (attempt {attempt})")
            else:
                print(f"CLI_AGENT {name} parse failed (attempt {attempt}): {output[:200]}")

        except subprocess.TimeoutExpired:
            trace("CLI_AGENT", f"{name} TIMEOUT after {timeout}s")
            print(f"CLI_AGENT {name} timeout after {timeout}s (attempt {attempt})")
        except Exception as exc:
            trace("CLI_AGENT", f"{name} ERROR: {exc}")
            print(f"CLI_AGENT {name} error (attempt {attempt}): {exc}")

        if attempt < retries:
            user_prompt = (
                f"RETRY: Your previous response could not be parsed as valid JSON "
                f"or was missing required fields. {user_prompt}"
            )

    return None


async def _run_single_agent(
    name: str,
    prompt: str,
    agent_def: Any,
    retries: int = agent_prompts.MAX_RETRIES,
    timeout: int = agent_infra.SDK_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Run a single agent directly with its system prompt."""
    from trace_logger import trace, trace_agent_prompt, trace_agent_response

    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(f"sdk-{name}", prompt, agent_def.prompt)
        trace(
            "AGENT_SDK",
            f"{name} attempt={attempt}/{retries} model={agent_def.model} tools={agent_def.tools}",
        )
        result_text = ""
        try:
            async for chunk in _query_with_timeout(prompt, agent_def, timeout):
                result_text += chunk
        except asyncio.TimeoutError:
            trace("AGENT_SDK", f"{name} TIMEOUT after {timeout}s")
            print(f"AGENT_SDK {name} timeout after {timeout}s (attempt {attempt})")
            if attempt < retries:
                continue
            return None
        except Exception as exc:
            trace("AGENT_SDK", f"{name} ERROR: {exc}")
            print(f"AGENT_SDK {name} error (attempt {attempt}): {exc}")
            if attempt < retries:
                continue
            return None

        parsed = agent_infra._parse_json(result_text)
        trace_agent_response(f"sdk-{name}", trace_id, result_text, parsed)
        if parsed is not None:
            if _validate_output(name, parsed):
                trace("AGENT_SDK", f"{name} VALIDATED OK")
                return parsed
            trace("AGENT_SDK", f"{name} quality check FAILED")
            print(f"AGENT_SDK {name} quality check failed (attempt {attempt})")
        else:
            trace("AGENT_SDK", f"{name} parse FAILED")
            print(f"AGENT_SDK {name} parse failed (attempt {attempt})")

        if attempt < retries:
            prompt = (
                f"RETRY: Your previous response could not be parsed as valid JSON "
                f"or was missing required fields. {prompt}"
            )

    if agent_infra.cli_fallback_enabled():
        trace("AGENT_SDK", f"{name} falling back to Claude CLI")
        return _run_cli_agent(name, agent_def.prompt, prompt, retries=1)

    return None
