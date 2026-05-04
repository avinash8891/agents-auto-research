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

from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS
from autoresearch_logging import get_logger
from improvement_flags import halo_enabled
from persistence_utils import write_text_atomic

log = get_logger(__name__)

HALO_BINARY = "halo"
HALO_TIMEOUT_SECONDS = int(os.environ.get(ENV_HALO_TIMEOUT_SECONDS, "600"))

DIAGNOSTIC_PROMPT = (
    "Analyze the attached trace events for systemic failure modes "
    "across hypotheses. Identify the top three across-trace patterns "
    "that lead to rejected or conductor_error outcomes. Propose "
    "concrete prompt or harness-code changes that would address each. "
    "Output Markdown."
)


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
    if shutil.which(HALO_BINARY) is None:
        log.error(
            "HALO halo CLI not installed; skipping. "
            "Action: install the halo binary on PATH or unset AUTORESEARCH_IMPROVEMENT_HALO."
        )
        return None
    try:
        completed = subprocess.run(
            [HALO_BINARY, str(jsonl_path), "-p", DIAGNOSTIC_PROMPT],
            capture_output=True,
            text=True,
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
