from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoresearch_orchestration as orch


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
                "required_diagnostics": ["Max_drawdown and pct_profitable_windows vs base"],
                "required_diagnostic_specs": [
                    {
                        "key": "max_drawdown_and_pct_profitable_windows_vs_base",
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
    assert ctrl.ctx.current_contract.required_diagnostic_specs[0].key == (
        "max_drawdown_and_pct_profitable_windows_vs_base"
    )


def test_build_missing_primitives_for_state_uses_round_root(tmp_path: Path, monkeypatch) -> None:
    state = {"state": "blocked", "job": 6, "research_round": 2}
    ctrl, written, _ = _controller(state, tmp_path)
    monkeypatch.setattr(
        "compiler_pipeline.build_missing_primitives",
        lambda root, thesis_id, artifact_root=None: {
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
                "required_diagnostics": ["Max_drawdown and pct_profitable_windows vs base"],
                "required_diagnostic_specs": [
                    {
                        "key": "max_drawdown_and_pct_profitable_windows_vs_base",
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
        lambda root, thesis_id, artifact_root=None: {
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
                "required_diagnostics": ["Max_drawdown and pct_profitable_windows vs base"],
                "required_diagnostic_specs": [
                    {
                        "key": "max_drawdown_and_pct_profitable_windows_vs_base",
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
        lambda root, thesis_id, artifact_root=None: {
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
        lambda root, thesis_id, artifact_root=None: {
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
        lambda root, thesis_id, artifact_root=None: {
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
