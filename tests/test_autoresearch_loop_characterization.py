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

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autoresearch_loop as loop_mod
import autoresearch_research as research_mod
from autoresearch_loop import AutoresearchController
from experiment_db import BaselineTracker, ExperimentDB
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
    jsonl_path = tmp_path / "ema_autoresearch.jsonl"
    current_md_path = tmp_path / "ema_autoresearch.current.md"
    ideas_md_path = tmp_path / "ema_autoresearch.ideas.md"
    runs_dir = tmp_path / family.runs_dirname

    # Write the JSONL config header that autoresearch_helper.py evaluate requires.
    # Without this, `cmd_evaluate` exits with code 1 ("No config found") and
    # evaluate_metric would always return "discard".
    jsonl_path.write_text(
        json.dumps(
            {
                "type": "config",
                "name": "ema",
                "metricName": "median_expectancy",
                "metricUnit": "",
                "bestDirection": "higher",
            }
        )
        + "\n"
    )

    controller = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
        family=family,
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
    for path in source_root.iterdir():
        if path.name in {".git", ".pytest_cache", "__pycache__", "tests"}:
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

    rc = controller.execute_once()

    assert rc == 0
    assert "configs/ema_base.yaml" in captured["command"]
    # The baseline path must have been the one selected.
    entries = controller.read_entries()
    metric_entries = [
        e for e in entries if "metric" in e and e.get("type") not in ("config", "research_round")
    ]
    assert len(metric_entries) == 1
    assert metric_entries[0]["asi"]["config"] == BASELINE_CONFIG


@pytest.mark.integration
def test_execute_once_runs_real_backtest_for_forced_tiny_ema_fixture(tmp_path):
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    _symlink_runtime_repo(REPO_ROOT, runtime_root)
    family = load_family("ema")
    state_path = runtime_root / "ema_autoresearch.next.json"
    jsonl_path = runtime_root / "ema_autoresearch.jsonl"
    current_md_path = runtime_root / "ema_autoresearch.current.md"
    ideas_md_path = runtime_root / "ema_autoresearch.ideas.md"
    runs_dir = runtime_root / family.runs_dirname
    controller = AutoresearchController(
        root=runtime_root,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
        family=family,
    )
    controller.experiment_db = ExperimentDB(runtime_root / "ema_experiments_db.json")
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
    # Seed JSONL with a baseline result so we are past the "no results" branch.
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
    # Evidence the research path was used: a research_round entry in JSONL.
    research_entries = [e for e in controller.read_entries() if e.get("type") == "research_round"]
    assert any(e.get("outcome") == "compiled" for e in research_entries)


# ────────────────────────────────────────────────────────────────────
# 4. Research returns needs_code -> halted
# ────────────────────────────────────────────────────────────────────
def test_execute_once_research_needs_code_halts(controller, monkeypatch):
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

    # execute_research_one calls the LLM research conductor — an external service.
    # Mocking it is allowed under rule G.
    monkeypatch.setattr(AutoresearchController, "execute_research_one", fake_research)

    import research_conductor

    monkeypatch.setattr(research_conductor, "reset_round_usage", lambda: None)
    monkeypatch.setattr(research_conductor, "get_round_usage", lambda: {"total": {}})

    rc = controller.execute_once()

    assert rc == 0
    state = controller.read_state()
    assert state["state"] == "halted"
    assert state["halted_reason"] == "requires_code_change"
    assert state["halted_thesis_id"] == "needs-code-thesis"


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
    blockers = state.get("blockers", [])
    assert any(b.get("kind") == "command_failed" for b in blockers)


# ────────────────────────────────────────────────────────────────────
# 6. Zero exit but no RESULT_JSON -> blocker.kind=metric_parse_failed
# ────────────────────────────────────────────────────────────────────
def test_execute_once_metric_parse_failure_blocks(controller, monkeypatch):
    monkeypatch.setattr(
        AutoresearchController,
        "run_command",
        lambda self, command: (0, "no metrics in this output"),
    )

    rc = controller.execute_once()

    assert rc == 1
    state = controller.read_state()
    assert state["state"] == "blocked"
    blockers = state.get("blockers", [])
    assert any(b.get("kind") == "metric_parse_failed" for b in blockers)


# ────────────────────────────────────────────────────────────────────
# 7. Success -> artifacts written, JSONL entry, ExperimentDB.add called
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

    # JSONL has a metric entry tagged for the baseline config.
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


def test_execute_once_end_to_end_tiny_ema_fixture(controller, monkeypatch, tmp_path):
    _seed_existing_result(controller, BASELINE_CONFIG)
    from experiment_db import BaselineCheckpoint

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
