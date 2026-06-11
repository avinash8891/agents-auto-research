from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch_planning import (
    build_research_failure_state,
    check_baseline_rerun,
    generate_combination_candidates,
    list_known_variant_configs,
    pending_configs,
    plan_next_action,
    select_research_next_action,
    should_terminate,
    thesis_family_for,
    thesis_statuses,
)
from autoresearch_state import BacktestResultRecord
from backtest_run_db import BaselineCheckpoint, BaselineTracker
from causal_model import save_model
from research_types import AccuracyPoint, CausalModel
from screening import ScreeningResult, write_screenings
from strategy_family import load_family


@pytest.fixture
def ema_family():
    return load_family("ema")


@pytest.fixture
def orb_family():
    return load_family("orb")


def test_list_known_variant_configs_returns_family_filtered_yaml_files(
    tmp_path: Path, ema_family
) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "ema_fast.yaml").write_text("ema_length: 4\n")
    (variants / "orb_other.yaml").write_text("or_minutes: 15\n")

    assert list_known_variant_configs(tmp_path, ema_family) == ["configs/variants/ema_fast.yaml"]


def test_list_known_variant_configs_includes_existing_family_defaults_and_skips_keep_file(
    tmp_path: Path, orb_family
) -> None:
    variants = tmp_path / "configs" / "variants"
    variants.mkdir(parents=True)
    (variants / "README.keep").write_text("")
    (tmp_path / "configs" / "variants" / "orb_spy_only.yaml").write_text("symbol: SPY\n")

    assert list_known_variant_configs(tmp_path, orb_family) == [
        "configs/variants/orb_spy_only.yaml"
    ]


def test_pending_configs_is_always_empty_when_variant_queueing_is_removed(
    tmp_path: Path, ema_family
) -> None:
    assert pending_configs(tmp_path, ema_family, tmp_path / "queue", []) == []


def test_thesis_statuses_are_derived_from_results_only(tmp_path: Path, ema_family) -> None:
    results = [BacktestResultRecord("configs/variants/ema_fast.yaml", 1.25, "keep", "", 100, {})]

    statuses = thesis_statuses(tmp_path, ema_family, tmp_path / "queue", results)

    assert statuses["configs/variants/ema_fast.yaml"]["status"] == "keep"
    assert statuses["configs/variants/ema_fast.yaml"]["last_metric"] == 1.25


def test_thesis_statuses_ignore_results_without_config(tmp_path: Path, ema_family) -> None:
    results = [BacktestResultRecord("", 1.25, "keep", "", 100, {})]

    assert thesis_statuses(tmp_path, ema_family, tmp_path / "queue", results) == {}


def test_thesis_family_for_uses_slug_map_without_loose_artifacts(
    tmp_path: Path, orb_family
) -> None:
    assert (
        thesis_family_for("configs/variants/orb_spy_only.yaml", orb_family, tmp_path, tmp_path)
        == "universe"
    )
    assert (
        thesis_family_for("configs/variants/orb_unknown.yaml", orb_family, tmp_path, tmp_path)
        == "unknown"
    )


def test_generate_combination_candidates_stays_disabled(tmp_path: Path, orb_family) -> None:
    results = [
        BacktestResultRecord("configs/variants/orb_spy_only.yaml", 1.0, "keep", "", 1, {}),
        BacktestResultRecord("configs/variants/orb_trailing_stop.yaml", 1.1, "keep", "", 2, {}),
    ]

    assert (
        generate_combination_candidates(tmp_path, orb_family, tmp_path / "proposals", results) == []
    )


def test_select_research_next_action_runs_baseline_first(tmp_path: Path, ema_family) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")

    out = select_research_next_action(
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
        [],
        job=3,
    )

    assert out["state"] == "running"
    assert out["research_round"] == 0
    assert out["next_action"]["source"] == "baseline"
    assert out["backtest_target_path"] == "runtime/jobs/job-3/research/round-0-baseline/backtest"


def test_select_research_next_action_blocks_when_baseline_config_missing(
    tmp_path: Path, ema_family
) -> None:
    out = select_research_next_action(
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
        [],
        job=3,
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "research"


def test_select_research_next_action_blocks_for_research_after_baseline(
    tmp_path: Path, ema_family
) -> None:
    results = [BacktestResultRecord("configs/ema_base.yaml", 1.0, "keep", "", 1, {})]

    out = select_research_next_action(
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
        results,
        job=3,
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["type"] == "research"


def _accuracy_point(round_number: int, skill: float) -> AccuracyPoint:
    return AccuracyPoint(
        round_number=round_number,
        model_version=round_number,
        pnl_weighted_accuracy=0.50 + skill,
        naive_accuracy=0.50,
        skill=skill,
        holdout_trade_count=100,
    )


def _killed_screening(rule: str = "gap_pct < 0") -> ScreeningResult:
    return ScreeningResult(
        rule=rule,
        verdict="kill_no_lift",
        sample_count=40,
        flagged_loss_rate=0.51,
        base_loss_rate=0.50,
        lift=0.01,
        p_value=0.80,
        overlap_with=None,
    )


def _save_plateau_model(family: str = "ema") -> None:
    save_model(
        CausalModel(
            family=family,
            version=5,
            accuracy_history=[
                _accuracy_point(1, 0.100),
                _accuracy_point(2, 0.104),
                _accuracy_point(3, 0.108),
                _accuracy_point(4, 0.109),
                _accuracy_point(5, 0.109),
            ],
        )
    )


def test_should_terminate_uses_model_plateau_and_zero_screening_pass_rate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ema_family
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    _save_plateau_model()
    db_path = tmp_path / "ema_backtest_runs.db"
    for round_number in range(1, 6):
        write_screenings(db_path, [_killed_screening()], round_number=round_number)

    assert (
        should_terminate(tmp_path, ema_family, tmp_path / "queue", tmp_path / "research", [], job=7)
        is True
    )


def test_should_terminate_uses_latest_screening_window_when_kills_do_not_append_accuracy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ema_family
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    _save_plateau_model()
    db_path = tmp_path / "ema_backtest_runs.db"
    for round_number in range(6, 11):
        write_screenings(db_path, [_killed_screening()], round_number=round_number)

    assert (
        should_terminate(tmp_path, ema_family, tmp_path / "queue", tmp_path / "research", [], job=7)
        is True
    )


def test_should_terminate_keeps_running_when_last_plateau_window_has_screening_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ema_family
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    _save_plateau_model()
    db_path = tmp_path / "ema_backtest_runs.db"
    for round_number in range(1, 5):
        write_screenings(db_path, [_killed_screening()], round_number=round_number)
    write_screenings(
        db_path,
        [
            _killed_screening().model_copy(
                update={"rule": "gap_pct > 0", "verdict": "pass", "lift": 0.20, "p_value": 0.01}
            )
        ],
        round_number=5,
    )

    assert (
        should_terminate(tmp_path, ema_family, tmp_path / "queue", tmp_path / "research", [], job=7)
        is False
    )


def test_should_terminate_ignores_legacy_terminal_findings_without_model_plateau(
    tmp_path: Path, ema_family
) -> None:
    research_dir = tmp_path / "research" / "round-1"
    research_dir.mkdir(parents=True)
    (research_dir / "round.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "job_id": 7,
                "round_number": 1,
                "findings": ["nothing left to try"],
            }
        )
    )

    assert (
        should_terminate(tmp_path, ema_family, tmp_path / "queue", tmp_path / "research", [], job=7)
        is False
    )


def test_should_terminate_returns_false_without_screening_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ema_family
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    _save_plateau_model()

    assert (
        should_terminate(tmp_path, ema_family, tmp_path / "queue", tmp_path / "research", [], job=7)
        is False
    )


def test_plan_next_action_preserves_forced_baseline_rerun_state(tmp_path: Path, ema_family) -> None:
    state = {
        "state": "running",
        "job": 3,
        "next_action": {
            "type": "run_round",
            "config": "configs/ema_base.yaml",
            "baseline_rerun_for_commit": "new-sha",
        },
    }

    out = plan_next_action(
        state,
        [BacktestResultRecord("configs/ema_base.yaml", 1.0, "keep", "", 1, {})],
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
    )

    assert out is state
    assert out["next_action"]["baseline_rerun_for_commit"] == "new-sha"


def test_plan_next_action_runs_baseline_and_clears_terminal_metadata(
    tmp_path: Path, ema_family
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "finished",
        "job": 3,
        "research_round": 9,
        "finished_reason": "old",
        "research_stop_reasoning": "old",
    }

    out = plan_next_action(
        state,
        [],
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
    )

    assert out["state"] == "running"
    assert out["selected_thesis_id"] == "baseline"
    assert "finished_reason" not in out
    assert "research_stop_reasoning" not in out


def test_plan_next_action_clears_terminal_metadata_when_baseline_branch_selected(
    tmp_path: Path, ema_family
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "finished",
        "job": 3,
        "finished_reason": "old",
        "research_stop_reasoning": "old",
    }

    out = plan_next_action(
        state,
        [],
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
    )

    assert out["state"] == "running"
    assert out["selected_thesis_id"] == "baseline"
    assert "finished_reason" not in out
    assert "research_stop_reasoning" not in out


def test_plan_next_action_queues_walkforward_when_model_plateaus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ema_family
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    _save_plateau_model()
    db_path = tmp_path / "ema_backtest_runs.db"
    for round_number in range(1, 6):
        write_screenings(db_path, [_killed_screening()], round_number=round_number)
    research_dir = tmp_path / "research" / "round-2"
    research_dir.mkdir(parents=True)
    (research_dir / "round.json").write_text(
        json.dumps(
            {
                "job_id": 3,
                "round_number": 2,
                "status": "completed",
                "findings": ["no edge remains"],
            }
        )
    )
    state = {
        "state": "blocked",
        "job": 3,
        "research_round": 2,
        "finished_reason": "old",
        "research_stop_reasoning": "old",
    }

    out = plan_next_action(
        state,
        [BacktestResultRecord("configs/ema_base.yaml", 1.0, "keep", "", 1, {})],
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
    )

    assert out["state"] == "running"
    assert out["next_action"]["type"] == "walkforward"
    assert out["next_action"]["reason"] == "model_plateau"
    assert out["finished_reason"] == "model_plateau_pending_walkforward"


def test_plan_next_action_finishes_after_plateau_walkforward_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ema_family
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    _save_plateau_model()
    db_path = tmp_path / "ema_backtest_runs.db"
    for round_number in range(1, 6):
        write_screenings(db_path, [_killed_screening()], round_number=round_number)
    state = {
        "state": "running",
        "job": 3,
        "research_round": 2,
        "walkforward_status": "completed",
        "finished_reason": "model_plateau_pending_walkforward",
    }

    out = plan_next_action(
        state,
        [BacktestResultRecord("configs/ema_base.yaml", 1.0, "keep", "", 1, {})],
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
    )

    assert out["state"] == "finished"
    assert out["next_action"]["type"] == "terminated"
    assert out["finished_reason"] == "model_plateau"


def test_plan_next_action_treats_malformed_job_as_unscoped_research_block(
    tmp_path: Path, ema_family
) -> None:
    state = {"state": "running", "job": "not-an-int", "research_round": 1}

    out = plan_next_action(
        state,
        [BacktestResultRecord("configs/ema_base.yaml", 1.0, "keep", "", 1, {})],
        tmp_path,
        tmp_path,
        ema_family,
        tmp_path / "queue",
        tmp_path / "proposals",
        tmp_path / "research",
    )

    assert out["state"] == "blocked"
    assert out["next_action"]["artifact_dir"] == "research"


def test_check_baseline_rerun_returns_action_when_code_commit_changed(
    tmp_path: Path, ema_family
) -> None:
    tracker = BaselineTracker(tmp_path / "baseline.json")
    tracker.record(
        BaselineCheckpoint(
            code_commit="old-sha",
            data_hash="data",
            config_hash="config",
            metrics={"profit_factor": 1.2},
            timestamp="2026-04-29T12:00:00+00:00",
        )
    )

    out = check_baseline_rerun(tmp_path, ema_family, tracker, "new-sha", [])

    assert out is not None
    assert out["type"] == "run_round"
    assert out["config"] == "configs/ema_base.yaml"
    assert out["baseline_rerun_for_commit"] == "new-sha"
    assert out["rerun_reason"] == "code changed old-sha -> new-sha"


def test_check_baseline_rerun_skips_when_no_checkpoint_exists(tmp_path: Path, ema_family) -> None:
    tracker = BaselineTracker(tmp_path / "baseline.json")

    assert check_baseline_rerun(tmp_path, ema_family, tracker, "new-sha", []) is None


def test_check_baseline_rerun_skips_when_commit_is_current(tmp_path: Path, ema_family) -> None:
    tracker = BaselineTracker(tmp_path / "baseline.json")
    tracker.record(
        BaselineCheckpoint(
            code_commit="same-sha",
            data_hash="data",
            config_hash="config",
            metrics={"profit_factor": 1.2},
            timestamp="2026-04-29T12:00:00+00:00",
        )
    )

    assert check_baseline_rerun(tmp_path, ema_family, tracker, "same-sha", []) is None


def test_check_baseline_rerun_skips_when_commit_already_has_newer_rerun(
    tmp_path: Path, ema_family
) -> None:
    tracker = BaselineTracker(tmp_path / "baseline.json")
    tracker.record(
        BaselineCheckpoint(
            code_commit="old-sha",
            data_hash="data",
            config_hash="config",
            metrics={"profit_factor": 1.2},
            timestamp="2026-04-29T12:00:00+00:00",
        )
    )
    results = [
        BacktestResultRecord(
            "configs/ema_base.yaml",
            1.3,
            "keep",
            "",
            "2026-04-30T12:00:00+00:00",
            {"baseline_rerun_for_commit": "new-sha"},
        )
    ]

    assert check_baseline_rerun(tmp_path, ema_family, tracker, "new-sha", results) is None


def test_build_research_failure_state_marks_interrupted(tmp_path: Path) -> None:
    out = build_research_failure_state(tmp_path, tmp_path / "research", "round 5 failed")

    assert out["state"] == "interrupted"
    assert out["next_action"]["type"] == "terminated"
