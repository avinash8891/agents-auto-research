from __future__ import annotations

from pathlib import Path

import research_subagents as subagents
from backtest_run_db import BacktestRunDB, BacktestRunRecord


def _record(
    *, job: int, round_number: int, baseline: bool = False, root: Path
) -> BacktestRunRecord:
    backtest_dir = (
        root / "runtime" / "jobs" / f"job-{job}" / "research" / "round-0-baseline" / "backtest"
        if baseline
        else root
        / "runtime"
        / "jobs"
        / f"job-{job}"
        / "research"
        / f"round-{round_number}"
        / "backtest"
    )
    backtest_dir.mkdir(parents=True, exist_ok=True)
    (backtest_dir / "trades.csv").write_text("entry_date,exit_date\n")
    (backtest_dir / "metrics.json").write_text("{}\n")
    (backtest_dir / "result.json").write_text("{}\n")
    (backtest_dir / "diagnostics.json").write_text("{}\n")
    (backtest_dir / "config.json").write_text('{"strategy_family":"ema","data_universe":"u1"}\n')
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
        validation_metrics={"profit_factor": 1.2 + round_number},
        trade_count=10,
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
        job=job,
        backtest_run_id=f"job-{job}-round-{round_number}-backtest",
        research_round_id=f"job-{job}-round-{round_number}",
        research_round_number=round_number,
        is_baseline=baseline,
    )


def test_analysis_manifest_defaults_to_baseline_when_round_zero_exists(
    tmp_path: Path, monkeypatch
) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    baseline = _record(job=7, round_number=0, baseline=True, root=tmp_path)
    latest = _record(job=7, round_number=1, root=tmp_path)
    db.add(baseline)
    db.add(latest)
    monkeypatch.setattr(
        subagents, "_resolve_backtest_db_path", lambda trades_file, family_name: db.path
    )

    manifest = subagents._analysis_manifest(
        trades_file=baseline.trades_file,
        family_name="ema",
    )

    assert manifest["default_scope"] == "baseline"
    assert manifest["default_round_ref"] == "baseline"
    assert manifest["artifacts"]["trades_csv"] == baseline.trades_file


def test_build_round_index_exposes_round_refs_and_latest_round(tmp_path: Path, monkeypatch) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    baseline = _record(job=7, round_number=0, baseline=True, root=tmp_path)
    round_one = _record(job=7, round_number=1, root=tmp_path)
    db.add(baseline)
    db.add(round_one)
    monkeypatch.setattr(
        subagents, "_resolve_backtest_db_path", lambda trades_file, family_name: db.path
    )

    index = subagents._build_round_index(trades_file=round_one.trades_file, family_name="ema")

    assert index is not None
    assert index.round_ref_map["round:0"] == "baseline"
    assert "round:1" in index.round_ref_map

    resolved_ref, artifacts, summary = subagents._resolve_artifacts_for_ref(index, "round:1")
    assert resolved_ref.startswith("sequence:")
    assert artifacts["trades_csv"] == round_one.trades_file
    assert summary["research_round_number"] == 1

    _, latest_artifacts, latest_summary = subagents._resolve_artifacts_for_ref(index, "latest")
    assert latest_artifacts["trades_csv"] == round_one.trades_file
    assert latest_summary["research_round_number"] == 1
