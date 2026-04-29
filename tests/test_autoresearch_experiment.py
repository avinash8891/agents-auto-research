"""Unit tests for autoresearch_experiment parsers and helpers.

Project rule G: behavioral assertions on quantitative outputs (trade counts,
profit factors, byte-exact filenames). Real RESULT_JSON fixture in
tests/fixtures/result_v1.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch_experiment import (
    artifact_dir_for,
    parse_benchmark_details,
    parse_benchmark_details_legacy,
    parse_metric,
    parse_result_json,
    primary_metric_name,
    sanitize_duplicate_entries,
)

# ── parse_result_json ────────────────────────────────────────────


def test_parse_result_json_returns_none_when_no_marker_in_output() -> None:
    assert parse_result_json("nothing useful here") is None


def test_parse_result_json_returns_none_when_referenced_file_missing(tmp_path: Path) -> None:
    output = f"RESULT_JSON {tmp_path / 'no-such.json'}\n"
    assert parse_result_json(output) is None


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
    assert parse_result_json(f"RESULT_JSON {bad}\n") is None


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
    jsonl = tmp_path / "log.jsonl"
    config = "configs/variants/ema_aggressive.yaml"
    rich = {
        "run": 1,
        "metric": 1.42,
        "status": "keep",
        "description": "strict-native loop: ema_aggressive",
        "asi": {"config": config, "trade_analysis": {"trade_count": 287}},
    }
    low_info = {
        "run": 2,
        "metric": 1.42,
        "status": "keep",
        "description": "loop: ema_aggressive",
        "asi": {"config": config},
    }
    jsonl.write_text(json.dumps(rich) + "\n" + json.dumps(low_info) + "\n")

    sanitize_duplicate_entries(jsonl, config)
    remaining = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    assert len(remaining) == 1
    assert remaining[0]["description"] == "strict-native loop: ema_aggressive"


def test_sanitize_preserves_config_header(tmp_path: Path) -> None:
    jsonl = tmp_path / "log.jsonl"
    header = {"type": "config", "metricName": "median_expectancy"}
    real = {
        "run": 1,
        "metric": 1.0,
        "status": "keep",
        "description": "strict-native loop: ema_base",
        "asi": {"config": "configs/ema_base.yaml"},
    }
    jsonl.write_text(json.dumps(header) + "\n" + json.dumps(real) + "\n")
    sanitize_duplicate_entries(jsonl, "configs/ema_base.yaml")
    out = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
    assert out[0] == header
    assert out[1]["status"] == "keep"
