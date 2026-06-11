from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from autoresearch_artifacts import serialize_artifact_path
from autoresearch_controller import AutoresearchController
from autoresearch_experiment import (
    ResultJsonError,
    _baseline_checkpoint_metrics,
    _baseline_metrics_from_first_result,
    _build_asi_dict,
    _build_db_record,
    _build_export_entry,
    _compute_run_output_dir,
    _contract_from_sidecar,
    _evaluate_against_thesis,
    _find_duplicate_artifact_output,
    _invalid_duplicate_result_summary,
    _record_baseline_checkpoint,
    _round_context_from_state,
    _thesis_sidecar_path,
    _validate_backtest_request,
    artifact_dir_for,
    derive_trade_analysis,
    log_experiment_result,
    parse_benchmark_details,
    parse_metric,
    parse_result_json,
    primary_metric_name,
    run_command,
    run_experiment,
)
from autoresearch_paths import resolve_config_path
from backtest_run_db import BacktestRunRecord, BaselineCheckpoint
from causal_harvest import (
    PendingCausalModelArtifact,
    apply_registered_verdict_to_causal_factor,
    evaluate_registered_predictions,
    request_registered_prediction_retest,
    write_feature_table_artifact,
)
from causal_model import load_model, save_model
from feature_table import load_feature_table
from research_types import BacktestContract, CausalFactor, CausalModel, HarvestVerdict
from strategy_family import load_family


def _write_result_manifest(
    output_dir: Path,
    metrics: dict,
    *,
    diagnostics: dict | None = None,
    trades_text: str = "entry_date,pnl_pct\n2026-01-01,1.25\n",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    trades_path = output_dir / "trades.csv"
    result_path = output_dir / "result.json"
    metrics_path.write_text(json.dumps(metrics) + "\n")
    trades_path.write_text(trades_text)
    payload = {
        "metrics_file": "metrics.json",
        "trades_file": str(trades_path),
        "strategy_events_file": "",
        "git_sha": "abcdef1",
    }
    if diagnostics is not None:
        diagnostics_path = output_dir / "diagnostics.json"
        diagnostics_path.write_text(json.dumps(diagnostics) + "\n")
        payload["diagnostics_file"] = "diagnostics.json"
    result_path.write_text(json.dumps(payload) + "\n")
    return result_path


def _controller_for_experiment(tmp_path: Path, command: str) -> AutoresearchController:
    family = SimpleNamespace(
        name="ema",
        base_config_filename="ema_base.yaml",
        discord_webhook="",
        benchmark_command=lambda config, output_dir=None: command.format(output_dir=output_dir),
    )
    controller = AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )
    controller.backtest_run_db.init_session(
        name="ema",
        metric_name="profit_factor",
        direction="higher",
    )
    return controller


def _write_causal_model_base_config(root: Path) -> None:
    config_dir = root / "configs"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "ema_base.yaml").write_text(
        "validation_start: '2020-01-01'\nvalidation_end: '2024-01-01'\n",
        encoding="utf-8",
    )


def _write_feature_table_data_universe(data_root: Path) -> None:
    universe = data_root / "universes" / "tiny_feature_data"
    universe.mkdir(parents=True)
    index = pd.to_datetime(["2024-01-02 14:30", "2024-01-02 14:35", "2024-01-02 14:40"], utc=True)
    frames = {
        "open": [100.0, 100.0, 100.0],
        "high": [100.5, 100.8, 101.0],
        "low": [99.5, 100.0, 100.2],
        "close": [100.2, 100.6, 100.9],
        "volume": [1000, 1100, 1200],
    }
    for name, values in frames.items():
        pd.DataFrame({"AAA": values}, index=index).to_parquet(universe / f"{name}.parquet")
    (universe / "manifest.json").write_text(
        json.dumps(
            {
                "data_universe": "tiny_feature_data",
                "symbol_count": 1,
                "symbols": ["AAA"],
                "start": "2024-01-02",
                "end": "2024-01-02",
            }
        )
        + "\n"
    )
    pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-01").date()],
            "regime_label": ["risk_on"],
        }
    ).to_parquet(data_root / "regime_labels.parquet", index=False)


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


@pytest.mark.parametrize(
    "state, config, message",
    [
        ({"job": None, "research_round": 1}, "configs/variants/ema_fast.yaml", "job id"),
        ({"job": 0, "research_round": 1}, "configs/variants/ema_fast.yaml", "job id"),
        ({"job": 1, "research_round": "bad"}, "configs/variants/ema_fast.yaml", "research round"),
        ({"job": 1, "research_round": -1}, "configs/variants/ema_fast.yaml", "must be >= 0"),
    ],
)
def test_round_context_rejects_invalid_job_and_round_values(
    state: dict, config: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _round_context_from_state(state, config=config)


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


def test_run_command_executes_real_process_and_returns_combined_output(tmp_path: Path) -> None:
    code, output = run_command(
        tmp_path,
        f"{sys.executable} -c \"import sys; print('stdout-ok'); print('stderr-ok', file=sys.stderr)\"",
    )

    assert code == 0
    assert "stdout-ok" in output
    assert "stderr-ok" in output


def test_run_command_reports_bad_shell_quoting_without_running_shell(tmp_path: Path) -> None:
    code, output = run_command(tmp_path, "python -c 'unterminated")

    assert code == 1
    assert "No closing quotation" in output


def test_run_command_reports_timeout_as_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="python slow.py", timeout=300)

    monkeypatch.setattr("autoresearch_experiment.subprocess.run", _timeout)

    code, output = run_command(tmp_path, f"{sys.executable} slow.py")

    assert code == 1
    assert output == "TIMEOUT"


def test_run_command_reports_os_errors_without_swallowing_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _os_error(*args, **kwargs):
        raise OSError("executable missing")

    monkeypatch.setattr("autoresearch_experiment.subprocess.run", _os_error)

    code, output = run_command(tmp_path, "missing-binary --version")

    assert code == 1
    assert output == "executable missing"


def test_parse_result_json_accepts_inline_payload_only_when_opted_in() -> None:
    payload = '{"metrics_file": "metrics.json"}'

    assert parse_result_json(payload) is None
    assert parse_result_json(payload, allow_inline_json=True) == {"metrics_file": "metrics.json"}
    assert parse_result_json("[1, 2]", allow_inline_json=True) is None
    assert parse_result_json("{bad", allow_inline_json=True) is None


def test_parse_metric_legacy_stdout_is_explicit_and_primary_metric_defaults() -> None:
    output = "METRIC custom_pf=1.45\n"

    assert parse_metric(output, name="custom_pf") is None
    assert parse_metric(output, name="custom_pf", allow_legacy=True) == 1.45
    assert parse_metric(output, name="missing", allow_legacy=True) is None
    assert primary_metric_name([]) == "profit_factor"
    assert primary_metric_name(
        [{"type": "note"}, {"type": "config", "metricName": "custom_pf"}]
    ) == ("custom_pf")


def test_parse_benchmark_details_reads_relative_manifest_artifacts(tmp_path: Path) -> None:
    result_path = _write_result_manifest(
        tmp_path / "run",
        {
            "trade_count": 3,
            "profit_factor": 1.7,
            "max_drawdown": -0.2,
            "median_expectancy": 0.4,
            "custom_alpha": 2.5,
            "_private": "ignored",
            "diagnostics": {"sample": "train"},
        },
        diagnostics={"trade_rejections_due_gap": 0, "accepted": 3},
    )

    details = parse_benchmark_details(f"RESULT_JSON {result_path}\n")

    assert details["trade_count"] == 3
    assert details["profit_factor"] == 1.7
    assert details["custom_alpha"] == 2.5
    assert "_private" not in details
    assert details["strategy_diagnostics"] == {"trade_rejections_due_gap": 0, "accepted": 3}
    assert details["diagnostics"] == {"sample": "train"}
    assert parse_metric(f"RESULT_JSON {result_path}\n", name="custom_alpha") == 2.5


def test_parse_benchmark_details_defaults_diagnostics_when_manifest_omits_file(
    tmp_path: Path,
) -> None:
    result_path = _write_result_manifest(
        tmp_path / "run",
        {"trade_count": 2, "profit_factor": 1.2},
    )

    details = parse_benchmark_details(f"RESULT_JSON {result_path}\n")

    assert details["strategy_diagnostics"] == {}
    assert primary_metric_name([{"type": "note", "metricName": "ignored"}]) == "profit_factor"


def test_parse_benchmark_details_fails_loudly_for_bad_result_manifest(tmp_path: Path) -> None:
    missing_result = tmp_path / "missing-result.json"
    with pytest.raises(ResultJsonError, match="path does not exist"):
        parse_benchmark_details(f"RESULT_JSON {missing_result}\n")

    bad_result = tmp_path / "bad-result.json"
    bad_result.write_text("{not-json")
    with pytest.raises(ResultJsonError, match="malformed JSON"):
        parse_benchmark_details(f"RESULT_JSON {bad_result}\n")

    no_metrics = tmp_path / "no-metrics.json"
    no_metrics.write_text(json.dumps({"trades_file": "trades.csv"}) + "\n")
    with pytest.raises(ResultJsonError, match="missing required metrics_file"):
        parse_benchmark_details(f"RESULT_JSON {no_metrics}\n")
    with pytest.raises(ResultJsonError, match="missing required metrics_file"):
        parse_metric(f"RESULT_JSON {no_metrics}\n")

    missing_metrics = tmp_path / "missing-metrics-result.json"
    missing_metrics.write_text(json.dumps({"metrics_file": "missing-metrics.json"}) + "\n")
    with pytest.raises(ResultJsonError, match="metrics.json not found"):
        parse_metric(f"RESULT_JSON {missing_metrics}\n")


def test_legacy_stdout_parsing_requires_explicit_opt_in() -> None:
    output = (
        "METRIC trade_count=4\n"
        "METRIC profit_factor=1.25\n"
        "METRIC max_drawdown=-0.5\n"
        'DIAGNOSTICS {"accepted": 4}\n'
        "TRADES_FILE /tmp/trades.csv\n"
    )

    with pytest.raises(ResultJsonError, match="legacy stdout parsing is disabled"):
        parse_benchmark_details(output)

    details = parse_benchmark_details(output, allow_legacy=True)
    assert details["trade_count"] == 4
    assert details["profit_factor"] == 1.25
    assert details["diagnostics"] == {"accepted": 4}
    assert details["trades_file"] == "/tmp/trades.csv"


def test_legacy_stdout_parsing_rejects_malformed_diagnostics() -> None:
    with pytest.raises(ValueError, match="malformed DIAGNOSTICS line"):
        parse_benchmark_details("DIAGNOSTICS {bad-json}\n", allow_legacy=True)


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


def test_validate_backtest_request_rejects_missing_config_and_bad_round(tmp_path: Path) -> None:
    controller = SimpleNamespace(ctx=SimpleNamespace(current_contract=None))

    with pytest.raises(ValueError, match="backtest requires config path"):
        _validate_backtest_request(controller, {"job": 2, "research_round": 1, "next_action": {}})

    with pytest.raises(ValueError, match="round 0 is reserved for baseline"):
        _validate_backtest_request(
            controller,
            {
                "job": 2,
                "research_round": 0,
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
        fallback_run_id="fallback",
        state=state,
    )

    assert isinstance(record, BacktestRunRecord)
    assert record.backtest_run_id == "job-9-round-2-backtest"
    assert record.run_id == "job-9-round-2-backtest"
    assert record.research_round_id == "job-9-round-2"
    assert record.research_round_number == 2
    assert record.is_baseline is False


def test_build_db_record_marks_duplicate_artifacts_invalid(tmp_path: Path) -> None:
    trades = tmp_path / "trades.csv"
    diagnostics = tmp_path / "diagnostics.json"
    trades.write_text("entry_date,pnl_pct\n2026-01-01,1.0\n")
    diagnostics.write_text('{"trade_rejections_due_gap": 0}\n')
    previous = BacktestRunRecord(
        run_id="previous-run",
        thesis_id="previous",
        config_path="runtime/jobs/job-9/research/round-1/selected_config.json",
        runtime_config={"ema_length": 5},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={},
        trade_count=12,
        trades_file=str(trades),
        strategy_events_file="",
        diagnostics_file=str(diagnostics),
        strategy_diagnostics={"trade_rejections_due_gap": 0},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="ok",
        parent_backtest_run_id="",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=9,
    )
    controller = SimpleNamespace(
        ctx=SimpleNamespace(
            current_contract=None, parent_backtest_run_id="", latest_config_contents={}
        ),
        family=SimpleNamespace(name="ema"),
        root=tmp_path,
        current_commit=lambda: "abc1234",
        backtest_run_db=SimpleNamespace(all=lambda: [previous]),
    )
    state = {"job": 9, "research_round": 2, "_last_round_usage": {}}

    record = _build_db_record(
        controller,
        config="runtime/jobs/job-9/research/round-2/selected_config.json",
        decision="keep",
        details={
            "profit_factor": 1.4,
            "trade_count": 12,
            "trades_file": str(trades),
            "diagnostics_file": str(diagnostics),
            "strategy_diagnostics": {"trade_rejections_due_gap": 0},
        },
        analysis={"trade_analysis": {"verdict": {"status": "accepted", "summary": "ok"}}},
        runtime_config={"ema_length": 7},
        fallback_run_id="fallback",
        state=state,
    )

    assert record.accepted is False
    assert record.verdict_status == "invalid_noop_config"
    assert "previous-run" in record.verdict_summary
    assert "trade_rejections_due_gap=0" in record.verdict_summary


def test_duplicate_artifact_detection_ignores_missing_hash_inputs(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        backtest_run_db=SimpleNamespace(all=lambda: []),
        family=SimpleNamespace(name="ema"),
    )

    assert (
        _find_duplicate_artifact_output(
            controller,
            runtime_config={},
            details={"trade_count": 1, "trades_file": "", "diagnostics_file": ""},
            state={"job": 1},
        )
        is None
    )


def test_duplicate_artifact_detection_skips_nonmatching_prior_records(tmp_path: Path) -> None:
    current_trades = tmp_path / "current_trades.csv"
    current_diagnostics = tmp_path / "current_diagnostics.json"
    other_trades = tmp_path / "other_trades.csv"
    other_diagnostics = tmp_path / "other_diagnostics.json"
    current_trades.write_text("entry_date,pnl_pct\n2026-01-01,1.0\n")
    current_diagnostics.write_text('{"accepted": 12}\n')
    other_trades.write_text("entry_date,pnl_pct\n2026-01-02,-1.0\n")
    other_diagnostics.write_text('{"accepted": 9}\n')

    def prior(
        run_id: str,
        *,
        family: str = "ema",
        job: int = 9,
        trade_count: int = 12,
        strategy_diagnostics: dict | None = None,
        trades_file: Path = current_trades,
        diagnostics_file: Path = current_diagnostics,
    ) -> BacktestRunRecord:
        return BacktestRunRecord(
            run_id=run_id,
            thesis_id=run_id,
            config_path="runtime/jobs/job-9/research/round-1/selected_config.json",
            runtime_config={"ema_length": 5},
            code_commit="abc1234",
            data_hash="data",
            train_metrics={},
            validation_metrics={},
            trade_count=trade_count,
            trades_file=str(trades_file),
            strategy_events_file="",
            diagnostics_file=str(diagnostics_file),
            strategy_diagnostics=strategy_diagnostics or {"accepted": 12},
            accepted=True,
            rejection_reason="",
            verdict_status="accepted",
            verdict_summary="ok",
            parent_backtest_run_id="",
            timestamp="2026-05-09T00:00:00+00:00",
            family=family,
            hypothesis="h",
            mechanism="m",
            job=job,
        )

    expected = prior("matching-duplicate")
    controller = SimpleNamespace(
        backtest_run_db=SimpleNamespace(
            all=lambda: [
                expected,
                prior("wrong-diagnostics-file", diagnostics_file=other_diagnostics),
                prior("wrong-trades-file", trades_file=other_trades),
                prior("wrong-strategy-diagnostics", strategy_diagnostics={"accepted": 11}),
                prior("wrong-trade-count", trade_count=10),
                prior("wrong-job", job=10),
                prior("wrong-family", family="orb"),
            ]
        ),
        family=SimpleNamespace(name="ema"),
    )

    duplicate = _find_duplicate_artifact_output(
        controller,
        runtime_config={"ema_length": 7},
        details={
            "trade_count": 12,
            "trades_file": str(current_trades),
            "diagnostics_file": str(current_diagnostics),
            "strategy_diagnostics": {"accepted": 12},
        },
        state={"job": 9},
    )

    assert duplicate is expected


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
        fallback_run_id="fallback",
        state=state,
    )

    assert record.research_round_number == 0
    assert record.is_baseline is True
    assert record.backtest_run_id == "job-9-round-0-backtest"


def test_invalid_duplicate_summary_distinguishes_identical_runtime_config(tmp_path: Path) -> None:
    duplicate = BacktestRunRecord(
        run_id="duplicate-run",
        thesis_id="previous",
        config_path="config.json",
        runtime_config={"ema_length": 5},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={},
        trade_count=4,
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="ok",
        parent_backtest_run_id="",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=1,
    )

    summary = _invalid_duplicate_result_summary(
        duplicate,
        {"trade_count": 4, "strategy_diagnostics": {"trade_rejections_due_gap": 0}},
        {"ema_length": 5},
    )

    assert summary.startswith("invalid_duplicate_result")
    assert "trade_rejections_due_gap=0" in summary


def test_invalid_duplicate_summary_ignores_bool_and_nonnumeric_zero_hints() -> None:
    duplicate = BacktestRunRecord(
        run_id="duplicate-run",
        thesis_id="previous",
        config_path="config.json",
        runtime_config={"ema_length": 5},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={},
        trade_count=9,
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="ok",
        parent_backtest_run_id="",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=1,
    )

    summary = _invalid_duplicate_result_summary(
        duplicate,
        {
            "strategy_diagnostics": {
                "trade_rejections_due_bool": False,
                "trade_rejections_due_text": "not-a-number",
                "trade_rejections_due_gap": 0,
            },
            "trade_count": 9,
        },
        {"ema_length": 8},
    )

    assert "trade_rejections_due_gap=0" in summary
    assert "trade_rejections_due_bool" not in summary
    assert "trade_rejections_due_text" not in summary


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


def test_derive_trade_analysis_loads_runtime_config_and_updates_context(tmp_path: Path) -> None:
    config_path = tmp_path / "runtime" / "jobs" / "job-2" / "research" / "round-1"
    config_path.mkdir(parents=True)
    (config_path / "selected_config.json").write_text(
        json.dumps({"runtime_config": {"ema_length": 8}}) + "\n"
    )
    result_path = _write_result_manifest(
        tmp_path / "run",
        {"trade_count": 2, "profit_factor": 1.8},
        diagnostics={"accepted": 2},
    )
    controller = SimpleNamespace(
        root=tmp_path,
        runtime_root=tmp_path,
        family=SimpleNamespace(name="ema"),
        ctx=SimpleNamespace(execution_root=None),
    )

    analysis = derive_trade_analysis(
        controller,
        "runtime/jobs/job-2/research/round-1/selected_config.json",
        1.8,
        "keep",
        output=f"RESULT_JSON {result_path}\n",
    )

    assert analysis["trade_analysis"]["profit_factor"] == 1.8
    assert analysis["runtime_config"] == {"ema_length": 8}
    assert controller.ctx.latest_config_contents == {"ema_length": 8}
    assert controller.ctx.latest_trades_file.endswith("trades.csv")


def test_build_asi_dict_records_baseline_rerun_and_absolute_artifact_dir(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-artifacts"
    state = {
        "job": 3,
        "next_action": {"source": "baseline", "baseline_rerun_for_commit": "abcdef1"},
    }
    controller = SimpleNamespace(
        root=tmp_path,
        ctx=SimpleNamespace(current_contract=None),
        read_state=lambda: state,
    )

    asi = _build_asi_dict(
        controller,
        config="configs/ema_base.yaml",
        artifact_dir=outside,
        analysis={
            "trade_analysis": {"profit_factor": 1.2},
            "insights": ["metric=1.2"],
            "why_not_data_fit": "independent",
        },
        thesis_id="baseline",
        config_changes={},
    )

    assert asi["artifact_dir"] == outside.as_posix()
    assert asi["baseline_rerun_for_commit"] == "abcdef1"
    assert serialize_artifact_path(tmp_path / "runtime", controller.root) == "runtime"


def test_contract_sidecar_drives_thesis_identity_and_diagnostics(tmp_path: Path) -> None:
    round_root = tmp_path / "runtime" / "jobs" / "job-5" / "research" / "round-2"
    round_root.mkdir(parents=True)
    sidecar = {
        "thesis_id": "ema-diagnostic-gate",
        "hypothesis": "Require regime diagnostics before accepting the candidate.",
        "mechanism": "The filter should improve profit factor with diagnostics present.",
        "config_changes": {"ema_length": 13},
        "expected_effects": [
            {"metric": "profit_factor", "direction": "increase", "threshold": 0.1}
        ],
        "required_diagnostic_specs": [
            {
                "key": "regime_breakdown",
                "surface": "strategy_diagnostics",
                "payload_fields": ["bull"],
            }
        ],
    }
    (round_root / "selected_thesis.json").write_text(json.dumps(sidecar) + "\n")
    controller = SimpleNamespace(
        root=tmp_path,
        runtime_root=tmp_path,
        family=SimpleNamespace(name="ema", base_config_filename="ema_base.yaml"),
        ctx=SimpleNamespace(current_contract=None, execution_root=None),
    )

    contract = _contract_from_sidecar(
        controller,
        "runtime/jobs/job-5/research/round-2/selected_config.json",
    )

    assert contract is not None
    assert contract.thesis_id == "ema-diagnostic-gate"
    assert contract.config_changes == {"ema_length": 13}
    assert contract.required_diagnostic_specs[0].key == "regime_breakdown"


def test_evaluate_against_thesis_rejects_missing_required_diagnostic(tmp_path: Path) -> None:
    contract = BacktestContract(
        contract_id="contract-1",
        thesis_id="ema-diagnostic-gate",
        strategy_family="ema",
        baseline_config_path="configs/ema_base.yaml",
        runtime_config={"ema_length": 13},
        hypothesis="Candidate needs a regime diagnostic to validate the mechanism.",
        mechanism="Regime conditioned entries should improve profit factor.",
        expected_effects=[{"metric": "profit_factor", "direction": "increase", "threshold": 0.1}],
        required_diagnostic_specs=[
            {
                "key": "regime_breakdown",
                "surface": "strategy_diagnostics",
                "payload_fields": ["bull"],
            }
        ],
    )
    controller = SimpleNamespace(
        root=tmp_path,
        runtime_root=tmp_path,
        ctx=SimpleNamespace(execution_root=None),
        baseline_tracker=None,
        read_results=lambda: [
            SimpleNamespace(asi={"trade_analysis": {"profit_factor": 1.0, "trade_count": 5}})
        ],
        primary_metric_name=lambda: "profit_factor",
    )
    config_dir = tmp_path / "runtime" / "jobs" / "job-5" / "research" / "round-2"
    config_dir.mkdir(parents=True)

    verdict, decision = _evaluate_against_thesis(
        controller,
        contract,
        "runtime/jobs/job-5/research/round-2/selected_config.json",
        1.3,
        "keep",
        {"profit_factor": 1.3, "trade_count": 6, "strategy_diagnostics": {}},
    )

    assert decision == "discard"
    assert verdict.status == "inconclusive"
    assert verdict.missing_required_diagnostics == ["regime_breakdown"]
    assert (config_dir / "verdict.json").exists()


def test_evaluate_against_thesis_applies_real_verdict_statuses(tmp_path: Path) -> None:
    accepted_contract = BacktestContract(
        contract_id="accepted-contract",
        thesis_id="ema-accepted",
        strategy_family="ema",
        baseline_config_path="configs/ema_base.yaml",
        runtime_config={"ema_length": 13},
        hypothesis="Candidate improves profit factor.",
        mechanism="Cleaner entries should raise profit factor.",
        expected_effects=[{"metric": "profit_factor", "direction": "increase", "threshold": 0.1}],
    )
    controller = SimpleNamespace(
        root=tmp_path,
        runtime_root=tmp_path,
        ctx=SimpleNamespace(execution_root=None),
        baseline_tracker=None,
        read_results=lambda: [
            SimpleNamespace(asi={"trade_analysis": {"profit_factor": 1.0, "trade_count": 5}})
        ],
        primary_metric_name=lambda: "profit_factor",
    )
    config_dir = tmp_path / "runtime" / "jobs" / "job-5" / "research" / "round-3"
    config_dir.mkdir(parents=True)

    accepted_verdict, accepted_decision = _evaluate_against_thesis(
        controller,
        accepted_contract,
        "runtime/jobs/job-5/research/round-3/selected_config.json",
        1.3,
        "discard",
        {"profit_factor": 1.3, "trade_count": 6},
    )

    assert accepted_verdict.status == "accepted"
    assert accepted_decision == "discard"
    assert json.loads((config_dir / "verdict.json").read_text())["status"] == "accepted"

    rejected_contract = BacktestContract(
        contract_id="rejected-contract",
        thesis_id="ema-rejected",
        strategy_family="ema",
        baseline_config_path="configs/ema_base.yaml",
        runtime_config={"ema_length": 21},
        hypothesis="Candidate improves profit factor unless drawdown explodes.",
        mechanism="More trades should improve return quality.",
        expected_effects=[{"metric": "profit_factor", "direction": "increase", "threshold": 0.1}],
        disqualifiers=[
            {
                "name": "drawdown_limit",
                "condition": "max_drawdown > 0.20",
                "severity": "hard_fail",
            }
        ],
    )

    rejected_verdict, rejected_decision = _evaluate_against_thesis(
        controller,
        rejected_contract,
        "runtime/jobs/job-5/research/round-3/selected_config.json",
        1.4,
        "keep",
        {"profit_factor": 1.4, "trade_count": 7, "max_drawdown": 0.35},
    )

    assert rejected_verdict.status == "rejected"
    assert rejected_verdict.triggered_disqualifiers == ["drawdown_limit"]
    assert rejected_decision == "discard"


def test_baseline_metrics_prefer_tracker_then_fall_back_to_first_result(tmp_path: Path) -> None:
    checkpoint = BaselineCheckpoint(
        code_commit="abc1234",
        data_hash="data",
        config_hash="config",
        metrics={"profit_factor": 1.8, "trade_count": 7},
        timestamp="2026-05-09T00:00:00+00:00",
    )
    controller = SimpleNamespace(
        baseline_tracker=SimpleNamespace(latest=lambda: checkpoint),
        read_results=lambda: [],
    )
    assert _baseline_metrics_from_first_result(controller) == {
        "profit_factor": 1.8,
        "trade_count": 7,
    }

    fallback = SimpleNamespace(
        baseline_tracker=SimpleNamespace(latest=lambda: None),
        read_results=lambda: [
            SimpleNamespace(
                asi={
                    "trade_analysis": {
                        "trade_count": 5,
                        "profit_factor": 1.2,
                        "max_drawdown": -0.1,
                        "ignored": 99,
                    }
                }
            )
        ],
    )
    assert _baseline_metrics_from_first_result(fallback) == {
        "trade_count": 5,
        "profit_factor": 1.2,
        "max_drawdown": -0.1,
    }


def test_registered_predictions_use_family_research_engine_thresholds(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text(
        "research_engine:\n  min_trades: 50\n",
        encoding="utf-8",
    )
    run_output_dir = tmp_path / "runtime/jobs/job-1/research/round-1/backtest"
    run_output_dir.mkdir(parents=True)
    (run_output_dir.parent / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "thesis-001",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 1.2}
                ],
            }
        ),
        encoding="utf-8",
    )
    controller = SimpleNamespace(
        root=tmp_path,
        family=SimpleNamespace(name="ema", base_config_filename="ema_base.yaml"),
        primary_metric_name=lambda: "profit_factor",
        baseline_tracker=SimpleNamespace(
            latest=lambda: SimpleNamespace(metrics={"profit_factor": 1.0, "trade_count": 100})
        ),
        read_results=lambda: [],
    )

    verdict = evaluate_registered_predictions(
        controller,
        run_output_dir,
        metric=1.3,
        details={"trade_count": 25, "profit_factor": 1.3},
    )

    assert verdict.status == "degenerate"
    assert "min_trades 50" in verdict.summary


def test_registered_predictions_use_validation_metrics_before_train_metrics(
    tmp_path: Path,
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("research_engine: {}\n", encoding="utf-8")
    run_output_dir = tmp_path / "runtime/jobs/job-1/research/round-1/backtest"
    run_output_dir.mkdir(parents=True)
    (run_output_dir.parent / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "thesis-001",
                "predictions": [
                    {"metric": "median_expectancy", "direction": "increase", "predicted": 1.3}
                ],
            }
        ),
        encoding="utf-8",
    )
    controller = SimpleNamespace(
        root=tmp_path,
        family=SimpleNamespace(name="ema", base_config_filename="ema_base.yaml"),
        primary_metric_name=lambda: "profit_factor",
        baseline_tracker=SimpleNamespace(
            latest=lambda: SimpleNamespace(
                metrics={"profit_factor": 1.0, "trade_count": 100, "median_expectancy": 1.0}
            )
        ),
        read_results=lambda: [],
    )

    verdict = evaluate_registered_predictions(
        controller,
        run_output_dir,
        metric=1.1,
        details={
            "trade_count": 25,
            "train_metrics": {"median_expectancy": 0.5},
            "validation_metrics": {"median_expectancy": 1.4},
        },
    )

    assert verdict.status == "supported"
    assert verdict.prediction_results[0]["metric"] == "median_expectancy"


def test_registered_verdict_promotion_merges_into_live_model_without_snapshot_overwrite(
    tmp_path: Path,
) -> None:
    controller = _controller_for_experiment(tmp_path, "")
    _write_causal_model_base_config(tmp_path)
    round_root = tmp_path / "runtime/jobs/job-1/research/round-1"
    round_root.mkdir(parents=True)
    config_path = round_root / "selected_config.json"
    config_path.write_text(json.dumps({"ema_length": 10}) + "\n", encoding="utf-8")
    (round_root / "selected_thesis.json").write_text(
        json.dumps({"thesis_id": "thesis-001", "rule": "gap_pct < 0"}) + "\n",
        encoding="utf-8",
    )
    pending_factor = CausalFactor(
        factor_id="pending-gap",
        story="Pending gap rule",
        rule="gap_pct < 0",
        direction="loss",
        status="candidate",
    )
    PendingCausalModelArtifact(round_root).write(
        CausalModel(family="ema", version=1, factors=[pending_factor], accuracy_history=[])
    )
    live_factor = CausalFactor(
        factor_id="live-other",
        story="Live factor added after pending snapshot",
        rule="vol_pctile_20d > 0.5",
        direction="loss",
        status="supported",
    )
    save_model(
        CausalModel(family="ema", version=9, factors=[live_factor], accuracy_history=[]),
        runtime_root=tmp_path,
        code_root=tmp_path,
    )

    apply_registered_verdict_to_causal_factor(
        controller,
        "runtime/jobs/job-1/research/round-1/selected_config.json",
        HarvestVerdict(
            thesis_id="thesis-001",
            status="supported",
            summary="supported: gap rule passed",
        ),
    )

    model = load_model("ema", runtime_root=tmp_path, code_root=tmp_path)
    assert model.version == 9
    assert [factor.rule for factor in model.factors] == [
        "vol_pctile_20d > 0.5",
        "gap_pct < 0",
    ]
    assert model.factors[1].status == "harvested"
    assert model.factors[1].lesson == "supported: gap rule passed"


def test_record_baseline_checkpoint_persists_critical_drift_and_clears_rerun_marker(
    tmp_path: Path,
) -> None:
    controller = _controller_for_experiment(tmp_path, "")
    controller.write_state(
        {
            "state": "running",
            "job": 1,
            "research_round": 0,
            "next_action": {
                "type": "run_round",
                "source": "baseline",
                "baseline_rerun_for_commit": "abc1234",
            },
        }
    )
    controller.baseline_tracker.record(
        BaselineCheckpoint(
            code_commit="abc1234",
            data_hash="old-data",
            config_hash="old-config",
            metrics={"profit_factor": 1.0},
            timestamp="2026-05-09T00:00:00+00:00",
        )
    )

    _record_baseline_checkpoint(
        controller,
        {"profit_factor": 1.5, "trade_count": 6, "git_sha": "abc1234"},
        {"ema_length": 8},
    )

    persisted = controller.read_state()
    assert persisted["baseline_drift"]["drifted"] is True
    assert "baseline_rerun_for_commit" not in persisted["next_action"]
    assert controller.baseline_tracker.latest().metrics == {
        "profit_factor": 1.5,
        "trade_count": 6.0,
    }


def test_write_feature_table_artifact_resolves_manifest_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_feature_table_data_universe(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    controller = _controller_for_experiment(tmp_path, "")
    artifact_dir = tmp_path / "runtime/jobs/job-1/research/round-1/backtest"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "trades.csv").write_text(
        "symbol,direction,entry_date,entry_price,stop,pnl,pnl_pct,hold_bars\n"
        "AAA,long,2024-01-02T14:35:00+00:00,100.6,99.6,1.0,0.01,3\n",
        encoding="utf-8",
    )
    pd.DataFrame([{"timestamp": "2024-01-02T14:35:00+00:00", "symbol": "AAA"}]).to_parquet(
        artifact_dir / "strategy_events.parquet",
        index=False,
    )
    config_path = tmp_path / "runtime/jobs/job-1/research/round-1/selected_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"data_universe": "tiny_feature_data"}) + "\n")
    details = {"trades_file": "trades.csv", "strategy_events_file": "strategy_events.parquet"}

    write_feature_table_artifact(
        controller,
        config=str(config_path),
        details=details,
        artifact_dir=artifact_dir,
    )

    table = load_feature_table(artifact_dir.parent)
    assert details["feature_table_file"] == str(artifact_dir.parent / "feature_table.parquet")
    assert table.loc[0, "trade_id"] == "AAA:2024-01-02T14:35:00+00:00"


def test_log_experiment_result_persists_artifacts_and_sqlite_record(tmp_path: Path) -> None:
    family = load_family("ema")
    controller = AutoresearchController(
        root=tmp_path,
        runtime_root=tmp_path,
        family=family,
        state_path=tmp_path / "ema_autoresearch.next.json",
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        jobs_root=tmp_path / "runtime" / "jobs",
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-4" / "research" / "round-1"
    round_root.mkdir(parents=True)
    config_path = round_root / "selected_config.json"
    config_path.write_text(json.dumps({"ema_length": 9}) + "\n")
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-delay",
                "hypothesis": "Delay entries after open.",
                "mechanism": "Avoid opening noise.",
                "config_changes": {"ema_length": 9},
            }
        )
        + "\n"
    )
    controller.write_state(
        {
            "state": "running",
            "job": 4,
            "research_round": 1,
            "next_action": {
                "type": "run_round",
                "config": "runtime/jobs/job-4/research/round-1/selected_config.json",
                "selected_thesis_id": "ema-delay",
            },
            "_last_round_usage": {"total_tokens": 11},
        }
    )
    result_path = _write_result_manifest(
        round_root / "backtest",
        {"trade_count": 5, "profit_factor": 1.9, "max_drawdown": -0.1},
        diagnostics={"accepted": 5},
    )

    log_experiment_result(
        controller,
        config="runtime/jobs/job-4/research/round-1/selected_config.json",
        metric=1.9,
        decision="keep",
        output=f"RESULT_JSON {result_path}\n",
        analysis={
            "trade_analysis": {"verdict": {"status": "accepted", "summary": "diagnostics pass"}},
            "runtime_config": {"ema_length": 9},
            "insights": ["metric=1.9"],
            "next_candidates": [],
            "why_not_data_fit": "Independent thesis evaluation only.",
            "prediction_verdict": "supported",
            "lesson": "Prediction gap confirmed the harvest.",
        },
        artifact_dir=round_root / "backtest",
    )

    records = controller.backtest_run_db.all()
    assert len(records) == 1
    record = records[0]
    assert record.run_id == "job-4-round-1-backtest"
    assert record.thesis_id == "ema-delay"
    assert record.accepted is True
    assert record.runtime_config == {"ema_length": 9}
    assert record.usage == {"total_tokens": 11}
    assert record.prediction_verdict == "supported"
    assert record.lesson == "Prediction gap confirmed the harvest."
    assert (round_root / "backtest" / "benchmark_output.txt").read_text() == (
        f"RESULT_JSON {result_path}\n"
    )
    assert json.loads((round_root / "backtest" / "analysis.json").read_text())[
        "runtime_config"
    ] == {"ema_length": 9}


def test_baseline_checkpoint_metrics_keep_only_numeric_scalars() -> None:
    metrics = _baseline_checkpoint_metrics(
        {
            "profit_factor": 1.5,
            "enabled": True,
            "nan_metric": float("nan"),
            "train_metrics": {"max_drawdown": -0.2, "nested": {"ignored": 1}},
        }
    )

    assert metrics == {"profit_factor": 1.5, "max_drawdown": -0.2}


def test_run_experiment_executes_command_logs_result_and_reconciles_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "write_result.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                "metrics = {'trade_count': 6, 'profit_factor': 2.1, 'max_drawdown': -0.1}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "(out / 'trades.csv').write_text('entry_date,pnl_pct\\n2026-01-01,1.0\\n')",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 6}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    _write_causal_model_base_config(tmp_path)
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    round_root = tmp_path / "runtime" / "jobs" / "job-6" / "research" / "round-1"
    round_root.mkdir(parents=True)
    (round_root / "selected_config.json").write_text(json.dumps({"ema_length": 10}) + "\n")
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-success",
                "hypothesis": "Use a delayed EMA signal.",
                "mechanism": "Delay noisy entries.",
                "config_changes": {"ema_length": 10},
            }
        )
        + "\n"
    )
    state = {
        "state": "running",
        "job": 6,
        "research_round": 1,
        "selected_thesis_id": "ema-command-success",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-6/research/round-1/selected_config.json",
            "selected_thesis_id": "ema-command-success",
            "source": "research",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    records = controller.backtest_run_db.all()
    assert len(records) == 1
    assert records[0].run_id == "job-6-round-1-backtest"
    assert records[0].thesis_id == "ema-command-success"
    assert records[0].accepted is True
    persisted = controller.read_state()
    assert persisted["state"] == "blocked"
    assert persisted["next_action"]["type"] == "research"
    assert "activity" not in persisted
    assert (round_root / "backtest" / "benchmark_output.txt").exists()
    assert controller.ctx.current_contract is None
    assert controller.ctx.execution_root is None


def test_run_experiment_uses_registered_predictions_without_force_discard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    script = tmp_path / "write_result.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                "metrics = {'trade_count': 25, 'profit_factor': 2.1, 'max_drawdown': 0.1}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "(out / 'trades.csv').write_text('entry_date,pnl_pct\\n2026-01-01,1.0\\n')",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 25}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    _write_causal_model_base_config(tmp_path)
    controller.baseline_tracker.record(
        BaselineCheckpoint(
            code_commit="abc1234",
            data_hash="data",
            config_hash="config",
            metrics={"profit_factor": 2.0, "max_drawdown": 0.2, "trade_count": 25},
            timestamp="2026-05-09T00:00:00+00:00",
        )
    )
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    round_root = tmp_path / "runtime" / "jobs" / "job-6" / "research" / "round-1"
    round_root.mkdir(parents=True)
    (round_root / "selected_config.json").write_text(json.dumps({"ema_length": 10}) + "\n")
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-success",
                "strategy_family": "ema",
                "hypothesis": "Gap-down entries should improve harvest metrics.",
                "mechanism": "Gap pressure creates better mean-reversion setups.",
                "rule": "gap_pct < 0",
                "expected_effects": [
                    {"metric": "profit_factor", "direction": "increase", "threshold": 0.1}
                ],
                "disqualifiers": [
                    {
                        "name": "legacy_drawdown_limit",
                        "condition": "max_drawdown > 0.05",
                        "severity": "hard_fail",
                    }
                ],
            }
        )
        + "\n"
    )
    (round_root / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-success",
                "registered_at_utc": "2026-06-10T00:00:00+00:00",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.4},
                    {"metric": "max_drawdown", "direction": "decrease", "predicted": 0.15},
                ],
            }
        )
        + "\n"
    )
    PendingCausalModelArtifact(round_root).write(
        CausalModel(
            family="ema",
            version=1,
            factors=[
                CausalFactor(
                    factor_id="f-gap-down",
                    story="Gap-down entries should improve harvest metrics.",
                    rule="gap_pct < 0",
                    direction="win",
                )
            ],
            accuracy_history=[],
        )
    )
    state = {
        "state": "running",
        "job": 6,
        "research_round": 1,
        "selected_thesis_id": "ema-command-success",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-6/research/round-1/selected_config.json",
            "selected_thesis_id": "ema-command-success",
            "source": "research",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    record = controller.backtest_run_db.all()[0]
    harvest = json.loads((round_root / "harvest_verdict.json").read_text())
    assert record.accepted is True
    assert record.prediction_verdict == "supported"
    assert "profit_factor direction=pass" in record.lesson
    assert record.verdict_status != "inconclusive"
    assert harvest["round"] == 1
    assert harvest["status"] == "supported"
    assert harvest["registered_predictions"][0]["metric"] == "profit_factor"
    assert "gap" in harvest["registered_predictions"][0]
    assert "profit_factor direction=pass" in harvest["lesson"]
    updated_model = load_model("ema")
    assert updated_model.version == 1
    assert updated_model.factors[0].status == "harvested"
    assert "profit_factor direction=pass" in updated_model.factors[0].lesson


def test_run_experiment_discards_refuted_registered_predictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    script = _write_registered_prediction_result_script(
        tmp_path,
        {"trade_count": 20, "profit_factor": 2.1},
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    _write_causal_model_base_config(tmp_path)
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    round_root = _prepare_registered_prediction_round(tmp_path, controller)
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-refuted",
                "strategy_family": "ema",
                "hypothesis": "Gap-down entries should improve harvest metrics.",
                "mechanism": "Gap pressure creates better mean-reversion setups.",
                "rule": "gap_pct < 0",
            }
        )
        + "\n"
    )
    (round_root / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-refuted",
                "registered_at_utc": "2026-06-10T00:00:00+00:00",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.4},
                    {"metric": "trade_count", "direction": "not_worse_than", "predicted": 25},
                ],
            }
        )
        + "\n"
    )
    PendingCausalModelArtifact(round_root).write(
        CausalModel(
            family="ema",
            version=1,
            factors=[
                CausalFactor(
                    factor_id="f-gap-down",
                    story="Gap-down entries should improve harvest metrics.",
                    rule="gap_pct < 0",
                    direction="win",
                )
            ],
            accuracy_history=[],
        )
    )
    state = {
        "state": "running",
        "job": 6,
        "research_round": 1,
        "selected_thesis_id": "ema-command-refuted",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-6/research/round-1/selected_config.json",
            "selected_thesis_id": "ema-command-refuted",
            "source": "research",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    record = controller.backtest_run_db.all()[0]
    assert record.accepted is False
    assert record.prediction_verdict == "refuted"
    updated_model = load_model("ema")
    assert updated_model.factors[0].status == "refuted"


def _write_registered_prediction_result_script(tmp_path: Path, metrics: dict[str, object]) -> Path:
    script = tmp_path / "write_registered_result.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                f"metrics = {metrics!r}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "(out / 'trades.csv').write_text('entry_date,pnl_pct\\n2026-01-01,1.0\\n')",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 25}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )
    return script


def _prepare_registered_prediction_round(
    tmp_path: Path,
    controller: AutoresearchController,
    *,
    selected_config_name: str = "selected_config.json",
) -> Path:
    controller.baseline_tracker.record(
        BaselineCheckpoint(
            code_commit="abc1234",
            data_hash="data",
            config_hash="config",
            metrics={"profit_factor": 2.0, "trade_count": 25},
            timestamp="2026-05-09T00:00:00+00:00",
        )
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-6" / "research" / "round-1"
    round_root.mkdir(parents=True)
    (round_root / selected_config_name).write_text(
        json.dumps(
            {
                "ema_length": 10,
                "validation_start": "2020-01-01",
                "validation_end": "2023-12-31",
                "research_engine": {"retest_extension_months": 9},
            }
        )
        + "\n"
    )
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-inconclusive",
                "strategy_family": "ema",
                "hypothesis": "Small PF lift should be retested on extended data.",
                "mechanism": "Gap pressure creates better mean-reversion setups.",
                "rule": "gap_pct < 0",
            }
        )
        + "\n"
    )
    (round_root / "registered_predictions.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-command-inconclusive",
                "registered_at_utc": "2026-06-10T00:00:00+00:00",
                "predictions": [
                    {"metric": "profit_factor", "direction": "increase", "predicted": 2.4},
                    {"metric": "trade_count", "direction": "not_worse_than", "predicted": 25},
                ],
            }
        )
        + "\n"
    )
    return round_root


def test_run_experiment_schedules_one_extended_retest_for_registered_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    script = _write_registered_prediction_result_script(
        tmp_path,
        {"trade_count": 25, "profit_factor": 2.01},
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    _write_causal_model_base_config(tmp_path)
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    _prepare_registered_prediction_round(tmp_path, controller)
    state = {
        "state": "running",
        "job": 6,
        "research_round": 1,
        "selected_thesis_id": "ema-command-inconclusive",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-6/research/round-1/selected_config.json",
            "selected_thesis_id": "ema-command-inconclusive",
            "source": "research",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    record = controller.backtest_run_db.all()[0]
    assert record.prediction_verdict == "inconclusive"
    persisted = controller.read_state()
    retest_action = persisted["next_action"]
    assert retest_action["source"] == "registered_prediction_retest"
    assert retest_action["registered_prediction_retest"]["attempt"] == 1
    assert "_preserve_next_action_once" not in persisted
    retest_config = tmp_path / retest_action["config"]
    assert json.loads(retest_config.read_text())["validation_start"] == "2019-04-01"


def test_request_registered_prediction_retest_returns_explicit_transition_without_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    controller = _controller_for_experiment(tmp_path, "echo noop")
    _prepare_registered_prediction_round(tmp_path, controller)
    state = {
        "state": "running",
        "job": 6,
        "research_round": 1,
        "selected_thesis_id": "ema-command-inconclusive",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-6/research/round-1/selected_config.json",
            "selected_thesis_id": "ema-command-inconclusive",
            "source": "research",
        },
    }
    controller.write_state(state)

    retest_request = request_registered_prediction_retest(
        controller,
        state,
        config="runtime/jobs/job-6/research/round-1/selected_config.json",
    )

    assert controller.read_state()["next_action"]["source"] == "research"
    assert retest_request.next_state["next_action"]["source"] == "registered_prediction_retest"
    assert "_preserve_next_action_once" not in retest_request.next_state


def test_run_experiment_forces_registered_inconclusive_after_retest_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    script = _write_registered_prediction_result_script(
        tmp_path,
        {"trade_count": 25, "profit_factor": 2.01},
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    _write_causal_model_base_config(tmp_path)
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    _prepare_registered_prediction_round(
        tmp_path,
        controller,
        selected_config_name="selected_config_retest.json",
    )
    round_root = tmp_path / "runtime" / "jobs" / "job-6" / "research" / "round-1"
    PendingCausalModelArtifact(round_root).write(
        CausalModel(
            family="ema",
            version=1,
            factors=[
                CausalFactor(
                    factor_id="f-gap-down",
                    story="Small PF lift should be retested on extended data.",
                    rule="gap_pct < 0",
                    direction="win",
                )
            ],
            accuracy_history=[],
        )
    )
    state = {
        "state": "running",
        "job": 6,
        "research_round": 1,
        "selected_thesis_id": "ema-command-inconclusive",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-6/research/round-1/selected_config_retest.json",
            "selected_thesis_id": "ema-command-inconclusive",
            "source": "registered_prediction_retest",
            "registered_prediction_retest": {"attempt": 1},
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    record = controller.backtest_run_db.all()[0]
    assert record.prediction_verdict == "supported"
    assert "forced_after_retest" in record.lesson
    assert controller.read_state().get("next_action", {}).get("source") != (
        "registered_prediction_retest"
    )
    updated_model = load_model("ema")
    assert updated_model.factors[0].status == "harvested"
    assert "forced_after_retest" in updated_model.factors[0].lesson


def test_run_experiment_writes_feature_table_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_feature_table_data_universe(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    script = tmp_path / "write_result.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                "metrics = {'trade_count': 1, 'profit_factor': 0.0, 'max_drawdown': 0.02}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "trades = (",
                "  'symbol,direction,entry_date,entry_price,stop,pnl,pnl_pct,mae,mfe,exit_reason,hold_bars\\n'",
                "  'AAA,long,2024-01-02T14:35:00+00:00,100.6,99.6,-2.0,-0.02,0.03,0.01,stop,3\\n'",
                ")",
                "(out / 'trades.csv').write_text(trades)",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 1}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    round_root = tmp_path / "runtime" / "jobs" / "job-7" / "research" / "round-1"
    round_root.mkdir(parents=True)
    runtime_config = {
        "family": "ema",
        "data_universe": "tiny_feature_data",
        "symbols": ["AAA"],
        "validation_start": "2024-01-02",
        "validation_end": "2024-01-02 23:59:59",
        "ema_length": 2,
    }
    (round_root / "selected_config.json").write_text(json.dumps(runtime_config) + "\n")
    (round_root / "selected_thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": "ema-feature-table",
                "hypothesis": "Use feature table artifact.",
                "mechanism": "Record entry-time state.",
                "config_changes": {"ema_length": 2},
            }
        )
        + "\n"
    )
    state = {
        "state": "running",
        "job": 7,
        "research_round": 1,
        "selected_thesis_id": "ema-feature-table",
        "next_action": {
            "type": "run_round",
            "config": "runtime/jobs/job-7/research/round-1/selected_config.json",
            "selected_thesis_id": "ema-feature-table",
            "source": "research",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    table = load_feature_table(round_root)
    record = controller.backtest_run_db.all()[0]
    assert record.validation_metrics["feature_table_file"] == str(
        round_root / "feature_table.parquet"
    )
    assert table.loc[0, "trade_id"] == "AAA:2024-01-02T14:35:00+00:00"
    assert table.loc[0, "regime_label"] == "risk_on"


def test_run_experiment_blocks_when_command_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "fail_command.py"
    script.write_text("import sys\nprint('backtest failed')\nsys.exit(7)\n")
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script}")
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "running",
        "job": 7,
        "research_round": 0,
        "next_action": {
            "type": "run_round",
            "config": "configs/ema_base.yaml",
            "source": "baseline",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 7
    persisted = controller.read_state()
    assert persisted["state"] == "blocked"
    assert persisted["next_action"]["reason"] == "command_failed"
    assert persisted["blockers"][0]["exit_code"] == 7
    assert "activity" not in persisted


def test_run_experiment_blocks_when_metric_marker_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "no_result_marker.py"
    script.write_text("print('completed without manifest')\n")
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script}")
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "running",
        "job": 8,
        "research_round": 0,
        "next_action": {
            "type": "run_round",
            "config": "configs/ema_base.yaml",
            "source": "baseline",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 1
    persisted = controller.read_state()
    assert persisted["state"] == "blocked"
    assert persisted["next_action"]["reason"] == "metric_parse_failed"
    assert persisted["blockers"][0]["kind"] == "metric_parse_failed"
    assert "activity" not in persisted


def test_run_experiment_records_interrupted_state_when_metric_evaluator_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "write_timeout_candidate.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                "metrics = {'trade_count': 5, 'profit_factor': 1.4, 'max_drawdown': -0.3}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "(out / 'trades.csv').write_text('entry_date,pnl_pct\\n2026-01-01,0.5\\n')",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 5}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)

    def _raise_timeout(self, metric, *, job_id=None):
        raise TimeoutError("autoresearch_cli evaluate timed out")

    monkeypatch.setattr(
        "backtest_run_db.BacktestRunDB.evaluate_metric",
        _raise_timeout,
    )
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "running",
        "job": 11,
        "research_round": 0,
        "_last_round_usage": {"total_tokens": 123},
        "next_action": {
            "type": "run_round",
            "config": "configs/ema_base.yaml",
            "source": "baseline",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 1
    persisted = controller.read_state()
    assert persisted["state"] == "interrupted"
    assert persisted["next_action"]["type"] == "terminated"
    assert persisted["next_action"]["reason"] == "autoresearch_cli evaluate timed out"
    assert "autoresearch_cli evaluate timed out" in persisted["blockers"][0]["detail"]
    assert "activity" not in persisted
    assert "_last_round_usage" not in persisted
    assert "autoresearch_cli evaluate timed out" in controller.current_md_path.read_text()
    assert controller.backtest_run_db.all() == []


def test_run_experiment_records_baseline_checkpoint_and_clears_rerun_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "write_baseline_result.py"
    script.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "out = Path(sys.argv[1])",
                "out.mkdir(parents=True, exist_ok=True)",
                "metrics = {'trade_count': 8, 'profit_factor': 1.6, 'max_drawdown': -0.2}",
                "(out / 'metrics.json').write_text(json.dumps(metrics) + '\\n')",
                "(out / 'trades.csv').write_text('entry_date,pnl_pct\\n2026-01-01,1.0\\n')",
                "(out / 'diagnostics.json').write_text(json.dumps({'accepted': 8}) + '\\n')",
                "payload = {",
                "  'metrics_file': str(out / 'metrics.json'),",
                "  'trades_file': str(out / 'trades.csv'),",
                "  'diagnostics_file': str(out / 'diagnostics.json'),",
                "  'strategy_events_file': '',",
                "  'git_sha': 'abcdef1',",
                "}",
                "(out / 'result.json').write_text(json.dumps(payload) + '\\n')",
                "print('RESULT_JSON ' + str(out / 'result.json'))",
            ]
        )
        + "\n"
    )
    controller = _controller_for_experiment(tmp_path, f"{sys.executable} {script} {{output_dir}}")
    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *args, **kwargs: None)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "running",
        "job": 9,
        "research_round": 0,
        "next_action": {
            "type": "run_round",
            "config": "configs/ema_base.yaml",
            "source": "baseline",
            "baseline_rerun_for_commit": "abcdef1",
        },
    }
    controller.write_state(state)

    code = run_experiment(controller, state)

    assert code == 0
    records = controller.backtest_run_db.all()
    assert len(records) == 1
    assert records[0].is_baseline is True
    checkpoint = controller.baseline_tracker.latest()
    assert checkpoint is not None
    assert checkpoint.metrics["profit_factor"] == 1.6
    assert checkpoint.metrics["trade_count"] == 8.0
    persisted = controller.read_state()
    assert "baseline_rerun_for_commit" not in persisted["next_action"]


def test_run_experiment_returns_one_when_family_has_no_benchmark_command(tmp_path: Path) -> None:
    controller = _controller_for_experiment(tmp_path, "")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "ema_base.yaml").write_text("ema_length: 5\n")
    state = {
        "state": "running",
        "job": 10,
        "research_round": 0,
        "next_action": {
            "type": "run_round",
            "config": "configs/ema_base.yaml",
            "source": "baseline",
        },
    }
    controller.write_state(state)

    assert run_experiment(controller, state) == 1
