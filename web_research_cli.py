from __future__ import annotations

import os
import shutil
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
            "--model",
            model,
            "--config",
            'web_search="live"',
        ]
        workdir = cwd or _ROOT
        try:
            completed = subprocess.run(
                command,
                input=full_prompt,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=resolved_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WebResearchCliError(
                f"codex web research timed out after {resolved_timeout}s"
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        output = ""
        if output_path.exists():
            output = output_path.read_text()
        if not output.strip():
            output = stdout

        metadata: dict[str, Any] = {
            "command": command,
            "cwd": str(workdir),
            "exit_code": completed.returncode,
            "stdout_len": len(stdout),
            "stderr_len": len(stderr),
            "output_len": len(output),
            "output_path_used": output_path.exists(),
        }
        log.info(
            "codex web research finished exit=%s stdout_len=%d stderr_len=%d output_len=%d",
            completed.returncode,
            len(stdout),
            len(stderr),
            len(output),
        )

        if completed.returncode != 0:
            raise WebResearchCliError(
                "codex web research failed "
                f"exit={completed.returncode} stdout_len={len(stdout)} stderr_len={len(stderr)}"
            )
        return output, metadata
