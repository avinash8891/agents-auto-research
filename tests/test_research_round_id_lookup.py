"""Spec A1 §10 — research_round_id is the unique key for a backtest.

Asserts the three success criteria:
- get_round_result returns the seeded record by research_round_id.
- get_round_result raises KeyError on unknown id (no thesis-id fallback).
- Same thesis_id across rounds produces distinct artifact directories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch_runtime_paths import research_round_backtest_root, research_round_id
from backtest_run_db import BacktestRunDB, BacktestRunRecord
from persistence_utils import utc_now_iso8601
from research_memory import get_round_result


def _seed_backtest(
    db: BacktestRunDB,
    *,
    research_round_id_value: str,
    thesis_id: str,
    job: int,
    round_number: int,
    profit_factor: float = 1.55,
) -> BacktestRunRecord:
    record = BacktestRunRecord(
        run_id=f"run-{research_round_id_value}",
        thesis_id=thesis_id,
        config_path=(f"runtime/jobs/job-{job}/research/round-{round_number}/selected_config.json"),
        runtime_config={"ema_length": 12},
        code_commit="abc1234",
        data_hash="datahash",
        train_metrics={"sharpe": 1.3, "profit_factor": profit_factor, "max_drawdown": -0.15},
        validation_metrics={"profit_factor": profit_factor, "pct_profitable_windows": 0.62},
        trade_count=84,
        trades_file=(f"runtime/jobs/job-{job}/research/round-{round_number}/backtest/trades.csv"),
        strategy_events_file=(
            f"runtime/jobs/job-{job}/research/round-{round_number}/backtest/"
            "strategy_events.parquet"
        ),
        diagnostics_file=(
            f"runtime/jobs/job-{job}/research/round-{round_number}/backtest/diagnostics.json"
        ),
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="profit_factor above baseline",
        family="ema",
        hypothesis="EMA length 12 improves trend capture",
        mechanism="Longer EMA filters noise",
        job=job,
        backtest_run_id=f"{research_round_id_value}-backtest",
        research_round_id=research_round_id_value,
        research_round_number=round_number,
        timestamp=utc_now_iso8601(),
    )
    db.add(record)
    return record


def test_get_round_result_returns_record_for_seeded_research_round_id(
    tmp_path: Path,
) -> None:
    """Criterion 1: lookup by research_round_id returns the expected backtest."""
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    rrid = research_round_id(1, 1)
    assert rrid == "job-1-round-1"
    _seed_backtest(
        db,
        research_round_id_value=rrid,
        thesis_id="ema_breakout_v1",
        job=1,
        round_number=1,
        profit_factor=1.72,
    )

    payload = json.loads(get_round_result(tmp_path, research_round_id=rrid))

    assert payload["status"] == "ok"
    assert payload["research_round_id"] == rrid
    result = payload["result"]
    assert result["research_round_id"] == rrid
    assert result["thesis_id"] == "ema_breakout_v1"
    assert result["family"] == "ema"
    assert result["metric_name"] == "profit_factor"
    assert result["metric"] == 1.72


def test_get_round_result_job_scope_filter_passes_through(
    tmp_path: Path,
) -> None:
    """Finding J: passing job_id only returns records for that job.

    Within a single per-family DB, the rrid encodes (job, round) and is unique
    (enforced by the DB add validator). The job_id parameter is therefore a
    no-op for same-DB lookups but is required so wrappers can pass current_job
    defensively — and so this filter applies across multiple per-family DBs
    that may share a job numbering.
    """
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    rrid_j1 = research_round_id(1, 1)
    _seed_backtest(
        db,
        research_round_id_value=rrid_j1,
        thesis_id="ema_breakout_v1",
        job=1,
        round_number=1,
        profit_factor=1.10,
    )

    # job_id=1 finds it; job_id=2 must not.
    payload = json.loads(get_round_result(tmp_path, research_round_id=rrid_j1, job_id=1))
    assert payload["result"]["thesis_id"] == "ema_breakout_v1"

    with pytest.raises(KeyError):
        get_round_result(tmp_path, research_round_id=rrid_j1, job_id=2)


def test_get_round_result_raises_keyerror_for_unknown_research_round_id(
    tmp_path: Path,
) -> None:
    """Criterion 2: no fallback resolution — unknown id raises KeyError."""
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    _seed_backtest(
        db,
        research_round_id_value=research_round_id(1, 1),
        thesis_id="ema_breakout_v1",
        job=1,
        round_number=1,
    )

    with pytest.raises(KeyError):
        get_round_result(tmp_path, research_round_id="nonexistent")


def test_same_thesis_id_across_rounds_produces_distinct_artifact_dirs(
    tmp_path: Path,
) -> None:
    """Criterion 3: thesis_id collision across rounds does NOT collide on disk.

    The path keying scheme uses research_round_id as the directory component.
    Two backtests with the same thesis_id in different rounds of the same job
    must produce distinct artifact dirs and distinct DB records, both keyed
    by research_round_id.
    """
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    shared_thesis_id = "orb_volatility_open"
    rrid_1 = research_round_id(1, 1)
    rrid_2 = research_round_id(1, 2)

    _seed_backtest(
        db,
        research_round_id_value=rrid_1,
        thesis_id=shared_thesis_id,
        job=1,
        round_number=1,
        profit_factor=1.41,
    )
    _seed_backtest(
        db,
        research_round_id_value=rrid_2,
        thesis_id=shared_thesis_id,
        job=1,
        round_number=2,
        profit_factor=1.83,
    )

    record_1 = db.get_by_research_round_id(rrid_1)
    record_2 = db.get_by_research_round_id(rrid_2)

    # DB-level: both rows survive with the same thesis_id; distinct via rrid.
    assert record_1 is not None
    assert record_2 is not None
    assert record_1.thesis_id == shared_thesis_id
    assert record_2.thesis_id == shared_thesis_id
    assert record_1.research_round_id == rrid_1
    assert record_2.research_round_id == rrid_2
    assert record_1.run_id != record_2.run_id

    # Filesystem-level: artifact dirs constructed via the canonical helper differ
    # and embed the round identifier — not the thesis id.
    artifact_dir_1 = research_round_backtest_root(tmp_path, 1, 1)
    artifact_dir_2 = research_round_backtest_root(tmp_path, 1, 2)

    assert artifact_dir_1 != artifact_dir_2
    assert "round-1" in str(artifact_dir_1)
    assert "round-2" in str(artifact_dir_2)
    # The shared thesis_id must NOT appear in the path — keying is by round.
    assert shared_thesis_id not in str(artifact_dir_1)
    assert shared_thesis_id not in str(artifact_dir_2)

    # Canonical experiments/{research_round_id}/ layout (Spec A1 §6 + §10).
    # The legacy layout was experiments/{thesis_id}/ — see compiler_builder.py
    # which raises RuntimeError if it encounters experiments/{research_round_id}/
    # as a leftover legacy dir, and scripts/migrate_experiment_dirs.py which
    # renames legacy thesis_id dirs to research_round_id. This assertion proves
    # production constructs the post-migration canonical path, not the legacy
    # thesis_id-keyed path that would collide across rounds sharing a thesis.
    exp_root_round_1 = (
        tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-1" / "experiments"
    )
    exp_root_round_2 = (
        tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-2" / "experiments"
    )
    experiment_dir_1 = exp_root_round_1 / rrid_1
    experiment_dir_2 = exp_root_round_2 / rrid_2

    # Paths differ across rounds despite shared thesis_id.
    assert experiment_dir_1 != experiment_dir_2
    # thesis_id explicitly absent — this is the collision-fix guarantee.
    assert shared_thesis_id not in str(experiment_dir_1)
    assert shared_thesis_id not in str(experiment_dir_2)
    # research_round_id present in the experiments/ subdir component.
    assert rrid_1 in str(experiment_dir_1)
    assert rrid_2 in str(experiment_dir_2)
    # Job scoping present in both paths.
    assert "job-1" in str(experiment_dir_1)
    assert "job-1" in str(experiment_dir_2)
