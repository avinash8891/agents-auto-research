"""Unit tests for autoresearch_experiment parsers and helpers.

Project rule G: behavioral assertions on quantitative outputs (trade counts,
profit factors, byte-exact filenames). Real RESULT_JSON fixture in
tests/fixtures/result_v1.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch_experiment import (
    ResultJsonError,
    _build_db_record,
    _compute_run_output_dir,
    _evaluate_against_thesis,
    artifact_dir_for,
    evaluate_metric,
    parse_benchmark_details,
    parse_benchmark_details_legacy,
    parse_metric,
    parse_result_json,
    primary_metric_name,
    sanitize_duplicate_entries,
)
from experiment_db import ExperimentDB

# ── parse_result_json ────────────────────────────────────────────


def test_parse_result_json_returns_none_when_no_marker_in_output() -> None:
    assert parse_result_json("nothing useful here") is None


def test_parse_result_json_returns_none_when_referenced_file_missing(tmp_path: Path) -> None:
    output = f"RESULT_JSON {tmp_path / 'no-such.json'}\n"
    with pytest.raises(ResultJsonError, match="does not exist"):
        parse_result_json(output)


def test_parse_result_json_loads_real_fixture(fixtures_dir: Path) -> None:
    path = fixtures_dir / "result_v1.json"
    output = f"some preamble\nRESULT_JSON {path}\n"
    payload = parse_result_json(output)
    assert payload is not None
    assert payload["metrics"]["trade_count"] == 287
    assert payload["git_sha"] == "b96e64e"


def test_parse_result_json_handles_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid")
    with pytest.raises(ResultJsonError, match="malformed JSON"):
        parse_result_json(f"RESULT_JSON {bad}\n")


def test_read_thesis_metadata_handles_malformed_sidecar_json(tmp_path) -> None:
    from types import SimpleNamespace

    from autoresearch_experiment import _read_thesis_metadata

    config = "experiments/bad/runtime_config.json"
    controller = SimpleNamespace(root=tmp_path, ctx=SimpleNamespace(current_contract=None))
    thesis_json = controller.root / "experiments" / "bad" / "thesis.json"
    thesis_json.parent.mkdir(parents=True, exist_ok=True)
    thesis_json.write_text("{not valid json")

    with pytest.raises(ValueError, match="THESIS_METADATA_MALFORMED"):
        _read_thesis_metadata(controller, config)


# ── parse_benchmark_details (RESULT_JSON path) ──────────────────


def test_parse_benchmark_details_extracts_metrics_from_real_fixture(fixtures_dir: Path) -> None:
    path = fixtures_dir / "result_v1.json"
    details = parse_benchmark_details(f"RESULT_JSON {path}\n")
    # Behavioral assertion: every documented metric copies through with the
    # exact value from the fixture.
    assert details["trade_count"] == 287
    assert details["profit_factor"] == 1.61
    assert details["max_drawdown"] == 0.184
    assert details["win_rate"] == 0.541
    assert details["pct_profitable_windows"] == 0.62
    assert details["avg_sharpe_across_windows"] == 0.91
    # Auxiliary fields propagate verbatim.
    assert details["trades_file"] == "trades.csv"
    assert details["strategy_events_file"] == "strategy_events.parquet"
    assert details["diagnostics_file"] == "diagnostics.json"
    assert details["strategy_diagnostics"] == {
        "exits_at_target": 102,
        "exits_at_stop": 78,
        "exits_at_close": 107,
    }
    assert details["git_sha"] == "b96e64e"
    assert details["config_hash"] == "ab12cd34ef56"


def test_parse_benchmark_details_falls_back_to_legacy_when_no_result_json() -> None:
    output = "METRIC trade_count=42\nMETRIC profit_factor=1.4\nMETRIC max_drawdown=0.1\n"
    details = parse_benchmark_details(output)
    assert details["trade_count"] == 42
    assert details["profit_factor"] == 1.4
    assert details["max_drawdown"] == 0.1


def test_parse_benchmark_details_preserves_12_char_config_hash() -> None:
    output = json.dumps(
        {
            "family": "ema",
            "config": "configs/ema_base.yaml",
            "config_hash": "123456789abc",
            "git_sha": "abcdef0",
            "timestamp": "2026-04-30T00:00:00+00:00",
            "metrics": {"median_expectancy": 1.5, "trade_count": 42},
            "diagnostics": {},
            "strategy_diagnostics": {},
            "trades_file": "",
            "strategy_events_file": "",
            "diagnostics_file": "",
        }
    )

    details = parse_benchmark_details(output)

    assert details["config_hash"] == "123456789abc"


def test_compute_run_output_dir_for_missing_config_uses_12_char_hash_slug(tmp_path: Path) -> None:
    controller = SimpleNamespace(
        root=tmp_path,
        runs_dir=tmp_path / "runs",
        read_state=lambda: {"job": 3},
    )

    run_dir, config_path_full = _compute_run_output_dir(controller, "configs/missing.yaml")

    assert config_path_full == tmp_path / "configs/missing.yaml"
    assert run_dir.parent.name == "job-3"
    assert len(run_dir.name) == 12


# ── parse_benchmark_details_legacy ──────────────────────────────


def test_parse_benchmark_details_legacy_parses_all_metric_lines() -> None:
    output = (
        "METRIC trade_count=287\n"
        "METRIC profit_factor=1.61\n"
        "METRIC max_drawdown=0.184\n"
        "METRIC pct_profitable_windows=0.62\n"
        "METRIC avg_sharpe_across_windows=0.91\n"
        "METRIC win_rate=0.541\n"
    )
    details = parse_benchmark_details_legacy(output)
    assert details["trade_count"] == 287
    assert details["profit_factor"] == 1.61
    assert details["max_drawdown"] == 0.184
    assert details["pct_profitable_windows"] == 0.62
    assert details["avg_sharpe_across_windows"] == 0.91
    assert details["win_rate"] == 0.541


def test_parse_benchmark_details_legacy_parses_diagnostics_block() -> None:
    output = 'DIAGNOSTICS {"exits_at_target": 50}\n'
    details = parse_benchmark_details_legacy(output)
    assert details["diagnostics"] == {"exits_at_target": 50}


def test_parse_benchmark_details_legacy_parses_trades_file_line() -> None:
    output = "TRADES_FILE /runs/job-1/abc/trades.csv\n"
    details = parse_benchmark_details_legacy(output)
    assert details["trades_file"] == "/runs/job-1/abc/trades.csv"


# ── parse_metric ────────────────────────────────────────────────


def test_parse_metric_reads_named_metric_from_result_json(fixtures_dir: Path) -> None:
    path = fixtures_dir / "result_v1.json"
    output = f"RESULT_JSON {path}\n"
    assert parse_metric(output, name="median_expectancy") == 1.42
    assert parse_metric(output, name="profit_factor") == 1.61


def test_parse_metric_returns_none_when_metric_missing(fixtures_dir: Path) -> None:
    path = fixtures_dir / "result_v1.json"
    output = f"RESULT_JSON {path}\n"
    # The fixture does not include "calmar" — should return None, not raise.
    assert parse_metric(output, name="calmar") is None


def test_parse_metric_legacy_path() -> None:
    output = "METRIC median_expectancy=1.42\n"
    assert parse_metric(output, name="median_expectancy") == 1.42


def test_parse_metric_returns_none_when_no_signal() -> None:
    assert parse_metric("just stdout junk\n", name="median_expectancy") is None


# ── primary_metric_name ─────────────────────────────────────────


def test_primary_metric_name_default_when_no_config_header() -> None:
    assert primary_metric_name([]) == "median_expectancy"


def test_primary_metric_name_reads_from_config_header() -> None:
    entries = [
        {"type": "config", "metricName": "calmar"},
        {"run": 1, "metric": 1.0, "status": "keep"},
    ]
    assert primary_metric_name(entries) == "calmar"


# ── artifact_dir_for ────────────────────────────────────────────


def test_artifact_dir_for_uses_job_number_from_state(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"job": 7}))
    runs_dir = tmp_path / "ema_autoresearch-runs"
    out = artifact_dir_for(state_path, runs_dir, "configs/variants/ema_aggressive.yaml")
    assert out == runs_dir.resolve() / "job-7" / "ema_aggressive"
    assert out.exists()


def test_artifact_dir_for_defaults_job_to_zero(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({}))  # no "job" key
    runs_dir = tmp_path / "runs"
    out = artifact_dir_for(state_path, runs_dir, "configs/ema_base.yaml")
    assert out == runs_dir.resolve() / "job-0" / "ema_base"


# ── sanitize_duplicate_entries ──────────────────────────────────


def test_sanitize_drops_low_information_loop_duplicate(tmp_path: Path) -> None:
    db = ExperimentDB(tmp_path / "experiments.db")
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")
    config = "configs/variants/ema_aggressive.yaml"
    rich = {
        "run": 1,
        "run_id": "rich",
        "metric": 1.42,
        "status": "keep",
        "description": "strict-native loop: ema_aggressive",
        "asi": {"config": config, "trade_analysis": {"trade_count": 287}},
    }
    low_info = {
        "run": 2,
        "run_id": "low",
        "metric": 1.42,
        "status": "keep",
        "description": "loop: ema_aggressive",
        "asi": {"config": config},
    }
    db.import_entries([rich, low_info])

    sanitize_duplicate_entries(db, config)
    remaining = [entry for entry in db.export_entries() if entry.get("type") != "config"]
    assert len(remaining) == 1
    assert remaining[0]["description"] == "strict-native loop: ema_aggressive"


def test_sanitize_preserves_config_header(tmp_path: Path) -> None:
    db = ExperimentDB(tmp_path / "experiments.db")
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")
    real = {
        "run": 1,
        "run_id": "real",
        "metric": 1.0,
        "status": "keep",
        "description": "strict-native loop: ema_base",
        "asi": {"config": "configs/ema_base.yaml"},
    }
    db.import_entries([real])
    sanitize_duplicate_entries(db, "configs/ema_base.yaml")
    out = db.export_entries()
    assert out[0]["type"] == "config"
    assert out[0]["metricName"] == "median_expectancy"
    assert out[1]["status"] == "keep"


def test_sanitize_does_not_mutate_private_db_cache(tmp_path: Path) -> None:
    db = ExperimentDB(tmp_path / "experiments.db")
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")
    config = "configs/variants/ema_aggressive.yaml"
    rich = {
        "run": 1,
        "run_id": "rich",
        "metric": 1.42,
        "status": "keep",
        "description": "strict-native loop: ema_aggressive",
        "asi": {"config": config, "trade_analysis": {"trade_count": 287}},
    }
    low_info = {
        "run": 2,
        "run_id": "low",
        "metric": 1.42,
        "status": "keep",
        "description": "loop: ema_aggressive",
        "asi": {"config": config},
    }
    db.import_entries([rich, low_info])

    original_records = list(db._records or [])
    sanitize_duplicate_entries(db, config)

    assert db._records is not None
    assert len(db._records) == 1
    assert db._records[0].experiment_id == "rich"
    assert len(original_records) == 2


def test_evaluate_effect_returns_none_when_metric_is_missing() -> None:
    from experiment_evaluator import evaluate_effect
    from research_types import ExpectedEffect

    effect = ExpectedEffect(metric="profit_factor", direction="increase")

    assert evaluate_effect(effect, {"profit_factor": 1.0}, {}) is None


def test_evaluate_metric_uses_sqlite_experiment_db(tmp_path: Path) -> None:
    db = ExperimentDB(tmp_path / "experiments.db")
    db.init_session(name="ema", metric_name="median_expectancy", direction="higher")
    db.add_from_sqlite_fields(
        experiment_id="e1",
        thesis_id="t1",
        config_path="configs/ema_base.yaml",
        runtime_config={},
        code_commit="abc123",
        data_hash="data1",
        metrics={"median_expectancy": 1.0},
        trade_analysis={},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="none",
        verdict_summary="",
        family="ema",
        job_id=1,
        run_id="run-1",
        primary_metric_name="median_expectancy",
        primary_metric_value=1.0,
    )

    decision = evaluate_metric(tmp_path, db.path.name, 1.1)

    assert decision == "keep"


def test_build_db_record_populates_expected_runtime_fields(tmp_path: Path) -> None:
    class _Family:
        name = "ema"

    class _Controller:
        family = _Family()
        root = tmp_path
        ctx = type("Ctx", (), {})()

        def current_commit(self) -> str:
            return "abc1234"

    controller = _Controller()
    controller.ctx.current_contract = None
    controller.ctx.parent_experiment_id = "parent-exp"
    controller.ctx.latest_config_contents = {"ema_length": 5}
    controller.ctx.latest_strategy_events_file = str(tmp_path / "strategy_events.parquet")
    controller.ctx.latest_diagnostics_file = str(tmp_path / "diagnostics.json")
    controller.ctx.latest_trades_file = str(tmp_path / "trades.csv")

    record = _build_db_record(
        controller,
        config="configs/ema_base.yaml",
        decision="keep",
        details={
            "trade_count": 2,
            "profit_factor": 1.1,
            "trades_file": controller.ctx.latest_trades_file,
            "strategy_events_file": controller.ctx.latest_strategy_events_file,
            "diagnostics_file": controller.ctx.latest_diagnostics_file,
            "strategy_diagnostics": {"exits_at_target": 1},
        },
        analysis={"trade_analysis": {"verdict": {"status": "accepted", "summary": "ok"}}},
        fallback_experiment_id="run-1",
        state={"job": 3, "_last_round_usage": {"tokens": 17}},
    )

    assert record.runtime_config == {"ema_length": 5}
    assert record.trades_file == str(tmp_path / "trades.csv")
    assert record.strategy_events_file == str(tmp_path / "strategy_events.parquet")
    assert record.diagnostics_file == str(tmp_path / "diagnostics.json")
    assert record.strategy_diagnostics == {"exits_at_target": 1}
    assert record.parent_experiment_id == "parent-exp"


def test_build_asi_and_entry_use_contract_identity_over_runtime_config_stem(tmp_path: Path) -> None:
    from autoresearch_experiment import _build_asi_dict, _build_export_entry

    class _Controller:
        root = tmp_path
        ctx = type("Ctx", (), {})()

        def read_state(self):
            return {"job": 1}

        def current_commit(self) -> str:
            return "abc1234"

    controller = _Controller()
    controller.ctx.current_contract = type(
        "Contract",
        (),
        {
            "thesis_id": "ema5",
            "experiment_id": "ema5",
            "hypothesis": "ema 5 should react faster",
        },
    )()

    artifact_dir = tmp_path / "ema_autoresearch-runs" / "job-1" / "c2821b0a43ba"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    analysis = {
        "trade_analysis": {},
        "insights": [],
        "next_candidates": [],
        "why_not_data_fit": "",
    }

    asi = _build_asi_dict(
        controller,
        config="experiments/ema5/runtime_config.json",
        artifact_dir=artifact_dir,
        analysis=analysis,
        thesis_id="ema5",
        config_changes={},
    )
    entry = _build_export_entry(
        controller,
        config="experiments/ema5/runtime_config.json",
        metric=1.0,
        decision="keep",
        details={"trade_count": 2},
        asi=asi,
        next_run=1,
        state={"job": 1},
    )

    assert asi["hypothesis"] == "ema5"
    assert asi["artifact_dir"] == "ema_autoresearch-runs/job-1/c2821b0a43ba"
    assert entry["description"] == "strict-native loop: ema5"


def test_build_asi_uses_real_artifact_hash_directory_for_contract_run(tmp_path: Path) -> None:
    from autoresearch_experiment import _build_asi_dict

    class _Controller:
        root = tmp_path
        ctx = type("Ctx", (), {})()

        def read_state(self):
            return {"job": 1}

    controller = _Controller()
    controller.ctx.current_contract = type(
        "Contract",
        (),
        {
            "thesis_id": "ema5",
            "experiment_id": "ema5",
        },
    )()
    artifact_dir = tmp_path / "ema_autoresearch-runs" / "job-1" / "c2821b0a43ba"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    asi = _build_asi_dict(
        controller,
        config="experiments/ema5/runtime_config.json",
        artifact_dir=artifact_dir,
        analysis={},
        thesis_id="ema5",
        config_changes={},
    )

    assert asi["artifact_dir"] == "ema_autoresearch-runs/job-1/c2821b0a43ba"


def test_build_asi_and_entry_use_deterministic_ids_without_trace_context(tmp_path: Path) -> None:
    from autoresearch_experiment import _build_asi_dict, _build_export_entry

    class _Controller:
        root = tmp_path
        ctx = type("Ctx", (), {})()

        def read_state(self):
            return {"job": 7}

        def current_commit(self) -> str:
            return "abc1234"

    controller = _Controller()
    controller.ctx.current_contract = type(
        "Contract",
        (),
        {
            "thesis_id": "ema5",
            "experiment_id": "ema5",
        },
    )()
    artifact_dir = tmp_path / "ema_autoresearch-runs" / "job-7" / "ema5"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    asi = _build_asi_dict(
        controller,
        config="experiments/ema5/runtime_config.json",
        artifact_dir=artifact_dir,
        analysis={},
        thesis_id="ema5",
        config_changes={},
    )
    entry = _build_export_entry(
        controller,
        config="experiments/ema5/runtime_config.json",
        metric=1.0,
        decision="keep",
        details={"trade_count": 2},
        asi=asi,
        next_run=1,
        state={"job": 7},
    )

    assert asi["run_id"] == "job-7-run-1-ema5"
    assert asi["hypothesis_id"] == "ema5"
    assert entry["run_id"] == "job-7-run-1-ema5"
    assert entry["hypothesis_id"] == "ema5"


def test_artifact_dir_for_uses_contract_identity_for_runtime_config_paths(tmp_path: Path) -> None:
    from autoresearch_experiment import artifact_dir_for

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"job": 1}))
    runs_dir = tmp_path / "ema_autoresearch-runs"

    out = artifact_dir_for(state_path, runs_dir, "experiments/ema5/runtime_config.json")

    assert out == runs_dir.resolve() / "job-1" / "ema5"


def test_build_db_record_populates_train_metrics_and_keep_verdict_defaults(tmp_path: Path) -> None:
    class _Family:
        name = "ema"

    class _Controller:
        family = _Family()
        root = tmp_path
        ctx = type("Ctx", (), {})()

        def current_commit(self) -> str:
            return "abc1234"

    controller = _Controller()
    controller.ctx.current_contract = None
    controller.ctx.parent_experiment_id = ""
    controller.ctx.latest_config_contents = {"ema_length": 5}

    details = {
        "trade_count": 2,
        "profit_factor": 1.1,
        "max_drawdown": 0.1,
        "train_metrics": {"trade_count": 2, "profit_factor": 1.1},
        "strategy_diagnostics": {"exits_at_target": 1},
    }
    record = _build_db_record(
        controller,
        config="configs/ema_base.yaml",
        decision="keep",
        details=details,
        analysis={"trade_analysis": {}},
        fallback_experiment_id="run-1",
        state={"job": 3, "_last_round_usage": {"tokens": 17}},
    )

    assert record.train_metrics == {"trade_count": 2, "profit_factor": 1.1}
    assert record.verdict_status == "accepted"
    assert record.verdict_summary == "kept by primary metric improvement"
    assert record.rejection_reason == "N/A"


def test_evaluate_against_thesis_raises_deterministic_evaluator_errors(monkeypatch) -> None:
    class _Contract:
        thesis_id = "t1"
        strategy_family = "ema"
        hypothesis = "h"
        mechanism = "m"
        expected_effects = [{"metric": "median_expectancy", "direction": "increase"}]
        disqualifiers = []
        required_diagnostics = []
        experiment_id = "exp-1"

    class _Controller:
        def read_results(self):
            return []

        root = Path(".")

    def fake_evaluate_experiment(**kwargs):
        raise KeyError("missing diagnostics")

    monkeypatch.setattr("experiment_evaluator.evaluate_experiment", fake_evaluate_experiment)

    with pytest.raises(KeyError):
        _evaluate_against_thesis(
            _Controller(),
            _Contract(),
            1.0,
            "keep",
            {"trade_count": 1},
        )


def test_write_run_artifacts_leaves_no_tmp_artifacts(tmp_path: Path) -> None:
    from autoresearch_experiment import _write_run_artifacts

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    _write_run_artifacts(artifact_dir, "benchmark ok\n", {"metric": 1.0})

    assert (artifact_dir / "benchmark_output.txt").exists()
    assert (artifact_dir / "analysis.json").exists()
    assert not list(artifact_dir.rglob("*.tmp"))
