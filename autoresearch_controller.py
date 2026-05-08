#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

# ── Module-function backing store ───────────────────────────────────────────
# The _artifacts_* and _experiment_* aliases below are the module-level
# implementations for AutoresearchController instance methods defined later in
# this file. They are NOT wrapper-only renames: they adapt module functions
# (which take explicit path/root parameters) to instance methods (which read
# those parameters from self). The aliases are private by convention (_prefix)
# because callers should always go through the controller, never the module
# functions directly.
from autoresearch_artifacts import (
    queue_from_thesis_artifacts as _artifacts_queue_from_thesis_artifacts,
)
from autoresearch_artifacts import (
    read_artifacts_relative_to_root as _artifacts_read_artifacts_relative_to_root,
)
from autoresearch_artifacts import read_research_artifacts as _artifacts_read_research_artifacts
from autoresearch_artifacts import read_run_queue as _artifacts_read_run_queue
from autoresearch_artifacts import read_thesis_artifacts as _artifacts_read_thesis_artifacts
from autoresearch_experiment import artifact_dir_for as _experiment_artifact_dir_for
from autoresearch_experiment import derive_trade_analysis as _experiment_derive_trade_analysis
from autoresearch_experiment import evaluate_metric as _experiment_evaluate_metric
from autoresearch_experiment import log_experiment_result as _experiment_log_experiment_result
from autoresearch_experiment import parse_benchmark_details as _experiment_parse_benchmark_details
from autoresearch_experiment import (
    parse_benchmark_details_legacy as _experiment_parse_benchmark_details_legacy,
)
from autoresearch_experiment import parse_metric as _experiment_parse_metric
from autoresearch_experiment import parse_result_json as _experiment_parse_result_json
from autoresearch_experiment import run_command as _experiment_run_command
from autoresearch_experiment import run_experiment as _experiment_run_experiment
from autoresearch_experiment import (
    sanitize_duplicate_entries as _experiment_sanitize_duplicate_entries,
)
from autoresearch_logging import get_logger
from autoresearch_orchestration import (
    apply_forced_baseline_rerun as _orchestration_apply_forced_baseline_rerun,
)
from autoresearch_orchestration import resolve_next_action as _orchestration_resolve_next_action
from autoresearch_orchestration import (
    try_resume_halted_thesis as _orchestration_try_resume_halted_thesis,
)
from autoresearch_planning import (
    COMBINATION_RULES,
    DEFAULT_CONFIG_ORDER,
    THESIS_FAMILY,
)
from autoresearch_planning import check_baseline_rerun as _planning_check_baseline_rerun
from autoresearch_planning import (
    generate_combination_candidates as _planning_generate_combination_candidates,
)
from autoresearch_planning import generate_theses_from_ideas as _planning_generate_theses_from_ideas
from autoresearch_planning import list_known_variant_configs as _planning_list_known_variant_configs
from autoresearch_planning import parse_ideas_backlog as _planning_parse_ideas_backlog
from autoresearch_planning import pending_configs as _planning_pending_configs
from autoresearch_planning import plan_next_action as _planning_plan_next_action
from autoresearch_planning import (
    select_research_next_action as _planning_select_research_next_action,
)
from autoresearch_planning import should_terminate as _planning_should_terminate
from autoresearch_planning import thesis_family_for as _planning_thesis_family_for
from autoresearch_planning import thesis_statuses as _planning_thesis_statuses
from autoresearch_research import accumulate_job_usage as _research_accumulate_job_usage
from autoresearch_research import execute_research_one as _research_execute_research_one
from autoresearch_research import execute_research_sdk as _research_execute_research_sdk
from autoresearch_research import load_baseline_config as _research_load_baseline_config
from autoresearch_research import log_research_round as _research_log_research_round
from autoresearch_research import notify_discord as _notify_discord
from autoresearch_research import queue_variants as _research_queue_variants
from autoresearch_research import results_to_dicts as _research_results_to_dicts
from autoresearch_research import run_research as _research_run_research
from autoresearch_runtime_paths import job_runtime_root as _job_runtime_root
from autoresearch_state import ExperimentRecord, RunContext
from autoresearch_state import _coerce_job_to_int as _state_coerce_job_to_int
from autoresearch_state import best_result as _state_best_result
from autoresearch_state import deduplicate_entries as _state_deduplicate_entries
from autoresearch_state import is_better as _state_is_better
from autoresearch_state import latest_result as _state_latest_result
from autoresearch_state import promote_missing_known_results as _state_promote_missing_known_results
from autoresearch_state import read_state as _state_read_state
from autoresearch_state import render_current_md as _state_render_current_md
from autoresearch_state import write_current_md as _state_write_current_md
from autoresearch_state import write_state as _state_write_state
from config_hash import _git_sha
from experiment_db import BaselineTracker, ExperimentDB
from strategy_family import StrategyFamily, load_family
from trace_autonomy_ledger import AutonomyLedger
from trace_sdk import trace, trace_state_change

log = get_logger(__name__)

ROOT = Path(__file__).resolve().parent
_AUTONOMY_LEDGER = AutonomyLedger()
STATE_PATH = ROOT / "autoresearch.next.json"
CURRENT_MD_PATH = ROOT / "autoresearch.current.md"


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


_RECOVERABLE_BLOCKED_RESUME_KINDS = {"builder_failed", "command_failed", "metric_parse_failed"}
_RESEARCH_BLOCKER_KINDS = {"research_required", "research_retry_required"}


def _blocker_kinds(state: dict[str, Any]) -> set[str]:
    blockers = state.get("blockers")
    if not isinstance(blockers, list):
        return set()
    return {
        str(blocker.get("kind"))
        for blocker in blockers
        if isinstance(blocker, dict) and blocker.get("kind")
    }


def validate_controller_state_invariants(state: dict[str, Any]) -> None:
    """Reject contradictory controller states before they reach disk."""
    if state.get("state") == "running" and _blocker_kinds(state):
        raise ValueError("running controller state cannot carry blockers")


def _dict_state_field(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _is_manual_review_resume_state(state: dict[str, Any]) -> bool:
    return (
        state.get("state") == "blocked"
        and isinstance(state.get("next_action"), dict)
        and state["next_action"].get("type") == "manual_review"
        and (
            state.get("halted_thesis_id")
            or state.get("manual_review_theses")
            or state.get("halted_reason") == "requires_code_change"
        )
    )


def _is_halted_code_change_resume_state(state: dict[str, Any]) -> bool:
    return (
        state.get("state") == "halted"
        and state.get("halted_reason") == "requires_code_change"
        and bool(state.get("halted_thesis_id"))
    )


def _is_builder_running_resume_state(state: dict[str, Any]) -> bool:
    next_action = state.get("next_action")
    return (
        state.get("state") == "building"
        and state.get("halted_reason") == "requires_code_change"
        and bool(state.get("halted_thesis_id"))
        and isinstance(next_action, dict)
        and next_action.get("type") == "builder_running"
    )


def _is_interrupted_research_failure_state(state: dict[str, Any]) -> bool:
    return state.get("state") == "interrupted" and "research_failed" in _blocker_kinds(state)


def _is_blocked_research_required_resume_state(state: dict[str, Any]) -> bool:
    if state.get("state") != "blocked":
        return False
    next_action = state.get("next_action")
    if not isinstance(next_action, dict) or next_action.get("type") != "research":
        return False
    return bool(_RESEARCH_BLOCKER_KINDS & _blocker_kinds(state))


def _is_blocked_failed_experiment_resume_state(state: dict[str, Any]) -> bool:
    if state.get("state") != "blocked":
        return False
    next_action = state.get("next_action")
    if not isinstance(next_action, dict) or next_action.get("type") not in {
        "blocked",
        "builder_failed",
    }:
        return False
    reason = next_action.get("reason")
    return bool(_RECOVERABLE_BLOCKED_RESUME_KINDS & _blocker_kinds(state)) or (
        reason in _RECOVERABLE_BLOCKED_RESUME_KINDS
    )


def _resume_interrupted_research_state(prior_state: dict[str, Any], job: int) -> dict[str, Any]:
    failed_round = prior_state.get("research_round", 0)
    try:
        retry_from_round = max(int(failed_round) - 1, 0)
    except (TypeError, ValueError):
        retry_from_round = 0

    prior_detail = ""
    blockers = prior_state.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    for blocker in blockers:
        if isinstance(blocker, dict) and blocker.get("kind") == "research_failed":
            prior_detail = str(blocker.get("detail") or "")
            break

    state = dict(prior_state)
    state.update(
        {
            "state": "blocked",
            "job": job,
            "research_round": retry_from_round,
            "blockers": [
                {
                    "kind": "research_required",
                    "detail": (
                        "Retrying interrupted research failure"
                        + (f": {prior_detail}" if prior_detail else ".")
                    ),
                }
            ],
            "next_action": {
                "type": "research",
                "reason": "resume_current_job_retry_interrupted_research",
            },
        }
    )
    state.pop("current_thesis", None)
    state.pop("pending_configs", None)
    state.pop("thesis_statuses", None)
    state.pop("finished_reason", None)
    state.pop("research_stop_reasoning", None)
    return state


def _running_resume_state(
    prior_state: dict[str, Any],
    job: int,
    *,
    preserve_resume_metadata: bool,
    resume_previous_blocker: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "state": "running",
        "job": job,
        "research_round": prior_state.get("research_round", 0),
        "job_usage": prior_state.get("job_usage"),
        "heartbeat": _dict_state_field(prior_state, "heartbeat"),
    }
    for key in ("current_best", "baseline_drift"):
        if key in prior_state:
            state[key] = prior_state[key]

    if preserve_resume_metadata:
        if prior_state.get("halted_reason") == "requires_code_change" and prior_state.get(
            "halted_thesis_id"
        ):
            for key in ("halted_reason", "halted_thesis_id", "halted_thesis"):
                if key in prior_state:
                    state[key] = prior_state[key]
        if prior_state.get("manual_review_theses"):
            state["manual_review_theses"] = list(prior_state["manual_review_theses"])

    if resume_previous_blocker:
        blocker_kinds = sorted(_blocker_kinds(prior_state))
        state["resume_previous_blocker"] = {
            "kind": blocker_kinds[0] if blocker_kinds else prior_state.get("state", "unknown"),
            "next_action": prior_state.get("next_action"),
            "current_thesis": prior_state.get("current_thesis"),
        }
    return state


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
        return int(allocator(prior_state))
    return max(_state_coerce_job_to_int(prior_state.get("job")) + 1, 1)


def normalize_controller_launch_state(
    prior_state: dict[str, Any],
    *,
    resume_current_job: bool,
    fresh_job: int | None = None,
) -> tuple[dict[str, Any], int]:
    job = _state_coerce_job_to_int(prior_state.get("job"))
    resume_manual_review = _is_manual_review_resume_state(prior_state)
    resume_halted_code_change = _is_halted_code_change_resume_state(prior_state)
    resume_builder_running = _is_builder_running_resume_state(prior_state)
    resume_research_failure = _is_interrupted_research_failure_state(prior_state)
    resume_blocked_research = _is_blocked_research_required_resume_state(prior_state)
    resume_failed_experiment = _is_blocked_failed_experiment_resume_state(prior_state)

    if resume_current_job:
        if not (
            resume_manual_review
            or resume_halted_code_change
            or resume_builder_running
            or resume_research_failure
            or resume_blocked_research
            or resume_failed_experiment
        ):
            raise ValueError(
                "--resume-current-job requires a recoverable halted code-change, "
                "builder-running, manual-review, blocked research-required, "
                "blocked command/metric failure, or interrupted research-failure state; "
                f"found state={prior_state.get('state')}"
            )
        if job < 1:
            job = 1
    else:
        # Fresh jobs start from the next job number so new launches stay
        # distinguishable from earlier runs in traces and experiment rows.
        job = fresh_job if fresh_job is not None else job + 1
        if job < 1:
            job = 1

    if resume_current_job and resume_research_failure:
        return _resume_interrupted_research_state(prior_state, job), job
    if resume_current_job and resume_blocked_research:
        state = dict(prior_state)
        state["job"] = job
        return state, job
    if resume_current_job and resume_failed_experiment:
        return (
            _running_resume_state(
                prior_state,
                job,
                preserve_resume_metadata=True,
                resume_previous_blocker=True,
            ),
            job,
        )
    if resume_current_job and (
        resume_halted_code_change or resume_builder_running or resume_manual_review
    ):
        return (
            _running_resume_state(prior_state, job, preserve_resume_metadata=True),
            job,
        )

    # Fresh jobs must be isolated from any prior resume/manual-review state.
    # Resume metadata is intentionally preserved only in the explicit
    # --resume-current-job branches above.
    return _fresh_launch_state(job), job


IDEAS_MD_PATH = ROOT / "autoresearch.ideas.md"


def default_controller_paths(root: Path, family: StrategyFamily) -> tuple[Path, Path, Path, Path]:
    """Returns state_path, current_md_path, ideas_md_path, runs_dir."""
    prefix = family.name
    return (
        root / f"{prefix}_autoresearch.next.json",
        root / f"{prefix}_autoresearch.current.md",
        root / f"{prefix}_autoresearch.ideas.md",
        root / family.runs_dirname,
    )


# Public API and historical re-exports. `_notify_discord` is intentionally
# included as a back-compat alias of autoresearch_research.notify_discord so
# tests that monkeypatch `loop_mod._notify_discord` continue to work and
# linters do not silently remove the import.
__all__ = (
    "AutoresearchController",
    "ExperimentRecord",
    "RunContext",
    "default_controller_paths",
    "main",
    "ROOT",
    "STATE_PATH",
    "CURRENT_MD_PATH",
    "IDEAS_MD_PATH",
    "DEFAULT_CONFIG_ORDER",
    "THESIS_FAMILY",
    "COMBINATION_RULES",
    "_notify_discord",
)


class AutoresearchController:
    def __init__(
        self,
        root: Path = ROOT,
        state_path: Path = STATE_PATH,
        current_md_path: Path = CURRENT_MD_PATH,
        ideas_md_path: Path = IDEAS_MD_PATH,
        runs_dir: Path | None = None,
        family: StrategyFamily | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state_path = state_path if state_path.is_absolute() else self.root / state_path
        self.current_md_path = (
            current_md_path if current_md_path.is_absolute() else self.root / current_md_path
        )
        self.ideas_md_path = (
            ideas_md_path if ideas_md_path.is_absolute() else self.root / ideas_md_path
        )
        if family is None:
            raise ValueError("AutoresearchController requires an explicit strategy family")
        self.family = family
        raw_runs_dir = runs_dir or Path(self.family.runs_dirname)
        self.runs_dir = (
            raw_runs_dir.resolve() if raw_runs_dir.is_absolute() else self.root / raw_runs_dir
        )
        self._set_runtime_paths_for_job(0)
        self.experiment_db = ExperimentDB(self.root / f"{self.family.name}_experiments.db")
        self.baseline_tracker = BaselineTracker(
            self.root / f"{self.family.name}_baseline_checkpoints.json"
        )
        # Transient cross-method state (formerly scattered self._* fields).
        self.ctx = RunContext()

    def _set_runtime_paths_for_job(self, job: int) -> None:
        self.job_runtime_root = _job_runtime_root(self.root, job)
        self.research_dir = self.job_runtime_root / "research"
        self.proposals_dir = self.job_runtime_root / "proposals"
        self.compilations_dir = self.job_runtime_root / "compilations"
        self.contracts_dir = self.job_runtime_root / "contracts"
        self.experiments_dir = self.job_runtime_root / "experiments"
        self.run_queue_dir = self.job_runtime_root / "run-queue"
        self.builder_requests_dir = self.job_runtime_root / "builder-requests"

    def _current_job(self) -> int | None:
        job = self.read_state().get("job")
        try:
            return int(job) if job is not None else None
        except (TypeError, ValueError):
            return None

    def _max_runtime_job_id(self) -> int:
        jobs_root = self.root / "runtime" / "jobs"
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

    def _max_job_in_legacy_queue_dir(self) -> int:
        legacy_queue_dir = self.root / self.family.run_queue_dirname
        if not legacy_queue_dir.exists():
            return 0
        max_job = 0
        for path in legacy_queue_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text())
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            max_job = max(max_job, _state_coerce_job_to_int(payload.get("job")))
        return max_job

    def next_fresh_job_id(self, prior_state: dict[str, Any] | None = None) -> int:
        """Pick a fresh job id that cannot collide with checkout history.

        Fresh launches must not reuse a historical job id from the local
        state file, experiment DB, runtime job tree, or legacy queue artifacts.
        Reusing one of those ids makes a "fresh" job inherit stale baseline
        decisions, queued theses, or old results.
        """
        prior = prior_state if prior_state is not None else self.read_state()
        max_seen = _state_coerce_job_to_int(prior.get("job"))
        max_seen = max(max_seen, self.experiment_db.max_job_id())
        max_seen = max(max_seen, self._max_runtime_job_id())
        max_seen = max(max_seen, self._max_job_in_legacy_queue_dir())
        return max_seen + 1 if max_seen > 0 else 1

    def clear_transient_context(self) -> None:
        """Clear per-experiment and per-research-round context.

        This prevents stale lineage metadata from leaking into later runs in
        the same controller process.
        """
        self.ctx.current_contract = None
        self.ctx.parent_experiment_id = ""

    def clear_terminal_metadata(self, state: dict[str, Any]) -> None:
        """Remove terminal-only fields when a transition reactivates the run."""
        state.pop("finished_reason", None)
        state.pop("research_stop_reasoning", None)

    def _ensure_job_metadata(self) -> None:
        """Stamp job-scoped defaults into state for direct loop entrypoints."""
        state = self.read_state()
        changed = False
        if not state.get("job"):
            state["job"] = 1
            changed = True
        if "research_round" not in state:
            state["research_round"] = 0
            changed = True
        if "job_usage" not in state:
            state["job_usage"] = None
            changed = True
        if changed:
            self.write_state(state)
        else:
            job = _state_coerce_job_to_int(state.get("job"))
            if job > 0:
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
        next_state = state.get("state", "unknown")
        if previous_state != next_state:
            trace_state_change(previous_state, next_state, state.get("finished_reason", ""))

    def read_entries(self) -> list[dict[str, Any]]:
        return self.experiment_db.export_entries()

    def write_entries(self, entries: list[dict[str, Any]]) -> None:
        self.experiment_db.import_entries(entries)

    def current_commit(self) -> str:
        return _git_sha()

    def direction(self) -> str:
        return self.experiment_db.best_direction()

    def read_results(self) -> list[ExperimentRecord]:
        all_results = self.experiment_db.read_results()
        state = self.read_state()
        job = state.get("job")
        if job is None:
            return all_results
        try:
            current_job = int(job)
        except (TypeError, ValueError):
            return all_results

        scoped: list[ExperimentRecord] = []
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
        return _artifacts_read_thesis_artifacts(self.proposals_dir, self.root)

    def is_better(self, candidate: float, current: float | None) -> bool:
        return _state_is_better(self.direction(), candidate, current)

    def best_result(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        return _state_best_result(results, self.direction())

    def latest_result(self, results: list[ExperimentRecord]) -> ExperimentRecord | None:
        return _state_latest_result(results)

    def list_known_variant_configs(self) -> list[str]:
        return _planning_list_known_variant_configs(self.root, self.family)

    def pending_configs(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_pending_configs(
            self.root, self.family, self.run_queue_dir, results, job=self._current_job()
        )

    def thesis_statuses(self, results: list[ExperimentRecord]) -> dict[str, dict[str, Any]]:
        return _planning_thesis_statuses(
            self.root, self.family, self.run_queue_dir, results, job=self._current_job()
        )

    def read_run_queue(self) -> list[dict[str, Any]]:
        return _artifacts_read_run_queue(self.run_queue_dir, self.root)

    def queue_from_thesis_artifacts(self, results: list[ExperimentRecord]) -> list[str]:
        return _artifacts_queue_from_thesis_artifacts(
            self.run_queue_dir, self.root, results, job=self._current_job()
        )

    def promote_missing_known_results(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_promote_missing_known_results(entries)

    def deduplicate_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_deduplicate_entries(entries)

    # ── WS-2: Thesis generation from ideas backlog ──────────────────────

    def parse_ideas_backlog(self) -> list[dict[str, Any]]:
        return _planning_parse_ideas_backlog(self.ideas_md_path, self.family)

    def generate_theses_from_ideas(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_generate_theses_from_ideas(
            self.root,
            self.family,
            self.ideas_md_path,
            self.run_queue_dir,
            self.proposals_dir,
            results,
            job=self._current_job(),
        )

    # ── WS-5: Combination phase ───────────────────────────────────────

    def thesis_family_for(self, config: str) -> str:
        return _planning_thesis_family_for(config, self.family, self.proposals_dir, self.root)

    def generate_combination_candidates(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_generate_combination_candidates(
            self.root, self.family, self.proposals_dir, results, job=self._current_job()
        )

    def parse_result_json(self, output: str) -> dict[str, Any] | None:
        return _experiment_parse_result_json(output)

    def parse_benchmark_details(self, output: str) -> dict[str, Any]:
        return _experiment_parse_benchmark_details(output)

    def _parse_benchmark_details_legacy(self, output: str) -> dict[str, Any]:
        return _experiment_parse_benchmark_details_legacy(output)

    def select_research_next_action(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        return _planning_select_research_next_action(
            self.root,
            self.family,
            self.run_queue_dir,
            self.proposals_dir,
            self.ideas_md_path,
            self.research_dir,
            results,
            job=self._current_job(),
        )

    def should_terminate(self, results: list[ExperimentRecord] | None = None) -> bool:
        current_results = results if results is not None else self.read_results()
        return _planning_should_terminate(
            self.root,
            self.family,
            self.run_queue_dir,
            self.research_dir,
            current_results,
            job=self._current_job(),
        )

    def plan_next_action(
        self, state: dict[str, Any], results: list[ExperimentRecord]
    ) -> dict[str, Any]:
        return _planning_plan_next_action(
            state,
            results,
            self.root,
            self.family,
            self.run_queue_dir,
            self.proposals_dir,
            self.ideas_md_path,
            self.research_dir,
        )

    def render_current_md(self, state: dict[str, Any], results: list[ExperimentRecord]) -> str:
        return _state_render_current_md(
            state,
            results,
            family_name=self.family.name,
            metric_name=self.primary_metric_name(),
        )

    def write_current_md(self, state: dict[str, Any], results: list[ExperimentRecord]) -> None:
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
        state["pending_configs"] = self.pending_configs(results)
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
        return _experiment_artifact_dir_for(self.state_path, self.runs_dir, config)

    def sanitize_duplicate_entries(self, config: str) -> None:
        _experiment_sanitize_duplicate_entries(self.experiment_db, config)

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
        mechanism_dimension: str = "",
        thesis_details: dict[str, Any] | None = None,
        rejection_reason: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        _research_log_research_round(
            self.experiment_db.path,
            self.state_path,
            round_number=round_number,
            thesis_id=thesis_id,
            hypothesis_id=hypothesis_id,
            outcome=outcome,
            config_changes=config_changes,
            hypothesis=hypothesis,
            mechanism=mechanism,
            mechanism_dimension=mechanism_dimension,
            thesis_details=thesis_details,
            rejection_reason=rejection_reason,
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
    ) -> None:
        _experiment_log_experiment_result(
            self,
            config=config,
            metric=metric,
            decision=decision,
            output=output,
            analysis=analysis,
            next_action=next_action,
            artifact_dir=artifact_dir,
        )

    def run_command(self, command: str) -> tuple[int, str]:
        return _experiment_run_command(self.root, command)

    def primary_metric_name(self) -> str:
        return self.experiment_db.primary_metric_name()

    def parse_metric(self, output: str, name: str = "profit_factor") -> float | None:
        return _experiment_parse_metric(output, name)

    def evaluate_metric(self, metric: float) -> str:
        state = self.read_state()
        raw_job = state.get("job")
        job_id: int | None = None
        try:
            if raw_job is not None:
                job_id = int(raw_job)
        except (TypeError, ValueError):
            job_id = None
        return _experiment_evaluate_metric(
            self.root,
            self.experiment_db.path.name,
            metric,
            job_id=job_id,
        )

    def derive_trade_analysis(
        self, config: str, metric: float, decision: str, output: str = ""
    ) -> dict[str, Any]:
        return _experiment_derive_trade_analysis(self, config, metric, decision, output)

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
            self.run_queue_dir,
            variants,
            thesis,
            primary_contract,
            baseline_config,
            experiments_dir=self.experiments_dir,
            job=self._current_job(),
            created_for_commit=self.current_commit(),
        )

    def _results_to_dicts(self, results: list) -> list[dict[str, Any]]:
        return _research_results_to_dicts(results)

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

    def _run_experiment(self, state: dict[str, Any]) -> int:
        return _experiment_run_experiment(self, state)

    def execute_once(self) -> int:
        """Run one iteration of the autoresearch loop.

        Flow:
        1. Resolve what to do next (baseline rerun, pending config, or research)
        2. If blocked for research: run conductor, compile thesis, wire config
        3. If running: execute the experiment (backtest + evaluate)
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
                            "must still pass experiment execution",
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

        # We have a config to run
        return self._run_experiment(state)


def main() -> int:
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
    args = parser.parse_args()
    if args.fresh_job and args.resume_current_job:
        parser.error("--fresh-job and --resume-current-job are mutually exclusive")

    family = load_family(args.family)
    state_path, current_md_path, ideas_md_path, runs_dir = default_controller_paths(ROOT, family)
    controller = AutoresearchController(
        family=family,
        state_path=state_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
    )
    prior_state = controller.read_state()
    fresh_job = (
        None if args.resume_current_job else _next_fresh_job_for_launch(controller, prior_state)
    )
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

    from trace_sdk import get_log_file, get_session_id, set_family

    set_family(args.family, job=job)
    trace(
        "MAIN",
        f"Autoresearch loop starting family={args.family} job={job} session={get_session_id()} log={get_log_file()}",
    )
    consecutive_research_required = 0
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
                consecutive_research_required += 1
                if consecutive_research_required >= max_consecutive_research_required():
                    trace(
                        "MAIN",
                        f"research_required blocker persisted for {consecutive_research_required} consecutive iterations; treating as terminal",
                    )
                    return 1
                continue
            # `execute_once()` already performs the only recoverable blocked
            # transition (`research_required`). Any remaining blocked state is
            # terminal for the process.
            return 1
        consecutive_research_required = 0


if __name__ == "__main__":
    raise SystemExit(main())
