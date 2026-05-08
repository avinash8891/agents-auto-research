"""Characterization tests for AutoresearchController.execute_once.

These tests pin the current observable behavior of the loop so that the
upcoming refactor (extracting helpers into separate modules) can be
verified to be a pure structural move with no behavior change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autoresearch_controller as loop_mod
import autoresearch_orchestration as orchestration_mod
import autoresearch_research as research_mod
from autoresearch_controller import AutoresearchController
from experiment_db import BaselineCheckpoint, BaselineTracker, ExperimentDB
from strategies import STRATEGIES
from strategy_family import load_family

BASELINE_CONFIG = "configs/ema_base.yaml"


@pytest.fixture
def controller(tmp_path, monkeypatch):
    """Build a controller rooted at tmp_path with an ema_base.yaml present."""
    family = load_family("ema")
    # Replace the discord webhook so notifications are no-ops without
    # patching the family object itself.
    monkeypatch.setattr(loop_mod, "_notify_discord", lambda *a, **k: None)
    monkeypatch.setattr(research_mod, "notify_discord", lambda *a, **k: None)

    # Mirror ema_base.yaml from the repo into the temp root so derive_trade_analysis
    # and _run_experiment can load it.
    src_yaml = REPO_ROOT / BASELINE_CONFIG
    dst_yaml = tmp_path / BASELINE_CONFIG
    dst_yaml.parent.mkdir(parents=True, exist_ok=True)
    dst_yaml.write_text(src_yaml.read_text())

    state_path = tmp_path / "ema_autoresearch.next.json"
    current_md_path = tmp_path / "ema_autoresearch.current.md"
    ideas_md_path = tmp_path / "ema_autoresearch.ideas.md"
    runs_dir = tmp_path / family.runs_dirname

    controller = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
        family=family,
    )
    controller.write_entries(
        [
            {
                "type": "config",
                "name": "ema",
                "metricName": "median_expectancy",
                "metricUnit": "",
                "bestDirection": "higher",
            }
        ]
    )
    # Seed minimal state with a job number so artifact_dir_for works.
    controller.write_state({"state": "running", "job": 1, "research_round": 0})
    return controller


def _seed_existing_result(
    controller: AutoresearchController, config: str = "configs/variants/some_prior.yaml"
) -> None:
    """Append one keep-result so the loop is past the 'no results' branch."""
    entries = [
        {
            "run": 1,
            "job": 1,
            "metric": 1.0,
            "metrics": {},
            "status": "keep",
            "description": f"strict-native loop: {Path(config).stem}",
            "timestamp": 1,
            "asi": {"config": config, "thesis_id": Path(config).stem},
        }
    ]
    controller.write_entries(entries)


def test_controller_anchors_relative_paths_to_root(tmp_path):
    family = load_family("ema")
    controller = AutoresearchController(
        root=tmp_path,
        state_path=Path("ema_autoresearch.next.json"),
        current_md_path=Path("ema_autoresearch.current.md"),
        ideas_md_path=Path("ema_autoresearch.ideas.md"),
        runs_dir=Path(family.runs_dirname),
        family=family,
    )

    assert controller.state_path == tmp_path / "ema_autoresearch.next.json"
    assert controller.current_md_path == tmp_path / "ema_autoresearch.current.md"
    assert controller.ideas_md_path == tmp_path / "ema_autoresearch.ideas.md"
    assert controller.runs_dir == tmp_path / family.runs_dirname
    assert controller.research_dir == tmp_path / family.research_dirname
    assert controller.proposals_dir == tmp_path / family.proposals_dirname
    assert controller.compilations_dir == tmp_path / family.compilations_dirname
    assert controller.contracts_dir == tmp_path / family.contracts_dirname
    assert controller.run_queue_dir == tmp_path / family.run_queue_dirname


def test_running_state_with_blockers_is_invalid() -> None:
    state = {
        "state": "running",
        "job": 1,
        "research_round": 3,
        "blockers": [{"kind": "research_required"}],
    }

    with pytest.raises(ValueError, match="running.*blockers"):
        loop_mod.validate_controller_state_invariants(state)


def test_builder_deterministic_failure_blocks_as_builder_failed_not_manual_review(
    controller,
):
    state = {"state": "building", "job": 1, "research_round": 4}
    thesis = {"thesis_id": "bad-builder-thesis"}
    result = {
        "status": "error",
        "error_code": "builder_implementation_contract_failed",
        "reason": "implementation_contract_failed: config_key_not_consumed_by_runtime:x",
        "implementation_verification_failures": ["config_key_not_consumed_by_runtime:x"],
    }

    updated = orchestration_mod._mark_builder_manual_review(
        controller,
        state,
        "bad-builder-thesis",
        thesis,
        result,
        research_round=4,
    )

    assert updated["state"] == "blocked"
    assert updated["next_action"]["type"] == "builder_failed"
    assert updated["blockers"][0]["kind"] == "builder_failed"
    assert "manual_review_theses" not in updated
    assert updated["builder_failed_theses"][-1]["builder_result"]["error_code"] == (
        "builder_implementation_contract_failed"
    )


def test_builder_config_validation_failure_routes_back_to_research(controller):
    state = {"state": "building", "job": 1, "research_round": 4}
    thesis = {"thesis_id": "research-should-revise", "hypothesis": "bad config shape"}
    result = {
        "status": "error",
        "error_code": "builder_config_validation_failed",
        "reason": "generated config failed validation: unsupported key",
    }

    updated = orchestration_mod._mark_builder_manual_review(
        controller,
        state,
        "research-should-revise",
        thesis,
        result,
        research_round=4,
    )

    assert updated["state"] == "blocked"
    assert updated["next_action"]["type"] == "research"
    assert updated["next_action"]["reason_code"] == "research_retry_required"
    assert updated["blockers"][0]["kind"] == "research_retry_required"
    assert "builder_config_validation_failed" in updated["rejection_feedback"]
    assert "manual_review_theses" not in updated


def test_current_commit_returns_git_sha(controller, monkeypatch):
    monkeypatch.setattr(loop_mod, "_git_sha", lambda: "abc1234")

    assert controller.current_commit() == "abc1234"


def test_execute_once_anchors_absolute_runs_dir_through_resolved_root(tmp_path, monkeypatch):
    family = load_family("ema")
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    src_yaml = REPO_ROOT / BASELINE_CONFIG
    dst_yaml = real_root / BASELINE_CONFIG
    dst_yaml.parent.mkdir(parents=True, exist_ok=True)
    dst_yaml.write_text(src_yaml.read_text())

    controller = AutoresearchController(
        root=symlink_root,
        state_path=Path("ema_autoresearch.next.json"),
        current_md_path=Path("ema_autoresearch.current.md"),
        ideas_md_path=Path("ema_autoresearch.ideas.md"),
        runs_dir=symlink_root / family.runs_dirname,
        family=family,
    )
    controller.write_entries(
        [
            {
                "type": "config",
                "name": "ema",
                "metricName": "median_expectancy",
                "metricUnit": "",
                "bestDirection": "higher",
            }
        ]
    )
    controller.write_state({"state": "running", "job": 1, "research_round": 0})

    captured = _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()

    assert rc == 0
    assert str(real_root) in captured["command"]
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    assert metric_entries[0]["asi"]["artifact_dir"].startswith("ema_autoresearch-runs/job-1/")


def _success_output(result_path: Path, metric: float = 1.5) -> str:
    payload = {
        "metrics": {
            "median_expectancy": metric,
            "trade_count": 42,
            "profit_factor": 1.4,
            "max_drawdown": 0.1,
            "win_rate": 0.55,
        },
        "trades_file": str(result_path.parent / "trades.csv"),
        "git_sha": "abc1234",
    }
    result_path.write_text(json.dumps(payload))
    return f"some preamble\nRESULT_JSON {result_path}\n"


def _patch_run_command_success(controller, monkeypatch, tmp_path) -> dict[str, Any]:
    """Patch run_command for a passing experiment.

    Returns a dict capturing the last command invoked.
    """
    captured: dict[str, Any] = {}
    result_json_path = tmp_path / "result.json"

    # run_command invokes an external subprocess (the backtest binary).
    # Mocking it is allowed under rule G; we return a captured real-fixture output.
    def fake_run_command(self, command: str):
        captured["command"] = command
        return 0, _success_output(result_json_path, metric=1.5)

    monkeypatch.setattr(AutoresearchController, "run_command", fake_run_command)
    return captured


def _symlink_runtime_repo(source_root: Path, runtime_root: Path) -> None:
    runtime_state_names = {
        "autoresearch-runs",
        "ema_autoresearch-runs",
        "ema_autoresearch.current.md",
        "ema_autoresearch.next.json",
        "ema_baseline_checkpoints.json",
        "ema_experiments.db",
        "orb_autoresearch-runs",
        "orb_autoresearch.current.md",
        "orb_autoresearch.next.json",
        "orb_baseline_checkpoints.json",
        "orb_experiments.db",
    }
    for path in source_root.iterdir():
        if path.name in {".git", ".pytest_cache", "__pycache__", "tests"}:
            continue
        if path.name in runtime_state_names:
            continue
        if path.name.startswith(".") and path.name not in {".coveragerc"}:
            continue
        target = runtime_root / path.name
        if target.exists():
            continue
        target.symlink_to(path, target_is_directory=path.is_dir())


# ────────────────────────────────────────────────────────────────────
# 1. No results -> baseline runs
# ────────────────────────────────────────────────────────────────────
def test_execute_once_runs_baseline_when_no_results(controller, monkeypatch, tmp_path):
    captured = _patch_run_command_success(controller, monkeypatch, tmp_path)

    state = controller.read_state()
    state["next_action"] = {
        "type": "run_experiment",
        "config": BASELINE_CONFIG,
        "source": "baseline",
    }
    controller.write_state(state)

    rc = controller.execute_once()

    assert rc == 0
    assert "configs/ema_base.yaml" in captured["command"]
    # The baseline path must have been the one selected.
    assert "baseline_rerun_for_commit" not in controller.read_state()["next_action"]
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    assert metric_entries[0]["asi"]["config"] == BASELINE_CONFIG


def test_execute_once_runs_initial_baseline_without_forced_rerun_metadata(
    controller, monkeypatch, tmp_path
):
    captured = _patch_run_command_success(controller, monkeypatch, tmp_path)

    state = controller.read_state()
    state["next_action"] = {
        "type": "run_experiment",
        "config": BASELINE_CONFIG,
        "source": "baseline",
    }
    controller.write_state(state)

    rc = controller.execute_once()

    assert rc == 0
    assert "configs/ema_base.yaml" in captured["command"]
    assert "baseline_rerun_for_commit" not in controller.read_state()["next_action"]
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    assert "baseline_rerun_for_commit" not in metric_entries[0]["asi"]


@pytest.mark.integration
def test_execute_once_runs_real_backtest_for_forced_tiny_ema_fixture(tmp_path):
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    _symlink_runtime_repo(REPO_ROOT, runtime_root)
    family = load_family("ema")
    state_path = runtime_root / "ema_autoresearch.next.json"
    current_md_path = runtime_root / "ema_autoresearch.current.md"
    ideas_md_path = runtime_root / "ema_autoresearch.ideas.md"
    runs_dir = runtime_root / family.runs_dirname
    controller = AutoresearchController(
        root=runtime_root,
        state_path=state_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
        family=family,
    )
    controller.experiment_db = ExperimentDB(runtime_root / "ema_experiments.db")
    controller.baseline_tracker = BaselineTracker(runtime_root / "ema_baseline_checkpoints.json")
    controller.write_entries(
        [
            {
                "type": "config",
                "name": "ema",
                "metricName": "median_expectancy",
                "metricUnit": "",
                "bestDirection": "higher",
            }
        ]
    )
    config_path = REPO_ROOT / "tests" / "fixtures" / "tiny_ema_runtime.json"
    controller.write_state(
        {
            "state": "running",
            "job": 1,
            "research_round": 0,
            "next_action": {
                "type": "run_experiment",
                "config": str(config_path),
                "requires_trade_analysis": True,
                "source": "integration_fixture",
                "baseline_rerun_for_commit": "fixture-forced-action",
            },
            "blockers": [],
        }
    )

    rc = controller.execute_once()

    assert rc == 0
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    entry = metric_entries[0]
    assert entry["asi"]["config"] == str(config_path)
    assert entry["metrics"]["trade_count"] == 0
    artifact_dir = runtime_root / entry["asi"]["artifact_dir"]
    assert (artifact_dir / "result.json").exists()
    assert (artifact_dir / "benchmark_output.txt").read_text().startswith("RESULT_JSON ")
    assert controller.experiment_db.all()[0].family == "ema"


# ────────────────────────────────────────────────────────────────────
# 2. Pending run-queue artifact runs before research
# ────────────────────────────────────────────────────────────────────
def test_execute_once_runs_pending_queue_before_research(controller, monkeypatch, tmp_path):
    # Seed a baseline result so we are past the "no results" branch.
    _seed_existing_result(controller, BASELINE_CONFIG)

    # Create a runtime config the queue artifact references.
    queued_config = "experiments/queued-thesis-001/runtime_config.json"
    queued_path = tmp_path / queued_config
    queued_path.parent.mkdir(parents=True, exist_ok=True)
    queued_path.write_text(json.dumps({"ema_length": 5, "rr_ratio": 3.0}))

    queue_artifact = {
        "thesis_id": "queued-thesis-001",
        "config": queued_config,
        "status": "pending",
        "source": "multi_variant_probe",
    }
    controller.run_queue_dir.mkdir(parents=True, exist_ok=True)
    (controller.run_queue_dir / "queued-thesis-001.json").write_text(json.dumps(queue_artifact))

    captured = _patch_run_command_success(controller, monkeypatch, tmp_path)

    # Research should NOT be called — fail loudly if it is.
    def _research_should_not_be_called(self):  # pragma: no cover - guard
        raise AssertionError("research conductor invoked when run-queue artifact was pending")

    monkeypatch.setattr(
        AutoresearchController, "execute_research_one", _research_should_not_be_called
    )

    rc = controller.execute_once()

    assert rc == 0
    assert queued_config in captured["command"]


# ────────────────────────────────────────────────────────────────────
# 3. Exhausted candidates -> research conductor produces config
# ────────────────────────────────────────────────────────────────────
def test_execute_once_blocked_research_generates_config(controller, monkeypatch, tmp_path):
    _seed_existing_result(controller, BASELINE_CONFIG)

    # Have research conductor return a freshly generated config.
    generated_config = "experiments/research-thesis-001/runtime_config.json"
    generated_path = tmp_path / generated_config
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_text(json.dumps({"ema_length": 7, "rr_ratio": 2.5}))

    def fake_research(self):
        return {
            "status": "completed",
            "generated_config": generated_config,
            "generated_config_needs_build": False,
            "generated_thesis_id": "research-thesis-001",
            "thesis_id": "research-thesis-001",
            "experiment_id": "research-thesis-001",
            "should_stop": False,
            "reasoning": "fake",
        }

    # execute_research_one calls the LLM research conductor — an external service.
    # Mocking it is allowed under rule G.
    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    # research_conductor.reset_round_usage / get_round_usage are imported lazily;
    # patch them on the imported module to avoid real LLM round bookkeeping.
    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(research_conductor, "get_round_usage", lambda: {"total": {}})

    captured = _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()

    assert rc == 0
    assert generated_config in captured["command"]
    state = controller.read_state()
    assert state["state"] in ("running", "blocked")  # post-reconcile may flip back to blocked
    # Evidence the research path was used: a persisted research_round export entry.
    research_entries = [e for e in controller.read_entries() if e.get("type") == "research_round"]
    assert any(e.get("outcome") == "compiled" for e in research_entries)


# ────────────────────────────────────────────────────────────────────
# 4. Research returns needs_code -> builder handoff
# ────────────────────────────────────────────────────────────────────
def test_execute_once_research_needs_code_invokes_builder(controller, monkeypatch):
    _seed_existing_result(controller, BASELINE_CONFIG)

    def fake_research(self):
        return {
            "status": "completed",
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": "needs-code-thesis",
            "thesis_id": "needs-code-thesis",
            "should_stop": False,
            "reasoning": "missing primitive",
            "thesis": {
                "thesis_id": "needs-code-thesis",
                "hypothesis": "h",
                "mechanism": "m",
                "config_changes": {"new_param": 1},
            },
        }

    def fake_builder(controller_obj, state, thesis_id, thesis, *, research_round=None):
        experiment_dir = controller_obj.root / "experiments" / thesis_id
        assert (experiment_dir / "thesis.json").exists()
        assert (experiment_dir / "contract.json").exists()
        state = dict(state)
        state["state"] = "running"
        state["current_thesis"] = {
            "config": "experiments/needs-code-thesis/runtime_config.json",
            "status": "ready_to_run",
        }
        state["next_action"] = {
            "type": "run_experiment",
            "config": "experiments/needs-code-thesis/runtime_config.json",
            "benchmark_command": controller_obj.family.benchmark_command(
                "experiments/needs-code-thesis/runtime_config.json"
            ),
            "requires_trade_analysis": True,
            "source": "builder",
            "builder_thesis_id": thesis_id,
        }
        state["blockers"] = []
        controller_obj.write_state(state)
        return state

    # execute_research_one calls the LLM research conductor — an external service.
    # Mocking it is allowed under rule G.
    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)
    monkeypatch.setattr(
        "autoresearch_research._orchestration_build_missing_primitives_for_state", fake_builder
    )
    monkeypatch.setattr(AutoresearchController, "_run_experiment", lambda self, state: 0)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(research_conductor, "get_round_usage", lambda: {"total": {}})

    rc = controller.execute_once()

    assert rc == 0
    state = controller.read_state()
    assert state["state"] == "running"
    assert state["next_action"]["source"] == "builder"
    assert state["next_action"]["builder_thesis_id"] == "needs-code-thesis"


def test_execute_once_research_failure_transitions_to_terminal_failure(controller, monkeypatch):
    _seed_existing_result(controller, BASELINE_CONFIG)

    def fake_research(self):
        return {
            "status": "conductor_error",
            "generated_config": None,
            "generated_config_needs_build": False,
            "generated_thesis_id": "bad-thesis",
            "thesis_id": "bad-thesis",
            "should_stop": False,
            "reasoning": "conductor crashed",
            "rejection_reason": "conductor crashed",
        }

    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(research_conductor, "get_round_usage", lambda: {"total": {}})

    rc = controller.execute_once()

    assert rc == 0
    state = controller.read_state()
    assert state["state"] == "interrupted"
    blockers = state.get("blockers", [])
    assert any(b.get("kind") == "research_failed" for b in blockers)


def test_execute_once_research_success_records_quality_refinement_and_bridges(
    controller, monkeypatch
):
    _seed_existing_result(controller, BASELINE_CONFIG)

    generated_config = "experiments/research-thesis-001/runtime_config.json"
    generated_path = controller.root / generated_config
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_text(json.dumps({"ema_length": 7, "rr_ratio": 2.5}))

    def fake_research(self):
        return {
            "status": "completed",
            "generated_config": generated_config,
            "generated_config_needs_build": False,
            "generated_thesis_id": "research-thesis-001",
            "thesis_id": "research-thesis-001",
            "experiment_id": "research-thesis-001",
            "should_stop": False,
            "reasoning": "fake",
            "config_changes": {"ema_length": 7},
            "hypothesis": "improve trend entry",
            "mechanism": "faster trend detection",
            "mechanism_dimension": "entry_timing",
        }

    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(
        research_conductor,
        "get_round_usage",
        lambda: {"total": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}},
    )
    _patch_run_command_success(controller, monkeypatch, controller.root)

    with (
        patch("autoresearch_research._QUALITY_HISTORY.append_run") as append_run,
        patch("autoresearch_research.emit_halo_event") as halo,
        patch("autoresearch_research.emit_recursive_improve_event") as recursive_improve,
        patch("autoresearch_research.emit_reflexio_event") as reflexio,
        patch("autoresearch_research._write_adapter_exports") as write_exports,
    ):
        rc = controller.execute_once()

    assert rc == 0
    append_run.assert_called_once()
    assert append_run.call_args.kwargs["run_label"] == "round-1"
    assert append_run.call_args.kwargs["overall_score"] == 1.0
    assert append_run.call_args.kwargs["dimension_scores"]["compiled"] == 1.0
    halo.assert_called_once()
    recursive_improve.assert_called_once()
    reflexio.assert_called_once()
    reflexio_payload = reflexio.call_args.kwargs["payload"]
    assert reflexio_payload["episode"]["round"] == 1
    assert reflexio_payload["trajectory"]
    write_exports.assert_called_once()


def test_execute_once_writes_adapter_export_packages_to_disk(controller, monkeypatch):
    _seed_existing_result(controller, BASELINE_CONFIG)

    generated_config = "experiments/research-thesis-001/runtime_config.json"
    generated_path = controller.root / generated_config
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_text(json.dumps({"ema_length": 7, "rr_ratio": 2.5}))

    def fake_research(self):
        return {
            "status": "completed",
            "generated_config": generated_config,
            "generated_config_needs_build": False,
            "generated_thesis_id": "research-thesis-001",
            "thesis_id": "research-thesis-001",
            "experiment_id": "research-thesis-001",
            "should_stop": False,
            "reasoning": "fake",
            "config_changes": {"ema_length": 7},
            "hypothesis": "improve trend entry",
            "mechanism": "faster trend detection",
            "mechanism_dimension": "entry_timing",
        }

    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(
        research_conductor,
        "get_round_usage",
        lambda: {"total": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}},
    )
    _patch_run_command_success(controller, monkeypatch, controller.root)

    rc = controller.execute_once()

    assert rc == 0
    export_root = controller.root / "trace_exports" / "round-001-research-thesis-001"
    assert (export_root / "halo" / "halo-event.json").exists()
    assert (export_root / "halo" / "package.json").exists()
    assert (export_root / "recursive_improve" / "recursive-improve-event.json").exists()
    assert (export_root / "reflexio" / "reflexio-event.json").exists()


def test_execute_once_research_validation_rejection_records_rule_proposal(controller, monkeypatch):
    _seed_existing_result(controller, BASELINE_CONFIG)

    def fake_research(self):
        return {
            "status": "thesis_rejected",
            "generated_config": None,
            "generated_config_needs_build": False,
            "generated_thesis_id": "bad-thesis",
            "thesis_id": "bad-thesis",
            "should_stop": False,
            "reasoning": "retry budget exhausted",
            "rejection_reason": "validator rejected thesis",
            "config_changes": {"ema_length": 2},
            "hypothesis": "bad hypothesis",
            "mechanism": "bad mechanism",
            "mechanism_dimension": "entry_timing",
        }

    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(research_conductor, "get_round_usage", lambda: {"total": {}})

    with patch("autoresearch_research._RULE_PROPOSALS.create_proposal") as create_proposal:
        rc = controller.execute_once()

    assert rc == 0
    create_proposal.assert_called_once()
    assert create_proposal.call_args.kwargs["title"] == "Round 1 rejected thesis bad-thesis"


def test_execute_once_records_autonomy_decision_and_audit_for_successful_research(
    controller, monkeypatch
):
    _seed_existing_result(controller, BASELINE_CONFIG)

    generated_config = "experiments/research-thesis-001/runtime_config.json"
    generated_path = controller.root / generated_config
    generated_path.parent.mkdir(parents=True, exist_ok=True)
    generated_path.write_text(json.dumps({"ema_length": 7, "rr_ratio": 2.5}))

    def fake_research(self):
        return {
            "status": "completed",
            "generated_config": generated_config,
            "generated_config_needs_build": False,
            "generated_thesis_id": "research-thesis-001",
            "thesis_id": "research-thesis-001",
            "experiment_id": "research-thesis-001",
            "should_stop": False,
            "reasoning": "fake",
        }

    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(research_conductor, "get_round_usage", lambda: {"total": {}})
    _patch_run_command_success(controller, monkeypatch, controller.root)

    with (
        patch("autoresearch_controller._AUTONOMY_LEDGER.record_decision") as record_decision,
        patch("autoresearch_controller._AUTONOMY_LEDGER.record_audit") as record_audit,
    ):
        record_decision.return_value = {"decision_id": "decision-0001"}
        rc = controller.execute_once()

    assert rc == 0
    record_decision.assert_called_once()
    assert record_decision.call_args.kwargs["decision_type"] == "research_transition"
    assert record_decision.call_args.kwargs["graduation_status"] == "supervised"
    assert record_decision.call_args.kwargs["outcome"] == "approved"
    record_audit.assert_called_once()
    assert record_audit.call_args.kwargs["approval_status"] == "approved"


# ────────────────────────────────────────────────────────────────────
# 5. Backtest exits non-zero -> blocker.kind=command_failed
# ────────────────────────────────────────────────────────────────────
def test_execute_once_backtest_failure_blocks(controller, monkeypatch):
    monkeypatch.setattr(
        AutoresearchController,
        "run_command",
        lambda self, command: (1, "boom"),
    )

    rc = controller.execute_once()

    assert rc == 1
    state = controller.read_state()
    assert state["state"] == "blocked"
    assert state.get("next_action", {}).get("type") == "blocked"
    blockers = state.get("blockers", [])
    assert any(b.get("kind") == "command_failed" for b in blockers)


# ────────────────────────────────────────────────────────────────────
# 6. Zero exit but legacy METRIC stdout -> blocker.kind=metric_parse_failed
# ────────────────────────────────────────────────────────────────────
def test_execute_once_metric_parse_failure_blocks(controller, monkeypatch):
    monkeypatch.setattr(
        AutoresearchController,
        "run_command",
        lambda self, command: (0, "METRIC median_expectancy=1.42\nMETRIC trade_count=12\n"),
    )

    rc = controller.execute_once()

    assert rc == 1
    state = controller.read_state()
    assert state["state"] == "blocked"
    assert state.get("next_action", {}).get("type") == "blocked"
    blockers = state.get("blockers", [])
    assert any(b.get("kind") == "metric_parse_failed" for b in blockers)


# ────────────────────────────────────────────────────────────────────
# 7. Success -> artifacts written, export entry, ExperimentDB.add called
# ────────────────────────────────────────────────────────────────────
def test_execute_once_success_preserves_artifacts_and_db_write(controller, monkeypatch, tmp_path):
    _patch_run_command_success(controller, monkeypatch, tmp_path)

    db_calls: list[Any] = []
    original_add = controller.experiment_db.add

    def spy_add(result):
        db_calls.append(result)
        return original_add(result)

    monkeypatch.setattr(controller.experiment_db, "add", spy_add)

    rc = controller.execute_once()

    assert rc == 0

    # Exported entries include a metric entry tagged for the baseline config.
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    metric_entry = metric_entries[0]
    assert metric_entry["asi"]["config"] == BASELINE_CONFIG
    artifact_rel = metric_entry["asi"]["artifact_dir"]
    assert artifact_rel  # relative-to-root path string

    # Artifacts are present in the run-output dir on disk.
    artifact_dir = controller.root / artifact_rel
    assert (artifact_dir / "benchmark_output.txt").exists()
    assert (artifact_dir / "analysis.json").exists()

    # ExperimentDB.add was called once.
    assert len(db_calls) == 1
    assert db_calls[0].config_path == BASELINE_CONFIG


def test_execute_once_success_persists_verdict_without_tmp_artifacts(
    controller, monkeypatch, tmp_path
):
    _patch_run_command_success(controller, monkeypatch, tmp_path)

    class _Contract:
        thesis_id = "thesis-1"
        strategy_family = "ema"
        hypothesis = "h"
        mechanism = "m"
        expected_effects = [{"metric": "profit_factor", "direction": "increase"}]
        disqualifiers = []
        required_diagnostics = []
        experiment_id = "exp-1"

    exp_dir = controller.root / "experiments" / "exp-1"
    exp_dir.mkdir(parents=True, exist_ok=True)

    def fake_eval(*args, **kwargs):
        class _Verdict:
            status = "accepted"
            passed_effects = []
            failed_effects = []
            triggered_disqualifiers = []
            summary = "ok"

            def model_dump(self):
                return {
                    "status": self.status,
                    "passed_effects": [],
                    "failed_effects": [],
                    "triggered_disqualifiers": [],
                    "summary": self.summary,
                }

            def model_dump_json(self, indent=2):
                return json.dumps(self.model_dump(), indent=indent)

        return _Verdict(), "keep"

    monkeypatch.setattr(
        "autoresearch_experiment._evaluate_against_thesis",
        fake_eval,
    )
    controller.ctx.current_contract = _Contract()

    from autoresearch_experiment import _persist_verdict

    verdict, _ = fake_eval()
    _persist_verdict(controller, _Contract(), verdict)

    assert (exp_dir / "verdict.json").exists()
    assert not list(exp_dir.rglob("*.tmp"))


# ────────────────────────────────────────────────────────────────────
# 8. Halted thesis with no missing config keys -> resumes as running
# ────────────────────────────────────────────────────────────────────
def test_execute_once_resumes_halted_thesis_when_keys_now_exist(controller, monkeypatch, tmp_path):
    """Audit reproduction: corrupting the resume branch passed all 7 prior
    tests, proving this path was untested. This regression test fires on the
    halted-resume branch in _resolve_next_action."""
    halted_thesis_id = "resume-this-thesis"
    # ema_length already exists in the real ema_base.yaml fixture, so the
    # `missing` set is empty and the resume path fires.
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "config_changes": {"ema_length": 7},
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 1,
            "research_round": 0,
        }
    )
    captured = _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()
    assert rc == 0

    expected_config = f"experiments/{halted_thesis_id}/runtime_config.json"
    # The resume branch must have written the runtime config to disk.
    written_runtime = controller.root / expected_config
    assert written_runtime.exists()
    runtime_payload = json.loads(written_runtime.read_text())
    assert runtime_payload.get("ema_length") == 7
    # And invoked the backtest with the resumed config.
    assert expected_config in captured["command"]

    # State should have advanced past `halted` and cleared the halted_* keys.
    state = controller.read_state()
    assert state["state"] in ("running", "blocked")
    assert "halted_thesis_id" not in state
    assert "halted_reason" not in state
    assert "halted_thesis" not in state


def test_try_resume_halted_thesis_handles_empty_baseline_yaml(controller) -> None:
    halted_thesis_id = "resume-empty-baseline"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "config_changes": {"ema_length": 7},
    }
    baseline_path = controller.root / BASELINE_CONFIG
    baseline_path.write_text("")
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 1,
            "research_round": 0,
        }
    )

    assert controller._try_resume_halted_thesis() is None
    assert not (controller.root / f"experiments/{halted_thesis_id}/runtime_config.json").exists()


def test_execute_once_resumes_halted_thesis_preserves_metadata(controller, monkeypatch, tmp_path):
    halted_thesis_id = "resume-this-thesis"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "mechanism": "faster signal response",
        "config_changes": {"ema_length": 7},
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 1,
            "research_round": 0,
        }
    )
    _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()

    assert rc == 0
    entry = next(
        e
        for e in controller.read_entries()
        if e.get("metric") is not None and e.get("type") not in ("config", "research_round")
    )
    assert entry["asi"]["thesis_id"] == halted_thesis_id
    assert entry["asi"]["hypothesis_id"] == halted_thesis_id
    latest = controller.experiment_db.latest(1)[0]
    assert latest.thesis_id == halted_thesis_id
    assert latest.hypothesis == "tighten ema length"
    assert latest.mechanism == "faster signal response"
    assert latest.parent_experiment_id == ""


def test_resolve_next_action_builds_halted_thesis_when_resume_fails(controller, monkeypatch):
    halted_thesis_id = "needs-builder"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "mechanism": "reduce lag",
        "config_changes": {"ema_length": 7},
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 11,
            "research_round": 3,
        }
    )

    def fake_builder(controller_obj, state, thesis_id, thesis, *, research_round=None):
        built_state = dict(state)
        built_state["state"] = "running"
        built_state["current_thesis"] = {
            "config": f"experiments/{thesis_id}/runtime_config.json",
            "status": "ready_to_run",
        }
        built_state["next_action"] = {
            "type": "run_experiment",
            "config": f"experiments/{thesis_id}/runtime_config.json",
            "benchmark_command": controller_obj.family.benchmark_command(
                f"experiments/{thesis_id}/runtime_config.json"
            ),
            "requires_trade_analysis": True,
            "source": "builder",
            "builder_thesis_id": thesis_id,
        }
        built_state["blockers"] = []
        return built_state

    monkeypatch.setattr(controller, "_check_baseline_rerun", lambda: None)
    monkeypatch.setattr(controller, "_try_resume_halted_thesis", lambda: None)
    monkeypatch.setattr(
        "autoresearch_orchestration.build_missing_primitives_for_state", fake_builder
    )

    resolved = controller._resolve_next_action()

    assert resolved["state"] == "running"
    assert resolved["next_action"]["source"] == "builder"
    assert resolved["next_action"]["builder_thesis_id"] == halted_thesis_id


def test_resolve_next_action_marks_builder_failed_when_builder_fails(controller, monkeypatch):
    halted_thesis_id = "builder-fails"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "mechanism": "reduce lag",
        "config_changes": {"ema_length": 7},
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 12,
            "research_round": 4,
        }
    )

    monkeypatch.setattr(controller, "_check_baseline_rerun", lambda: None)
    monkeypatch.setattr(controller, "_try_resume_halted_thesis", lambda: None)

    resolved = controller._resolve_next_action()

    assert resolved["state"] == "blocked"
    assert any(b.get("kind") == "builder_failed" for b in resolved.get("blockers", []))
    assert resolved["next_action"]["type"] == "builder_failed"
    builder_failed = resolved.get("builder_failed_theses", [])
    assert builder_failed
    assert builder_failed[-1]["thesis_id"] == halted_thesis_id
    assert resolved["heartbeat"]["blocked_thesis"] == halted_thesis_id
    assert resolved["heartbeat"]["blocked_builder_status"] == "builder_failed"
    assert resolved["heartbeat"]["blocked_builder_result_status"] == "error"
    assert "Builder failed" in resolved["heartbeat"]["blocked_reason"]


def test_resolve_next_action_marks_manual_review_when_builder_raises(controller, monkeypatch):
    halted_thesis_id = "builder-raises"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "mechanism": "reduce lag",
        "config_changes": {"ema_length": 7},
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 13,
            "research_round": 5,
        }
    )

    def raise_builder(*args, **kwargs):
        raise RuntimeError("builder boom")

    monkeypatch.setattr(controller, "_check_baseline_rerun", lambda: None)
    monkeypatch.setattr(controller, "_try_resume_halted_thesis", lambda: None)
    monkeypatch.setattr("compiler_pipeline.build_missing_primitives", raise_builder)

    resolved = controller._resolve_next_action()

    assert resolved["state"] == "blocked"
    assert any(b.get("kind") == "manual_review" for b in resolved.get("blockers", []))
    assert resolved["next_action"]["type"] == "manual_review"
    assert resolved["manual_review_theses"][-1]["thesis_id"] == halted_thesis_id
    assert resolved["halted_reason"] == "requires_code_change"
    assert resolved["halted_thesis_id"] == halted_thesis_id
    assert resolved["halted_thesis"]["thesis_id"] == halted_thesis_id


def test_execute_once_clears_stale_parent_experiment_id_before_logging(
    controller, monkeypatch, tmp_path
):
    controller.ctx.parent_experiment_id = "stale-parent"
    _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()

    assert rc == 0
    latest = controller.experiment_db.latest(1)[0]
    assert latest.parent_experiment_id == ""


def test_execute_once_clears_stale_last_round_usage_before_logging(
    controller, monkeypatch, tmp_path
):
    controller.write_state(
        {
            "state": "running",
            "job": 1,
            "research_round": 0,
            "_last_round_usage": {
                "total": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18}
            },
        }
    )
    _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()

    assert rc == 0
    latest = controller.experiment_db.latest(1)[0]
    assert latest.usage == {}


def test_execute_once_does_not_resume_halted_thesis_when_runtime_scope_is_invalid(
    controller, monkeypatch, tmp_path
):
    halted_thesis_id = "resume-invalid-thesis"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "mechanism": "faster signal response",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": "Tests whether a missing bounded runtime scope still blocks resume.",
        "config_changes": {"ema_length": 7},
        "expected_effects": [
            {"metric": "profit_factor", "direction": "increase", "rationale": "faster response"}
        ],
        "disqualifiers": [
            {
                "name": "drawdown_expansion",
                "condition": "max_drawdown worsens materially",
                "severity": "hard_fail",
            }
        ],
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 1,
            "research_round": 0,
        }
    )

    def _fail_run_command(*args, **kwargs):
        raise AssertionError("run_command should not be called for invalid resumed config")

    monkeypatch.setattr(AutoresearchController, "run_command", _fail_run_command)
    monkeypatch.setattr(
        STRATEGIES["ema"],
        "validate_runtime_config_scope",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("scope invalid")),
    )
    monkeypatch.setattr(AutoresearchController, "_check_baseline_rerun", lambda self: None)
    monkeypatch.setattr(
        AutoresearchController, "plan_next_action", lambda self, state, results: state
    )

    rc = controller.execute_once()

    assert rc == 0
    assert not (controller.root / f"experiments/{halted_thesis_id}/runtime_config.json").exists()
    state = controller.read_state()
    assert state["state"] == "blocked"
    assert state["next_action"]["type"] == "builder_failed"
    assert any(b.get("kind") == "builder_failed" for b in state.get("blockers", []))
    assert state["builder_failed_theses"][-1]["thesis_id"] == halted_thesis_id


def test_execute_once_resume_halted_thesis_leaves_no_tmp_artifacts(
    controller, monkeypatch, tmp_path
):
    halted_thesis_id = "resume-this-thesis"
    halted_thesis = {
        "thesis_id": halted_thesis_id,
        "hypothesis": "tighten ema length",
        "config_changes": {"ema_length": 7},
    }
    controller.write_state(
        {
            "state": "halted",
            "halted_reason": "requires_code_change",
            "halted_thesis_id": halted_thesis_id,
            "halted_thesis": halted_thesis,
            "job": 1,
            "research_round": 0,
        }
    )
    _patch_run_command_success(controller, monkeypatch, tmp_path)

    rc = controller.execute_once()

    assert rc == 0
    assert not list(controller.root.rglob("*.tmp"))


def test_execute_once_end_to_end_tiny_ema_fixture(controller, monkeypatch, tmp_path):
    _seed_existing_result(controller, BASELINE_CONFIG)
    controller.baseline_tracker.record(
        BaselineCheckpoint(
            code_commit="8dfae61",
            data_hash="fixture-data",
            config_hash="fixture-config",
            metrics={"median_expectancy": 0.0},
            timestamp="2026-04-29T00:00:00Z",
            round_number=1,
        )
    )

    config_rel = "experiments/tiny-ema/runtime_config.json"
    config_path = tmp_path / config_rel
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text((REPO_ROOT / "tests" / "fixtures" / "tiny_ema_runtime.json").read_text())

    controller.run_queue_dir.mkdir(parents=True, exist_ok=True)
    (controller.run_queue_dir / "tiny-ema.json").write_text(
        json.dumps(
            {
                "thesis_id": "tiny-ema",
                "config": config_rel,
                "status": "pending",
                "source": "characterization",
            }
        )
    )

    calls: list[str] = []

    def fake_run_command(self, command: str):
        calls.append(command)
        parts = command.split()
        output_dir = Path(parts[parts.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        config_arg = parts[parts.index("--config") + 1]
        payload = {
            "family": "ema",
            "config": config_arg,
            "config_hash": "tinyfixture12",
            "git_sha": "3154bec",
            "timestamp": "2026-04-30T00:00:00Z",
            "metrics": {
                "median_expectancy": 0.0,
                "trade_count": 0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "pct_profitable_windows": 0.0,
                "avg_sharpe_across_windows": 0.0,
            },
            "diagnostics": {},
            "strategy_diagnostics": {
                "trade_count": 0,
                "event_counts": {},
                "rejection_breakdown": {},
            },
            "trades_file": "",
            "strategy_events_file": str(output_dir / "strategy_events.parquet"),
            "diagnostics_file": str(output_dir / "diagnostics.json"),
        }
        result_path = output_dir / "result.json"
        result_path.write_text(json.dumps(payload) + "\n")
        (output_dir / "analysis.json").write_text(json.dumps({"metric": 0.0}) + "\n")
        (output_dir / "benchmark_output.txt").write_text("benchmark ok\n")
        return 0, f"RESULT_JSON {result_path}\n"

    monkeypatch.setattr(AutoresearchController, "run_command", fake_run_command)

    rc = controller.execute_once()

    assert rc == 0
    assert calls and BASELINE_CONFIG in calls[0]
    state = controller.read_state()
    next_action = state.get("next_action", {})
    assert next_action.get("config") == config_rel
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    assert metric_entries[0]["asi"]["config"] == BASELINE_CONFIG
    artifact_dir = controller.root / metric_entries[0]["asi"]["artifact_dir"]
    assert (artifact_dir / "benchmark_output.txt").exists()
    assert (artifact_dir / "analysis.json").exists()


def test_execute_once_queued_runtime_config_uses_thesis_sidecar_metadata(
    controller, monkeypatch, tmp_path
):
    _seed_existing_result(controller, BASELINE_CONFIG)

    config_rel = "experiments/tiny-ema/runtime_config.json"
    config_path = tmp_path / config_rel
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text((REPO_ROOT / "tests" / "fixtures" / "tiny_ema_runtime.json").read_text())
    (config_path.parent / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "tiny-ema-thesis",
                "hypothesis": "tiny hypothesis",
                "mechanism": "tiny mechanism",
                "config_changes": {"ema_length": 5},
            }
        )
    )

    controller.run_queue_dir.mkdir(parents=True, exist_ok=True)
    (controller.run_queue_dir / "tiny-ema.json").write_text(
        json.dumps(
            {
                "thesis_id": "tiny-ema",
                "config": config_rel,
                "status": "pending",
                "source": "characterization",
            }
        )
    )

    def fake_run_command(self, command: str):
        parts = command.split()
        output_dir = Path(parts[parts.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        config_arg = parts[parts.index("--config") + 1]
        payload = {
            "family": "ema",
            "config": config_arg,
            "config_hash": "tinyfixture12",
            "git_sha": "3154bec",
            "timestamp": "2026-04-30T00:00:00Z",
            "metrics": {
                "median_expectancy": 1.0,
                "trade_count": 1,
                "profit_factor": 1.0,
                "max_drawdown": 0.0,
                "pct_profitable_windows": 1.0,
                "avg_sharpe_across_windows": 1.0,
            },
            "diagnostics": {},
            "strategy_diagnostics": {
                "trade_count": 1,
                "event_counts": {},
                "rejection_breakdown": {},
            },
            "trades_file": str(output_dir / "trades.csv"),
            "strategy_events_file": str(output_dir / "strategy_events.parquet"),
            "diagnostics_file": str(output_dir / "diagnostics.json"),
        }
        result_path = output_dir / "result.json"
        result_path.write_text(json.dumps(payload) + "\n")
        return 0, f"RESULT_JSON {result_path}\n"

    monkeypatch.setattr(AutoresearchController, "run_command", fake_run_command)

    rc = controller.execute_once()

    assert rc == 0
    latest = controller.experiment_db.latest(1)[0]
    assert latest.thesis_id == "tiny-ema-thesis"
    assert latest.hypothesis == "tiny hypothesis"
    assert latest.mechanism == "tiny mechanism"


def test_reconcile_state_clears_stale_current_best_when_no_kept_results_remain(controller) -> None:
    controller.write_state(
        {
            "state": "running",
            "job": 1,
            "current_best": {"config": "configs/variants/stale.yaml", "metric": 9.9},
            "heartbeat": {
                "current_best": {"config": "configs/variants/stale.yaml", "metric": 9.9},
                "last_completed_thesis": "configs/variants/stale.yaml",
                "last_result": "keep",
                "last_metric": 9.9,
            },
        }
    )
    controller.write_entries(
        [
            {
                "type": "config",
                "name": "ema",
                "metricName": "median_expectancy",
                "metricUnit": "",
                "bestDirection": "higher",
            },
        ]
    )

    state = controller.reconcile_state()

    assert state["current_best"] == {}
    assert state["heartbeat"]["current_best"] == {}
    assert "last_completed_thesis" not in state["heartbeat"]
    assert "last_result" not in state["heartbeat"]
    assert "last_metric" not in state["heartbeat"]


def test_reconcile_state_trace_reports_pre_and_post_plan_states(controller, monkeypatch) -> None:
    controller.write_state({"state": "blocked", "job": 1, "blockers": []})
    events: list[tuple[str, str]] = []

    def fake_plan_next_action(self, state, results):
        state["state"] = "running"
        state["next_action"] = {
            "type": "run_experiment",
            "config": BASELINE_CONFIG,
        }
        return state

    monkeypatch.setattr(AutoresearchController, "plan_next_action", fake_plan_next_action)
    monkeypatch.setattr(loop_mod, "trace", lambda event, message: events.append((event, message)))

    controller.reconcile_state()

    reconcile_events = [message for event, message in events if event == "RECONCILE"]
    assert reconcile_events
    assert "previous_state=blocked" in reconcile_events[-1]
    assert "state=running" in reconcile_events[-1]


def test_forced_baseline_rerun_clears_terminal_metadata(controller, monkeypatch) -> None:
    controller.write_state(
        {
            "state": "finished",
            "finished_reason": "research_recommends_stop",
            "research_stop_reasoning": "no more justified theses",
            "job": 1,
            "blockers": [],
        }
    )
    baseline_action = {
        "type": "run_experiment",
        "config": BASELINE_CONFIG,
        "source": "baseline",
        "baseline_rerun_for_commit": "new-commit",
    }

    monkeypatch.setattr(controller, "_try_resume_halted_thesis", lambda: None)
    monkeypatch.setattr(controller, "_check_baseline_rerun", lambda: baseline_action)

    state = controller._resolve_next_action()

    assert state["state"] == "running"
    assert state["next_action"] == baseline_action
    assert "finished_reason" not in state
    assert "research_stop_reasoning" not in state


def test_orchestration_resolve_next_action_prefers_forced_baseline(controller, monkeypatch) -> None:
    baseline_action = {
        "type": "run_experiment",
        "config": BASELINE_CONFIG,
        "source": "baseline",
    }

    calls: list[str] = []

    monkeypatch.setattr(
        controller,
        "_try_resume_halted_thesis",
        lambda: calls.append("resume") or None,
    )
    monkeypatch.setattr(
        controller,
        "_check_baseline_rerun",
        lambda: calls.append("baseline") or baseline_action,
    )
    monkeypatch.setattr(
        controller,
        "_apply_forced_baseline_rerun",
        lambda action: calls.append("apply") or {"state": "running", "next_action": action},
    )
    monkeypatch.setattr(
        controller,
        "reconcile_state",
        lambda: (_ for _ in ()).throw(AssertionError("reconcile_state should not be called")),
    )

    state = orchestration_mod.resolve_next_action(controller)

    assert calls == ["baseline", "apply"]
    assert state["state"] == "running"
    assert state["next_action"] == baseline_action


def test_resolve_next_action_baseline_first_on_fresh_job_with_halted_metadata(
    controller, monkeypatch
):
    calls: list[str] = []

    controller.write_state(
        {
            "state": "running",
            "job": 9,
            "research_round": 0,
            "halted_reason": "requires_code_change",
            "halted_thesis_id": "stale-thesis",
            "halted_thesis": {"thesis_id": "stale-thesis", "config_changes": {"ema_length": 21}},
        }
    )
    monkeypatch.setattr(controller, "_check_baseline_rerun", lambda: None)
    monkeypatch.setattr(controller, "read_results", lambda: [])
    monkeypatch.setattr(
        controller,
        "reconcile_state",
        lambda: calls.append("reconcile")
        or {"state": "running", "next_action": {"type": "run_experiment", "source": "baseline"}},
    )
    monkeypatch.setattr(
        controller,
        "_try_resume_halted_thesis",
        lambda: calls.append("resume") or None,
    )
    monkeypatch.setattr(
        "autoresearch_orchestration.build_missing_primitives_for_state",
        lambda *args, **kwargs: calls.append("build") or {"state": "blocked"},
    )

    state = orchestration_mod.resolve_next_action(controller)

    assert calls == ["reconcile"]
    assert state["next_action"]["source"] == "baseline"


def test_controller_resolve_next_action_does_not_pre_read_state(controller, monkeypatch) -> None:
    calls: list[str] = []

    def _fake_read_state():
        calls.append("read_state")
        return {"state": "running"}

    monkeypatch.setattr(controller, "read_state", _fake_read_state)
    monkeypatch.setattr(
        loop_mod, "_orchestration_resolve_next_action", lambda _controller: {"state": "running"}
    )

    state = controller._resolve_next_action()

    assert state == {"state": "running"}
    assert calls == []


def test_resolve_conductor_inputs_handles_fresh_run_context(controller) -> None:
    from autoresearch_research import _resolve_conductor_inputs

    trades_file, strategy_events_file, diagnostics_file, latest_outcome = _resolve_conductor_inputs(
        controller,
        [],
    )

    assert trades_file == ""
    assert strategy_events_file == ""
    assert diagnostics_file == ""
    assert latest_outcome == {}


def test_resolve_conductor_inputs_uses_real_thesis_id_not_runtime_config_stem(
    controller,
) -> None:
    from autoresearch_research import _resolve_conductor_inputs
    from autoresearch_state import ExperimentRecord

    latest = ExperimentRecord(
        config="experiments/real-thesis-id/runtime_config.json",
        metric=2.34,
        status="discard",
        description="strict-native loop: real-thesis-id",
        timestamp="2026-05-06T00:00:00+00:00",
        asi={
            "thesis_id": "real_thesis_id",
            "trade_analysis": {
                "trade_count": 123,
                "profit_factor": 2.34,
            },
        },
        job=1,
    )

    _, _, _, latest_outcome = _resolve_conductor_inputs(controller, [latest])

    assert latest_outcome["thesis_id"] == "real_thesis_id"
    assert latest_outcome["metric"] == 2.34
    assert latest_outcome["decision"] == "discard"
    assert latest_outcome["trade_count"] == 123
    assert latest_outcome["profit_factor"] == 2.34


def test_resolve_conductor_inputs_passes_invalid_noop_feedback_to_conductor(
    controller,
) -> None:
    from autoresearch_research import _resolve_conductor_inputs
    from autoresearch_state import ExperimentRecord

    latest = ExperimentRecord(
        config="experiments/noop-filter/runtime_config.json",
        metric=1.8813,
        status="discard",
        description="strict-native loop: noop-filter",
        timestamp="2026-05-06T00:00:00+00:00",
        asi={
            "thesis_id": "noop_filter",
            "trade_analysis": {
                "trade_count": 3122,
                "profit_factor": 1.8813,
                "verdict": {
                    "status": "invalid_noop_config",
                    "summary": (
                        "invalid_noop_config: identical trades/diagnostics as previous "
                        "experiment baseline; trade_rejections_due_to_alert_range_filter=0"
                    ),
                },
            },
        },
        job=20,
    )

    _, _, _, latest_outcome = _resolve_conductor_inputs(
        controller,
        [latest],
        current_job=20,
    )

    assert latest_outcome["verdict_status"] == "invalid_noop_config"
    assert "trade_rejections_due_to_alert_range_filter=0" in latest_outcome["verdict_summary"]
    assert latest_outcome["research_feedback"] == (
        "Previous candidate was invalid_noop_config: identical trades/diagnostics as previous "
        "experiment baseline; trade_rejections_due_to_alert_range_filter=0. "
        "If this was a threshold/gating thesis, revise the threshold so it changes behavior "
        "or abandon the mechanism."
    )


def test_resolve_conductor_inputs_stringifies_verdict_feedback_and_avoids_double_period(
    controller,
) -> None:
    from autoresearch_research import _resolve_conductor_inputs
    from autoresearch_state import ExperimentRecord

    latest = ExperimentRecord(
        config="experiments/noop-filter/runtime_config.json",
        metric=1.0,
        status="discard",
        description="strict-native loop: noop-filter",
        timestamp="2026-05-06T00:00:00+00:00",
        asi={
            "thesis_id": "noop_filter",
            "trade_analysis": {
                "verdict": {
                    "status": 404,
                    "summary": "already punctuated.",
                },
            },
        },
        job=20,
    )

    _, _, _, latest_outcome = _resolve_conductor_inputs(controller, [latest], current_job=20)

    assert latest_outcome["verdict_status"] == "404"
    assert latest_outcome["verdict_summary"] == "already punctuated."
    assert latest_outcome["research_feedback"] == "Previous candidate was 404: already punctuated."


def test_resolve_conductor_inputs_filters_latest_and_artifacts_by_current_job(
    controller,
    monkeypatch,
) -> None:
    from autoresearch_research import _resolve_conductor_inputs
    from autoresearch_state import ExperimentRecord

    job20 = ExperimentRecord(
        config="experiments/job20-thesis/runtime_config.json",
        metric=2.0,
        status="keep",
        description="strict-native loop: job20-thesis",
        timestamp="2026-05-06T00:00:00+00:00",
        asi={"thesis_id": "job20_thesis", "trade_analysis": {"trade_count": 20}},
        job=20,
    )
    newer_other_job = ExperimentRecord(
        config="experiments/job21-thesis/runtime_config.json",
        metric=9.9,
        status="discard",
        description="strict-native loop: job21-thesis",
        timestamp="2026-05-06T01:00:00+00:00",
        asi={"thesis_id": "wrong_job21_thesis", "trade_analysis": {"trade_count": 21}},
        job=21,
    )
    seen: dict[str, ExperimentRecord] = {}

    def fake_backfill(controller_arg, latest, trades_file, strategy_events_file, diagnostics_file):
        seen["latest"] = latest
        seen["input_files"] = (trades_file, strategy_events_file, diagnostics_file)
        thesis_id = latest.asi["thesis_id"]
        return (
            f"/tmp/{thesis_id}/trades.csv",
            f"/tmp/{thesis_id}/strategy_events.parquet",
            f"/tmp/{thesis_id}/diagnostics.json",
        )

    monkeypatch.setattr(research_mod, "_backfill_artifact_files_from_latest_dir", fake_backfill)
    controller.ctx.latest_trades_file = "/tmp/wrong-job/trades.csv"
    controller.ctx.latest_strategy_events_file = "/tmp/wrong-job/strategy_events.parquet"
    controller.ctx.latest_diagnostics_file = "/tmp/wrong-job/diagnostics.json"

    trades_file, strategy_events_file, diagnostics_file, latest_outcome = _resolve_conductor_inputs(
        controller,
        [job20, newer_other_job],
        current_job=20,
    )

    assert seen["latest"] is job20
    assert seen["input_files"] == ("", "", "")
    assert trades_file == "/tmp/job20_thesis/trades.csv"
    assert strategy_events_file == "/tmp/job20_thesis/strategy_events.parquet"
    assert diagnostics_file == "/tmp/job20_thesis/diagnostics.json"
    assert latest_outcome["thesis_id"] == "job20_thesis"
    assert latest_outcome["metric"] == 2.0
    assert latest_outcome["decision"] == "keep"
    assert latest_outcome["trade_count"] == 20


def test_resolve_conductor_inputs_uses_persisted_artifact_files_before_backfill(
    controller,
    monkeypatch,
) -> None:
    from autoresearch_research import _resolve_conductor_inputs
    from autoresearch_state import ExperimentRecord

    artifact_dir = controller.root / "runs" / "job-20" / "abc"
    artifact_dir.mkdir(parents=True)
    trades = artifact_dir / "trades.csv"
    events = artifact_dir / "strategy_events.parquet"
    diagnostics = artifact_dir / "diagnostics.json"
    trades.write_text("entry_date,pnl_pct\n")
    events.write_text("")
    diagnostics.write_text("{}")

    latest = ExperimentRecord(
        config="experiments/job20-thesis/runtime_config.json",
        metric=2.0,
        status="discard",
        description="strict-native loop: job20-thesis",
        timestamp="2026-05-06T00:00:00+00:00",
        asi={
            "thesis_id": "job20_thesis",
            "trade_analysis": {"trade_count": 20, "profit_factor": 2.0},
            "trades_file": "runs/job-20/abc/trades.csv",
            "strategy_events_file": "runs/job-20/abc/strategy_events.parquet",
            "diagnostics_file": "runs/job-20/abc/diagnostics.json",
        },
        job=20,
    )

    def fail_backfill(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("persisted artifact paths should avoid artifact_dir scan")

    monkeypatch.setattr(research_mod, "_backfill_artifact_files_from_latest_dir", fail_backfill)

    trades_file, strategy_events_file, diagnostics_file, latest_outcome = _resolve_conductor_inputs(
        controller,
        [latest],
        current_job=20,
    )

    assert trades_file == str(trades.resolve())
    assert strategy_events_file == str(events.resolve())
    assert diagnostics_file == str(diagnostics.resolve())
    assert latest_outcome["thesis_id"] == "job20_thesis"
    assert latest_outcome["trade_count"] == 20
    assert latest_outcome["profit_factor"] == 2.0


def test_call_conductor_traces_input_boundary(monkeypatch) -> None:
    import research_conductor
    from autoresearch_research import _call_conductor

    traces: list[tuple[str, str]] = []
    captured: dict[str, Any] = {}

    def fake_run_research_conductor_sync(**kwargs):
        captured.update(kwargs)
        return {"suggested_theses": [], "should_stop": False}

    monkeypatch.setattr(
        research_mod, "trace", lambda category, message: traces.append((category, message))
    )
    monkeypatch.setattr(
        research_conductor,
        "run_research_conductor_sync",
        fake_run_research_conductor_sync,
    )

    result = _call_conductor(
        7,
        1,
        trades_file="/tmp/trades.csv",
        strategy_events_file="/tmp/events.parquet",
        diagnostics_file="/tmp/diagnostics.json",
        experiment_results="summary",
        latest_outcome={"thesis_id": "latest"},
        family_name="ema",
        rejection_feedback="validator rejected prior thesis",
        current_job=20,
    )

    assert result == {"suggested_theses": [], "should_stop": False}
    assert captured["current_job"] == 20
    assert (
        "CONDUCTOR",
        (
            "INPUT_BOUNDARY job=20 round=7 attempt=2 family=ema "
            "trades=YES events=YES diagnostics=YES rejection_feedback=YES"
        ),
    ) in traces


def test_main_exits_on_persisted_blocked_state(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    class _Controller:
        def __init__(self, **kwargs):
            self.calls = 0
            self.state = {"state": "blocked"}

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)

        def execute_once(self):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("main() should stop after seeing a blocked state")
            self.state = {"state": "blocked"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["autoresearch_controller.py", "--family", "ema"])

    assert loop_mod.main() == 1


def test_main_continues_from_baseline_blocked_research_handoff(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    class _Controller:
        def __init__(self, **kwargs):
            self.calls = 0
            self.state = {
                "state": "running",
                "blockers": [],
                "job": 1,
                "research_round": 0,
                "job_usage": None,
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)

        def execute_once(self):
            self.calls += 1
            if self.calls == 1:
                self.state = {
                    "state": "blocked",
                    "blockers": [{"kind": "research_required"}],
                }
                return 0
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["autoresearch_controller.py", "--family", "ema"])

    assert loop_mod.main() == 0


def test_main_resume_current_job_continues_blocked_research_required_state(monkeypatch, tmp_path):
    family = load_family("ema")
    captured: dict[str, object] = {}

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "blocked",
                "job": 20,
                "research_round": 12,
                "blockers": [
                    {
                        "kind": "research_required",
                        "detail": "Research subagent will generate the next thesis one at a time.",
                    }
                ],
                "next_action": {
                    "type": "research",
                    "requires_subagent": True,
                    "artifact_dir": "ema-research",
                },
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            captured["executed_state"] = dict(self.state)
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 0
    assert captured["written_state"] == captured["executed_state"]
    assert captured["executed_state"]["state"] == "blocked"
    assert captured["executed_state"]["job"] == 20
    assert captured["executed_state"]["research_round"] == 12
    assert captured["executed_state"]["next_action"]["type"] == "research"


def test_main_handles_legacy_string_job_state(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "running",
                "job": "2026-05-02",
                "research_round": 0,
                "job_usage": None,
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["autoresearch_controller.py", "--family", "ema"])

    assert loop_mod.main() == 0
    assert captured["written_state"]["job"] == 1


def test_main_starts_new_job_when_prior_state_is_halted_without_resume_flag(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.calls = 0
            self.state = {
                "state": "halted",
                "job": 7,
                "halted_reason": "requires_code_change",
                "halted_thesis_id": "thesis-123",
                "halted_thesis": {"thesis_id": "thesis-123", "config_changes": {"ema_length": 21}},
                "next_action": {"type": "stale"},
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            self.calls += 1
            self.state = {"state": "blocked"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["autoresearch_controller.py", "--family", "ema"])

    assert loop_mod.main() == 1
    written = captured["written_state"]
    assert written == {
        "state": "running",
        "job": 8,
        "research_round": 0,
        "job_usage": None,
        "heartbeat": {},
    }
    assert "next_action" not in written


def test_main_resume_current_job_accepts_halted_thesis_before_execute_once(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "halted",
                "job": 20,
                "research_round": 21,
                "halted_reason": "requires_code_change",
                "halted_thesis_id": "needs-builder",
                "halted_thesis": {
                    "thesis_id": "needs-builder",
                    "config_changes": {"ema_length": 21},
                },
                "next_action": {"type": "terminated"},
                "blockers": [{"kind": "requires_code_change"}],
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            captured["executed_state"] = dict(self.state)
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 0
    assert captured["written_state"] == captured["executed_state"]
    assert captured["executed_state"]["state"] == "running"
    assert captured["executed_state"]["job"] == 20
    assert captured["executed_state"]["research_round"] == 21
    assert captured["executed_state"]["halted_reason"] == "requires_code_change"
    assert captured["executed_state"]["halted_thesis_id"] == "needs-builder"
    assert "next_action" not in captured["executed_state"]
    assert "blockers" not in captured["executed_state"]


def test_main_resume_current_job_normalizes_command_failed_block_without_new_job(
    monkeypatch, tmp_path
):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "blocked",
                "job": 20,
                "research_round": 21,
                "current_thesis": {
                    "config": "experiments/invalid/runtime_config.json",
                    "status": "ready_to_run",
                },
                "next_action": {
                    "type": "blocked",
                    "reason": "command_failed",
                    "command": ".venv/bin/python -m backtest.runner --strategy ema",
                    "exit_code": 1,
                },
                "blockers": [{"kind": "command_failed", "exit_code": 1}],
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            captured["executed_state"] = dict(self.state)
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 0
    assert captured["written_state"] == captured["executed_state"]
    assert captured["executed_state"]["state"] == "running"
    assert captured["executed_state"]["job"] == 20
    assert captured["executed_state"]["research_round"] == 21
    assert captured["executed_state"]["resume_previous_blocker"]["kind"] == "command_failed"
    assert "current_thesis" not in captured["executed_state"]
    assert "next_action" not in captured["executed_state"]
    assert "blockers" not in captured["executed_state"]


def test_main_resume_current_job_tolerates_malformed_heartbeat(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "halted",
                "job": 20,
                "research_round": 21,
                "heartbeat": "corrupt",
                "halted_reason": "requires_code_change",
                "halted_thesis_id": "needs-builder",
                "halted_thesis": {"thesis_id": "needs-builder"},
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            captured["executed_state"] = dict(self.state)
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 0
    assert captured["executed_state"]["heartbeat"] == {}


def test_main_starts_new_job_when_prior_state_is_manual_review_without_resume_flag(
    monkeypatch, tmp_path
):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.calls = 0
            self.state = {
                "state": "blocked",
                "job": 9,
                "research_round": 2,
                "job_usage": {"input_tokens": 123, "output_tokens": 456, "total_tokens": 579},
                "heartbeat": {"last_completed_thesis": "stale"},
                "halted_reason": "requires_code_change",
                "halted_thesis_id": "thesis-456",
                "halted_thesis": {"thesis_id": "thesis-456", "config_changes": {"ema_length": 13}},
                "manual_review_theses": [
                    {
                        "thesis_id": "thesis-456",
                        "round": 2,
                        "builder_result": {"status": "error", "reason": "timeout"},
                    }
                ],
                "next_action": {"type": "manual_review"},
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            self.calls += 1
            self.state = {"state": "blocked"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["autoresearch_controller.py", "--family", "ema"])

    assert loop_mod.main() == 1
    written = captured["written_state"]
    assert written == {
        "state": "running",
        "job": 10,
        "research_round": 0,
        "job_usage": None,
        "heartbeat": {},
    }
    assert "next_action" not in written


@pytest.mark.parametrize(
    "prior_state",
    [
        {
            "state": "halted",
            "job": 9,
            "research_round": 5,
            "job_usage": {"total_tokens": 123},
            "heartbeat": {"builder_status": "stale"},
            "halted_reason": "requires_code_change",
            "halted_thesis_id": "stale-thesis",
            "halted_thesis": {"thesis_id": "stale-thesis"},
            "next_action": {"type": "terminated"},
        },
        {
            "state": "blocked",
            "job": 9,
            "research_round": 5,
            "job_usage": {"total_tokens": 123},
            "heartbeat": {"blocked_thesis": "stale-thesis"},
            "halted_reason": "requires_code_change",
            "halted_thesis_id": "stale-thesis",
            "halted_thesis": {"thesis_id": "stale-thesis"},
            "manual_review_theses": [{"thesis_id": "stale-thesis"}],
            "next_action": {"type": "manual_review"},
        },
        {
            "state": "building",
            "job": 9,
            "research_round": 5,
            "job_usage": {"total_tokens": 123},
            "heartbeat": {"builder_status": "running"},
            "halted_reason": "requires_code_change",
            "halted_thesis_id": "stale-thesis",
            "halted_thesis": {"thesis_id": "stale-thesis"},
            "next_action": {"type": "builder_running"},
        },
        {
            "state": "interrupted",
            "job": 9,
            "research_round": 5,
            "job_usage": {"total_tokens": 123},
            "heartbeat": {"last_error": "research_failed"},
            "blockers": [{"kind": "research_failed", "detail": "stale failure"}],
            "current_thesis": {"config": "experiments/stale/runtime_config.json"},
            "pending_configs": ["experiments/stale/runtime_config.json"],
            "next_action": {"type": "research"},
        },
        {
            "state": "finished",
            "job": 9,
            "research_round": 5,
            "job_usage": {"total_tokens": 123},
            "heartbeat": {"finished": True},
            "finished_reason": "max_rounds",
            "current_best": {"config": "experiments/stale/runtime_config.json"},
            "baseline_drift": {"status": "stale"},
        },
    ],
)
def test_fresh_launch_state_has_exact_clean_key_set_for_prior_terminal_states(
    prior_state,
):
    state, job = loop_mod.normalize_controller_launch_state(
        prior_state,
        resume_current_job=False,
    )

    assert job == 10
    assert state == {
        "state": "running",
        "job": 10,
        "research_round": 0,
        "job_usage": None,
        "heartbeat": {},
    }


def test_main_resume_current_job_preserves_manual_review_history(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.calls = 0
            self.state = {
                "state": "blocked",
                "job": 9,
                "research_round": 2,
                "job_usage": {"input_tokens": 123, "output_tokens": 456, "total_tokens": 579},
                "heartbeat": {"last_completed_thesis": "stale"},
                "halted_reason": "requires_code_change",
                "halted_thesis_id": "thesis-456",
                "halted_thesis": {"thesis_id": "thesis-456", "config_changes": {"ema_length": 13}},
                "manual_review_theses": [
                    {
                        "thesis_id": "thesis-456",
                        "round": 2,
                        "builder_result": {"status": "error", "reason": "timeout"},
                    }
                ],
                "next_action": {"type": "manual_review"},
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            self.calls += 1
            self.state = {"state": "blocked"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys, "argv", ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"]
    )

    assert loop_mod.main() == 1
    written = captured["written_state"]
    assert written["job"] == 9
    assert written["research_round"] == 2
    assert written["job_usage"] == {"input_tokens": 123, "output_tokens": 456, "total_tokens": 579}
    assert written["halted_reason"] == "requires_code_change"
    assert written["halted_thesis_id"] == "thesis-456"
    assert written["manual_review_theses"][-1]["thesis_id"] == "thesis-456"
    assert "next_action" not in written


def test_resume_current_job_recovers_interrupted_builder_running_state() -> None:
    prior_state = {
        "state": "building",
        "job": 20,
        "research_round": 36,
        "job_usage": {"total_tokens": 123},
        "heartbeat": {"builder_status": "running", "builder_thesis": "thesis-456"},
        "halted_reason": "requires_code_change",
        "halted_thesis_id": "thesis-456",
        "halted_thesis": {"thesis_id": "thesis-456", "config_changes": {"new_key": True}},
        "next_action": {
            "type": "builder_running",
            "builder_thesis_id": "thesis-456",
        },
    }

    state, job = loop_mod.normalize_controller_launch_state(
        prior_state,
        resume_current_job=True,
    )

    assert job == 20
    assert state["state"] == "running"
    assert state["job"] == 20
    assert state["research_round"] == 36
    assert state["halted_reason"] == "requires_code_change"
    assert state["halted_thesis_id"] == "thesis-456"
    assert state["halted_thesis"]["thesis_id"] == "thesis-456"
    assert state["heartbeat"]["builder_status"] == "running"


def test_main_resume_current_job_retries_interrupted_research_failure_without_incrementing_job(
    monkeypatch, tmp_path
):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    captured: dict[str, dict] = {}

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "interrupted",
                "job": 20,
                "research_round": 9,
                "job_usage": {"rounds": 8, "total_tokens": 1234},
                "heartbeat": {
                    "last_completed_thesis": "experiments/prev/runtime_config.json",
                    "last_result": "discard",
                },
                "current_best": {
                    "config": "experiments/best/runtime_config.json",
                    "metric": 4.9409,
                },
                "current_thesis": {
                    "config": "experiments/stale/runtime_config.json",
                    "status": "ready_to_run",
                },
                "next_action": {
                    "type": "terminated",
                    "reason": "round 9 failed: research conductor failed: exception",
                    "artifact_dir": "ema-research",
                },
                "blockers": [
                    {
                        "kind": "research_failed",
                        "detail": "round 9 failed: research conductor failed: exception",
                    }
                ],
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            self.state = dict(state)
            captured["written_state"] = dict(state)

        def execute_once(self):
            self.state = {"state": "finished", "blockers": [], "finished_reason": "done"}
            return 0

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr("trace_sdk.get_log_file", lambda: "test.log")
    monkeypatch.setattr("trace_sdk.get_session_id", lambda: "session-1")
    monkeypatch.setattr("trace_sdk.set_family", lambda *args, **kwargs: None)
    monkeypatch.setattr(loop_mod, "trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 0
    written = captured["written_state"]
    assert written["state"] == "blocked"
    assert written["job"] == 20
    assert written["research_round"] == 8
    assert written["job_usage"] == {"rounds": 8, "total_tokens": 1234}
    assert written["heartbeat"]["last_completed_thesis"] == "experiments/prev/runtime_config.json"
    assert written["current_best"]["metric"] == 4.9409
    assert written["blockers"] == [
        {
            "kind": "research_required",
            "detail": (
                "Retrying interrupted research failure: "
                "round 9 failed: research conductor failed: exception"
            ),
        }
    ]
    assert written["next_action"]["type"] == "research"
    assert written["next_action"]["reason"] == "resume_current_job_retry_interrupted_research"
    assert "current_thesis" not in written


def test_main_resume_current_job_rejects_non_recoverable_state(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {"state": "finished", "job": 20, "research_round": 9}

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            raise AssertionError(f"should not rewrite unrecoverable state: {state}")

        def execute_once(self):
            raise AssertionError("should not execute unrecoverable resume")

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 1


def test_main_resume_current_job_rejects_inconsistent_blocked_research_state(monkeypatch, tmp_path):
    family = load_family("ema")

    monkeypatch.setattr(loop_mod, "load_family", lambda _name: family)
    monkeypatch.setattr(
        loop_mod,
        "default_controller_paths",
        lambda _root, _family: (
            tmp_path / "ema_autoresearch.next.json",
            tmp_path / "ema_autoresearch.current.md",
            tmp_path / "ema_autoresearch.ideas.md",
            tmp_path / family.runs_dirname,
        ),
    )

    class _Controller:
        def __init__(self, **kwargs):
            self.state = {
                "state": "blocked",
                "job": 20,
                "research_round": 12,
                "blockers": [{"kind": "research_required"}],
                "next_action": {"type": "manual_review"},
            }

        def read_state(self):
            return dict(self.state)

        def write_state(self, state):
            raise AssertionError(f"should not rewrite inconsistent blocked state: {state}")

        def execute_once(self):
            raise AssertionError("should not execute inconsistent blocked resume")

    monkeypatch.setattr(loop_mod, "AutoresearchController", _Controller)
    monkeypatch.setattr(
        sys,
        "argv",
        ["autoresearch_controller.py", "--family", "ema", "--resume-current-job"],
    )

    assert loop_mod.main() == 1


def test_resume_interrupted_research_state_tolerates_malformed_blockers() -> None:
    state = loop_mod._resume_interrupted_research_state(
        {
            "state": "interrupted",
            "job": 20,
            "research_round": 9,
            "blockers": None,
        },
        job=20,
    )

    assert state["state"] == "blocked"
    assert state["job"] == 20
    assert state["research_round"] == 8
    assert state["blockers"] == [
        {
            "kind": "research_required",
            "detail": "Retrying interrupted research failure.",
        }
    ]
