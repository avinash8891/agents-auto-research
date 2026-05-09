from __future__ import annotations

from pathlib import Path

import research_subagents as subagents
from backtest_run_db import BacktestRunDB, BacktestRunRecord


def _record(root: Path, *, round_number: int) -> BacktestRunRecord:
    backtest_dir = (
        root / "runtime" / "jobs" / "job-4" / "research" / f"round-{round_number}" / "backtest"
    )
    backtest_dir.mkdir(parents=True, exist_ok=True)
    (backtest_dir / "trades.csv").write_text("entry_date,exit_date\n")
    (backtest_dir / "metrics.json").write_text("{}\n")
    (backtest_dir / "result.json").write_text("{}\n")
    (backtest_dir / "diagnostics.json").write_text("{}\n")
    (backtest_dir / "config.json").write_text('{"strategy_family":"ema"}\n')
    return BacktestRunRecord(
        run_id=f"exp-{round_number}",
        thesis_id=f"thesis-{round_number}",
        config_path=f"runtime/jobs/job-4/research/round-{round_number}/selected_config.json",
        runtime_config={},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={"profit_factor": 1.0 + round_number},
        trade_count=5,
        trades_file=str((backtest_dir / "trades.csv").resolve()),
        strategy_events_file=str((backtest_dir / "strategy_events.parquet").resolve()),
        diagnostics_file=str((backtest_dir / "diagnostics.json").resolve()),
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="good",
        parent_backtest_run_id="",
        timestamp=f"2026-05-09T00:00:0{round_number}+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=4,
        backtest_run_id=f"job-4-round-{round_number}-backtest",
        research_round_id=f"job-4-round-{round_number}",
        research_round_number=round_number,
        is_baseline=False,
    )


def test_resolve_artifacts_for_specific_round(tmp_path: Path, monkeypatch) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    round_one = _record(tmp_path, round_number=1)
    round_two = _record(tmp_path, round_number=2)
    db.add(round_one)
    db.add(round_two)
    monkeypatch.setattr(
        subagents, "_resolve_backtest_db_path", lambda trades_file, family_name: db.path
    )

    index = subagents._build_round_index(trades_file=round_two.trades_file, family_name="ema")

    _, artifacts, summary = subagents._resolve_artifacts_for_ref(index, "round:2")
    assert artifacts["trades_csv"] == round_two.trades_file
    assert summary["research_round_number"] == 2


def test_analysis_manifest_exposes_round_refs_for_analyst_fetch(
    tmp_path: Path, monkeypatch
) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    round_one = _record(tmp_path, round_number=1)
    db.add(round_one)
    monkeypatch.setattr(
        subagents, "_resolve_backtest_db_path", lambda trades_file, family_name: db.path
    )

    manifest = subagents._analysis_manifest(trades_file=round_one.trades_file, family_name="ema")

    assert manifest["job_round_index"]["round_refs"]["round:1"].startswith("sequence:")
