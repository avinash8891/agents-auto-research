from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from causal_harvest import (
    _drain_stream_with_timeout,
    _flatten_metrics,
    attach_harvest_lesson,
    retest_baseline_metrics,
)
from research_types import HarvestVerdict


def _retest_controller(tmp_path: Path, records: list, *, output: str, exit_code: int = 0):
    family = SimpleNamespace(
        name="ema",
        base_config_filename="ema_base.yaml",
        benchmark_command=lambda config, output_dir=None: f"{config}|{output_dir}",
    )
    return SimpleNamespace(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        backtest_run_db=SimpleNamespace(all=lambda: records),
        run_command=lambda command: (exit_code, output),
        parse_benchmark_details=lambda raw: json.loads(raw),
        parse_metric=lambda raw, name="profit_factor": json.loads(raw).get(name),
        primary_metric_name=lambda: "profit_factor",
    )


def _retest_state() -> dict:
    return {
        "job": 6,
        "next_action": {
            "registered_prediction_retest": {"validation_end": "2024-09-30"},
        },
    }


def _baseline_record(job: int, validation_start: str) -> SimpleNamespace:
    return SimpleNamespace(
        job=job,
        is_baseline=True,
        accepted=True,
        runtime_config={"validation_start": validation_start, "validation_end": "2023-12-31"},
    )


def test_retest_baseline_metrics_scopes_to_current_job(tmp_path: Path) -> None:
    records = [
        _baseline_record(5, "2018-01-01"),  # another job's baseline must be ignored
        _baseline_record(6, "2020-01-01"),
        SimpleNamespace(job=6, is_baseline=False, accepted=True, runtime_config={}),
    ]
    controller = _retest_controller(
        tmp_path, records, output=json.dumps({"profit_factor": 1.4, "trade_count": 40})
    )
    run_output_dir = tmp_path / "round" / "backtest"
    run_output_dir.mkdir(parents=True)

    metrics = retest_baseline_metrics(controller, _retest_state(), run_output_dir)

    assert metrics is not None
    assert metrics["profit_factor"] == 1.4
    rerun_config = json.loads((tmp_path / "round" / "baseline_retest" / "config.json").read_text())
    assert rerun_config["validation_start"] == "2020-01-01"
    assert rerun_config["validation_end"] == "2024-09-30"


def test_retest_baseline_metrics_falls_back_on_parse_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    controller = _retest_controller(
        tmp_path,
        [_baseline_record(6, "2020-01-01")],
        output="not json at all",  # rerun exits 0 but emits malformed output
    )
    run_output_dir = tmp_path / "round" / "backtest"
    run_output_dir.mkdir(parents=True)

    with caplog.at_level(logging.ERROR):
        metrics = retest_baseline_metrics(controller, _retest_state(), run_output_dir)

    assert metrics is None
    assert "retest baseline rerun parse failed" in caplog.text


def test_flatten_metrics_validation_wins_over_train_and_top_level_duplicates() -> None:
    details = {
        "max_drawdown": 0.05,  # divergent top-level copy must not shadow validation
        "trades_file": "trades.csv",
        "metrics": {"max_drawdown": 0.30, "trade_count": 25},
        "train_metrics": {"max_drawdown": 0.08, "profit_factor": 2.0},
        "validation_metrics": {"max_drawdown": 0.31, "profit_factor": 1.1},
    }

    flat = _flatten_metrics(details)

    assert flat["max_drawdown"] == 0.31
    assert flat["profit_factor"] == 1.1
    assert flat["trade_count"] == 25
    assert flat["trades_file"] == "trades.csv"


class _NeverEndingStream:
    async def stream_events(self):
        while True:
            await asyncio.sleep(1)
            yield object()


def test_drain_stream_with_timeout_bounds_hung_stream() -> None:
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_drain_stream_with_timeout(_NeverEndingStream(), timeout_seconds=0.01))


def test_attach_harvest_lesson_falls_back_when_llm_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    round_root = tmp_path / "runtime/jobs/job-1/research/round-1"
    run_output_dir = round_root / "backtest"
    run_output_dir.mkdir(parents=True)
    verdict = HarvestVerdict(
        thesis_id="ema-gap",
        status="supported",
        prediction_results=[{"metric": "profit_factor", "gap": 0.2}],
        summary="deterministic harvest summary",
    )
    controller = SimpleNamespace(family=SimpleNamespace(name="ema"))
    monkeypatch.setattr("causal_harvest._harvest_lesson_llm_enabled", lambda: True)
    monkeypatch.setattr(
        "causal_harvest._generate_harvest_lesson",
        lambda **kwargs: (_ for _ in ()).throw(asyncio.TimeoutError()),
    )

    caplog.set_level(logging.WARNING, logger="causal_harvest")
    updated = attach_harvest_lesson(controller, run_output_dir, verdict)

    assert updated.lesson == "deterministic harvest summary"
    assert "harvest lesson LLM synthesis timed out" in caplog.text
