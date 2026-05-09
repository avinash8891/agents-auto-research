from __future__ import annotations

import sqlite3
from pathlib import Path

from backtest_run_db import BacktestRunDB, BacktestRunRecord


def _record(*, round_number: int, job: int, baseline: bool = False) -> BacktestRunRecord:
    backtest_dir = (
        f"runtime/jobs/job-{job}/research/round-0-baseline/backtest"
        if baseline
        else f"runtime/jobs/job-{job}/research/round-{round_number}/backtest"
    )
    return BacktestRunRecord(
        run_id=f"exp-{job}-{round_number}",
        thesis_id="baseline" if baseline else f"thesis-{round_number}",
        config_path=(
            "configs/ema_base.yaml"
            if baseline
            else f"runtime/jobs/job-{job}/research/round-{round_number}/selected_config.json"
        ),
        runtime_config={},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={"profit_factor": 1.5},
        trade_count=10,
        trades_file=f"{backtest_dir}/trades.csv",
        strategy_events_file=f"{backtest_dir}/strategy_events.parquet",
        diagnostics_file=f"{backtest_dir}/diagnostics.json",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="good",
        parent_backtest_run_id="",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=job,
        backtest_run_id=f"job-{job}-round-{round_number}-backtest",
        research_round_id=f"job-{job}-round-{round_number}",
        research_round_number=round_number,
        is_baseline=baseline,
    )


def test_sqlite_schema_keeps_backtest_runs_table_with_round_columns(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    with sqlite3.connect(db.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")}

    assert "research_round_id" in columns
    assert "research_round_number" in columns
    assert "backtest_run_id" in columns


def test_read_results_reports_round_scoped_artifact_dir(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(_record(round_number=1, job=1))

    result = db.read_results()[0]

    assert result.asi["artifact_dir"] == "runtime/jobs/job-1/research/round-1/backtest"
    assert result.asi["research_round_id"] == "job-1-round-1"
    assert result.asi["backtest_run_id"] == "job-1-round-1-backtest"


def test_export_entries_use_backtest_run_type(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(_record(round_number=0, job=3, baseline=True))

    entries = [entry for entry in db.export_entries() if entry.get("type") != "config"]

    assert entries[0]["type"] == "backtest_run"
    assert entries[0]["is_baseline"] is True
    assert entries[0]["research_round_number"] == 0


def test_list_research_rounds_keeps_round_zero_baseline(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    state_path = tmp_path / "state.json"
    state_path.write_text('{"job": 3}\n')
    db.log_research_round(
        state_path,
        round_number=0,
        thesis_id="baseline",
        hypothesis_id="baseline",
        outcome="baseline_backtest_completed",
    )

    rows = db.list_research_rounds()

    assert rows[0]["round_number"] == 0
    assert rows[0]["selected_thesis_id"] == "baseline"
