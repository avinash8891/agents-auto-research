from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from autoresearch_constants import DEFAULT_AGENT_MODEL
from autoresearch_logging import get_logger
from research_paths import _ROOT

log = get_logger(__name__)

WEB_RESEARCH_CLI_TIMEOUT_ENV = "AUTORESEARCH_WEB_RESEARCH_TIMEOUT"
DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS = 300
WEB_RESEARCH_REASONING_EFFORT = "low"


class WebResearchCliError(RuntimeError):
    """Raised when the Codex CLI web-search boundary fails before JSON parsing."""


def _find_codex_cli() -> str | None:
    return shutil.which("codex")


def _resolve_timeout_seconds(timeout_seconds: int | None) -> int:
    if timeout_seconds is not None:
        return timeout_seconds
    raw_timeout = os.environ.get(WEB_RESEARCH_CLI_TIMEOUT_ENV)
    if raw_timeout is None:
        return DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS
    try:
        parsed_timeout = int(raw_timeout)
    except ValueError:
        log.warning(
            "invalid %s=%r; using default timeout %ds",
            WEB_RESEARCH_CLI_TIMEOUT_ENV,
            raw_timeout,
            DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS,
        )
        return DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS
    if parsed_timeout <= 0:
        log.warning(
            "non-positive %s=%r; using default timeout %ds",
            WEB_RESEARCH_CLI_TIMEOUT_ENV,
            raw_timeout,
            DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS,
        )
        return DEFAULT_WEB_RESEARCH_CLI_TIMEOUT_SECONDS
    return parsed_timeout


def _extract_codex_token_usage(stdout: str) -> tuple[dict[str, int], str] | None:
    usage_result: tuple[dict[str, int], str] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            raw_usage = event["usage"]
            input_tokens = int(raw_usage.get("input_tokens") or 0)
            output_tokens = int(raw_usage.get("output_tokens") or 0)
            if "total_tokens" in raw_usage and raw_usage["total_tokens"] is not None:
                total_tokens = int(raw_usage["total_tokens"])
            else:
                total_tokens = input_tokens + output_tokens
            usage_result = (
                {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": int(raw_usage.get("cached_input_tokens") or 0),
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": int(raw_usage.get("reasoning_output_tokens") or 0),
                    "total_tokens": total_tokens,
                },
                "codex_json_turn_completed",
            )
            continue
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        raw_usage = info.get("last_token_usage") or info.get("total_token_usage")
        if not isinstance(raw_usage, dict):
            continue
        usage_result = (
            {
                "input_tokens": int(raw_usage.get("input_tokens") or 0),
                "cached_input_tokens": int(raw_usage.get("cached_input_tokens") or 0),
                "output_tokens": int(raw_usage.get("output_tokens") or 0),
                "reasoning_output_tokens": int(raw_usage.get("reasoning_output_tokens") or 0),
                "total_tokens": int(raw_usage.get("total_tokens") or 0),
            },
            "codex_json_last_token_usage",
        )
    return usage_result


def run_codex_web_research(
    prompt: str,
    *,
    instructions: str,
    model: str = DEFAULT_AGENT_MODEL,
    cwd: Path | None = None,
    timeout_seconds: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run web research through Codex CLI with OpenAI web search enabled.

    The direct Responses API path currently returns completed responses with
    empty ``output`` on the VPS OAuth proxy. Codex CLI is the verified
    OpenAI/OAuth-backed path that returns final text for live web search.
    """
    cli = _find_codex_cli()
    if not cli:
        raise WebResearchCliError("codex CLI not found on PATH")
    resolved_timeout = _resolve_timeout_seconds(timeout_seconds)

    full_prompt = (
        f"{instructions.strip()}\n\n"
        f"USER REQUEST:\n{prompt.strip()}\n\n"
        "Return ONLY the JSON object. Do not include markdown fences or commentary."
    )

    with tempfile.TemporaryDirectory(prefix="autoresearch-web-") as tmp_dir:
        output_path = Path(tmp_dir) / "last_message.json"
        command = [
            cli,
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--output-last-message",
            str(output_path),
            "--json",
            "--model",
            model,
            "--config",
            'web_search="live"',
            "--config",
            f'model_reasoning_effort="{WEB_RESEARCH_REASONING_EFFORT}"',
        ]
        workdir = cwd or _ROOT
        stdout = ""
        stderr = ""
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=workdir,
                start_new_session=True,
            )
            stdout, stderr = process.communicate(input=full_prompt, timeout=resolved_timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise WebResearchCliError(
                f"codex web research timed out after {resolved_timeout}s "
                f"stdout_len={len(stdout or '')} stderr_len={len(stderr or '')}"
            ) from exc

        stdout = stdout or ""
        stderr = stderr or ""
        output = ""
        if output_path.exists():
            output = output_path.read_text()
        if not output.strip():
            output = stdout

        metadata: dict[str, Any] = {
            "command": command,
            "cwd": str(workdir),
            "exit_code": process.returncode,
            "stdout_len": len(stdout),
            "stderr_len": len(stderr),
            "output_len": len(output),
            "output_path_used": output_path.exists(),
        }
        usage_result = _extract_codex_token_usage(stdout)
        if usage_result is not None:
            usage, usage_source = usage_result
            metadata["usage"] = usage
            metadata["usage_source"] = usage_source
        log.info(
            "codex web research finished exit=%s stdout_len=%d stderr_len=%d output_len=%d",
            process.returncode,
            len(stdout),
            len(stderr),
            len(output),
        )

        if process.returncode != 0:
            raise WebResearchCliError(
                "codex web research failed "
                f"exit={process.returncode} stdout_len={len(stdout)} stderr_len={len(stderr)}"
            )
        return output, metadata


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate Codex CLI and any child process spawned by the Node wrapper."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        process.terminate()
