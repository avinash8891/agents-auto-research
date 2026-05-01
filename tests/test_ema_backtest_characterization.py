from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backtest.filters import _exclude_signals_on_days, _filter_signals_to_days
from backtest.runtime_config import load_runtime_config, validate_runtime_config_scope
from strategies import STRATEGIES
from strategies.ema.contract import compile_ema_contract, map_ema_config_changes_to_contract
from strategies.ema.signals import generate_signals_for_frame
from strategies.ema.validate import validate_ema_runtime_config

FIXTURES = REPO_ROOT / "tests" / "fixtures"
TINY_CONFIG = FIXTURES / "tiny_ema_runtime.json"


def _tiny_config() -> dict:
    return json.loads(TINY_CONFIG.read_text())


def test_ema_strategy_run_returns_event_logger_keys() -> None:
    result = STRATEGIES["ema"].run(_tiny_config())

    assert "_trades_df" not in result
    assert result["trade_count"] == 0
    assert "_event_logger" in result
    assert result["_event_logger"].build_diagnostics(result["trade_count"]) == {
        "trade_count": 0,
        "event_counts": {},
        "rejection_breakdown": {},
    }


def test_ema_strategy_does_not_write_strategy_events_when_no_events_are_generated(
    tmp_path: Path,
) -> None:
    result = STRATEGIES["ema"].run(_tiny_config())

    events_path = tmp_path / "strategy_events.parquet"
    result["_event_logger"].write_parquet(events_path)

    assert not events_path.exists()
    assert result["_event_logger"].to_dataframe().empty


def test_ema_strategy_writes_diagnostics_json_with_event_counts(tmp_path: Path) -> None:
    result = STRATEGIES["ema"].run(_tiny_config())
    diagnostics_path = tmp_path / "diagnostics.json"

    written = result["_event_logger"].write_diagnostics(diagnostics_path, result["trade_count"])

    assert written == {
        "trade_count": 0,
        "event_counts": {},
        "rejection_breakdown": {},
    }
    assert json.loads(diagnostics_path.read_text()) == written


def test_main_emits_result_json_marker_on_stdout(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "backtest_5ema.py"),
            "--config",
            str(TINY_CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"RESULT_JSON {tmp_path / 'result.json'}" in proc.stdout


def test_main_writes_result_json_with_full_schema(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "backtest_5ema.py"),
            "--config",
            str(TINY_CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads((tmp_path / "result.json").read_text())

    assert payload["family"] == "ema"
    assert payload["config"] == str(TINY_CONFIG)
    assert isinstance(payload["config_hash"], str) and len(payload["config_hash"]) == 12
    assert isinstance(payload["git_sha"], str) and payload["git_sha"]
    assert isinstance(payload["timestamp"], str) and payload["timestamp"]
    assert payload["metrics"] == {
        "median_expectancy": 0.0,
        "trade_count": 0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "pct_profitable_windows": 0.0,
        "avg_sharpe_across_windows": 0.0,
    }
    assert payload["diagnostics"] == {}
    assert payload["strategy_diagnostics"] == {
        "trade_count": 0,
        "event_counts": {},
        "rejection_breakdown": {},
    }
    assert payload["trades_file"] == ""
    assert payload["strategy_events_file"] == str(tmp_path / "strategy_events.parquet")
    assert payload["diagnostics_file"] == str(tmp_path / "diagnostics.json")


def test_generic_runner_emits_result_json_marker_on_stdout(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "backtest" / "runner.py"),
            "--strategy",
            "ema",
            "--config",
            str(TINY_CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"RESULT_JSON {tmp_path / 'result.json'}" in proc.stdout


def test_generic_runner_writes_ema_family_result_schema(tmp_path: Path) -> None:
    subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "backtest" / "runner.py"),
            "--strategy",
            "ema",
            "--config",
            str(TINY_CONFIG),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads((tmp_path / "result.json").read_text())

    assert payload["family"] == "ema"
    assert payload["config"] == str(TINY_CONFIG)


def test_validate_runtime_config_scope_refuses_unbounded() -> None:
    with pytest.raises(ValueError, match="Refusing unbounded EMA backtest"):
        validate_runtime_config_scope(
            {"ema_length": 5}, source_path=Path("x.json"), strategy_name="ema"
        )


def test_validate_runtime_config_scope_accepts_bounded() -> None:
    config = {"validation_start": "2024-01-01", "validation_end": "2024-01-02", "ema_length": 5}
    assert validate_runtime_config_scope(config.copy(), strategy_name="ema") == config


def test_exclude_signals_on_days_has_no_post_return_effect() -> None:
    idx = pd.to_datetime(["2024-01-02 09:30", "2024-01-03 09:30", "2024-01-04 09:30"])
    frame = pd.DataFrame(index=idx)

    class Signals:
        def __init__(self) -> None:
            self.entries = pd.Series([True, True, False], index=idx)
            self.entry_price = pd.Series([1.0, 2.0, np.nan], index=idx)
            self.stop_price = pd.Series([0.5, 1.5, np.nan], index=idx)
            self.alert_bar_idx = pd.Series([10, 11, -1], index=idx)

    signals = Signals()
    out = _exclude_signals_on_days(signals, frame, {idx[0].date()})

    assert out.entries.tolist() == [False, True, False]
    assert np.isnan(out.entry_price.iloc[0])
    assert np.isnan(out.stop_price.iloc[0])
    assert out.alert_bar_idx.tolist() == [10, 11, -1]


def test_generate_signals_alert_bar_idx_is_recomputed_via_arange() -> None:
    idx = pd.to_datetime(["2024-01-02 09:30", "2024-01-02 09:35", "2024-01-02 09:40"])
    frame = pd.DataFrame(
        {
            "open": [10.0, 9.0, 8.5],
            "high": [10.5, 9.2, 9.6],
            "low": [9.8, 8.8, 8.7],
            "close": [10.0, 9.0, 9.4],
        },
        index=idx,
    )

    signals = generate_signals_for_frame(frame, "long", ema_length=2)

    assert signals.entries.tolist() == [False, False, True]
    assert signals.alert_bar_idx.tolist() == [-1, -1, 1]
    assert np.isnan(signals.entry_price.iloc[0])
    assert np.isnan(signals.entry_price.iloc[1])
    assert signals.entry_price.iloc[2] == 9.2
    assert np.isnan(signals.stop_price.iloc[0])
    assert np.isnan(signals.stop_price.iloc[1])
    assert signals.stop_price.iloc[2] == 8.8


def test_generate_signals_preserves_carried_alert_bar_idx() -> None:
    idx = pd.to_datetime(
        ["2024-01-02 09:30", "2024-01-02 09:35", "2024-01-02 09:40", "2024-01-02 09:45"]
    )
    frame = pd.DataFrame(
        {
            "open": [10.0, 9.0, 9.0, 9.2],
            "high": [10.5, 9.2, 9.1, 9.3],
            "low": [9.8, 8.8, 8.9, 8.7],
            "close": [10.0, 9.0, 9.0, 9.0],
        },
        index=idx,
    )

    signals = generate_signals_for_frame(frame, "long", ema_length=2)

    assert signals.entries.tolist() == [False, False, False, True]
    assert signals.alert_bar_idx.tolist() == [-1, -1, -1, 2]
    assert signals.entry_price.iloc[3] == 9.1
    assert signals.stop_price.iloc[3] == 8.9


def test_map_ema_config_changes_to_contract_keeps_filter_primitives() -> None:
    contract = map_ema_config_changes_to_contract(
        {
            "gap_filter": True,
            "gap_pct": 0.02,
            "use_range_shift": True,
            "range_shift_lookback": 30,
        }
    )

    assert {"type": "gap_filter", "enabled": True, "gap_pct": 0.02} in contract
    assert {"type": "range_shift", "enabled": True, "lookback": 30} in contract


def test_map_ema_config_changes_to_contract_preserves_supported_defaults_surface() -> None:
    contract = map_ema_config_changes_to_contract(
        {
            "max_hold_bars": 7,
            "trail_after_r": 1.5,
            "symbols": ["SPY"],
            "validation_start": "2024-01-01",
            "validation_end": "2024-12-31",
        }
    )

    rendered_keys = {primitive["type"] for primitive in contract}
    assert "config_changes_passthrough" in rendered_keys


def test_compile_ema_contract_returns_ready_to_run_for_valid_contract() -> None:
    result = compile_ema_contract(
        [
            {"type": "ema_length", "value": 5},
            {"type": "timeframe_long", "minutes": 15},
            {"type": "timeframe_short", "minutes": 5},
            {"type": "risk_reward", "rr_ratio": 3.0},
        ]
    )

    assert result.status == "ready_to_run"
    assert result.runtime_config["ema_length"] == 5
    assert result.runtime_config["timeframe_long"] == 15
    assert result.runtime_config["timeframe_short"] == 5
    assert result.runtime_config["rr_ratio"] == 3.0


def test_validate_ema_runtime_config_rejects_negative_ema_length() -> None:
    violations = validate_ema_runtime_config({"ema_length": -5})
    assert any("ema_length=-5" in violation for violation in violations)


def test_load_runtime_config_accepts_runtime_config_wrapper(tmp_path: Path) -> None:
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"runtime_config": _tiny_config()}) + "\n")

    loaded = load_runtime_config(str(wrapped), "ema")

    assert loaded["ema_length"] == _tiny_config()["ema_length"]


@pytest.mark.parametrize(
    ("family", "config", "expected"),
    [
        (
            "ema",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "ema_length": 1,
            },
            "ema_length=1: must be >= 2",
        ),
        (
            "orb",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "or_minutes": 2,
                "timeframe_minutes": 5,
            },
            "or_minutes=2 out of range [5, 120]",
        ),
    ],
)
def test_load_runtime_config_rejects_invalid_runtime_config(
    tmp_path: Path, family: str, config: dict, expected: str
) -> None:
    path = tmp_path / f"{family}.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    with pytest.raises(ValueError, match=f"Config validation failed.*{re.escape(expected)}"):
        load_runtime_config(str(path), family)


@pytest.mark.parametrize(
    ("family", "config", "expected"),
    [
        (
            "ema",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "range_shift_lookback": "20",
            },
            "range_shift_lookback='20': must be numeric",
        ),
        (
            "orb",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "or_minutes": "30",
            },
            "or_minutes='30': must be numeric",
        ),
    ],
)
def test_load_runtime_config_rejects_malformed_numeric_types(
    tmp_path: Path, family: str, config: dict, expected: str
) -> None:
    path = tmp_path / f"{family}.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    with pytest.raises(ValueError, match=f"Config validation failed.*{re.escape(expected)}"):
        load_runtime_config(str(path), family)


def test_filter_signals_to_days_clears_rejected_metadata() -> None:
    idx = pd.to_datetime(["2024-01-02 09:30", "2024-01-03 09:30", "2024-01-04 09:30"])
    frame = pd.DataFrame(index=idx)

    class Signals:
        def __init__(self) -> None:
            self.entries = pd.Series([True, True, True], index=idx)
            self.entry_price = pd.Series([1.0, 2.0, 3.0], index=idx)
            self.stop_price = pd.Series([0.5, 1.5, 2.5], index=idx)
            self.alert_bar_idx = pd.Series([10, 11, 12], index=idx)

    signals = Signals()
    out = _filter_signals_to_days(signals, frame, {idx[1].date()})

    assert out.entries.tolist() == [False, True, False]
    assert np.isnan(out.entry_price.iloc[0])
    assert out.entry_price.iloc[1] == 2.0
    assert np.isnan(out.entry_price.iloc[2])
    assert np.isnan(out.stop_price.iloc[0])
    assert out.stop_price.iloc[1] == 1.5
    assert np.isnan(out.stop_price.iloc[2])
    assert out.alert_bar_idx.tolist() == [-1, 11, -1]


def test_demo_strategy_runner_succeeds(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "backtest" / "runner.py"),
            "--strategy",
            "_demo",
            "--config",
            str(REPO_ROOT / "tests" / "fixtures" / "demo_runtime.json"),
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert f"RESULT_JSON {tmp_path / 'result.json'}" in proc.stdout
    payload = json.loads((tmp_path / "result.json").read_text())
    assert payload["family"] == "_demo"
    assert payload["metrics"]["trade_count"] == 0
    assert payload["strategy_diagnostics"] == {
        "trade_count": 0,
        "event_counts": {},
        "rejection_breakdown": {},
    }
