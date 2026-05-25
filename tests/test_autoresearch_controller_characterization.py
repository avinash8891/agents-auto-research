from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch_controller import AutoresearchController
from strategies import STRATEGIES
from strategy_family import load_family


@pytest.fixture
def controller(tmp_path: Path) -> AutoresearchController:
    family = load_family("ema")
    return AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )


def test_controller_uses_job_scoped_round_first_runtime_paths(
    controller: AutoresearchController,
) -> None:
    controller.write_state({"state": "running", "job": 23, "research_round": 0})

    assert controller.job_runtime_root == controller.root / "runtime" / "jobs" / "job-23"
    assert controller.research_dir == controller.job_runtime_root / "research"
    assert controller.builder_requests_dir == controller.job_runtime_root / "builder-requests"
    assert not hasattr(controller, "run_queue_dir")
    assert not hasattr(controller, "experiments_dir")


def test_controller_uses_configured_jobs_root_when_runtime_is_split(tmp_path: Path) -> None:
    family = load_family("ema")
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    code_root.mkdir()
    runtime_root.mkdir()
    jobs_root = runtime_root / "runtime" / "jobs"
    controller = AutoresearchController(
        root=code_root,
        runtime_root=runtime_root,
        family=family,
        state_path=runtime_root / "ema_autoresearch.next.json",
        current_md_path=runtime_root / "ema_autoresearch.current.md",
        jobs_root=jobs_root,
    )

    controller.write_state({"state": "running", "job": 23, "research_round": 0})

    assert controller.job_runtime_root == jobs_root / "job-23"
    assert controller.artifact_dir_for("configs/ema_base.yaml") == (
        runtime_root / "runtime" / "jobs" / "job-23" / "research" / "round-0-baseline" / "backtest"
    )


def test_controller_exported_state_paths_follow_late_runtime_root_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import autoresearch_controller as loop_mod

    runtime_root = tmp_path / "runtime-home"
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))

    assert loop_mod.STATE_PATH == runtime_root / "autoresearch.next.json"
    assert loop_mod.CURRENT_MD_PATH == runtime_root / "autoresearch.current.md"


def test_plan_next_action_runs_baseline_first_for_fresh_job(
    controller: AutoresearchController,
) -> None:
    (controller.root / "configs").mkdir()
    (controller.root / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")

    out = controller.plan_next_action({"state": "idle", "job": 1, "research_round": 0}, [])

    assert out["state"] == "running"
    assert out["research_round"] == 0
    assert out["selected_thesis_id"] == "baseline"
    assert out["next_action"]["source"] == "baseline"


def test_plan_next_action_blocks_for_research_after_results_exist(
    controller: AutoresearchController,
) -> None:
    out = controller.plan_next_action(
        {"state": "idle", "job": 1, "research_round": 0},
        [
            type(
                "Result",
                (),
                {
                    "config": "configs/ema_base.yaml",
                    "metric": 1.0,
                    "status": "keep",
                    "description": "",
                    "timestamp": 1,
                    "asi": {},
                    "job": 1,
                },
            )()
        ],
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "research"


def test_try_resume_halted_thesis_writes_selected_round_artifacts(
    controller: AutoresearchController,
) -> None:
    (controller.root / "configs").mkdir(parents=True, exist_ok=True)
    (controller.root / "configs" / "ema_base.yaml").write_text(
        json.dumps(STRATEGIES["ema"].get_defaults()) + "\n"
    )
    controller.write_state(
        {
            "state": "blocked",
            "job": 7,
            "research_round": 3,
            "halted_thesis_id": "resume-me",
            "halted_reason": "requires_code_change",
            "halted_thesis": {
                "thesis_id": "resume-me",
                "hypothesis": "Tighten EMA length.",
                "mechanism": "Reduce lag.",
                "config_changes": {"ema_length": 7},
            },
        }
    )

    out = controller._try_resume_halted_thesis()

    round_root = controller.root / "runtime" / "jobs" / "job-7" / "research" / "round-3"
    assert out is not None
    assert (
        out["next_action"]["config"] == "runtime/jobs/job-7/research/round-3/selected_config.json"
    )
    assert out["selected_thesis_id"] == "resume-me"
    assert out["backtest_target_path"] == "runtime/jobs/job-7/research/round-3/backtest"
    assert (round_root / "selected_config.json").exists()
    assert (round_root / "selected_thesis.json").exists()
    assert (round_root / "selected_contract.json").exists()


def test_try_resume_halted_thesis_rejects_non_round_scoped_resume(
    controller: AutoresearchController,
) -> None:
    (controller.root / "configs").mkdir(parents=True, exist_ok=True)
    (controller.root / "configs" / "ema_base.yaml").write_text(
        json.dumps(STRATEGIES["ema"].get_defaults()) + "\n"
    )
    controller.write_state(
        {
            "state": "blocked",
            "job": 7,
            "research_round": 0,
            "halted_thesis_id": "resume-me",
            "halted_reason": "requires_code_change",
            "halted_thesis": {
                "thesis_id": "resume-me",
                "hypothesis": "Tighten EMA length.",
                "mechanism": "Reduce lag.",
                "config_changes": {"ema_length": 7},
            },
        }
    )

    with pytest.raises(RuntimeError, match="non-baseline research round"):
        controller._try_resume_halted_thesis()
