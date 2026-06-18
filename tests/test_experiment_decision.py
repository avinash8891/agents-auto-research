from __future__ import annotations

from backtest_run_db import BacktestRunRecord
from experiment_decision import Decision, decide_verdict


def _duplicate_record(*, runtime_config: dict) -> BacktestRunRecord:
    return BacktestRunRecord(
        run_id="R-20260101-000000-r1-backtest",
        thesis_id="ema-pullback",
        config_path="configs/variants/ema-pullback.yaml",
        runtime_config=runtime_config,
        code_commit="abcdef1",
        data_hash="data",
        train_metrics={"profit_factor": 1.4},
        validation_metrics={"profit_factor": 1.3},
        trade_count=12,
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="N/A",
        verdict_status="accepted",
        verdict_summary="accepted",
        timestamp="2026-01-01T00:00:00+00:00",
        family="ema",
        hypothesis="EMA pullback improves entries.",
        mechanism="Filter noisy crosses.",
        job=4,
    )


def test_registered_verdict_passes_through_unchanged() -> None:
    decision = decide_verdict(
        decision="keep",
        registered_verdict={"status": "accepted_drift_watch", "summary": "kept under drift watch"},
        duplicate=None,
        runtime_config={"ema_length": 21},
    )
    assert decision == Decision("keep", "accepted_drift_watch", "kept under drift watch")


def test_missing_verdict_falls_back_to_keep_decision() -> None:
    decision = decide_verdict(
        decision="keep",
        registered_verdict={},
        duplicate=None,
        runtime_config={"ema_length": 21},
    )
    assert decision == Decision("keep", "accepted", "kept by primary metric improvement")


def test_missing_verdict_falls_back_to_reject_decision() -> None:
    decision = decide_verdict(
        decision="reject",
        registered_verdict={},
        duplicate=None,
        runtime_config={"ema_length": 21},
    )
    assert decision == Decision("reject", "rejected", "rejected by primary metric comparison")


def test_identical_runtime_config_duplicate_is_invalid_duplicate_result() -> None:
    runtime_config = {"ema_length": 21, "atr_mult": 1.5}
    decision = decide_verdict(
        decision="keep",
        registered_verdict={"status": "accepted", "summary": "kept"},
        duplicate=_duplicate_record(runtime_config=runtime_config),
        runtime_config=runtime_config,
        duplicate_summary="invalid_duplicate_result: identical artifacts as R-1",
    )
    assert decision == Decision(
        "discard",
        "invalid_duplicate_result",
        "invalid_duplicate_result: identical artifacts as R-1",
    )


def test_differing_runtime_config_duplicate_is_invalid_noop_config() -> None:
    decision = decide_verdict(
        decision="keep",
        registered_verdict={"status": "accepted", "summary": "kept"},
        duplicate=_duplicate_record(runtime_config={"ema_length": 21}),
        runtime_config={"ema_length": 34},
        duplicate_summary="invalid_noop_config: identical trades despite different config",
    )
    assert decision == Decision(
        "discard",
        "invalid_noop_config",
        "invalid_noop_config: identical trades despite different config",
    )
