#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from autoresearch_artifacts import (
    queue_from_thesis_artifacts as _artifacts_queue_from_thesis_artifacts,
    read_artifacts_relative_to_root as _artifacts_read_artifacts_relative_to_root,
    read_research_artifacts as _artifacts_read_research_artifacts,
    read_run_queue as _artifacts_read_run_queue,
    read_thesis_artifacts as _artifacts_read_thesis_artifacts,
)
from autoresearch_research import (
    accumulate_job_usage as _research_accumulate_job_usage,
    execute_research_one as _research_execute_research_one,
    execute_research_sdk as _research_execute_research_sdk,
    load_baseline_config as _research_load_baseline_config,
    log_research_round as _research_log_research_round,
    notify_discord as _notify_discord,
    queue_variants as _research_queue_variants,
    results_to_dicts as _research_results_to_dicts,
    run_research as _research_run_research,
)
from autoresearch_planning import (
    COMBINATION_RULES,
    DEFAULT_CONFIG_ORDER,
    THESIS_FAMILY,
    build_finish_summary as _planning_build_finish_summary,
    check_baseline_rerun as _planning_check_baseline_rerun,
    generate_combination_candidates as _planning_generate_combination_candidates,
    generate_theses_from_ideas as _planning_generate_theses_from_ideas,
    list_known_variant_configs as _planning_list_known_variant_configs,
    parse_ideas_backlog as _planning_parse_ideas_backlog,
    pending_configs as _planning_pending_configs,
    plan_next_action as _planning_plan_next_action,
    select_research_next_action as _planning_select_research_next_action,
    should_terminate as _planning_should_terminate,
    thesis_family_for as _planning_thesis_family_for,
    thesis_statuses as _planning_thesis_statuses,
)
from autoresearch_state import (
    ExperimentRecord,
    deduplicate_entries as _state_deduplicate_entries,
    direction as _state_direction,
    is_better as _state_is_better,
    best_result as _state_best_result,
    latest_result as _state_latest_result,
    promote_missing_known_results as _state_promote_missing_known_results,
    read_entries as _state_read_entries,
    read_results as _state_read_results,
    read_state as _state_read_state,
    render_current_md as _state_render_current_md,
    write_current_md as _state_write_current_md,
    write_entries as _state_write_entries,
    write_state as _state_write_state,
)
from trace_logger import (
    trace, trace_benchmark, trace_ssh,
    begin_hypothesis, end_hypothesis, current_hypothesis_id, get_run_id,
)
from experiment_db import (
    ExperimentDB, ExperimentResult, BaselineTracker, BaselineCheckpoint,
    build_data_hash, build_config_hash,
)
from strategy_family import StrategyFamily, load_family

ROOT = Path(__file__).resolve().parent
MAX_RESEARCH_ROUNDS = 100  # safeguard: max single-thesis research iterations
STATE_PATH = ROOT / "autoresearch.next.json"
JSONL_PATH = ROOT / "autoresearch.jsonl"
CURRENT_MD_PATH = ROOT / "autoresearch.current.md"
IDEAS_MD_PATH = ROOT / "autoresearch.ideas.md"
RUNS_DIR = ROOT / "autoresearch-runs"
RESEARCH_DIR = ROOT / "research"
THESIS_DIR = ROOT / "theses"


def default_controller_paths(root: Path, family: StrategyFamily) -> tuple[Path, Path, Path, Path, Path]:
    prefix = family.name
    return (
        root / f"{prefix}_autoresearch.next.json",
        root / f"{prefix}_autoresearch.jsonl",
        root / f"{prefix}_autoresearch.current.md",
        root / f"{prefix}_autoresearch.ideas.md",
        root / family.runs_dirname,
    )


# Re-exports for any external callers that historically imported these names
# from autoresearch_loop. Keep this near the top so they remain discoverable.
__all__ = ("DEFAULT_CONFIG_ORDER", "THESIS_FAMILY", "COMBINATION_RULES")


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
        self.thesis_dir = root / "theses"
        self.proposals_dir = root / self.family.proposals_dirname
        self.compilations_dir = root / self.family.compilations_dirname
        self.contracts_dir = root / self.family.contracts_dirname
        self.run_queue_dir = root / self.family.run_queue_dirname
        self.experiment_db = ExperimentDB(root / f"{self.family.name}_experiments_db.json")
        self.baseline_tracker = BaselineTracker(root / f"{self.family.name}_baseline_checkpoints.json")

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
            self.root, self.ideas_md_path, self.run_queue_dir, self.proposals_dir, results,
        )

    # ── WS-5: Combination phase ───────────────────────────────────────

    def thesis_family_for(self, config: str) -> str:
        return _planning_thesis_family_for(config, self.proposals_dir, self.root)

    def generate_combination_candidates(self, results: list[ExperimentRecord]) -> list[str]:
        return _planning_generate_combination_candidates(self.root, self.proposals_dir, results)

    # ── WS-3: Research stage hook ─────────────────────────────────────

    def emit_research_request(self, results: list[ExperimentRecord]) -> Path:
        """Write a research request artifact for external execution."""
        requests_dir = self.research_dir / "requests"
        requests_dir.mkdir(parents=True, exist_ok=True)
        request_id = f"research-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
        best = self.best_result(results)
        kept = [r.config for r in results if r.status == "keep"]
        discarded = [r.config for r in results if r.status == "discard"]

        request = {
            "request_id": f"{self.family.name}-{request_id}",
            "type": "wide_scan",
            "status": "pending",
            "family": self.family.name,
            "context": {
                "strategy": self.family.name.upper(),
                "current_best": best,
                "kept_theses": kept,
                "discarded_theses": discarded,
            },
            "questions": [
                "What structural improvements to opening range breakout strategies are documented in literature?",
                "What regime filters have been shown to improve mean-reversion or breakout strategies?",
                "What exit mechanisms beyond fixed stop/target improve intraday momentum strategies?",
                "What universe selection methods improve intraday breakout strategy performance?",
            ],
        }
        path = requests_dir / f"{request_id}.json"
        path.write_text(json.dumps(request, indent=2) + "\n")
        return path

    def check_research_completion(self) -> list[dict[str, Any]]:
        """Return completed research artifacts (status=completed)."""
        completed: list[dict[str, Any]] = []
        if not self.research_dir.exists():
            return completed
        for path in sorted(self.research_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "completed":
                payload["artifact_path"] = path.relative_to(self.root).as_posix()
                completed.append(payload)
        return completed

    def has_pending_research_request(self) -> bool:
        """Check if there is already a pending research request."""
        requests_dir = self.research_dir / "requests"
        if not requests_dir.exists():
            return False
        for path in requests_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "pending":
                return True
        return False

    # ── WS-4: Parse real benchmark output ─────────────────────────────

    def parse_result_json(self, output: str) -> dict[str, Any] | None:
        """Find RESULT_JSON line in output, read and return the JSON file."""
        match = re.search(r"^RESULT_JSON (.+)$", output, flags=re.MULTILINE)
        if not match:
            return None
        result_path = Path(match.group(1).strip())
        if not result_path.exists():
            return None
        try:
            return json.loads(result_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def parse_benchmark_details(self, output: str) -> dict[str, Any]:
        """Extract metrics from result.json written by backtest."""
        result_json = self.parse_result_json(output)
        if result_json:
            details: dict[str, Any] = {}
            metrics = result_json.get("metrics", {})
            for key in ("trade_count", "profit_factor", "max_drawdown",
                        "pct_profitable_windows", "avg_sharpe_across_windows",
                        "win_rate"):
                if key in metrics:
                    details[key] = metrics[key]
            if result_json.get("diagnostics"):
                details["diagnostics"] = result_json["diagnostics"]
            if result_json.get("trades_file"):
                details["trades_file"] = result_json["trades_file"]
            if result_json.get("strategy_events_file"):
                details["strategy_events_file"] = result_json["strategy_events_file"]
            if result_json.get("diagnostics_file"):
                details["diagnostics_file"] = result_json["diagnostics_file"]
            if result_json.get("strategy_diagnostics"):
                details["strategy_diagnostics"] = result_json["strategy_diagnostics"]
            if result_json.get("git_sha"):
                details["git_sha"] = result_json["git_sha"]
            if result_json.get("config_hash"):
                details["config_hash"] = result_json["config_hash"]
            return details

        # Fallback: should not happen with new backtest, but keeps old runs working
        trace("LOOP", "WARNING: no RESULT_JSON found, falling back to stdout parsing")
        return self._parse_benchmark_details_legacy(output)

    def _parse_benchmark_details_legacy(self, output: str) -> dict[str, Any]:
        """Legacy stdout parsing u2014 only used if result.json is missing."""
        details: dict[str, Any] = {}
        patterns = {
            "trade_count": r"^METRIC trade_count=(\d+)",
            "profit_factor": r"^METRIC profit_factor=([-+]?\d*\.?\d+)",
            "max_drawdown": r"^METRIC max_drawdown=([-+]?\d*\.?\d+)",
            "pct_profitable_windows": r"^METRIC pct_profitable_windows=([-+]?\d*\.?\d+)",
            "avg_sharpe_across_windows": r"^METRIC avg_sharpe_across_windows=([-+]?\d*\.?\d+)",
            "win_rate": r"^METRIC win_rate=([-+]?\d*\.?\d+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, output, flags=re.MULTILINE)
            if match:
                val = match.group(1)
                details[key] = int(val) if key == "trade_count" else float(val)
        diag_match = re.search(r"^DIAGNOSTICS (.+)$", output, flags=re.MULTILINE)
        if diag_match:
            try:
                details["diagnostics"] = json.loads(diag_match.group(1))
            except json.JSONDecodeError:
                pass
        trades_match = re.search(r"^TRADES_FILE (.+)$", output, flags=re.MULTILINE)
        if trades_match:
            details["trades_file"] = trades_match.group(1).strip()
        return details

    def select_research_next_action(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        return _planning_select_research_next_action(
            self.root, self.family, self.run_queue_dir, self.proposals_dir,
            self.ideas_md_path, self.research_dir, results,
        )

    def should_terminate(self, results: list[ExperimentRecord] | None = None) -> bool:
        current_results = results if results is not None else self.read_results()
        return _planning_should_terminate(self.root, self.run_queue_dir, self.research_dir, current_results)

    def _build_finish_summary(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        return _planning_build_finish_summary(results, self.check_research_completion())

    def plan_next_action(self, state: dict[str, Any], results: list[ExperimentRecord]) -> dict[str, Any]:
        return _planning_plan_next_action(
            state, results, self.root, self.family, self.run_queue_dir,
            self.proposals_dir, self.ideas_md_path, self.research_dir,
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
        trace("RECONCILE", f"state={old_state_val} best={best} next_action_type={next_action.get('type')}")
        return state

    def artifact_dir_for(self, config: str) -> Path:
        state = self.read_state()
        job = state.get("job", 0)
        path = self.runs_dir.resolve() / f"job-{job}" / Path(config).stem
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sanitize_duplicate_entries(self, config: str) -> None:
        filtered: list[dict[str, Any]] = []
        slug = Path(config).stem
        for entry in self.read_entries():
            if entry.get("type") == "config":
                filtered.append(entry)
                continue
            asi = entry.get("asi") or {}
            same_config = asi.get("config") == config
            low_information = entry.get("description") == f"loop: {slug}"
            if same_config and low_information:
                continue
            filtered.append(entry)
        self.write_entries(filtered)

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
            self.jsonl_path, self.state_path,
            round_number=round_number, thesis_id=thesis_id, outcome=outcome,
            config_changes=config_changes, hypothesis=hypothesis,
            mechanism=mechanism, mechanism_dimension=mechanism_dimension,
            rejection_reason=rejection_reason, usage=usage,
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
        self.sanitize_duplicate_entries(config)
        # Reuse artifact dir from derive_trade_analysis if available
        artifact_dir = getattr(self, "_current_artifact_dir", None) or self.artifact_dir_for(config)
        self._current_artifact_dir = None  # reset for next hypothesis
        (artifact_dir / "benchmark_output.txt").write_text(output)
        (artifact_dir / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")

        # Load thesis metadata (thesis_id, config_changes) from the
        # experiment directory so the results table shows WHAT was tried.
        contract = getattr(self, "_current_contract", None)
        thesis_id = contract.thesis_id if contract else Path(config).stem
        config_changes: dict[str, Any] = {}
        thesis_json_path = self.root / "experiments" / (contract.experiment_id if contract else Path(config).stem) / "thesis.json"
        if thesis_json_path.exists():
            try:
                tj = json.loads(thesis_json_path.read_text())
                thesis_id = tj.get("thesis_id", thesis_id)
                config_changes = tj.get("config_changes", {})
            except Exception:
                pass

        asi = {
            "job": self.read_state().get("job"),
            "run_id": get_run_id(),
            "hypothesis_id": current_hypothesis_id(),
            "hypothesis": Path(config).stem,
            "config": config,
            "artifact_dir": artifact_dir.relative_to(self.root).as_posix(),
            "trade_analysis": analysis.get("trade_analysis", {}),
            "insights": analysis.get("insights", []),
            "next_candidates": analysis.get("next_candidates", []),
            "next_thesis_suggestion": analysis.get("next_thesis_suggestion", ""),
            "why_not_data_fit": analysis.get("why_not_data_fit"),
            "insight_brief": analysis.get("insight_brief", ""),
            "thesis_id": thesis_id,
            "config_changes": config_changes,
        }
        # Tag baseline reruns for drift detection
        rerun_commit = self.read_state().get("next_action", {}).get("baseline_rerun_for_commit")
        if rerun_commit:
            asi["baseline_rerun_for_commit"] = rerun_commit
        details = self.parse_benchmark_details(output)
        entries = self.read_entries()
        next_run = 1 + len([entry for entry in entries if entry.get("type") != "config"])
        state = self.read_state()
        entry = {
            "run": next_run,
            "job": state.get("job"),
            "run_id": get_run_id(),
            "hypothesis_id": current_hypothesis_id(),
            "commit": self.current_commit(),
            "metric": metric,
            "metrics": details,
            "status": decision,
            "description": f"strict-native loop: {Path(config).stem}",
            "timestamp": int(time.time() * 1000),
            "segment": 0,
            "confidence": None,
            "asi": asi,
        }
        entries.append(entry)
        self.write_entries(entries)
        trace_benchmark(config, metric, decision, details)

        # Write to structured experiment database
        contract = getattr(self, "_current_contract", None)
        verdict = analysis.get("trade_analysis", {}).get("verdict", {})
        runtime_config = getattr(self, "_latest_config_contents", {}) or {}
        self.experiment_db.add(ExperimentResult(
            experiment_id=contract.experiment_id if contract else entry["run_id"],
            thesis_id=contract.thesis_id if contract else Path(config).stem,
            config_path=config,
            runtime_config=runtime_config,
            code_commit=self.current_commit(),
            data_hash=build_data_hash(runtime_config),
            train_metrics={},
            validation_metrics=details,
            trade_count=details.get("trade_count", 0),
            trades_file=details.get("trades_file", ""),
            strategy_events_file=details.get("strategy_events_file", ""),
            diagnostics_file=details.get("diagnostics_file", ""),
            strategy_diagnostics=details.get("strategy_diagnostics", {}),
            accepted=decision == "keep",
            rejection_reason=verdict.get("summary", "") if decision != "keep" else "",
            verdict_status=verdict.get("status", "none"),
            verdict_summary=verdict.get("summary", ""),
            parent_experiment_id=getattr(self, "_parent_experiment_id", ""),
            timestamp=int(time.time() * 1000),
            family=self.family.name,
            hypothesis=contract.hypothesis if contract else "",
            mechanism=contract.mechanism if contract else "",
            job=state.get("job", 0),
            usage=state.get("_last_round_usage", {}),
        ))

    def run_command(self, command: str) -> tuple[int, str]:
        import sys
        try:
            trace("COMMAND", f"START: {command}")
            print(f"RUN_COMMAND start: {command[:80]}", flush=True)
            sys.stdout.flush()
            result = subprocess.run(
                command, shell=True, cwd=self.root,
                capture_output=True, text=True, timeout=1800,
                stdin=subprocess.DEVNULL,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            trace_ssh(command, result.returncode, stdout, stderr)
            print(f"RUN_COMMAND done: exit={result.returncode}", flush=True)
            sys.stdout.flush()
            output = stdout + stderr
            return int(result.returncode), output
        except subprocess.TimeoutExpired:
            trace("COMMAND", f"TIMEOUT (1800s): {command[:100]}")
            print(f"COMMAND TIMEOUT (1800s): {command[:100]}", flush=True)
            return 1, "TIMEOUT"
        except Exception as exc:
            trace("COMMAND", f"ERROR: {exc}")
            print(f"RUN_COMMAND error: {exc}", flush=True)
            return 1, str(exc)

    def primary_metric_name(self) -> str:
        """Read the primary metric name from the JSONL config header."""
        for entry in self.read_entries():
            if entry.get("type") == "config":
                return entry.get("metricName", "median_expectancy")
        return "median_expectancy"

    def parse_metric(self, output: str, name: str = "median_expectancy") -> float | None:
        result_json = self.parse_result_json(output)
        if result_json:
            val = result_json.get("metrics", {}).get(name)
            return float(val) if val is not None else None
        # Legacy fallback
        match = re.search(rf"^METRIC {re.escape(name)}=([-+]?\d*\.?\d+)", output, flags=re.MULTILINE)
        return float(match.group(1)) if match else None

    def evaluate_metric(self, metric: float) -> str:
        result = subprocess.run(
            [
                "python3",
                "autoresearch_helper.py",
                "evaluate",
                "--jsonl",
                self.jsonl_path.name,
                "--metric",
                str(metric),
                "--direction",
                "higher",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        text = (result.stdout or "") + (result.stderr or "")
        return "keep" if "DECISION: keep" in text else "discard"

    def derive_trade_analysis(
        self, config: str, metric: float, decision: str, output: str = ""
    ) -> dict[str, Any]:
        best = self.best_result(self.read_results())
        details = self.parse_benchmark_details(output)

        # Load full config contents for analyst context
        config_contents: dict[str, Any] = {}
        config_path = self.root / config
        if config_path.exists():
            try:
                if config_path.suffix in (".yaml", ".yml"):
                    import yaml
                    raw = yaml.safe_load(config_path.read_text())
                else:
                    raw = json.loads(config_path.read_text())
                if isinstance(raw, dict) and "runtime_config" in raw:
                    config_contents = raw["runtime_config"]
                elif isinstance(raw, dict):
                    config_contents = raw
                else:
                    from ema_contract import compile_ema_contract
                    config_contents = compile_ema_contract(raw).runtime_config
            except Exception:
                pass

        # Trades file path comes from result.json (backtest writes it to the run dir)
        trades_file = details.get("trades_file", "")
        self._latest_trades_file = trades_file
        self._latest_strategy_events_file = details.get("strategy_events_file", "")
        self._latest_diagnostics_file = details.get("diagnostics_file", "")
        self._latest_config_contents = config_contents

        # Build analysis record
        trade_analysis: dict[str, Any] = {
            "what_changed_vs_baseline": f"{Path(config).stem} evaluated independently.",
            "primary_metric_improved": decision == "keep",
            **details,
        }
        result = {
            "trade_analysis": trade_analysis,
            "insights": [f"metric={metric}", f"decision={decision}"],
            "next_candidates": [],
            "why_not_data_fit": "Independent thesis evaluation only.",
        }
        return result

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
            self.root, self.run_queue_dir,
            variants, thesis, primary_contract, baseline_config,
        )

    def _results_to_dicts(self, results: list) -> list[dict[str, Any]]:
        return _research_results_to_dicts(results)

    def execute_research_one(self) -> dict[str, Any]:
        return _research_execute_research_one(self)

    def _check_baseline_rerun(self) -> dict[str, Any] | None:
        return _planning_check_baseline_rerun(
            self.root, self.family, self.baseline_tracker,
            self.current_commit(), self.read_results(),
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
                base = _yaml.safe_load((self.root / "configs" / self.family.base_config_filename).read_text())
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
        """Run a single experiment (backtest + evaluate + log). Returns exit code."""
        next_action = state["next_action"]
        config = next_action["config"]

        # Compute config hash and create per-run output dir
        import hashlib
        config_path_full = self.root / config
        if config_path_full.exists():
            if config_path_full.suffix in (".yaml", ".yml"):
                import yaml
                _cfg = yaml.safe_load(config_path_full.read_text())
            else:
                _cfg = json.loads(config_path_full.read_text())
            if isinstance(_cfg, dict) and "runtime_config" in _cfg:
                _cfg = _cfg["runtime_config"]
            config_hash = hashlib.sha256(
                json.dumps(_cfg, sort_keys=True).encode()
            ).hexdigest()[:12]
        else:
            config_hash = hashlib.sha256(config.encode()).hexdigest()[:12]

        state = self.read_state()
        job = state.get("job", 0)
        run_output_dir = self.runs_dir.resolve() / f"job-{job}" / config_hash
        run_output_dir.mkdir(parents=True, exist_ok=True)
        self._current_artifact_dir = run_output_dir

        if config_path_full.exists():
            import shutil
            shutil.copy2(config_path_full, run_output_dir / "config.json")

        command = self.family.benchmark_command(config, output_dir=str(run_output_dir))
        if not command:
            print("LOOP_STOP missing_benchmark_command")
            return 1

        hyp_name = Path(config).stem if config else "unknown"
        begin_hypothesis(hyp_name)
        trace("LOOP", f"BENCHMARK START: {command}")
        print(f"HEARTBEAT running {command}")
        code, output = self.run_command(command)

        if code != 0:
            state["state"] = "blocked"
            state["blockers"] = [{"kind": "command_failed", "detail": command, "exit_code": code}]
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            print(f"LOOP_STOP state=blocked exit_code={code}")
            _notify_discord(
                f"\u26a0\ufe0f {self.family.name.upper()} BLOCKED \u2014 backtest failed",
                f"**Command:** `{command[:200]}`\n**Exit code:** {code}",
                webhook=self.family.discord_webhook, color=0xFFA500,
            )
            return code

        metric = self.parse_metric(output, name=self.primary_metric_name())
        if metric is None:
            state["state"] = "blocked"
            state["blockers"] = [{"kind": "metric_parse_failed", "detail": command}]
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            print("LOOP_STOP state=blocked metric_parse_failed")
            _notify_discord(
                f"\u26a0\ufe0f {self.family.name.upper()} BLOCKED \u2014 metric parse failed",
                f"**Command:** `{command[:200]}`\nCould not extract metric from output.",
                webhook=self.family.discord_webhook, color=0xFFA500,
            )
            return 1

        details = self.parse_benchmark_details(output)
        decision = self.evaluate_metric(metric)
        trace("LOOP", f"METRIC parsed: {metric} decision={decision} config={config}")

        # Evaluate against thesis predictions (if we have a contract)
        verdict = None
        contract = getattr(self, "_current_contract", None)
        if contract and contract.expected_effects:
            try:
                from experiment_evaluator import evaluate_experiment
                from research_types import ResearchThesis

                results = self.read_results()
                baseline_result = results[0] if results else None
                baseline_metrics: dict[str, Any] = {}
                if baseline_result:
                    bta = baseline_result.asi.get("trade_analysis", {})
                    for k in ("trade_count", "profit_factor", "max_drawdown",
                              "pct_profitable_windows", "avg_sharpe_across_windows",
                              "median_expectancy"):
                        if bta.get(k) is not None:
                            baseline_metrics[k] = bta[k]

                candidate_metrics = dict(details)
                candidate_metrics["median_expectancy"] = metric

                thesis_for_eval = ResearchThesis(
                    thesis_id=contract.thesis_id,
                    strategy_family=contract.strategy_family,
                    hypothesis=contract.hypothesis,
                    mechanism=contract.mechanism,
                    expected_effects=contract.expected_effects,
                    disqualifiers=contract.disqualifiers,
                    required_diagnostics=contract.required_diagnostics,
                )

                verdict = evaluate_experiment(
                    thesis=thesis_for_eval,
                    baseline_metrics=baseline_metrics,
                    candidate_metrics=candidate_metrics,
                    experiment_id=contract.experiment_id,
                    strategy_diagnostics=details.get("strategy_diagnostics"),
                )
                trace("EVAL", f"verdict={verdict.status} passed={verdict.passed_effects} failed={verdict.failed_effects} dq={verdict.triggered_disqualifiers}")
                print(f"VERDICT {verdict.status}: {verdict.summary}")

                experiment_dir = self.root / "experiments" / contract.experiment_id
                if experiment_dir.exists():
                    (experiment_dir / "verdict.json").write_text(
                        verdict.model_dump_json(indent=2) + "\n"
                    )

                if verdict.status == "rejected":
                    decision = "discard"
                elif verdict.status == "accepted" and decision == "discard":
                    trace("EVAL", "thesis accepted despite metric threshold")

            except Exception as exc:
                trace("EVAL", f"evaluation error: {exc}")
                print(f"EVAL error (non-fatal): {exc}")

        self._current_contract = None

        analysis = self.derive_trade_analysis(config, metric, decision, output=output)
        if verdict:
            analysis["trade_analysis"]["verdict"] = verdict.model_dump()
        self.log_experiment_result(config=config, metric=metric, decision=decision, output=output, analysis=analysis)

        # Record baseline checkpoint
        is_baseline_run = next_action.get("source") == "baseline"
        if is_baseline_run:
            runtime_cfg = getattr(self, "_latest_config_contents", {}) or {}
            new_checkpoint = BaselineCheckpoint(
                code_commit=self.current_commit(),
                data_hash=build_data_hash(runtime_cfg),
                config_hash=build_config_hash(runtime_cfg),
                metrics=details,
                timestamp=int(time.time() * 1000),
                round_number=len(self.baseline_tracker.all_checkpoints()),
            )
            drift = self.baseline_tracker.check_drift(new_checkpoint)
            self.baseline_tracker.record(new_checkpoint)
            trace("BASELINE", f"checkpoint recorded commit={self.current_commit()}")
            if drift["drifted"]:
                drift_details = [d for d in drift["details"] if d.get("severity") == "critical"]
                trace("BASELINE", f"DRIFT DETECTED: {drift_details}")
                state = self.read_state()
                state["baseline_drift"] = drift
                self.write_state(state)

            # Clear the baseline_rerun_for_commit flag so plan_next_action
            # doesn't short-circuit on the stale flag and loop forever.
            persisted = self.read_state()
            na = persisted.get("next_action", {})
            if na.get("baseline_rerun_for_commit"):
                na.pop("baseline_rerun_for_commit", None)
                persisted["next_action"] = na
                self.write_state(persisted)

        end_hypothesis(decision=decision, metric=metric)

        # After experiment, reconcile to find next work
        state = self.reconcile_state()
        trace("LOOP", f"ITERATION DONE thesis={config} metric={metric} decision={decision} verdict={verdict.status if verdict else 'none'} next={state.get('next_action', {}).get('type')}")
        best = state.get("current_best", {})
        verdict_str = verdict.status if verdict else "none"
        emoji = "\u2705" if decision == "keep" else "\u274c" if decision == "discard" else "\U0001f504"
        _notify_discord(
            f"{emoji} {self.family.name.upper()} \u2014 {Path(config).stem}",
            f"**PF:** {metric}  |  **Decision:** {decision}  |  **Verdict:** {verdict_str}\n"
            f"**Best so far:** `{Path(best.get('config', '?')).stem}` PF={best.get('metric', '?')}",
            webhook=self.family.discord_webhook, color=0x00CC00 if decision == "keep" else 0xFF4500,
        )
        print(
            f"HEARTBEAT complete thesis={config} result={decision} metric={metric} "
            f"verdict={verdict.status if verdict else 'none'} "
            f"next_action={state.get('next_action', {}).get('type')}"
        )
        return 0

    def execute_once(self) -> int:
        """Run one iteration of the autoresearch loop.

        Flow:
        1. Resolve what to do next (baseline rerun, pending config, or research)
        2. If blocked for research: run conductor, compile thesis, wire config
        3. If running: execute the experiment (backtest + evaluate)
        """
        trace("LOOP", "=== execute_once START ===")

        state = self._resolve_next_action()
        trace("LOOP", f"state={state.get('state')} blockers={[b.get('kind') for b in state.get('blockers', [])]}")

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
    state_path, jsonl_path, current_md_path, ideas_md_path, runs_dir = default_controller_paths(ROOT, family)
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
    state["job_usage"] = None    # reset token usage for new job
    controller.write_state(state)

    from trace_logger import get_log_file, get_session_id, set_family
    set_family(args.family, job=job)
    trace("MAIN", f"Autoresearch loop starting family={args.family} job={job} session={get_session_id()} log={get_log_file()}")
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
