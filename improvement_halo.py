"""HALO trace mining: invoke the external ``halo`` CLI on the round's
OpenInference JSONL and write a Markdown report under
``improvement_reports/halo/round-NNN.md``.

Default-off via ``AUTORESEARCH_IMPROVEMENT_HALO``. Degrades silently
when the binary is missing or the subprocess fails — never raises.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import agent_infra
from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS
from autoresearch_logging import get_logger
from improvement_flags import halo_enabled
from persistence_utils import parse_positive_int_env, write_text_atomic

log = get_logger(__name__)


HALO_BINARY = "halo"
HALO_MODEL = "gpt-5.2"
HALO_TIMEOUT_SECONDS = parse_positive_int_env(ENV_HALO_TIMEOUT_SECONDS, 600, logger=log)
HALO_BINARY_ENV = "AUTORESEARCH_HALO_BINARY"
DEFAULT_HALO_BINARY = Path("/opt/autoresearch-tools/halo/venv/bin/halo")

DIAGNOSTIC_PROMPT = (
    "Analyze the attached trace events for systemic failure modes "
    "across hypotheses. Identify the top three across-trace patterns "
    "that lead to rejected or conductor_error outcomes. Propose "
    "concrete prompt or harness-code changes that would address each. "
    "Output Markdown."
)


def _halo_oauth_env() -> dict[str, str]:
    """Route HALO's OpenAI SDK calls through the local openai-oauth proxy."""
    agent_infra._ensure_oauth_proxy()
    env = os.environ.copy()
    env["OPENAI_BASE_URL"] = agent_infra._OAUTH_PROXY_URL
    env["OPENAI_API_KEY"] = "unused"
    env["OPENAI_AGENTS_DISABLE_TRACING"] = "true"
    return env


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _resolve_halo_binary() -> str | None:
    configured = os.environ.get(HALO_BINARY_ENV)
    if configured:
        configured_path = Path(configured).expanduser()
        if _is_executable_file(configured_path):
            return str(configured_path)
        log.warning(
            f"HALO binary configured by {HALO_BINARY_ENV} is not executable: {configured_path}. "
            "Falling back to PATH/default discovery. "
            "Action: run scripts/provision_halo_tool.sh or set AUTORESEARCH_HALO_BINARY to a valid halo CLI."
        )

    existing = shutil.which(HALO_BINARY)
    if existing:
        return existing

    if _is_executable_file(DEFAULT_HALO_BINARY):
        return str(DEFAULT_HALO_BINARY)

    return None


def run_halo_after_round(
    research_round: int,
    jsonl_path: Path,
    output_dir: Path,
) -> Path | None:
    """Run the halo CLI on `jsonl_path` and persist the report.

    Returns the report path on success; ``None`` on flag-off, missing
    binary, missing input, or subprocess failure.
    """
    if not halo_enabled():
        return None
    if not jsonl_path.exists():
        log.warning(
            f"HALO skip: trace JSONL not found at {jsonl_path}. "
            f"Action: confirm trace_sdk.get_event_file() points to the live trace."
        )
        return None
    halo_binary = _resolve_halo_binary()
    if halo_binary is None:
        log.error(
            "HALO halo CLI unavailable; skipping. "
            "Action: run scripts/provision_halo_tool.sh, set AUTORESEARCH_HALO_BINARY, or unset AUTORESEARCH_IMPROVEMENT_HALO."
        )
        return None
    try:
        halo_env = _halo_oauth_env()
    except RuntimeError as exc:
        log.error(
            f"HALO openai-oauth proxy unavailable on round={research_round}: {exc}. "
            "Action: start openai-oauth.service before enabling AUTORESEARCH_IMPROVEMENT_HALO."
        )
        return None
    adapted_trace_path = output_dir / f"round-{research_round:03d}-traces.halo.jsonl"
    try:
        from trace_adapters.halo import export_halo_trace_jsonl, verify_halo_trace_jsonl

        export_halo_trace_jsonl(jsonl_path, adapted_trace_path)
        verify_halo_trace_jsonl(adapted_trace_path)
    except (OSError, ValueError) as exc:
        log.error(
            f"HALO trace adaptation failed on round={research_round}: {exc}. "
            f"Action: inspect canonical trace at {jsonl_path}."
        )
        return None
    try:
        completed = subprocess.run(
            [halo_binary, str(adapted_trace_path), "-p", DIAGNOSTIC_PROMPT, "--model", HALO_MODEL],
            capture_output=True,
            text=True,
            env=halo_env,
            timeout=HALO_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error(
            f"HALO timeout after {HALO_TIMEOUT_SECONDS}s on round={research_round}. "
            f"Action: investigate halo CLI hang or raise HALO_TIMEOUT_SECONDS."
        )
        return None
    except OSError as exc:
        log.error(
            f"HALO subprocess OS error on round={research_round}: {exc}. "
            f"Action: confirm halo binary is executable and on PATH."
        )
        return None
    if completed.returncode != 0:
        log.error(
            f"HALO non-zero exit={completed.returncode} on round={research_round}. "
            f"stderr={(completed.stderr or '').strip()[:400]}. "
            f"Action: re-run the halo binary manually with the same JSONL to reproduce."
        )
        return None
    report_path = output_dir / f"round-{research_round:03d}.md"
    write_text_atomic(report_path, completed.stdout or "")
    log.info(f"HALO wrote {report_path}")
    return report_path
