#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

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
from autoresearch_experiment import primary_metric_name as _experiment_primary_metric_name
from autoresearch_experiment import run_command as _experiment_run_command
from autoresearch_experiment import run_experiment as _experiment_run_experiment
from autoresearch_experiment import (
    sanitize_duplicate_entries as _experiment_sanitize_duplicate_entries,
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
from autoresearch_state import (
    ExperimentRecord,
    RunContext,
)
from autoresearch_state import best_result as _state_best_result
from autoresearch_state import deduplicate_entries as _state_deduplicate_entries
from autoresearch_state import direction as _state_direction
from autoresearch_state import is_better as _state_is_better
from autoresearch_state import latest_result as _state_latest_result
from autoresearch_state import promote_missing_known_results as _state_promote_missing_known_results
from autoresearch_state import read_entries as _state_read_entries
from autoresearch_state import read_results as _state_read_results
from autoresearch_state import read_state as _state_read_state
from autoresearch_state import render_current_md as _state_render_current_md
from autoresearch_state import write_current_md as _state_write_current_md
from autoresearch_state import write_entries as _state_write_entries
from autoresearch_state import write_state as _state_write_state
from experiment_db import BaselineTracker, ExperimentDB
from strategy_family import StrategyFamily, load_family
from trace_logger import trace

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "autoresearch.next.json"
JSONL_PATH = ROOT / "autoresearch.jsonl"
CURRENT_MD_PATH = ROOT / "autoresearch.current.md"
IDEAS_MD_PATH = ROOT / "autoresearch.ideas.md"


def default_controller_paths(
    root: Path, family: StrategyFamily
) -> tuple[Path, Path, Path, Path, Path]:
    prefix = family.name
    return (
        root / f"{prefix}_autoresearch.next.json",
        root / f"{prefix}_autoresearch.jsonl",
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
    "JSONL_PATH",
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
        jsonl_path: Path = JSONL_PATH,
        current_md_path: Path = CURRENT_MD_PATH,
        ideas_md_path: Path = IDEAS_MD_PATH,
        runs_dir: Path | None = None,
        family: StrategyFamily | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state_path = state_path
        self.jsonl_path = jsonl_path
        self.current_md_path = current_md_path
        self.ideas_md_path = ideas_md_path
        self.family = family or load_family("orb")
        self.runs_dir = runs_dir or (root / self.family.runs_dirname)
        self.research_dir = root / self.family.research_dirname
        self.proposals_dir = root / self.family.proposals_dirname
        self.compilations_dir = root / self.family.compilations_dirname
        self.contracts_dir = root / self.family.contracts_dirname
        self.run_queue_dir = root / self.family.run_queue_dirname
        self.experiment_db = ExperimentDB(root / f"{self.family.name}_experiments_db.json")
        self.baseline_tracker = BaselineTracker(
            root / f"{self.family.name}_baseline_checkpoints.json"
        )
        # Transient cross-method state (formerly scattered self._* fields).
        self.ctx = RunContext()

    def read_state(self) -> dict[str, Any]:
        return _state_read_state(self.state_path)

    def write_state(self, state: dict[str, Any]) -> None:
        _state_write_state(self.state_path, state)

    def read_entries(self) -> list[dict[str, Any]]:
        return _state_read_entries(self.jsonl_path)

    def write_entries(self, entries: list[dict[str, Any]]) -> None:
        _state_write_entries(self.jsonl_path, entries)

    def current_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        commit = (result.stdout or "").strip()
        return commit or "unknown"

    def direction(self) -> str:
        return _state_direction(self.read_entries())

    def read_results(self) -> list[ExperimentRecord]:
        return _state_read_results(self.read_entries())

    def read_json_artifacts(self, directory: Path) -> list[dict[str, Any]]:
        return _artifacts_read_artifacts_relative_to_root(directory, self.root)

    def read_research_artifacts(self) -> list[dict[str, Any]]:
        return _artifacts_read_research_artifacts(self.research_dir, self.root)

    def read_thesis_artifacts(self) -> list[dict[str, Any]]:
        return _artifacts_read_thesis_artifacts(self.proposals_dir, self.root)

    def is_better(self, candidate: float, current: float | None) -> bool:
        return _state_is_better(self.direction(), candidate, current)

    def best_result(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        return _state_best_result(results, self.direction())

    def latest_result(self, results: list[ExperimentRecord]) -> ExperimentRecord | None:
        return _state_latest_result(results)

    def list_known_variant_configs(self) -> list[str]:
        return _planning_list_known_variant_configs(self.root)

    def pending_configs(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_pending_configs(self.root, results)

    def thesis_statuses(self, results: list[ExperimentRecord]) -> dict[str, dict[str, Any]]:
        return _planning_thesis_statuses(self.root, self.run_queue_dir, results)

    def read_run_queue(self) -> list[dict[str, Any]]:
        return _artifacts_read_run_queue(self.run_queue_dir, self.root)

    def queue_from_thesis_artifacts(self, results: list[ExperimentRecord]) -> list[str]:
        return _artifacts_queue_from_thesis_artifacts(self.run_queue_dir, self.root, results)

    def promote_missing_known_results(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_promote_missing_known_results(entries)

    def deduplicate_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_deduplicate_entries(entries)

    # ── WS-2: Thesis generation from ideas backlog ──────────────────────

    def parse_ideas_backlog(self) -> list[dict[str, Any]]:
        return _planning_parse_ideas_backlog(self.ideas_md_path)

    def generate_theses_from_ideas(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_generate_theses_from_ideas(
            self.root,
            self.ideas_md_path,
            self.run_queue_dir,
            self.proposals_dir,
            results,
        )

    # ── WS-5: Combination phase ───────────────────────────────────────

    def thesis_family_for(self, config: str) -> str:
        return _planning_thesis_family_for(config, self.proposals_dir, self.root)

    def generate_combination_candidates(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_generate_combination_candidates(self.root, self.proposals_dir, results)

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
        )

    def should_terminate(self, results: list[ExperimentRecord] | None = None) -> bool:
        current_results = results if results is not None else self.read_results()
        return _planning_should_terminate(
            self.root, self.run_queue_dir, self.research_dir, current_results
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
        return _state_render_current_md(state, results)

    def write_current_md(self, state: dict[str, Any], results: list[ExperimentRecord]) -> None:
        _state_write_current_md(self.current_md_path, state, results)

    def reconcile_state(self) -> dict[str, Any]:
        entries = self.read_entries()
        reconciled_entries = self.promote_missing_known_results(entries)
        reconciled_entries = self.deduplicate_entries(reconciled_entries)
        if reconciled_entries != entries:
            self.write_entries(reconciled_entries)
        state = self.read_state()
        results = self.read_results()
        best = self.best_result(results)
        if best:
            state["current_best"] = best
        state["pending_configs"] = self.pending_configs(results)
        state["thesis_statuses"] = self.thesis_statuses(results)

        latest = self.latest_result(results)
        heartbeat = state.setdefault("heartbeat", {})
        if latest is not None:
            heartbeat["last_completed_thesis"] = latest.config
            heartbeat["last_result"] = latest.status
            heartbeat["last_metric"] = latest.metric
            heartbeat["current_best"] = state.get("current_best", {})

        state = self.plan_next_action(state, results)
        old_state_val = state.get("state", "unknown")
        self.write_state(state)
        self.write_current_md(state, results)
        next_action = state.get("next_action", {})
        trace(
            "RECONCILE",
            f"state={old_state_val} best={best} next_action_type={next_action.get('type')}",
        )
        return state

    def artifact_dir_for(self, config: str) -> Path:
        return _experiment_artifact_dir_for(self.state_path, self.runs_dir, config)

    def sanitize_duplicate_entries(self, config: str) -> None:
        _experiment_sanitize_duplicate_entries(self.jsonl_path, config)

    def _accumulate_job_usage(self, round_usage: dict[str, Any]) -> None:
        _research_accumulate_job_usage(self.state_path, round_usage)

    def log_research_round(
        self,
        *,
        round_number: int,
        thesis_id: str,
        outcome: str,
        config_changes: dict[str, Any] | None = None,
        hypothesis: str = "",
        mechanism: str = "",
        mechanism_dimension: str = "",
        rejection_reason: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        _research_log_research_round(
            self.jsonl_path,
            self.state_path,
            round_number=round_number,
            thesis_id=thesis_id,
            outcome=outcome,
            config_changes=config_changes,
            hypothesis=hypothesis,
            mechanism=mechanism,
            mechanism_dimension=mechanism_dimension,
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
    ) -> None:
        _experiment_log_experiment_result(
            self,
            config=config,
            metric=metric,
            decision=decision,
            output=output,
            analysis=analysis,
        )

    def run_command(self, command: str) -> tuple[int, str]:
        return _experiment_run_command(self.root, command)

    def primary_metric_name(self) -> str:
        return _experiment_primary_metric_name(self.read_entries())

    def parse_metric(self, output: str, name: str = "median_expectancy") -> float | None:
        return _experiment_parse_metric(output, name)

    def evaluate_metric(self, metric: float) -> str:
        return _experiment_evaluate_metric(self.root, self.jsonl_path.name, metric)

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

    def _resolve_next_action(self) -> dict[str, Any]:
        """Decide what to do next. Returns a state dict with state/next_action/blockers.

        Priority order:
        0. Resume halted thesis (code was implemented, runtime_config now exists)
        1. Forced baseline rerun (code changed or periodic)
        2. reconcile_state() discovery (pending configs, thesis queue, combos, ideas)
        3. Blocked for research
        """
        # Resume a halted thesis if the missing config keys now exist in base yaml
        state = self.read_state()
        halted_id = state.get("halted_thesis_id")
        if halted_id and state.get("halted_reason") == "requires_code_change":
            raw_thesis = state.get("halted_thesis", {})
            config_changes = raw_thesis.get("config_changes", {})
            if config_changes:
                import yaml as _yaml

                base = _yaml.safe_load(
                    (self.root / "configs" / self.family.base_config_filename).read_text()
                )
                missing = set(config_changes) - set(base)
                if not missing:
                    runtime = {**base, **config_changes}
                    exp_dir = self.root / "experiments" / halted_id
                    exp_dir.mkdir(parents=True, exist_ok=True)
                    config_path = f"experiments/{halted_id}/runtime_config.json"
                    (self.root / config_path).write_text(json.dumps(runtime, indent=2) + "\n")
                    state["state"] = "running"
                    state["current_thesis"] = {"config": config_path, "status": "ready_to_run"}
                    state["next_action"] = {
                        "type": "run_experiment",
                        "config": config_path,
                        "benchmark_command": self.family.benchmark_command(config_path),
                        "requires_trade_analysis": True,
                        "source": "resumed_halted_thesis",
                    }
                    state["blockers"] = []
                    state.pop("halted_thesis_id", None)
                    state.pop("halted_reason", None)
                    state.pop("halted_thesis", None)
                    self.write_state(state)
                    trace("LOOP", f"resumed halted thesis={halted_id}")
                    return state

        baseline_action = self._check_baseline_rerun()
        if baseline_action:
            state = self.read_state()
            state["state"] = "running"
            state["next_action"] = baseline_action
            state["blockers"] = []
            self.write_state(state)
            return state

        return self.reconcile_state()

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

        state = self._resolve_next_action()
        trace(
            "LOOP",
            f"state={state.get('state')} blockers={[b.get('kind') for b in state.get('blockers', [])]}",
        )

        # Handle blocked state: invoke research conductor
        if state.get("state") == "blocked":
            blockers = state.get("blockers", [])
            if any(b.get("kind") == "research_required" for b in blockers):
                state = self._run_research(state)

        # Terminal states
        if state.get("state") != "running":
            print(f"LOOP_STOP state={state.get('state')}")
            return 0

        # We have a config to run
        return self._run_experiment(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run autoresearch controller")
    parser.add_argument("--family", default="orb", help="Strategy family to run (orb or ema)")
    args = parser.parse_args()

    family = load_family(args.family)
    state_path, jsonl_path, current_md_path, ideas_md_path, runs_dir = default_controller_paths(
        ROOT, family
    )
    controller = AutoresearchController(
        family=family,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
    )
    # Increment job number on each loop start
    state = controller.read_state()
    job = state.get("job", 0) + 1
    state["job"] = job
    state["research_round"] = 0  # reset round counter for clean job isolation
    state["job_usage"] = None  # reset token usage for new job
    controller.write_state(state)

    from trace_logger import get_log_file, get_session_id, set_family

    set_family(args.family, job=job)
    trace(
        "MAIN",
        f"Autoresearch loop starting family={args.family} job={job} session={get_session_id()} log={get_log_file()}",
    )
    while True:
        code = controller.execute_once()
        if code != 0:
            return code
        state = controller.read_state()
        current = state.get("state")
        if current in ("finished", "interrupted", "halted"):
            return 0
        # "blocked" with research_required is handled by execute_once on next
        # iteration. Other blocked states (command_failed, etc.) also terminate
        # via non-zero return code from execute_once.


if __name__ == "__main__":
    raise SystemExit(main())
