from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch_planning import (
    build_research_failure_state,
    generate_combination_candidates,
    list_known_variant_configs,
    pending_configs,
    select_research_next_action,
    should_terminate,
    thesis_family_for,
    thesis_statuses,
)
from autoresearch_state import BacktestResultRecord
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


def test_pending_configs_is_always_empty_when_variant_queueing_is_removed(
    tmp_path: Path, ema_family
) -> None:
    assert pending_configs(tmp_path, ema_family, tmp_path / "queue", []) == []


def test_thesis_statuses_are_derived_from_results_only(tmp_path: Path, ema_family) -> None:
    results = [BacktestResultRecord("configs/variants/ema_fast.yaml", 1.25, "keep", "", 100, {})]

    statuses = thesis_statuses(tmp_path, ema_family, tmp_path / "queue", results)

    assert statuses["configs/variants/ema_fast.yaml"]["status"] == "keep"
    assert statuses["configs/variants/ema_fast.yaml"]["last_metric"] == 1.25


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
    assert (
        out["backtest_target_path"] == "runtime/jobs/job-{job}/research/round-0-baseline/backtest"
    )


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


def test_should_terminate_reads_completed_round_json_without_queue_dependency(
    tmp_path: Path, ema_family
) -> None:
    research_dir = tmp_path / "research" / "round-1"
    research_dir.mkdir(parents=True)
    (research_dir / "round.json").write_text(
        json.dumps({"status": "completed", "job": 7, "findings": ["nothing left to try"]})
    )

    assert (
        should_terminate(tmp_path, ema_family, tmp_path / "queue", tmp_path / "research", [], job=7)
        is True
    )


def test_build_research_failure_state_marks_interrupted(tmp_path: Path) -> None:
    out = build_research_failure_state(tmp_path, tmp_path / "research", "round 5 failed")

    assert out["state"] == "interrupted"
    assert out["next_action"]["type"] == "terminated"
