from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch_experiment import (
    _build_db_record,
    _build_export_entry,
    _compute_run_output_dir,
    _round_context_from_state,
    _thesis_sidecar_path,
    _validate_backtest_request,
    artifact_dir_for,
)
from autoresearch_paths import resolve_config_path
from backtest_run_db import BacktestRunRecord


def test_round_context_requires_baseline_only_in_round_zero() -> None:
    assert _round_context_from_state(
        {"job": 1, "research_round": 0}, config="configs/ema_base.yaml"
    ) == (1, 0, True)

    with pytest.raises(ValueError, match="round 0 is reserved for baseline"):
        _round_context_from_state(
            {"job": 1, "research_round": 0}, config="configs/variants/ema_fast.yaml"
        )

    with pytest.raises(ValueError, match="baseline backtest must run only in round 0"):
        _round_context_from_state({"job": 1, "research_round": 2}, config="configs/ema_base.yaml")


def test_thesis_sidecar_path_prefers_round_selected_thesis_and_rejects_legacy(
    tmp_path: Path,
) -> None:
    controller = SimpleNamespace(
        root=tmp_path, ctx=SimpleNamespace(execution_root=None, current_contract=None)
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-3" / "research" / "round-2"
    round_root.mkdir(parents=True)
    (round_root / "selected_thesis.json").write_text("{}\n")

    assert (
        _thesis_sidecar_path(
            controller,
            "runtime/jobs/job-3/research/round-2/selected_config.json",
            "slug",
        )
        == round_root / "selected_thesis.json"
    )

    legacy = tmp_path / "experiments" / "slug" / "thesis.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}\n")
    with pytest.raises(ValueError, match="legacy experiment sidecar path is not supported"):
        _thesis_sidecar_path(controller, "configs/x.yaml", "slug")


def test_resolve_config_path_rejects_paths_outside_allowed_roots(tmp_path: Path) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    code_root.mkdir()
    runtime_root.mkdir()

    with pytest.raises(ValueError, match="escapes allowed roots"):
        resolve_config_path(
            "../escape.yaml",
            code_root=code_root,
            runtime_root=runtime_root,
        )

    with pytest.raises(ValueError, match="escapes allowed roots"):
        resolve_config_path(
            tmp_path.parent / "escape.yaml",
            code_root=code_root,
            runtime_root=runtime_root,
        )


def test_artifact_dir_for_uses_round_backtest_root(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"job": 7, "research_round": 4}))

    out = artifact_dir_for(
        state_path,
        tmp_path,
        "configs/variants/ema_fast.yaml",
        git_commit="ignored",
        config_hash="ignored",
    )

    assert out == tmp_path / "runtime" / "jobs" / "job-7" / "research" / "round-4" / "backtest"


def test_compute_run_output_dir_uses_baseline_round_zero_path(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        root=tmp_path,
        read_state=lambda: {"job": 5, "research_round": 0},
        current_commit=lambda: "abc123",
    )

    run_dir, config_path = _compute_run_output_dir(controller, "configs/ema_base.yaml")

    assert config_path == tmp_path / "configs" / "ema_base.yaml"
    assert (
        run_dir
        == tmp_path / "runtime" / "jobs" / "job-5" / "research" / "round-0-baseline" / "backtest"
    )


def test_compute_run_output_dir_uses_runtime_root_when_split_from_code_root(
    tmp_path: Path,
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    controller = SimpleNamespace(
        root=code_root,
        runtime_root=runtime_root,
        read_state=lambda: {"job": 5, "research_round": 0},
        current_commit=lambda: "abc123",
    )

    run_dir, config_path = _compute_run_output_dir(controller, "configs/ema_base.yaml")

    assert config_path == code_root / "configs" / "ema_base.yaml"
    assert run_dir == (
        runtime_root / "runtime" / "jobs" / "job-5" / "research" / "round-0-baseline" / "backtest"
    )


def test_validate_backtest_request_requires_round_job_and_selected_thesis_for_nonbaseline(
    tmp_path: Path,
) -> None:
    controller = SimpleNamespace(ctx=SimpleNamespace(current_contract=None))

    with pytest.raises(ValueError, match="selected thesis id"):
        _validate_backtest_request(
            controller,
            {
                "job": 2,
                "research_round": 1,
                "next_action": {"config": "configs/variants/ema_fast.yaml"},
            },
        )

    _validate_backtest_request(
        controller,
        {
            "job": 2,
            "research_round": 1,
            "selected_thesis_id": "ema-fast",
            "next_action": {
                "config": "configs/variants/ema_fast.yaml",
                "selected_thesis_id": "ema-fast",
            },
        },
    )


def test_build_db_record_sets_round_and_backtest_run_metadata(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        ctx=SimpleNamespace(
            current_contract=None, parent_backtest_run_id="", latest_config_contents={}
        ),
        family=SimpleNamespace(name="ema"),
        root=tmp_path,
        current_commit=lambda: "abc1234",
    )
    state = {"job": 9, "research_round": 2, "_last_round_usage": {"input_tokens": 1}}

    record = _build_db_record(
        controller,
        config="runtime/jobs/job-9/research/round-2/selected_config.json",
        decision="keep",
        details={"profit_factor": 1.4, "trade_count": 12},
        analysis={"trade_analysis": {"verdict": {"status": "accepted", "summary": "ok"}}},
        runtime_config={"ema_length": 7},
        fallback_experiment_id="fallback",
        state=state,
    )

    assert isinstance(record, BacktestRunRecord)
    assert record.backtest_run_id == "job-9-round-2-backtest"
    assert record.run_id == "job-9-round-2-backtest"
    assert record.research_round_id == "job-9-round-2"
    assert record.research_round_number == 2
    assert record.is_baseline is False


def test_build_db_record_preserves_round_zero_baseline_identity(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        ctx=SimpleNamespace(
            current_contract=None, parent_backtest_run_id="", latest_config_contents={}
        ),
        family=SimpleNamespace(name="ema"),
        root=tmp_path,
        current_commit=lambda: "abc1234",
    )
    state = {"job": 9, "research_round": 0, "_last_round_usage": {}}

    record = _build_db_record(
        controller,
        config="runtime/jobs/job-9/research/round-0-baseline/selected_config.json",
        decision="keep",
        details={"profit_factor": 1.4, "trade_count": 12},
        analysis={"trade_analysis": {"verdict": {"status": "accepted", "summary": "ok"}}},
        runtime_config={"ema_length": 7},
        fallback_experiment_id="fallback",
        state=state,
    )

    assert record.research_round_number == 0
    assert record.is_baseline is True
    assert record.backtest_run_id == "job-9-round-0-backtest"


def test_build_export_entry_uses_backtest_run_type(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        ctx=SimpleNamespace(current_contract=None, execution_root=None),
        root=tmp_path,
        current_commit=lambda: "abc1234",
    )
    entry = _build_export_entry(
        controller,
        config="runtime/jobs/job-1/research/round-1/selected_config.json",
        metric=1.2,
        decision="keep",
        details={"profit_factor": 1.2, "git_sha": "abc1234"},
        asi={"artifact_dir": "runtime/jobs/job-1/research/round-1/backtest"},
        next_run=1,
        state={"job": 1, "research_round": 1},
    )

    assert entry["type"] == "backtest_run"
    assert entry["backtest_run_id"] == "job-1-round-1-backtest"
    assert entry["research_round_id"] == "job-1-round-1"


def test_build_export_entry_preserves_round_zero_baseline_identity(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        ctx=SimpleNamespace(current_contract=None, execution_root=None),
        root=tmp_path,
        current_commit=lambda: "abc1234",
    )
    entry = _build_export_entry(
        controller,
        config="runtime/jobs/job-1/research/round-0-baseline/selected_config.json",
        metric=1.2,
        decision="keep",
        details={"profit_factor": 1.2, "git_sha": "abc1234"},
        asi={"artifact_dir": "runtime/jobs/job-1/research/round-0-baseline/backtest"},
        next_run=1,
        state={"job": 1, "research_round": 0},
    )

    assert entry["backtest_run_id"] == "job-1-round-0-backtest"
    assert entry["research_round_id"] == "job-1-round-0"
    assert entry["research_round_number"] == 0
    assert entry["is_baseline"] is True
