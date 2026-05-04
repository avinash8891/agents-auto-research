"""HALO auto-apply: apply a HALO report via a Claude Code subprocess, then
re-run the held-out eval and emit a keep/revert recommendation.

Default-off via ``AUTORESEARCH_IMPROVEMENT_HALO_APPLY``. Never invokes
destructive git — only ``git rev-parse`` for the pre-edit HEAD record.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from autoresearch_constants import ENV_CLAUDE_TIMEOUT_SECONDS
from autoresearch_logging import get_logger
from eval_harness import EVAL_RESULTS_DIRNAME, latest_eval_result_path, run_eval
from eval_metrics import (
    DECISION_ABORTED,
    DECISION_INCONCLUSIVE,
    DECISION_SKIP,
    EvalResult,
    classify_delta_in_stdevs,
    compare_eval_results,
)
from improvement_flags import halo_apply_enabled
from persistence_utils import utc_now_iso8601

log = get_logger(__name__)


def _parse_timeout(env_key: str, default: int) -> int:
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("invalid value %r for %s; using default %d", raw, env_key, default)
        return default


CLAUDE_BINARY = "claude"
CLAUDE_TIMEOUT_SECONDS = _parse_timeout(ENV_CLAUDE_TIMEOUT_SECONDS, 1800)

DEFAULT_EDIT_SCOPE = (
    "agent_prompts.py",
    "agent_orchestrator_helpers.py",
    "research_prompts.py",
)


def _git_head(repo_root: Path, runner=subprocess.run) -> str | None:
    try:
        completed = runner(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None


def _build_prompt(report_path: Path, edit_scope: tuple[str, ...]) -> str:
    scope_lines = "\n".join(f"  - {name}" for name in edit_scope)
    return (
        f"Read this HALO diagnostic report:\n  {report_path}\n\n"
        f"Apply the recommended changes by editing ONLY these files:\n"
        f"{scope_lines}\n\n"
        f"Do not edit other files. Do not run git commands. After edits, "
        f"exit cleanly so the harness can re-run the held-out eval."
    )


def _acquire_lock(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = open(lock_path, "x", encoding="utf-8")
    except FileExistsError:
        return False
    # Owner-side write must not leave a stale lock if it raises (ENOSPC, EIO,
    # signal). Unlink on any error so future runs aren't permanently blocked.
    try:
        try:
            fd.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "host": socket.gethostname(),
                        "started": utc_now_iso8601(),
                    }
                )
            )
        finally:
            fd.close()
    except Exception:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return True


def _release_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _run_claude(prompt: str, repo_root: Path, subprocess_run) -> tuple[bool, str | None]:
    try:
        completed = subprocess_run(
            [CLAUDE_BINARY, "-p", prompt],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error(
            f"HALO_APPLY claude subprocess failed: {type(exc).__name__}: {exc}. "
            f"Action: re-run with --verbose or extend CLAUDE_TIMEOUT_SECONDS."
        )
        return False, "subprocess_error"
    if completed.returncode != 0:
        log.error(
            f"HALO_APPLY claude non-zero exit={completed.returncode}. "
            f"Action: re-run with the same prompt to reproduce."
        )
        return False, "claude_nonzero_exit"
    return True, None


def _decide_against_prior(
    new_result: EvalResult, prior_path: Path | None, pre_head: str | None
) -> dict:
    if prior_path is None:
        return {
            "status": DECISION_INCONCLUSIVE,
            "reason": "no_prior_eval",
            "pre_head": pre_head,
            "current_metric": new_result.primary_metric_mean,
        }
    prior = EvalResult.from_dict(json.loads(prior_path.read_text(encoding="utf-8")))
    delta = compare_eval_results(new_result, prior)
    status, _ = classify_delta_in_stdevs(delta["delta_in_stdevs"], delta=delta["delta"])
    decision: dict = {"status": status, "pre_head": pre_head, "delta": delta}
    if delta["delta_in_stdevs"] is None:
        decision["reason"] = "no_variance_baseline"
    elif status == DECISION_INCONCLUSIVE:
        decision["reason"] = "within_one_stdev"
    return decision


def apply_halo_report(
    report_path: Path,
    repo_root: Path,
    *,
    edit_scope: tuple[str, ...] = DEFAULT_EDIT_SCOPE,
    eval_runner=run_eval,
    subprocess_run=subprocess.run,
) -> dict:
    """Apply a HALO report by editing harness code, then re-run eval.

    Decision ``status`` is one of ``DECISION_SKIP``, ``DECISION_ABORTED``,
    ``DECISION_KEEP``, ``DECISION_REVERT``, ``DECISION_INCONCLUSIVE``.
    """
    if not halo_apply_enabled():
        return {"status": DECISION_SKIP}

    lock_path = report_path.parent / ".apply.lock"
    if not _acquire_lock(lock_path):
        log.warning(
            f"HALO_APPLY lock held at {lock_path}; another apply is in progress. "
            f"Action: wait or remove the stale lock manually."
        )
        return {"status": DECISION_ABORTED, "reason": "lock_held"}

    try:
        if shutil.which(CLAUDE_BINARY) is None:
            log.error(
                "HALO_APPLY claude CLI not installed; skipping. "
                "Action: install Claude Code on PATH or unset AUTORESEARCH_IMPROVEMENT_HALO_APPLY."
            )
            return {"status": DECISION_ABORTED, "reason": "missing_claude_binary"}

        pre_head = _git_head(repo_root, runner=subprocess_run)
        ok, abort_reason = _run_claude(
            _build_prompt(report_path, edit_scope), repo_root, subprocess_run
        )
        if not ok:
            return {"status": DECISION_ABORTED, "reason": abort_reason}

        prior_path = latest_eval_result_path(repo_root / EVAL_RESULTS_DIRNAME)
        try:
            new_result = eval_runner(label=f"halo-trial-{report_path.stem}")
        except Exception as exc:
            log.error(
                f"HALO_APPLY post-edit eval raised {type(exc).__name__}: {exc}. "
                f"Action: investigate eval_harness logs."
            )
            return {"status": DECISION_ABORTED, "reason": "eval_failed", "pre_head": pre_head}

        try:
            decision = _decide_against_prior(new_result, prior_path, pre_head)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            log.error(
                f"HALO_APPLY prior-eval comparison failed: {type(exc).__name__}: {exc}. "
                f"Action: inspect prior eval result at {prior_path}."
            )
            return {
                "status": DECISION_ABORTED,
                "reason": "prior_compare_failed",
                "pre_head": pre_head,
            }

        decisions_log = report_path.parent / "decisions.jsonl"
        decisions_log.parent.mkdir(parents=True, exist_ok=True)
        with decisions_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"report": str(report_path), **decision}) + "\n")
        log.info(f"HALO_APPLY decision={decision['status']} report={report_path}")
        return decision
    finally:
        _release_lock(lock_path)
