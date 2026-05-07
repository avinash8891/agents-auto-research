from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import autoresearch_orchestration as orch

# ── Controller factory ────────────────────────────────────────────────────────


def _make_controller(
    *,
    state: dict[str, Any],
    root: Path,
    family_name: str = "ema",
    base_config_filename: str = "ema_base.yaml",
    benchmark_command: Any = None,
    written_states: list | None = None,
) -> SimpleNamespace:
    if written_states is None:
        written_states = []

    def _write_state(s: dict) -> None:
        written_states.append(dict(s))

    ctx = SimpleNamespace(current_contract=None, parent_experiment_id="some-id")

    family = SimpleNamespace(
        name=family_name,
        base_config_filename=base_config_filename,
        benchmark_command=benchmark_command or (lambda cfg: f"run {cfg}"),
    )

    return SimpleNamespace(
        read_state=lambda: dict(state),
        write_state=_write_state,
        written_states=written_states,
        clear_terminal_metadata=lambda s: s.pop("terminal_metadata", None),
        read_results=lambda: [],
        write_current_md=lambda state, results: None,
        root=root,
        family=family,
        ctx=ctx,
    )


# ── try_resume_halted_thesis ──────────────────────────────────────────────────


def test_try_resume_returns_none_when_no_halted_id(tmp_path):
    ctrl = _make_controller(state={"state": "halted"}, root=tmp_path)
    assert orch.try_resume_halted_thesis(ctrl) is None


def test_try_resume_returns_none_when_halted_reason_not_requires_code_change(tmp_path):
    state = {
        "halted_thesis_id": "entry_window_test",
        "halted_reason": "analyst_failed",
        "halted_thesis": {"config_changes": {"entry_cutoff_time": "09:35"}},
    }
    ctrl = _make_controller(state=state, root=tmp_path)
    assert orch.try_resume_halted_thesis(ctrl) is None


def test_try_resume_returns_none_when_config_changes_empty(tmp_path):
    state = {
        "halted_thesis_id": "entry_window_test",
        "halted_reason": "requires_code_change",
        "halted_thesis": {"config_changes": {}},
    }
    ctrl = _make_controller(state=state, root=tmp_path)
    assert orch.try_resume_halted_thesis(ctrl) is None


def test_try_resume_returns_none_when_config_key_not_in_base(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("entry_time: '09:30'\n")

    state = {
        "halted_thesis_id": "entry_window_test",
        "halted_reason": "requires_code_change",
        "halted_thesis": {"config_changes": {"nonexistent_key_xyz": "value"}},
    }
    ctrl = _make_controller(state=state, root=tmp_path)
    assert orch.try_resume_halted_thesis(ctrl) is None


def test_try_resume_returns_none_when_validate_raises(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("entry_cutoff_time: '09:30'\n")

    state = {
        "halted_thesis_id": "entry_window_test",
        "halted_reason": "requires_code_change",
        "halted_thesis": {"config_changes": {"entry_cutoff_time": "09:35"}},
    }
    monkeypatch.setitem(
        orch.STRATEGIES,
        "ema",
        SimpleNamespace(
            validate_runtime_config_scope=lambda r: (_ for _ in ()).throw(ValueError("bad scope"))
        ),
    )
    ctrl = _make_controller(state=state, root=tmp_path)
    assert orch.try_resume_halted_thesis(ctrl) is None


def test_try_resume_happy_path_writes_config_and_thesis_files(tmp_path, monkeypatch):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("entry_cutoff_time: '09:30'\n")

    halted_thesis = {
        "thesis_id": "entry_window_test",
        "hypothesis": "narrow entry window",
        "mechanism": "filter noise",
        "config_changes": {"entry_cutoff_time": "09:35"},
        "expected_effects": [],
        "disqualifiers": [],
    }
    state = {
        "halted_thesis_id": "entry_window_test",
        "halted_reason": "requires_code_change",
        "halted_thesis": halted_thesis,
        "research_round": 3,
    }

    validated_runtime = {"entry_cutoff_time": "09:35"}
    monkeypatch.setitem(
        orch.STRATEGIES,
        "ema",
        SimpleNamespace(validate_runtime_config_scope=lambda r: validated_runtime),
    )
    monkeypatch.setattr(orch, "trace", lambda *a, **k: None)

    written_states: list = []
    ctrl = _make_controller(state=state, root=tmp_path, written_states=written_states)
    result = orch.try_resume_halted_thesis(ctrl)

    assert result is not None
    assert result["state"] == "running"
    assert result["next_action"]["type"] == "run_experiment"
    assert result["next_action"]["source"] == "resumed_halted_thesis"
    assert result["blockers"] == []
    assert "halted_thesis_id" not in result
    assert "halted_reason" not in result
    assert "halted_thesis" not in result

    # runtime_config.json written
    config_path = tmp_path / "experiments" / "entry_window_test" / "runtime_config.json"
    assert config_path.exists()
    assert json.loads(config_path.read_text()) == validated_runtime

    # thesis.json sidecar written
    thesis_path = tmp_path / "experiments" / "entry_window_test" / "thesis.json"
    assert thesis_path.exists()
    sidecar = json.loads(thesis_path.read_text())
    assert sidecar["thesis_id"] == "entry_window_test"
    assert sidecar["strategy_family"] == "ema"

    # controller.write_state called
    assert len(written_states) == 1


# ── apply_forced_baseline_rerun ───────────────────────────────────────────────


def test_apply_forced_baseline_rerun_updates_state_and_writes(tmp_path):
    baseline_action = {
        "type": "run_experiment",
        "config": "configs/ema_base.yaml",
        "benchmark_command": "python run_ema.py",
        "requires_trade_analysis": False,
        "source": "forced_baseline",
    }
    state = {"state": "halted", "blockers": [{"kind": "baseline_drift"}]}
    written_states: list = []
    ctrl = _make_controller(state=state, root=tmp_path, written_states=written_states)

    result = orch.apply_forced_baseline_rerun(ctrl, baseline_action)

    assert result["state"] == "running"
    assert result["next_action"] == baseline_action
    assert result["blockers"] == []
    assert len(written_states) == 1
    assert written_states[0]["next_action"] == baseline_action


# ── build_missing_primitives_for_state ────────────────────────────────────────


def test_build_missing_primitives_persists_builder_running_state_before_cli(tmp_path, monkeypatch):
    thesis_id = "needs_builder"
    (tmp_path / "experiments" / thesis_id).mkdir(parents=True)
    (tmp_path / "experiments" / thesis_id / "runtime_config.json").write_text("{}\n")
    state = {
        "state": "blocked",
        "terminal_metadata": {"finished_reason": "old_done"},
        "halted_reason": "requires_code_change",
        "halted_thesis_id": thesis_id,
        "halted_thesis": {"thesis_id": thesis_id},
        "next_action": {"type": "research"},
        "heartbeat": {
            "last_metric": 1.8813,
            "blocked_builder_status": "error",
            "blocked_reason": "old builder failure",
            "blocked_thesis": thesis_id,
            "blocked_builder_result_status": "error",
        },
    }
    written_states: list = []
    ctrl = _make_controller(state=state, root=tmp_path, written_states=written_states)

    def fake_build(root: Path, received_thesis_id: str) -> dict[str, Any]:
        assert received_thesis_id == thesis_id
        assert written_states
        active = written_states[-1]
        assert active["state"] == "building"
        assert active["next_action"]["type"] == "builder_running"
        assert active["next_action"]["builder_thesis_id"] == thesis_id
        assert active["heartbeat"]["builder_status"] == "running"
        assert active["heartbeat"]["builder_thesis"] == thesis_id
        assert active["heartbeat"]["last_metric"] == 1.8813
        assert "blocked_builder_status" not in active["heartbeat"]
        assert "blocked_reason" not in active["heartbeat"]
        assert "blocked_thesis" not in active["heartbeat"]
        assert "blocked_builder_result_status" not in active["heartbeat"]
        assert "terminal_metadata" not in active
        return {
            "status": "completed",
            "generated_config": f"experiments/{thesis_id}/runtime_config.json",
            "validation_passed": True,
        }

    monkeypatch.setattr("compiler_pipeline.build_missing_primitives", fake_build)

    result = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        thesis_id,
        {"thesis_id": thesis_id},
        research_round=36,
    )

    assert result["state"] == "running"
    assert result["next_action"]["type"] == "run_experiment"
    assert result["heartbeat"]["builder_status"] == "completed"
    assert "builder_finished_at" in result["heartbeat"]
    assert written_states[0]["state"] == "building"
    assert written_states[-1]["state"] == "running"


def test_build_missing_primitives_routes_import_failure_to_manual_review(tmp_path, monkeypatch):
    thesis_id = "needs_builder"
    state = {
        "state": "blocked",
        "halted_reason": "requires_code_change",
        "halted_thesis_id": thesis_id,
        "halted_thesis": {"thesis_id": thesis_id},
        "next_action": {"type": "research"},
        "heartbeat": {},
    }
    written_states: list = []
    ctrl = _make_controller(state=state, root=tmp_path, written_states=written_states)
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "compiler_pipeline":
            raise ImportError("compiler import boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        thesis_id,
        {"thesis_id": thesis_id},
        research_round=36,
    )

    assert written_states[0]["state"] == "building"
    assert result["state"] == "blocked"
    assert result["next_action"]["type"] == "manual_review"
    assert result["heartbeat"]["builder_status"] == "manual_review"
    assert "builder_finished_at" in result["heartbeat"]
    assert result["heartbeat"]["blocked_builder_status"] == "manual_review"
    assert result["heartbeat"]["blocked_builder_result_status"] == "error"
    assert (
        "ImportError: compiler import boom"
        in result["manual_review_theses"][-1]["builder_result"]["reason"]
    )


def test_build_missing_primitives_does_not_mark_invalid_completed_builder_as_completed(
    tmp_path, monkeypatch
):
    thesis_id = "needs_builder"
    state = {
        "state": "blocked",
        "halted_reason": "requires_code_change",
        "halted_thesis_id": thesis_id,
        "halted_thesis": {"thesis_id": thesis_id},
        "next_action": {"type": "research"},
        "heartbeat": {},
    }
    written_states: list = []
    ctrl = _make_controller(state=state, root=tmp_path, written_states=written_states)

    def fake_build(root: Path, received_thesis_id: str) -> dict[str, Any]:
        assert received_thesis_id == thesis_id
        return {
            "status": "completed",
            "generated_config": f"experiments/{thesis_id}/missing_config.json",
            "validation_passed": False,
        }

    monkeypatch.setattr("compiler_pipeline.build_missing_primitives", fake_build)

    result = orch.build_missing_primitives_for_state(
        ctrl,
        state,
        thesis_id,
        {"thesis_id": thesis_id},
        research_round=36,
    )

    assert result["state"] == "blocked"
    assert result["next_action"]["type"] == "manual_review"
    assert result["heartbeat"]["builder_status"] == "manual_review"
    assert result["heartbeat"]["blocked_builder_status"] == "manual_review"
    assert result["heartbeat"]["blocked_builder_result_status"] == "completed"


# ── resolve_next_action ───────────────────────────────────────────────────────


def test_resolve_next_action_calls_apply_baseline_when_check_returns_action(tmp_path):
    baseline_action = {"type": "run_experiment", "source": "forced_baseline"}
    applied: list = []

    ctrl = SimpleNamespace(
        _check_baseline_rerun=lambda: baseline_action,
        _apply_forced_baseline_rerun=lambda a: applied.append(a) or {"state": "running"},
        read_state=lambda: {},
        read_results=lambda: [],
        reconcile_state=lambda: {"from": "reconcile"},
        _try_resume_halted_thesis=lambda: None,
        root=tmp_path,
        family=SimpleNamespace(name="ema"),
        ctx=SimpleNamespace(),
    )
    result = orch.resolve_next_action(ctrl)

    assert applied == [baseline_action]
    assert result == {"state": "running"}


def test_resolve_next_action_calls_reconcile_when_running_with_no_results(tmp_path):
    ctrl = SimpleNamespace(
        _check_baseline_rerun=lambda: None,
        read_state=lambda: {"state": "running"},
        read_results=lambda: [],
        reconcile_state=lambda: {"from": "reconcile"},
        _try_resume_halted_thesis=lambda: None,
        root=tmp_path,
        family=SimpleNamespace(name="ema"),
        ctx=SimpleNamespace(),
    )
    result = orch.resolve_next_action(ctrl)
    assert result == {"from": "reconcile"}


def test_resolve_next_action_resumes_halted_when_thesis_satisfiable(tmp_path):
    resumed_state = {"state": "running", "next_action": {"type": "run_experiment"}}
    ctrl = SimpleNamespace(
        _check_baseline_rerun=lambda: None,
        read_state=lambda: {
            "halted_thesis_id": "entry_window_test",
            "halted_reason": "requires_code_change",
            "halted_thesis": {"config_changes": {"k": "v"}},
            "research_round": 2,
        },
        read_results=lambda: [{"thesis_id": "baseline"}],
        reconcile_state=lambda: {"from": "reconcile"},
        _try_resume_halted_thesis=lambda: resumed_state,
        root=tmp_path,
        family=SimpleNamespace(name="ema"),
        ctx=SimpleNamespace(),
    )
    result = orch.resolve_next_action(ctrl)
    assert result == resumed_state


def test_resolve_next_action_builds_primitives_when_resume_returns_none(tmp_path, monkeypatch):
    built: list = []

    def fake_build(ctrl, state, thesis_id, raw_thesis, *, research_round):
        built.append(thesis_id)
        return {"state": "blocked", "next_action": {"type": "manual_review"}}

    monkeypatch.setattr(orch, "build_missing_primitives_for_state", fake_build)

    ctrl = SimpleNamespace(
        _check_baseline_rerun=lambda: None,
        read_state=lambda: {
            "halted_thesis_id": "entry_window_test",
            "halted_reason": "requires_code_change",
            "halted_thesis": {"config_changes": {"k": "v"}},
            "research_round": 2,
        },
        read_results=lambda: [{"thesis_id": "baseline"}],
        reconcile_state=lambda: {"from": "reconcile"},
        _try_resume_halted_thesis=lambda: None,
        root=tmp_path,
        family=SimpleNamespace(name="ema"),
        ctx=SimpleNamespace(),
    )
    result = orch.resolve_next_action(ctrl)

    assert built == ["entry_window_test"]
    assert result["next_action"]["type"] == "manual_review"


def test_resolve_next_action_falls_through_to_reconcile_for_normal_state(tmp_path):
    ctrl = SimpleNamespace(
        _check_baseline_rerun=lambda: None,
        read_state=lambda: {"state": "running"},
        read_results=lambda: [{"thesis_id": "baseline"}],
        reconcile_state=lambda: {"from": "reconcile"},
        _try_resume_halted_thesis=lambda: None,
        root=tmp_path,
        family=SimpleNamespace(name="ema"),
        ctx=SimpleNamespace(),
    )
    result = orch.resolve_next_action(ctrl)
    assert result == {"from": "reconcile"}
