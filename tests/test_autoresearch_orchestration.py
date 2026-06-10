from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoresearch_orchestration as orch
from autoresearch_controller import AutoresearchController
from strategy_family import load_family


def _controller(state: dict, root: Path, runtime_root: Path | None = None):
    written: list[dict] = []
    current_md: list[dict] = []
    resolved_runtime_root = runtime_root or root

    def _write_state(new_state):
        snapshot = dict(new_state)
        state.clear()
        state.update(snapshot)
        written.append(snapshot)

    ctrl = SimpleNamespace(
        root=root,
        runtime_root=resolved_runtime_root,
        family=SimpleNamespace(
            name="ema",
            base_config_filename="ema_base.yaml",
            benchmark_command=lambda config: f"bench {config}",
        ),
        read_state=lambda: state,
        write_state=_write_state,
        write_current_md=lambda s, r: current_md.append(dict(s)),
        clear_terminal_metadata=lambda s: None,
        read_results=lambda: [],
        ctx=SimpleNamespace(current_contract=None, parent_backtest_run_id=""),
    )
    return ctrl, written, current_md


def test_controller_research_artifact_dir_uses_runtime_root_when_split(tmp_path: Path) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    controller = SimpleNamespace(
        root=code_root,
        runtime_root=runtime_root,
        research_dir=runtime_root / "runtime" / "jobs" / "job-2" / "research",
    )

    assert orch._controller_research_artifact_dir(controller) == "runtime/jobs/job-2/research"


def test_controller_research_artifact_dir_preserves_absolute_path_outside_runtime_root(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime-home"
    outside = tmp_path / "outside" / "research"
    controller = SimpleNamespace(root=tmp_path, runtime_root=runtime_root, research_dir=outside)

    assert orch._controller_research_artifact_dir(controller) == outside.resolve().as_posix()


def test_controller_research_artifact_dir_requires_research_dir(tmp_path: Path) -> None:
    controller = SimpleNamespace(root=tmp_path, runtime_root=tmp_path)

    with pytest.raises(RuntimeError, match="job-scoped research_dir"):
        orch._controller_research_artifact_dir(controller)


def test_try_resume_halted_thesis_writes_round_selected_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text(json.dumps({"ema_length": 5}) + "\n")
    state = {
        "state": "blocked",
        "job": 11,
        "research_round": 4,
        "halted_thesis_id": "resume-me",
        "halted_reason": "requires_code_change",
        "halted_thesis": {
            "thesis_id": "resume-me",
            "hypothesis": "h",
            "mechanism": "m",
            "config_changes": {"ema_length": 7},
        },
    }
    monkeypatch.setitem(
        orch.STRATEGIES,
        "ema",
        SimpleNamespace(validate_runtime_config_scope=lambda runtime: runtime),
    )
    ctrl, written, _ = _controller(state, tmp_path)

    out = orch.try_resume_halted_thesis(ctrl)

    assert out is not None
    assert (
        out["next_action"]["config"] == "runtime/jobs/job-11/research/round-4/selected_config.json"
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-11" / "research" / "round-4"
    assert (round_root / "selected_config.json").exists()
    assert (round_root / "selected_thesis.json").exists()
    assert (round_root / "selected_contract.json").exists()
    assert written[-1]["backtest_target_path"] == "runtime/jobs/job-11/research/round-4/backtest"


def test_try_resume_halted_thesis_uses_runtime_root_when_split(tmp_path: Path, monkeypatch) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    code_root.mkdir()
    runtime_root.mkdir()
    (code_root / "configs").mkdir()
    (code_root / "configs" / "ema_base.yaml").write_text(json.dumps({"ema_length": 5}) + "\n")
    state = {
        "state": "blocked",
        "job": 11,
        "research_round": 4,
        "halted_thesis_id": "resume-me",
        "halted_reason": "requires_code_change",
        "halted_thesis": {
            "thesis_id": "resume-me",
            "hypothesis": "h",
            "mechanism": "m",
            "config_changes": {"ema_length": 7},
        },
    }
    monkeypatch.setitem(
        orch.STRATEGIES,
        "ema",
        SimpleNamespace(validate_runtime_config_scope=lambda runtime: runtime),
    )
    ctrl, written, _ = _controller(state, code_root, runtime_root)

    out = orch.try_resume_halted_thesis(ctrl)

    round_root = runtime_root / "runtime" / "jobs" / "job-11" / "research" / "round-4"
    assert out is not None
    assert (
        out["next_action"]["config"] == "runtime/jobs/job-11/research/round-4/selected_config.json"
    )
    assert (round_root / "selected_config.json").exists()
    assert (round_root / "selected_thesis.json").exists()
    assert (round_root / "selected_contract.json").exists()
    assert (
        written[-1]["selected_config_path"]
        == "runtime/jobs/job-11/research/round-4/selected_config.json"
    )


def test_try_resume_halted_thesis_requires_nonbaseline_round(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text(json.dumps({"ema_length": 5}) + "\n")
    state = {
        "state": "blocked",
        "job": 11,
        "research_round": 0,
        "halted_thesis_id": "resume-me",
        "halted_reason": "requires_code_change",
        "halted_thesis": {"thesis_id": "resume-me", "config_changes": {"ema_length": 7}},
    }
    monkeypatch.setitem(
        orch.STRATEGIES,
        "ema",
        SimpleNamespace(validate_runtime_config_scope=lambda runtime: runtime),
    )
    ctrl, _, _ = _controller(state, tmp_path)

    try:
        orch.try_resume_halted_thesis(ctrl)
    except RuntimeError as exc:
        assert "non-baseline research round" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_activate_builder_config_reads_selected_thesis_sidecar(tmp_path: Path) -> None:
    thesis_id = "builder-round"
    round_root = tmp_path / "runtime" / "jobs" / "job-5" / "research" / "round-3"
    round_root.mkdir(parents=True)
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "h",
                "mechanism": "m",
                "required_diagnostics": ["Max_drawdown vs base"],
                "required_diagnostic_specs": [
                    {
                        "key": "max_drawdown_vs_base",
                        "surface": "experiment_evaluation",
                        "payload_fields": ["candidate_max_drawdown"],
                        "aliases": [],
                        "description": "baseline comparison diagnostic",
                    }
                ],
            }
        )
        + "\n"
    )
    state = {"state": "building", "job": 5, "research_round": 3, "blockers": []}
    ctrl, written, _ = _controller(state, tmp_path)

    out = orch._activate_builder_config(
        ctrl,
        state,
        thesis_id,
        "runtime/jobs/job-5/research/round-3/selected_config.json",
        research_round=3,
    )

    assert out["selected_thesis_id"] == thesis_id
    assert written[-1]["backtest_target_path"] == "runtime/jobs/job-5/research/round-3/backtest"
    assert ctrl.ctx.current_contract.required_diagnostic_specs[0].key == ("max_drawdown_vs_base")


def test_build_missing_primitives_for_state_uses_round_root(tmp_path: Path, monkeypatch) -> None:
    state = {"state": "blocked", "job": 6, "research_round": 2}
    ctrl, written, _ = _controller(state, tmp_path)
    monkeypatch.setattr(
        "compiler_pipeline.build_missing_primitives",
        lambda root, thesis_id, artifact_root=None, research_round_id=None: {
            "status": "completed",
            "generated_config": "runtime/jobs/job-6/research/round-2/selected_config.json",
            "validation_passed": True,
        },
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-6" / "research" / "round-2"
    round_root.mkdir(parents=True, exist_ok=True)
    (round_root / "selected_config.json").write_text("{}\n")
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "builder-success",
                "strategy_family": "ema",
                "hypothesis": "h",
                "mechanism": "m",
                "required_diagnostics": ["Max_drawdown vs base"],
                "required_diagnostic_specs": [
                    {
                        "key": "max_drawdown_vs_base",
                        "surface": "experiment_evaluation",
                        "payload_fields": ["candidate_max_drawdown"],
                        "aliases": [],
                        "description": "baseline comparison diagnostic",
                    }
                ],
            }
        )
        + "\n"
    )

    out = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        "builder-success",
        {"thesis_id": "builder-success"},
        research_round=2,
    )

    assert (
        out["next_action"]["config"] == "runtime/jobs/job-6/research/round-2/selected_config.json"
    )
    assert (
        written[-1]["selected_config_path"]
        == "runtime/jobs/job-6/research/round-2/selected_config.json"
    )


def test_build_missing_primitives_for_state_uses_runtime_root_when_split(
    tmp_path: Path, monkeypatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    code_root.mkdir()
    runtime_root.mkdir()
    state = {"state": "blocked", "job": 6, "research_round": 2}
    ctrl, written, _ = _controller(state, code_root, runtime_root)
    monkeypatch.setattr(
        "compiler_pipeline.build_missing_primitives",
        lambda root, thesis_id, artifact_root=None, research_round_id=None: {
            "status": "completed",
            "generated_config": "runtime/jobs/job-6/research/round-2/selected_config.json",
            "validation_passed": True,
        },
    )
    round_root = runtime_root / "runtime" / "jobs" / "job-6" / "research" / "round-2"
    round_root.mkdir(parents=True, exist_ok=True)
    (round_root / "selected_config.json").write_text("{}\n")
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "builder-success",
                "strategy_family": "ema",
                "hypothesis": "h",
                "mechanism": "m",
                "required_diagnostics": ["Max_drawdown vs base"],
                "required_diagnostic_specs": [
                    {
                        "key": "max_drawdown_vs_base",
                        "surface": "experiment_evaluation",
                        "payload_fields": ["candidate_max_drawdown"],
                        "aliases": [],
                        "description": "baseline comparison diagnostic",
                    }
                ],
            }
        )
        + "\n"
    )

    out = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        "builder-success",
        {"thesis_id": "builder-success"},
        research_round=2,
    )

    assert (
        out["next_action"]["config"] == "runtime/jobs/job-6/research/round-2/selected_config.json"
    )
    assert (
        written[-1]["selected_config_path"]
        == "runtime/jobs/job-6/research/round-2/selected_config.json"
    )


def test_build_missing_primitives_routes_retryable_builder_failure_to_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"state": "blocked", "job": 8, "research_round": 2, "halted_reason": "old"}
    ctrl, written, current_md = _controller(state, tmp_path)
    ctrl.research_dir = tmp_path / "runtime" / "jobs" / "job-8" / "research"
    monkeypatch.setattr(
        "compiler_pipeline.build_missing_primitives",
        lambda root, thesis_id, artifact_root=None, research_round_id=None: {
            "status": "error",
            "error_code": "builder_missing_primitive_contract",
            "reason": "primitive sidecar missing required fields",
            "validation_passed": False,
            "builder_task": "compile-runtime-config",
        },
    )

    out = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        "retry-thesis",
        {"thesis_id": "retry-thesis", "config_changes": {"ema_length": 7}},
        research_round=2,
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "research"
    assert out["next_action"]["reason_code"] == "research_retry_required"
    assert out["blockers"][0]["kind"] == "research_retry_required"
    assert out["heartbeat"]["blocked_builder_status"] == "research_retry_required"
    assert written[-1]["next_action"]["failed_thesis_id"] == "retry-thesis"
    assert current_md[-1]["state"] == "blocked"


def test_build_missing_primitives_records_deterministic_builder_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"state": "blocked", "job": 8, "research_round": 2}
    ctrl, written, current_md = _controller(state, tmp_path)
    monkeypatch.setattr(
        "compiler_pipeline.build_missing_primitives",
        lambda root, thesis_id, artifact_root=None, research_round_id=None: {
            "status": "error",
            "error_code": "builder_timeout",
            "reason": "builder exceeded its runtime budget",
            "validation_passed": False,
            "promoted_files": ["compiler_output.py"],
        },
    )

    out = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        "timeout-thesis",
        {"thesis_id": "timeout-thesis", "config_changes": {"ema_length": 7}},
        research_round=2,
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "builder_failed"
    assert out["blockers"][0]["kind"] == "builder_failed"
    assert out["builder_failed_theses"][0]["thesis_id"] == "timeout-thesis"
    assert out["heartbeat"]["blocked_error_code"] == "builder_timeout"
    assert written[-1]["next_action"]["artifact_dir"] == "ema-builder-failed"
    assert current_md[-1]["builder_failed_theses"][0]["round"] == 2


def test_build_missing_primitives_marks_unknown_builder_error_manual_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"state": "blocked", "job": 8, "research_round": 2}
    ctrl, written, current_md = _controller(state, tmp_path)
    monkeypatch.setattr(
        "compiler_pipeline.build_missing_primitives",
        lambda root, thesis_id, artifact_root=None, research_round_id=None: {
            "status": "error",
            "error_code": "unexpected_builder_shape",
            "reason": "builder returned an unclassified result",
            "validation_passed": False,
            "usage": {"total_tokens": 42},
        },
    )

    out = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        "manual-thesis",
        {"thesis_id": "manual-thesis", "config_changes": {"ema_length": 7}},
        research_round=2,
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "manual_review"
    assert out["blockers"][0]["kind"] == "manual_review"
    assert out["manual_review_theses"][0]["thesis_id"] == "manual-thesis"
    assert out["heartbeat"]["builder_result_context"]["usage"] == {"total_tokens": 42}
    assert written[-1]["next_action"]["artifact_dir"] == "ema-manual-review"
    assert current_md[-1]["manual_review_theses"][0]["round"] == 2


def test_apply_forced_baseline_rerun_archives_existing_blocker(tmp_path: Path) -> None:
    state = {
        "state": "blocked",
        "job": 9,
        "research_round": 4,
        "research_round_in_progress": 4,
        "halted_reason": "requires_code_change",
        "halted_thesis_id": "stalled-thesis",
        "halted_thesis": {"thesis_id": "stalled-thesis", "config_changes": {"ema_length": 7}},
        "manual_review_theses": [{"thesis_id": "manual-one"}],
        "builder_failed_theses": [{"thesis_id": "failed-one"}],
        "current_thesis": {"status": "needs_code"},
        "next_action": {"type": "manual_review", "reason": "operator required"},
        "blockers": [{"kind": "manual_review"}],
    }
    ctrl, written, _ = _controller(state, tmp_path)
    baseline_action = {
        "type": "run_round",
        "config": "configs/ema_base.yaml",
        "source": "baseline",
        "baseline_rerun_for_commit": "abc1234",
    }

    out = orch.apply_forced_baseline_rerun(ctrl, baseline_action)

    assert out["state"] == "running"
    assert out["research_round"] == 0
    assert "research_round_in_progress" not in out
    assert out["next_action"] == baseline_action
    assert out["blockers"] == []
    assert out["history"]["last_blocker"]["halted_thesis_id"] == "stalled-thesis"
    assert out["history"]["last_blocker"]["manual_review_theses"] == [{"thesis_id": "manual-one"}]
    assert out["resume_context"]["source"] == "forced_baseline_rerun"
    assert "halted_thesis_id" not in out
    assert "manual_review_theses" not in out
    assert written[-1]["next_action"]["baseline_rerun_for_commit"] == "abc1234"


def test_resolve_next_action_prioritizes_baseline_rerun_before_state_reconcile(
    tmp_path: Path,
) -> None:
    baseline_action = {
        "type": "run_round",
        "config": "configs/ema_base.yaml",
        "source": "baseline",
    }
    calls: list[str] = []
    controller = SimpleNamespace(
        _check_baseline_rerun=lambda: baseline_action,
        _apply_forced_baseline_rerun=lambda action: calls.append("baseline") or {"applied": action},
        read_state=lambda: (_ for _ in ()).throw(AssertionError("state should not be read")),
        read_results=lambda: (_ for _ in ()).throw(AssertionError("results should not be read")),
        reconcile_state=lambda: (_ for _ in ()).throw(AssertionError("reconcile should not run")),
    )

    assert orch.resolve_next_action(controller) == {"applied": baseline_action}
    assert calls == ["baseline"]


def test_resolve_next_action_resumes_halted_thesis_with_real_controller(
    tmp_path: Path,
) -> None:
    (tmp_path / "configs").mkdir()
    source = Path(__file__).resolve().parents[1] / "configs" / "ema_base.yaml"
    (tmp_path / "configs" / "ema_base.yaml").write_text(source.read_text())
    family = load_family("ema")
    controller = AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )
    controller.backtest_run_db.init_session(
        name="ema",
        metric_name="profit_factor",
        direction="higher",
    )
    controller.write_state(
        {
            "state": "blocked",
            "job": 3,
            "research_round": 2,
            "halted_thesis_id": "needs-code",
            "halted_reason": "requires_code_change",
            "halted_thesis": {
                "thesis_id": "needs-code",
                "hypothesis": "Increasing EMA length should reduce noisy crosses.",
                "mechanism": "Smooth entries with a slower EMA.",
                "config_changes": {"ema_length": 7},
            },
            "blockers": [{"kind": "manual_review", "detail": "builder missing primitive"}],
        }
    )

    out = orch.resolve_next_action(controller)

    assert out["state"] == "running"
    assert out["next_action"]["source"] == "resumed_halted_thesis"
    assert out["next_action"]["selected_thesis_id"] == "needs-code"
    assert out["backtest_target_path"] == "runtime/jobs/job-3/research/round-2/backtest"
    assert (
        tmp_path / "runtime" / "jobs" / "job-3" / "research" / "round-2" / "selected_config.json"
    ).exists()
    assert controller.ctx.current_contract is not None


def test_resolve_next_action_builds_halted_thesis_when_resume_cannot_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "configs").mkdir()
    source = Path(__file__).resolve().parents[1] / "configs" / "ema_base.yaml"
    (tmp_path / "configs" / "ema_base.yaml").write_text(source.read_text())
    family = load_family("ema")
    controller = AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )
    controller.backtest_run_db.init_session(
        name="ema",
        metric_name="profit_factor",
        direction="higher",
    )
    controller.backtest_run_db.add_from_sqlite_fields(
        run_id="baseline-run",
        thesis_id="baseline",
        config_path="configs/ema_base.yaml",
        runtime_config={"ema_length": 5},
        code_commit="abcdef1",
        data_hash="data",
        metrics={"profit_factor": 1.0},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="accepted",
        verdict_summary="accepted",
        family="ema",
        job_id=3,
        primary_metric_name="profit_factor",
        primary_metric_value=1.0,
        research_round_id="job-3-round-2",
        research_round_number=2,
    )
    controller.write_state(
        {
            "state": "blocked",
            "job": 3,
            "research_round": 2,
            "halted_thesis_id": "needs-code",
            "halted_reason": "requires_code_change",
            "halted_thesis": {"thesis_id": "needs-code", "config_changes": {"unknown_gate": True}},
        }
    )

    def _builder(root, thesis_id, artifact_root=None, research_round_id=None):
        assert thesis_id == "needs-code"
        assert artifact_root == (tmp_path / "runtime" / "jobs" / "job-3" / "research" / "round-2")
        return {
            "status": "error",
            "reason": "builder cannot materialize unknown_gate",
            "error_code": "builder_unknown_strategy_family",
            "generated_config": None,
            "validation_passed": False,
        }

    monkeypatch.setattr("compiler_pipeline.build_missing_primitives", _builder)

    out = orch.resolve_next_action(controller)

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "builder_failed"
    assert out["builder_failed_theses"][0]["thesis_id"] == "needs-code"


def test_resolve_next_action_reconciles_fresh_running_job_before_halted_recovery(
    tmp_path: Path,
) -> None:
    state = {
        "state": "running",
        "halted_thesis_id": "preserved-for-later",
        "halted_reason": "requires_code_change",
    }
    controller = SimpleNamespace(
        _check_baseline_rerun=lambda: None,
        read_state=lambda: state,
        read_results=lambda: [],
        reconcile_state=lambda: {"state": "running", "next_action": {"type": "baseline"}},
        _try_resume_halted_thesis=lambda: (_ for _ in ()).throw(
            AssertionError("fresh running job should reconcile baseline first")
        ),
    )

    assert orch.resolve_next_action(controller)["next_action"]["type"] == "baseline"


def test_reflexio_export_refresh_rewrites_package_for_matching_thesis(tmp_path: Path) -> None:
    export_dir = (
        tmp_path / "research" / "round-3" / "trace_exports" / "round-003-20260525" / "reflexio"
    )
    export_dir.mkdir(parents=True)
    current = export_dir / "reflexio-event.json"
    current.write_text(
        json.dumps(
            {
                "episode": {"round": 3, "thesis_id": "refresh-me", "family": "ema"},
                "reflection": {
                    "reasoning": "Builder should compile this thesis.",
                    "validation_failure_reason": "",
                    "quality": {"score": 0.8},
                },
                "resources": {"usage": {"total_tokens": 12}},
            }
        )
        + "\n"
    )
    stale = export_dir.parent.parent / "round-003-stale" / "reflexio"
    stale.mkdir(parents=True)
    (stale / "reflexio-event.json").write_text("{not-json\n")

    found = orch._find_reflexio_export_for_thesis(
        tmp_path,
        3,
        "refresh-me",
        artifact_root=tmp_path,
    )
    assert found == current

    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("{}\n")
    orch._refresh_reflexio_export_after_builder(
        tmp_path,
        research_round=3,
        thesis_id="refresh-me",
        family="ema",
        builder_result={"status": "completed", "validation_passed": True},
        canonical_trace_path=trace_path,
    )

    refreshed = json.loads(current.read_text())
    package = json.loads((export_dir / "package.json").read_text())
    assert refreshed["episode"]["outcome"] == "compiled"
    assert package["files"]["reflexio-event.json"]["episode"]["thesis_id"] == "refresh-me"


def test_reflexio_export_refresh_marks_builder_failures(tmp_path: Path) -> None:
    export_dir = (
        tmp_path / "research" / "round-4" / "trace_exports" / "round-004-20260525" / "reflexio"
    )
    export_dir.mkdir(parents=True)
    current = export_dir / "reflexio-event.json"
    current.write_text(
        json.dumps(
            {
                "episode": {"round": 4, "thesis_id": "failed-builder", "family": "ema"},
                "reflection": {"reasoning": "Builder returned a validation error."},
                "resources": {},
            }
        )
        + "\n"
    )

    orch._refresh_reflexio_export_after_builder(
        tmp_path,
        research_round=4,
        thesis_id="failed-builder",
        family="ema",
        builder_result={"status": "error", "validation_passed": False},
        canonical_trace_path=tmp_path / "trace.jsonl",
    )

    assert json.loads(current.read_text())["episode"]["outcome"] == "builder_failed"
