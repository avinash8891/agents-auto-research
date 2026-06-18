#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Module-function backing store ───────────────────────────────────────────
# The _artifacts_* and _round_* aliases below are the module-level
# implementations for AutoresearchController instance methods defined later in
# this file. They are NOT wrapper-only renames: they adapt module functions
# (which take explicit path/root parameters) to instance methods (which read
# those parameters from self). The aliases are private by convention (_prefix)
# because callers should always go through the controller, never the module
# functions directly.
from autoresearch_artifacts import (
    read_artifacts_relative_to_root as _artifacts_read_artifacts_relative_to_root,
)
from autoresearch_artifacts import read_research_artifacts as _artifacts_read_research_artifacts
from autoresearch_constants import PREPARE_RESULT_MARKER
from autoresearch_experiment import artifact_dir_for as _round_artifact_dir_for
from autoresearch_experiment import derive_trade_analysis as _round_derive_trade_analysis
from autoresearch_experiment import evaluate_metric as _round_evaluate_metric
from autoresearch_experiment import log_experiment_result as _round_log_experiment_result
from autoresearch_experiment import parse_benchmark_details as _round_parse_benchmark_details
from autoresearch_experiment import (
    parse_benchmark_details_legacy as _round_parse_benchmark_details_legacy,
)
from autoresearch_experiment import parse_metric as _round_parse_metric
from autoresearch_experiment import parse_result_json as _round_parse_result_json
from autoresearch_experiment import run_command as _round_run_command
from autoresearch_experiment import run_experiment as _round_run_experiment
from autoresearch_experiment import sanitize_duplicate_entries as _round_sanitize_duplicate_entries
from autoresearch_logging import get_logger
from autoresearch_orchestration import (
    apply_forced_baseline_rerun as _orchestration_apply_forced_baseline_rerun,
)
from autoresearch_orchestration import resolve_next_action as _orchestration_resolve_next_action
from autoresearch_orchestration import (
    try_resume_halted_thesis as _orchestration_try_resume_halted_thesis,
)
from autoresearch_paths import resolve_runtime_root as _resolve_runtime_root
from autoresearch_planning import (
    COMBINATION_RULES,
    DEFAULT_CONFIG_ORDER,
    THESIS_FAMILY,
)
from autoresearch_planning import check_baseline_rerun as _planning_check_baseline_rerun
from autoresearch_planning import list_known_variant_configs as _planning_list_known_variant_configs
from autoresearch_planning import plan_next_action as _planning_plan_next_action
from autoresearch_planning import (
    select_research_next_action as _planning_select_research_next_action,
)
from autoresearch_planning import should_terminate as _planning_should_terminate
from autoresearch_planning import thesis_family_for as _planning_thesis_family_for
from autoresearch_research import accumulate_job_usage as _research_accumulate_job_usage
from autoresearch_research import execute_research_one as _research_execute_research_one
from autoresearch_research import execute_research_sdk as _research_execute_research_sdk
from autoresearch_research import load_baseline_config as _research_load_baseline_config
from autoresearch_research import log_research_round as _research_log_research_round
from autoresearch_research import notify_discord as _notify_discord
from autoresearch_research import queue_variants as _research_queue_variants
from autoresearch_research import run_research as _research_run_research
from autoresearch_resume import (
    _RESEARCH_BLOCKER_KINDS,
    ResumeType,
    _blocker_kinds,
    apply_resume_transition,
    resumable_state_type,
)
from autoresearch_runtime_paths import AutoresearchRuntimeContext
from autoresearch_state import BacktestResultRecord, RunContext
from autoresearch_state import best_result as _state_best_result
from autoresearch_state import coerce_job_to_int as _state_coerce_job_to_int
from autoresearch_state import deduplicate_entries as _state_deduplicate_entries
from autoresearch_state import is_better as _state_is_better
from autoresearch_state import latest_result as _state_latest_result
from autoresearch_state import promote_missing_known_results as _state_promote_missing_known_results
from autoresearch_state import read_state as _state_read_state
from autoresearch_state import render_current_md as _state_render_current_md
from autoresearch_state import write_current_md as _state_write_current_md
from autoresearch_state import write_state as _state_write_state
from backtest_run_db import BacktestRunDB, BaselineTracker
from config_hash import _git_sha
from strategy_family import StrategyFamily, load_family
from trace_autonomy_ledger import AutonomyLedger
from trace_sdk import trace, trace_state_change
from walkforward import run_walkforward_queue as _walkforward_run_walkforward_queue

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent
_AUTONOMY_LEDGER = AutonomyLedger()
RUNTIME_ROOT = _resolve_runtime_root(ROOT)


class _DynamicRuntimePath:
    def __init__(self, suffix: str) -> None:
        self._suffix = suffix

    def _path(self) -> Path:
        return _resolve_runtime_root(ROOT) / self._suffix

    def __fspath__(self) -> str:
        return str(self._path())

    def __str__(self) -> str:
        return str(self._path())

    def __repr__(self) -> str:
        return f"_DynamicRuntimePath({self._suffix!r}, path={self._path()!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _DynamicRuntimePath):
            return self._path() == other._path()
        try:
            return self._path() == Path(other)  # type: ignore[arg-type]
        except TypeError:
            return False

    def __truediv__(self, key: object) -> Path:
        return self._path() / key  # type: ignore[operator]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._path(), name)


STATE_PATH = _DynamicRuntimePath("autoresearch.next.json")
CURRENT_MD_PATH = _DynamicRuntimePath("autoresearch.current.md")


def max_consecutive_research_required() -> int:
    """Read AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED at call time.

    Lazy so pytest's monkeypatch.setenv after import still takes effect.
    """
    raw = os.environ.get("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "10")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED must be an integer, got {raw!r}"
        ) from exc
    if value < 1:
        raise ValueError(
            f"AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED must be >= 1, got {value}"
        )
    return value


def validate_controller_state_invariants(state: dict[str, Any]) -> None:
    """Reject contradictory controller states before they reach disk."""
    if state.get("state") == "running" and _blocker_kinds(state):
        raise ValueError("running controller state cannot carry blockers")


def _fresh_launch_state(job: int) -> dict[str, Any]:
    """Create the only state shape allowed for a new job launch."""
    return {
        "state": "running",
        "job": job,
        "research_round": 0,
        "job_usage": None,
        "heartbeat": {},
    }


def _next_fresh_job_for_launch(controller: Any, prior_state: dict[str, Any]) -> int:
    allocator = getattr(controller, "next_fresh_job_id", None)
    if callable(allocator):
        try:
            fresh_job = int(allocator(prior_state))
        except (TypeError, ValueError):
            fresh_job = 0
        if fresh_job >= 1:
            return fresh_job
    return max(_state_coerce_job_to_int(prior_state.get("job")) + 1, 1)


def normalize_controller_launch_state(
    prior_state: dict[str, Any],
    *,
    resume_current_job: bool,
    fresh_job: int | None = None,
) -> tuple[dict[str, Any], int]:
    job = _state_coerce_job_to_int(prior_state.get("job"))
    resume_type = resumable_state_type(prior_state)

    if resume_current_job:
        if resume_type is None:
            raise ValueError(
                "--resume-current-job requires a recoverable halted code-change, "
                "builder-running, manual-review, blocked research-required, "
                "blocked command/metric failure, or interrupted research-failure state; "
                f"found state={prior_state.get('state')}"
            )
        if job < 1:
            job = 1
        return apply_resume_transition(resume_type, prior_state, job), job

    # Fresh jobs start from the next job number so new launches stay
    # distinguishable from earlier runs in traces and backtest rows, and are
    # isolated from any prior resume/manual-review state.
    job = fresh_job if fresh_job is not None else job + 1
    if job < 1:
        job = 1
    return _fresh_launch_state(job), job


def _validate_current_executable_state(prior_state: dict[str, Any]) -> int:
    job = _state_coerce_job_to_int(prior_state.get("job"))
    executable_blocked = resumable_state_type(prior_state) is ResumeType.BLOCKED_RESEARCH
    if (
        prior_state.get("state") not in {"running", "blocked"}
        or (prior_state.get("state") == "blocked" and not executable_blocked)
        or job < 1
    ):
        raise ValueError(
            "--run-current-state requires an already prepared executable state with a valid job id; "
            f"found state={prior_state.get('state')}"
        )
    return job


def default_controller_paths(runtime_root: Path, family: StrategyFamily) -> tuple[Path, Path, Path]:
    """Returns state_path, current_md_path, jobs_root."""
    paths = AutoresearchRuntimeContext.for_family(
        code_root=ROOT,
        runtime_root=runtime_root,
        family_name=family.name,
    )
    return paths.state_path, paths.current_md_path, paths.jobs_root


# Public API and historical re-exports. `_notify_discord` is intentionally
# included as a back-compat alias of autoresearch_research.notify_discord so
# tests that monkeypatch `loop_mod._notify_discord` continue to work and
# linters do not silently remove the import.
__all__ = (
    "AutoresearchController",
    "BacktestResultRecord",
    "RunContext",
    "default_controller_paths",
    "main",
    "ROOT",
    "RUNTIME_ROOT",
    "STATE_PATH",
    "CURRENT_MD_PATH",
    "DEFAULT_CONFIG_ORDER",
    "THESIS_FAMILY",
    "COMBINATION_RULES",
    "_notify_discord",
)


def _run_controller_loop(
    controller: "AutoresearchController", *, family_name: str, job: int
) -> int:
    from trace_sdk import get_log_file, get_session_id, set_family

    set_family(family_name, job=job)
    trace(
        "MAIN",
        f"Autoresearch loop starting family={family_name} job={job} session={get_session_id()} log={get_log_file()}",
    )
    unchanged_research_required_count = 0
    last_research_required_state_hash = ""
    while True:
        code = controller.execute_once()
        if code != 0:
            return code
        state = controller.read_state()
        current = state.get("state")
        if current in ("finished", "interrupted", "halted"):
            return 0
        if current == "blocked":
            blockers = state.get("blockers", [])
            if any(b.get("kind") in _RESEARCH_BLOCKER_KINDS for b in blockers):
                current_hash = _state_hash(state)
                if current_hash == last_research_required_state_hash:
                    unchanged_research_required_count += 1
                else:
                    unchanged_research_required_count = 0
                    last_research_required_state_hash = current_hash
                if unchanged_research_required_count >= max_consecutive_research_required():
                    trace(
                        "MAIN",
                        "research_required blocker state did not change for "
                        f"{unchanged_research_required_count} consecutive iterations; "
                        "treating as terminal",
                    )
                    return 1
                continue
            # `execute_once()` already performs the only recoverable blocked
            # transition (`research_required`). Any remaining blocked state is
            # terminal for the process.
            return 1
        unchanged_research_required_count = 0
        last_research_required_state_hash = ""


def _state_hash(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)


def _emit_prepare_result(state: dict[str, Any], job: int) -> None:
    next_action = state.get("next_action")
    payload = {
        "ok": True,
        "job": job,
        "state": str(state.get("state") or ""),
        "research_round": state.get("research_round"),
        "research_round_in_progress": state.get("research_round_in_progress"),
        "next_action_type": next_action.get("type") if isinstance(next_action, dict) else None,
    }
    sys.stdout.write(f"{PREPARE_RESULT_MARKER} {json.dumps(payload, sort_keys=True)}\n")
    sys.stdout.flush()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run autoresearch controller")
    parser.add_argument("--family", required=True, help="Strategy family to run")
    parser.add_argument(
        "--fresh-job",
        action="store_true",
        help="Start a new job and ignore prior job-scoped queues.",
    )
    parser.add_argument(
        "--resume-current-job",
        action="store_true",
        help=(
            "Resume a recoverable blocked/interrupted state without incrementing the job id. "
            "Interrupted research failures are retried in the same research round."
        ),
    )
    parser.add_argument(
        "--prepare-launch-state-only",
        action="store_true",
        help=(
            "Normalize and persist launch state, then exit without executing the controller loop."
        ),
    )
    parser.add_argument(
        "--run-current-state",
        action="store_true",
        help=(
            "Execute the controller loop against the already prepared running state without "
            "renormalizing launch state."
        ),
    )
    return parser


def _parse_main_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.fresh_job and args.resume_current_job:
        parser.error("--fresh-job and --resume-current-job are mutually exclusive")
    if args.run_current_state and (args.fresh_job or args.resume_current_job):
        parser.error(
            "--run-current-state cannot be combined with --fresh-job or --resume-current-job"
        )
    if args.prepare_launch_state_only and args.run_current_state:
        parser.error("--prepare-launch-state-only and --run-current-state are mutually exclusive")
    return args


def _should_hard_exit_prepare_cli(argv: list[str]) -> bool:
    try:
        return bool(_parse_main_args(argv).prepare_launch_state_only)
    except SystemExit:
        return False


class AutoresearchController:
    def __init__(
        self,
        root: Path | None = None,
        runtime_root: Path | None = None,
        state_path: Path | None = None,
        current_md_path: Path | None = None,
        jobs_root: Path | None = None,
        family: StrategyFamily | None = None,
    ) -> None:
        if family is None:
            raise ValueError("AutoresearchController requires an explicit strategy family")
        self.family = family
        self.paths = AutoresearchRuntimeContext.for_family(
            code_root=root or ROOT,
            runtime_root=runtime_root or (root or ROOT),
            family_name=self.family.name,
            state_path=state_path,
            current_md_path=current_md_path,
            jobs_root=jobs_root,
        )
        self.root = self.paths.code_root
        self.runtime_root = self.paths.runtime_root
        self.state_path = self.paths.state_path
        self.current_md_path = self.paths.current_md_path
        self.jobs_root = self.paths.jobs_root
        self._clear_runtime_paths()
        backtest_run_db_path = self.paths.backtest_db_path
        self.backtest_run_db = BacktestRunDB(backtest_run_db_path)
        self.baseline_tracker = BaselineTracker(
            self.paths.baseline_checkpoints_path,
            db_path=backtest_run_db_path,
            strategy_family=self.family.name,
        )
        # Transient cross-method state (formerly scattered self._* fields).
        self.ctx = RunContext()

    def _clear_runtime_paths(self) -> None:
        self.job_runtime_root = self.jobs_root
        self.research_dir = self.jobs_root
        self.builder_requests_dir = self.jobs_root

    def _set_runtime_paths_for_job(self, job: int) -> None:
        if job < 1:
            raise ValueError(f"job id is required for job-scoped runtime paths; got {job!r}")
        self.job_runtime_root = self.paths.job_runtime_root(job)
        self.research_dir = self.paths.research_dir(job)
        self.builder_requests_dir = self.paths.builder_requests_dir(job)

    def _current_job(self) -> int | None:
        job = self.read_state().get("job")
        try:
            return int(job) if job is not None else None
        except (TypeError, ValueError):
            return None

    def _max_runtime_job_id(self) -> int:
        jobs_root = self.jobs_root
        if not jobs_root.exists():
            return 0
        max_job = 0
        for path in jobs_root.iterdir():
            if not path.is_dir() or not path.name.startswith("job-"):
                continue
            try:
                max_job = max(max_job, int(path.name.removeprefix("job-")))
            except ValueError:
                continue
        return max_job

    def next_fresh_job_id(self, prior_state: dict[str, Any] | None = None) -> int:
        """Pick a fresh job id that cannot collide with checkout history.

        Fresh launches must not reuse a historical job id from the local
        state file, backtest run DB, or runtime job tree. Reusing one of those
        ids makes a "fresh" job inherit stale baseline decisions, queued
        theses, or old results.
        """
        prior = prior_state if prior_state is not None else self.read_state()
        max_seen = _state_coerce_job_to_int(prior.get("job"))
        max_seen = max(max_seen, self.backtest_run_db.max_job_id())
        max_seen = max(max_seen, self._max_runtime_job_id())
        return max_seen + 1 if max_seen > 0 else 1

    def clear_transient_context(self) -> None:
        """Clear per-round context.

        This prevents stale lineage metadata from leaking into later runs in
        the same controller process.
        """
        self.ctx.current_contract = None
        self.ctx.parent_backtest_run_id = ""
        self.ctx.execution_root = None

    def clear_terminal_metadata(self, state: dict[str, Any]) -> None:
        """Remove terminal-only fields when a transition reactivates the run."""
        state.pop("finished_reason", None)

    def _ensure_job_metadata(self) -> None:
        """Validate job-scoped state before direct loop entrypoints."""
        state = self.read_state()
        raw_job = state.get("job")
        job = _state_coerce_job_to_int(raw_job)
        if job < 1:
            raise ValueError(f"job id is required for controller execution; got {raw_job!r}")
        changed = False
        if "research_round" not in state:
            state["research_round"] = 0
            changed = True
        if "job_usage" not in state:
            state["job_usage"] = None
            changed = True
        if changed:
            self.write_state(state)
        else:
            self._set_runtime_paths_for_job(job)

    def read_state(self) -> dict[str, Any]:
        return _state_read_state(self.state_path)

    def write_state(self, state: dict[str, Any]) -> None:
        validate_controller_state_invariants(state)
        previous_state = self.read_state().get("state", "unknown")
        _state_write_state(self.state_path, state)
        job = _state_coerce_job_to_int(state.get("job"))
        if job > 0:
            self._set_runtime_paths_for_job(job)
        else:
            self._clear_runtime_paths()
        next_state = state.get("state", "unknown")
        if previous_state != next_state:
            trace_state_change(previous_state, next_state, state.get("finished_reason", ""))

    def read_entries(self) -> list[dict[str, Any]]:
        return self.backtest_run_db.export_entries()

    def write_entries(self, entries: list[dict[str, Any]]) -> None:
        self.backtest_run_db.import_entries(entries)

    def current_commit(self) -> str:
        return _git_sha()

    def direction(self) -> str:
        return self.backtest_run_db.best_direction()

    def read_results(self) -> list[BacktestResultRecord]:
        all_results = self.backtest_run_db.read_results()
        state = self.read_state()
        job = state.get("job")
        if job is None:
            return all_results
        try:
            current_job = int(job)
        except (TypeError, ValueError):
            return all_results

        scoped: list[BacktestResultRecord] = []
        for result in all_results:
            try:
                result_job = int(result.job)
            except (TypeError, ValueError):
                continue
            if result_job == current_job:
                scoped.append(result)
        return scoped

    def read_json_artifacts(self, directory: Path) -> list[dict[str, Any]]:
        return _artifacts_read_artifacts_relative_to_root(directory, self.root)

    def read_research_artifacts(self) -> list[dict[str, Any]]:
        return _artifacts_read_research_artifacts(
            self.research_dir, self.root, job=self._current_job()
        )

    def read_thesis_artifacts(self) -> list[dict[str, Any]]:
        return []

    def is_better(self, candidate: float, current: float | None) -> bool:
        return _state_is_better(self.direction(), candidate, current)

    def best_result(self, results: list[BacktestResultRecord]) -> dict[str, Any]:
        return _state_best_result(results, self.direction())

    def latest_result(self, results: list[BacktestResultRecord]) -> BacktestResultRecord | None:
        return _state_latest_result(results)

    def list_known_variant_configs(self) -> list[str]:
        return _planning_list_known_variant_configs(self.root, self.family)

    def thesis_statuses(self, results: list[BacktestResultRecord]) -> dict[str, dict[str, Any]]:
        return {}

    def promote_missing_known_results(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_promote_missing_known_results(entries)

    def deduplicate_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_deduplicate_entries(entries)

    def thesis_family_for(self, config: str) -> str:
        return _planning_thesis_family_for(
            config, self.family, self.builder_requests_dir, self.root
        )

    def parse_result_json(self, output: str) -> dict[str, Any] | None:
        return _round_parse_result_json(output)

    def parse_benchmark_details(self, output: str) -> dict[str, Any]:
        return _round_parse_benchmark_details(output)

    def _parse_benchmark_details_legacy(self, output: str) -> dict[str, Any]:
        return _round_parse_benchmark_details_legacy(output)

    def select_research_next_action(self, results: list[BacktestResultRecord]) -> dict[str, Any]:
        return _planning_select_research_next_action(
            self.root,
            self.runtime_root,
            self.family,
            self.builder_requests_dir,
            self.builder_requests_dir,
            self.research_dir,
            results,
            job=self._current_job(),
        )

    def should_terminate(self, results: list[BacktestResultRecord] | None = None) -> bool:
        current_results = results if results is not None else self.read_results()
        return _planning_should_terminate(
            self.runtime_root,
            self.family,
            self.builder_requests_dir,
            self.research_dir,
            current_results,
            job=self._current_job(),
        )

    def plan_next_action(
        self, state: dict[str, Any], results: list[BacktestResultRecord]
    ) -> dict[str, Any]:
        return _planning_plan_next_action(
            state,
            results,
            self.root,
            self.runtime_root,
            self.family,
            self.builder_requests_dir,
            self.builder_requests_dir,
            self.research_dir,
        )

    def render_current_md(self, state: dict[str, Any], results: list[BacktestResultRecord]) -> str:
        return _state_render_current_md(
            state,
            results,
            family_name=self.family.name,
            metric_name=self.primary_metric_name(),
        )

    def write_current_md(self, state: dict[str, Any], results: list[BacktestResultRecord]) -> None:
        _state_write_current_md(
            self.current_md_path,
            state,
            results,
            family_name=self.family.name,
            metric_name=self.primary_metric_name(),
        )

    def reconcile_state(self) -> dict[str, Any]:
        entries = self.read_entries()
        reconciled_entries = self.promote_missing_known_results(entries)
        reconciled_entries = self.deduplicate_entries(reconciled_entries)
        if reconciled_entries != entries:
            self.write_entries(reconciled_entries)
        state = self.read_state()
        results = self.read_results()
        best = self.best_result(results)
        state["current_best"] = best
        state["thesis_statuses"] = self.thesis_statuses(results)

        latest = self.latest_result(results)
        heartbeat = state.setdefault("heartbeat", {})
        if latest is not None:
            heartbeat["last_completed_thesis"] = latest.config
            heartbeat["last_result"] = latest.status
            heartbeat["last_metric"] = latest.metric
        else:
            heartbeat.pop("last_completed_thesis", None)
            heartbeat.pop("last_result", None)
            heartbeat.pop("last_metric", None)
        heartbeat["current_best"] = state.get("current_best", {})

        previous_state = state.get("state", "unknown")
        state = self.plan_next_action(state, results)
        next_state = state.get("state", "unknown")
        self.write_state(state)
        self.write_current_md(state, results)
        next_action = state.get("next_action", {})
        trace(
            "RECONCILE",
            f"previous_state={previous_state} state={next_state} "
            f"best={best} next_action_type={next_action.get('type')}",
        )
        return state

    def artifact_dir_for(self, config: str) -> Path:
        return _round_artifact_dir_for(self.state_path, self.runtime_root, config)

    def sanitize_duplicate_entries(self, config: str) -> None:
        _round_sanitize_duplicate_entries(self.backtest_run_db, config)

    def _accumulate_job_usage(self, round_usage: dict[str, Any]) -> None:
        _research_accumulate_job_usage(self.state_path, round_usage)

    def log_research_round(
        self,
        *,
        round_number: int,
        thesis_id: str,
        hypothesis_id: str = "",
        outcome: str,
        config_changes: dict[str, Any] | None = None,
        hypothesis: str = "",
        mechanism: str = "",
        thesis_details: dict[str, Any] | None = None,
        validation_failure_reason: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        _research_log_research_round(
            self.backtest_run_db.path,
            self.state_path,
            round_number=round_number,
            thesis_id=thesis_id,
            hypothesis_id=hypothesis_id,
            outcome=outcome,
            config_changes=config_changes,
            hypothesis=hypothesis,
            mechanism=mechanism,
            thesis_details=thesis_details,
            validation_failure_reason=validation_failure_reason,
            usage=usage,
        )

    def log_experiment_result(
        self,
        *,
        config: str,
        metric: float,
        decision: str,
        output: str,
        analysis: dict[str, Any],
        next_action: dict[str, Any] | None = None,
        artifact_dir: Path | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        _round_log_experiment_result(
            self,
            config=config,
            metric=metric,
            decision=decision,
            output=output,
            analysis=analysis,
            next_action=next_action,
            artifact_dir=artifact_dir,
            details=details,
        )

    def run_command(self, command: str) -> tuple[int, str]:
        return _round_run_command(self.ctx.execution_root or self.root, command)

    def primary_metric_name(self) -> str:
        return self.backtest_run_db.primary_metric_name()

    def parse_metric(self, output: str, name: str = "profit_factor") -> float | None:
        return _round_parse_metric(output, name)

    def evaluate_metric(self, metric: float) -> str:
        state = self.read_state()
        raw_job = state.get("job")
        job_id: int | None = None
        try:
            if raw_job is not None:
                job_id = int(raw_job)
        except (TypeError, ValueError):
            job_id = None
        return _round_evaluate_metric(
            self.runtime_root,
            self.backtest_run_db.path.name,
            metric,
            job_id=job_id,
        )

    def derive_trade_analysis(
        self, config: str, metric: float, decision: str, output: str = ""
    ) -> dict[str, Any]:
        return _round_derive_trade_analysis(self, config, metric, decision, output)

    def execute_research_sdk(self) -> dict[str, Any]:
        return _research_execute_research_sdk(self)

    def _load_baseline_config(self) -> dict[str, Any] | None:
        return _research_load_baseline_config(self.root, self.family)

    def _queue_variants(
        self,
        variants: list[dict[str, Any]],
        thesis: Any,
        primary_contract: Any,
        baseline_config: dict[str, Any],
    ) -> None:
        _research_queue_variants(
            self.root,
            self.builder_requests_dir,
            variants,
            thesis,
            primary_contract,
            baseline_config,
            builder_requests_dir=self.builder_requests_dir,
            job=self._current_job(),
            created_for_commit=self.current_commit(),
        )

    def execute_research_one(self) -> dict[str, Any]:
        return _research_execute_research_one(self)

    def _check_baseline_rerun(self) -> dict[str, Any] | None:
        return _planning_check_baseline_rerun(
            self.root,
            self.family,
            self.baseline_tracker,
            self.current_commit(),
            self.read_results(),
        )

    def _try_resume_halted_thesis(self) -> dict[str, Any] | None:
        return _orchestration_try_resume_halted_thesis(self)

    def _apply_forced_baseline_rerun(self, baseline_action: dict[str, Any]) -> dict[str, Any]:
        return _orchestration_apply_forced_baseline_rerun(self, baseline_action)

    def _resolve_next_action(self) -> dict[str, Any]:
        return _orchestration_resolve_next_action(self)

    def _run_research(self, state: dict[str, Any]) -> dict[str, Any]:
        return _research_run_research(self, state)

    def _run_round(self, state: dict[str, Any]) -> int:
        return _round_run_experiment(self, state)

    def _run_walkforward(self, state: dict[str, Any]) -> int:
        return _walkforward_run_walkforward_queue(self, state)

    def execute_once(self) -> int:
        """Run one iteration of the autoresearch loop.

        Flow:
        1. Resolve what to do next (baseline rerun, pending config, or research)
        2. If blocked for research: run conductor, compile thesis, wire config
        3. If running: execute the round (backtest + evaluate)
        """
        trace("LOOP", "=== execute_once START ===")
        self._ensure_job_metadata()

        state = self._resolve_next_action()
        trace(
            "LOOP",
            f"state={state.get('state')} blockers={[b.get('kind') for b in state.get('blockers', [])]}",
        )

        # Handle blocked state: invoke research conductor
        if state.get("state") == "blocked":
            blockers = state.get("blockers", [])
            if any(b.get("kind") in _RESEARCH_BLOCKER_KINDS for b in blockers):
                state = self._run_research(state)
                if state.get("state") == "running":
                    decision = _AUTONOMY_LEDGER.record_decision(
                        summary="controller accepted blocker-cleared research next action",
                        decision_type="research_transition",
                        score=None,
                        graduation_status="supervised",
                        outcome="approved",
                        rationale=(
                            "research round produced a runnable next action after "
                            "clearing research blockers"
                        ),
                        constraints=[
                            "must clear blockers before running",
                            "must still pass round execution",
                        ],
                        evidence_event_ids=[],
                    )
                    _AUTONOMY_LEDGER.record_audit(
                        summary="controller applied blocker-cleared research next action",
                        action="transition_to_running",
                        approval_status="approved",
                        actor="autoresearch_controller",
                        related_decision_id=decision["decision_id"],
                    )

        # Terminal states
        if state.get("state") != "running":
            log.info(f"LOOP_STOP state={state.get('state')}")
            return 0

        next_action = state.get("next_action")
        if isinstance(next_action, dict) and next_action.get("type") == "walkforward":
            return self._run_walkforward(state)

        # We have a config to run
        return self._run_round(state)


def main() -> int:
    args = _parse_main_args()
    family = load_family(args.family)
    runtime_root = _resolve_runtime_root(ROOT)
    state_path, current_md_path, jobs_root = default_controller_paths(runtime_root, family)
    controller = AutoresearchController(
        root=ROOT,
        runtime_root=runtime_root,
        family=family,
        state_path=state_path,
        current_md_path=current_md_path,
        jobs_root=jobs_root,
    )
    prior_state = controller.read_state()
    if args.run_current_state:
        try:
            job = _validate_current_executable_state(prior_state)
        except ValueError as exc:
            log.error("%s", exc)
            return 1
        return _run_controller_loop(controller, family_name=args.family, job=job)

    launch_fresh_job = args.fresh_job or not args.resume_current_job
    fresh_job = None
    if launch_fresh_job:
        fresh_job = _next_fresh_job_for_launch(controller, prior_state)
    try:
        state, job = normalize_controller_launch_state(
            prior_state,
            resume_current_job=args.resume_current_job,
            fresh_job=fresh_job,
        )
    except ValueError as exc:
        log.error("%s", exc)
        return 1
    controller.write_state(state)
    if args.prepare_launch_state_only:
        _emit_prepare_result(state, job)
        return 0
    return _run_controller_loop(controller, family_name=args.family, job=job)


if __name__ == "__main__":
    exit_code = main()
    if _should_hard_exit_prepare_cli(sys.argv[1:]):
        os._exit(exit_code)
    raise SystemExit(exit_code)
