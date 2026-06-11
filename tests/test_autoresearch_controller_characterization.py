from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from autoresearch_controller import (
    AutoresearchController,
    _emit_prepare_result,
    _parse_main_args,
    _run_controller_loop,
    _should_hard_exit_prepare_cli,
    _validate_current_executable_state,
    default_controller_paths,
    main,
    max_consecutive_research_required,
    normalize_controller_launch_state,
    validate_controller_state_invariants,
)
from backtest_run_db import BacktestRunRecord
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


def _record(*, job: int, run_id: str, metric: float) -> BacktestRunRecord:
    return BacktestRunRecord(
        run_id=run_id,
        thesis_id=f"thesis-{run_id}",
        config_path=f"configs/variants/{run_id}.yaml",
        runtime_config={"ema_length": 5},
        code_commit="abcdef1",
        data_hash="data",
        train_metrics={"profit_factor": metric},
        validation_metrics={"profit_factor": metric},
        trade_count=4,
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="accepted",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="EMA should improve entries.",
        mechanism="Filter noisy crosses.",
        job=job,
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


def test_controller_requires_explicit_strategy_family(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit strategy family"):
        AutoresearchController(root=tmp_path, runtime_root=tmp_path, family=None)


def test_default_controller_paths_are_family_scoped(tmp_path: Path) -> None:
    family = load_family("ema")

    state_path, current_md_path, jobs_root = default_controller_paths(tmp_path, family)

    assert state_path == tmp_path / "ema_autoresearch.next.json"
    assert current_md_path == tmp_path / "ema_autoresearch.current.md"
    assert jobs_root == tmp_path / "runtime" / "jobs"


def test_write_state_rejects_running_state_with_blockers(
    controller: AutoresearchController,
) -> None:
    with pytest.raises(ValueError, match="running controller state cannot carry blockers"):
        controller.write_state(
            {
                "state": "running",
                "job": 2,
                "blockers": [{"kind": "research_required"}],
            }
        )

    assert controller.read_state() == {"state": "running"}


def test_validate_controller_state_invariants_ignores_terminal_blockers() -> None:
    validate_controller_state_invariants(
        {
            "state": "blocked",
            "job": 2,
            "blockers": [{"kind": "research_required"}],
        }
    )


def test_ensure_job_metadata_backfills_round_and_usage(
    controller: AutoresearchController,
) -> None:
    controller.write_state({"state": "running", "job": "11"})

    controller._ensure_job_metadata()

    assert controller.read_state()["research_round"] == 0
    assert controller.read_state()["job_usage"] is None
    assert controller.job_runtime_root == controller.jobs_root / "job-11"


def test_ensure_job_metadata_rejects_missing_job(controller: AutoresearchController) -> None:
    controller.write_state({"state": "running"})

    with pytest.raises(ValueError, match="job id is required"):
        controller._ensure_job_metadata()


def test_next_fresh_job_id_uses_state_db_and_runtime_tree(
    controller: AutoresearchController,
) -> None:
    controller.backtest_run_db.add(_record(job=4, run_id="db-job-4", metric=1.1))
    (controller.jobs_root / "job-8" / "research").mkdir(parents=True)
    (controller.jobs_root / "job-not-a-number").mkdir()

    assert controller.next_fresh_job_id({"job": 6}) == 9


def test_read_results_scopes_to_current_job(controller: AutoresearchController) -> None:
    controller.backtest_run_db.add(_record(job=3, run_id="job-3-result", metric=1.3))
    controller.backtest_run_db.add(_record(job=4, run_id="job-4-result", metric=1.8))
    controller.write_state({"state": "running", "job": 4, "research_round": 1})

    results = controller.read_results()

    assert [result.config for result in results] == ["configs/variants/job-4-result.yaml"]
    assert results[0].metric == 1.8


def test_read_results_returns_all_when_state_job_is_invalid(
    controller: AutoresearchController,
) -> None:
    controller.backtest_run_db.add(_record(job=3, run_id="job-3-result", metric=1.3))
    controller.backtest_run_db.add(_record(job=4, run_id="job-4-result", metric=1.8))
    controller.write_state({"state": "running", "job": "bad", "research_round": 1})

    results = controller.read_results()

    assert {result.job for result in results} == {3, 4}


def test_read_results_skips_records_with_invalid_result_job(
    controller: AutoresearchController,
) -> None:
    valid = _record(job=4, run_id="job-4-result", metric=1.8)
    invalid = _record(job=4, run_id="job-bad-result", metric=2.1)
    invalid.job = "bad"
    controller.backtest_run_db.add(valid)
    controller.backtest_run_db.add(invalid)
    controller.write_state({"state": "running", "job": 4, "research_round": 1})

    results = controller.read_results()

    assert [result.config for result in results] == ["configs/variants/job-4-result.yaml"]


def test_controller_reads_job_scoped_research_artifacts(
    controller: AutoresearchController,
) -> None:
    controller.write_state({"state": "running", "job": 12, "research_round": 1})
    artifact_dir = controller.research_dir / "round-1"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "round.json"
    artifact_path.write_text(
        json.dumps(
            {
                "job_id": 12,
                "round_number": 1,
                "selected_thesis_id": "ema-artifact",
            }
        )
        + "\n"
    )

    direct = controller.read_json_artifacts(artifact_dir)
    research = controller.read_research_artifacts()

    assert direct[0]["selected_thesis_id"] == "ema-artifact"
    assert research[0]["selected_thesis_id"] == "ema-artifact"
    assert controller.read_thesis_artifacts() == []


def test_clear_transient_context_removes_lineage_and_execution_root(
    controller: AutoresearchController,
    tmp_path: Path,
) -> None:
    controller.ctx.current_contract = {"contract": "value"}
    controller.ctx.parent_backtest_run_id = "parent-run"
    controller.ctx.execution_root = tmp_path / "execution-root"

    controller.clear_transient_context()

    assert controller.ctx.current_contract is None
    assert controller.ctx.parent_backtest_run_id == ""
    assert controller.ctx.execution_root is None


def test_clear_terminal_metadata_removes_only_terminal_fields(
    controller: AutoresearchController,
) -> None:
    state = {
        "state": "running",
        "job": 2,
        "finished_reason": "max_rounds",
        "research_stop_reasoning": "manual halt",
        "next_action": {"type": "research"},
    }

    controller.clear_terminal_metadata(state)

    assert "finished_reason" not in state
    assert "research_stop_reasoning" not in state
    assert state["next_action"] == {"type": "research"}


def test_launch_state_fresh_job_strips_prior_resume_metadata() -> None:
    state, job = normalize_controller_launch_state(
        {
            "state": "blocked",
            "job": 5,
            "research_round": 3,
            "halted_thesis_id": "old-thesis",
            "manual_review_theses": [{"thesis_id": "old-thesis"}],
            "next_action": {"type": "manual_review"},
        },
        resume_current_job=False,
        fresh_job=9,
    )

    assert job == 9
    assert state == {
        "state": "running",
        "job": 9,
        "research_round": 0,
        "job_usage": None,
        "heartbeat": {},
    }


def test_launch_state_resume_manual_review_preserves_blocker_history() -> None:
    state, job = normalize_controller_launch_state(
        {
            "state": "blocked",
            "job": 5,
            "research_round": 3,
            "halted_reason": "requires_code_change",
            "halted_thesis_id": "ema-resume",
            "halted_thesis": {"thesis_id": "ema-resume"},
            "manual_review_theses": [{"thesis_id": "ema-resume"}],
            "next_action": {"type": "manual_review"},
            "heartbeat": {"last_result": "blocked"},
        },
        resume_current_job=True,
    )

    assert job == 5
    assert state["state"] == "running"
    assert state["research_round"] == 3
    assert state["resume_context"]["source"] == "resume_current_job"
    assert state["history"]["last_blocker"]["halted_thesis_id"] == "ema-resume"
    assert state["heartbeat"] == {"last_result": "blocked"}


def test_launch_state_resume_interrupted_research_rewinds_to_failed_round() -> None:
    state, job = normalize_controller_launch_state(
        {
            "state": "interrupted",
            "job": 6,
            "research_round": 4,
            "current_thesis": {"thesis_id": "interrupted"},
            "pending_configs": ["stale"],
            "blockers": [{"kind": "research_failed", "detail": "agent process exited"}],
        },
        resume_current_job=True,
    )

    assert job == 6
    assert state["state"] == "blocked"
    assert state["research_round"] == 3
    assert state["research_round_in_progress"] == 4
    assert state["next_action"]["reason"] == "resume_current_job_retry_interrupted_research"
    assert state["blockers"][0]["kind"] == "research_required"
    assert "current_thesis" not in state
    assert "pending_configs" not in state


def test_launch_state_resume_blocked_experiment_restores_running_with_source_snapshot() -> None:
    state, job = normalize_controller_launch_state(
        {
            "state": "blocked",
            "job": 7,
            "research_round": 2,
            "current_thesis": {"thesis_id": "ema-command"},
            "next_action": {"type": "blocked", "reason": "command_failed"},
            "blockers": [{"kind": "command_failed", "detail": "exit 7"}],
        },
        resume_current_job=True,
    )

    assert job == 7
    assert state["state"] == "running"
    assert state["resume_previous_blocker"]["kind"] == "command_failed"
    assert state["resume_context"]["blocker"]["current_thesis"] == {"thesis_id": "ema-command"}


def test_launch_state_resume_research_required_keeps_blocked_state() -> None:
    prior = {
        "state": "blocked",
        "job": 8,
        "research_round": 1,
        "next_action": {"type": "research", "reason": "needs_next_thesis"},
        "blockers": [{"kind": "research_required"}],
    }

    state, job = normalize_controller_launch_state(prior, resume_current_job=True)

    assert job == 8
    assert state == prior


def test_launch_state_rejects_unrecoverable_resume_state() -> None:
    with pytest.raises(ValueError, match="requires a recoverable"):
        normalize_controller_launch_state(
            {"state": "finished", "job": 3, "research_round": 1},
            resume_current_job=True,
        )


def test_validate_current_executable_state_accepts_running_and_research_blocked() -> None:
    assert _validate_current_executable_state({"state": "running", "job": 3}) == 3
    assert (
        _validate_current_executable_state(
            {
                "state": "blocked",
                "job": 4,
                "next_action": {"type": "research"},
                "blockers": [{"kind": "research_required"}],
            }
        )
        == 4
    )


def test_validate_current_executable_state_rejects_nonexecutable_state() -> None:
    with pytest.raises(ValueError, match="already prepared executable state"):
        _validate_current_executable_state({"state": "blocked", "job": 4})


def test_parse_main_args_rejects_conflicting_launch_modes() -> None:
    with pytest.raises(SystemExit):
        _parse_main_args(["--family", "ema", "--fresh-job", "--resume-current-job"])
    with pytest.raises(SystemExit):
        _parse_main_args(["--family", "ema", "--prepare-launch-state-only", "--run-current-state"])


def test_should_hard_exit_prepare_cli_only_for_valid_prepare_mode() -> None:
    assert _should_hard_exit_prepare_cli(["--family", "ema", "--prepare-launch-state-only"]) is True
    assert _should_hard_exit_prepare_cli(["--family", "ema"]) is False
    assert _should_hard_exit_prepare_cli(["--family", "ema", "--unknown"]) is False


def test_emit_prepare_result_reports_launch_state(capsys: pytest.CaptureFixture[str]) -> None:
    _emit_prepare_result(
        {
            "state": "blocked",
            "research_round": 3,
            "research_round_in_progress": 4,
            "next_action": {"type": "research"},
        },
        17,
    )

    marker, payload = capsys.readouterr().out.strip().split(" ", 1)
    assert marker == "AUTORESEARCH_PREPARE_RESULT"
    parsed = json.loads(payload)
    assert parsed == {
        "job": 17,
        "next_action_type": "research",
        "ok": True,
        "research_round": 3,
        "research_round_in_progress": 4,
        "state": "blocked",
    }


def test_max_consecutive_research_required_reads_env_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "3")
    assert max_consecutive_research_required() == 3

    monkeypatch.setenv("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "0")
    with pytest.raises(ValueError, match="must be >= 1"):
        max_consecutive_research_required()

    monkeypatch.setenv("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "many")
    with pytest.raises(ValueError, match="must be an integer"):
        max_consecutive_research_required()


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


def test_reconcile_state_records_latest_result_and_writes_current_md(
    controller: AutoresearchController,
) -> None:
    controller.backtest_run_db.add(_record(job=15, run_id="job-15-baseline", metric=1.2))
    controller.write_state({"state": "running", "job": 15, "research_round": 0})

    out = controller.reconcile_state()

    assert out["current_best"]["metric"] == 1.2
    assert out["heartbeat"]["last_completed_thesis"] == "configs/variants/job-15-baseline.yaml"
    assert out["heartbeat"]["last_metric"] == 1.2
    assert out["next_action"]["type"] == "research"
    assert controller.current_md_path.exists()


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


def test_execute_once_runs_baseline_experiment_through_real_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "write_result.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                "metrics = {'trade_count': 6, 'profit_factor': 2.1}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "(out / 'trades.csv').write_text('entry_date,pnl_pct\\n2026-01-01,1.0\\n')",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 6}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )

    class Family:
        name = "ema"
        base_config_filename = "ema_base.yaml"
        discord_webhook = ""

        @property
        def baseline_config_path(self) -> str:
            return f"configs/{self.base_config_filename}"

        def benchmark_command(self, config, output_dir=None) -> str:
            return f"{sys.executable} {script} {output_dir}"

    family = Family()
    controller = AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )
    controller.backtest_run_db.init_session(
        name="ema", metric_name="profit_factor", direction="higher"
    )
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    controller.write_state({"state": "running", "job": 13, "research_round": 0})
    calls: list[str] = []
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)

    class Ledger:
        def record_decision(self, **kwargs):
            calls.append(f"decision:{kwargs['decision_type']}")
            return {"decision_id": "decision-1"}

        def record_audit(self, **kwargs):
            calls.append(f"audit:{kwargs['action']}")

    monkeypatch.setattr("autoresearch_controller._AUTONOMY_LEDGER", Ledger())

    assert controller.execute_once() == 0
    assert calls == []
    records = controller.backtest_run_db.all()
    assert len(records) == 1
    assert records[0].thesis_id == "ema_base"
    assert records[0].accepted is True
    assert (
        tmp_path
        / "runtime"
        / "jobs"
        / "job-13"
        / "research"
        / "round-0-baseline"
        / "backtest"
        / "benchmark_output.txt"
    ).exists()


def test_execute_once_stops_without_experiment_for_terminal_blocked_state(
    controller: AutoresearchController,
) -> None:
    controller.write_state({"state": "running", "job": 14, "research_round": 1})
    controller._resolve_next_action = lambda: {  # type: ignore[method-assign]
        "state": "blocked",
        "job": 14,
        "research_round": 1,
        "blockers": [{"kind": "manual_review", "detail": "operator required"}],
    }
    controller._run_round = lambda state: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("terminal blocked state must not run round")
    )

    assert controller.execute_once() == 0


def test_execute_once_dispatches_walkforward_action(
    controller: AutoresearchController,
) -> None:
    controller.write_state({"state": "running", "job": 14, "research_round": 6})
    walkforward_state = {
        "state": "running",
        "job": 14,
        "research_round": 6,
        "next_action": {"type": "walkforward", "reason": "model_plateau"},
        "finished_reason": "model_plateau_pending_walkforward",
    }
    calls: list[dict] = []
    controller._resolve_next_action = lambda: walkforward_state  # type: ignore[method-assign]
    controller._run_walkforward = lambda state: calls.append(dict(state)) or 0  # type: ignore[attr-defined, method-assign]
    controller._run_round = lambda state: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("walkforward action must not run a normal backtest round")
    )

    assert controller.execute_once() == 0
    assert calls == [walkforward_state]


def test_run_controller_loop_exits_on_terminal_state(monkeypatch: pytest.MonkeyPatch) -> None:
    states = [{"state": "running"}, {"state": "finished"}]

    class LoopController:
        def execute_once(self) -> int:
            states.pop(0)
            return 0

        def read_state(self) -> dict:
            return states[0]

    events: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "trace_sdk.set_family", lambda family, job=None: events.append((family, job))
    )

    assert _run_controller_loop(LoopController(), family_name="ema", job=21) == 0
    assert events == [("ema", 21)]


def test_run_controller_loop_returns_nonzero_execute_once_code() -> None:
    class LoopController:
        def execute_once(self) -> int:
            return 7

        def read_state(self) -> dict:
            raise AssertionError("loop must not read state after non-zero iteration")

    assert _run_controller_loop(LoopController(), family_name="ema", job=23) == 7


def test_run_controller_loop_treats_nonresearch_blocked_state_as_terminal() -> None:
    class LoopController:
        def execute_once(self) -> int:
            return 0

        def read_state(self) -> dict:
            return {"state": "blocked", "blockers": [{"kind": "manual_review"}]}

    assert _run_controller_loop(LoopController(), family_name="ema", job=24) == 1


def test_run_controller_loop_stops_after_repeated_research_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LoopController:
        def __init__(self) -> None:
            self.calls = 0

        def execute_once(self) -> int:
            self.calls += 1
            return 0

        def read_state(self) -> dict:
            return {"state": "blocked", "blockers": [{"kind": "research_required"}]}

    controller = LoopController()
    monkeypatch.setenv("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "2")

    assert _run_controller_loop(controller, family_name="ema", job=22) == 1
    assert controller.calls == 3


def test_run_controller_loop_does_not_count_advancing_research_rounds_as_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = [
        {"state": "blocked", "research_round": 1, "blockers": [{"kind": "research_required"}]},
        {"state": "blocked", "research_round": 2, "blockers": [{"kind": "research_required"}]},
        {"state": "finished", "research_round": 2, "blockers": []},
    ]

    class LoopController:
        def __init__(self) -> None:
            self.calls = 0

        def execute_once(self) -> int:
            self.calls += 1
            return 0

        def read_state(self) -> dict:
            return states.pop(0)

    controller = LoopController()
    monkeypatch.setenv("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "2")

    assert _run_controller_loop(controller, family_name="ema", job=22) == 0
    assert controller.calls == 3


def test_run_controller_loop_does_not_count_changed_blocked_state_as_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = [
        {
            "state": "blocked",
            "research_round": 1,
            "activity": {"phase": "conductor_running"},
            "blockers": [{"kind": "research_required"}],
        },
        {
            "state": "blocked",
            "research_round": 1,
            "activity": {"phase": "compiler_running"},
            "blockers": [{"kind": "research_required"}],
        },
        {"state": "finished", "research_round": 1, "blockers": []},
    ]

    class LoopController:
        def __init__(self) -> None:
            self.calls = 0

        def execute_once(self) -> int:
            self.calls += 1
            return 0

        def read_state(self) -> dict:
            return states.pop(0)

    controller = LoopController()
    monkeypatch.setenv("AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED", "2")

    assert _run_controller_loop(controller, family_name="ema", job=22) == 0
    assert controller.calls == 3


def test_main_prepare_launch_state_only_writes_family_scoped_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_root = tmp_path / "runtime-home"
    runtime_root.mkdir()
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(
        "sys.argv",
        [
            "autoresearch_controller.py",
            "--family",
            "ema",
            "--prepare-launch-state-only",
            "--fresh-job",
        ],
    )

    assert main() == 0

    marker, payload = capsys.readouterr().out.strip().split(" ", 1)
    parsed = json.loads(payload)
    state = json.loads((runtime_root / "ema_autoresearch.next.json").read_text())
    assert marker == "AUTORESEARCH_PREPARE_RESULT"
    assert parsed["job"] == 1
    assert parsed["state"] == "running"
    assert state["job"] == 1
    assert state["research_round"] == 0


def test_main_run_current_state_rejects_unprepared_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime-home"
    runtime_root.mkdir()
    (runtime_root / "ema_autoresearch.next.json").write_text(
        json.dumps({"state": "blocked", "job": 3, "blockers": [{"kind": "manual_review"}]}) + "\n"
    )
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(
        "sys.argv",
        ["autoresearch_controller.py", "--family", "ema", "--run-current-state"],
    )

    assert main() == 1
