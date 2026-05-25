from __future__ import annotations

import json
import os
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

from backtest.data_universe import load_universe_data
from backtest.event_diagnostics import build_event_diagnostics, write_event_diagnostics
from backtest.filters import _exclude_signals_on_days, _filter_signals_to_days
from backtest.runtime_config import load_runtime_config, validate_runtime_config_scope
from config_hash import _config_hash
from strategies import STRATEGIES
from strategies.ema.contract import compile_ema_contract, map_ema_config_changes_to_contract
from strategies.ema.signals import generate_signals_for_frame
from strategies.ema.strategy import _log_raw_setups
from strategies.ema.validate import validate_ema_runtime_config
from strategy_event_logger import StrategyEventLogger

FIXTURES = REPO_ROOT / "tests" / "fixtures"
TINY_CONFIG = FIXTURES / "tiny_ema_runtime.json"


@pytest.fixture(autouse=True)
def tiny_data_universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "autoresearch-data"
    _write_wide_dataset(data_root / "universes" / "tiny_ema_data", ["AAA", "BBB"])
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))


def _tiny_config() -> dict:
    return json.loads(TINY_CONFIG.read_text())


def _write_wide_dataset(universe_path: Path, symbols: list[str]) -> None:
    universe_path.mkdir(parents=True)
    index = pd.to_datetime(["2024-01-02 09:30", "2024-01-02 09:35"]).tz_localize("US/Eastern")
    for name, value in {
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1000,
    }.items():
        pd.DataFrame({symbol: [value, value] for symbol in symbols}, index=index).to_parquet(
            universe_path / f"{name}.parquet"
        )
    (universe_path / "manifest.json").write_text(
        json.dumps(
            {
                "data_universe": universe_path.name,
                "symbol_count": len(symbols),
                "symbols": symbols,
                "start": "2024-01-02",
                "end": "2024-01-02",
            }
        )
        + "\n"
    )


def test_ema_strategy_run_returns_event_logger_keys() -> None:
    result = STRATEGIES["ema"].run(_tiny_config())

    assert "_trades_df" not in result
    assert result["trade_count"] == 0
    assert "_event_logger" in result
    assert build_event_diagnostics(
        trade_count=result["trade_count"],
        event_frame=result["_event_logger"].to_dataframe(),
        event_counts=result["_event_logger"].event_counts_snapshot(),
        rejection_breakdown=result["_event_logger"].rejection_breakdown_snapshot(),
    ) == build_event_diagnostics(
        trade_count=0,
        event_frame=pd.DataFrame(),
        event_counts={},
        rejection_breakdown={},
    )


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

    written = write_event_diagnostics(
        diagnostics_path,
        trade_count=result["trade_count"],
        event_frame=result["_event_logger"].to_dataframe(),
        event_counts=result["_event_logger"].event_counts_snapshot(),
        rejection_breakdown=result["_event_logger"].rejection_breakdown_snapshot(),
    )

    assert written == build_event_diagnostics(
        trade_count=0,
        event_frame=pd.DataFrame(),
        event_counts={},
        rejection_breakdown={},
    )
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
    assert "diagnostics" not in payload
    assert "strategy_diagnostics" not in payload
    assert payload["metrics_file"] == str(tmp_path / "metrics.json")
    assert payload["diagnostics_file"] == str(tmp_path / "diagnostics.json")
    assert json.loads((tmp_path / "metrics.json").read_text()) == {
        "median_expectancy": 0.0,
        "trade_count": 0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "pct_profitable_windows": 0.0,
        "avg_sharpe_across_windows": 0.0,
        "diagnostics": {},
        "exit_reason_counts": {},
    }
    assert json.loads((tmp_path / "diagnostics.json").read_text()) == build_event_diagnostics(
        trade_count=0,
        event_frame=pd.DataFrame(),
        event_counts={},
        rejection_breakdown={},
    )
    assert payload["trades_file"] == ""
    assert payload["strategy_events_file"] == str(tmp_path / "strategy_events.parquet")


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


def test_ema_raw_setup_events_emit_standardized_event_columns() -> None:
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
    logger = StrategyEventLogger()

    _log_raw_setups(logger, frame, signals, "AAA", "long")
    events = logger.to_dataframe()

    assert list(events.columns[:11]) == [
        "timestamp",
        "symbol",
        "direction",
        "event_type",
        "reason",
        "entry_price",
        "stop_price",
        "stop_distance_pct",
        "trigger_bar_timestamp",
        "entry_bar_index_from_open",
        "ema.alert_bar_timestamp",
    ]
    assert len(events) == 1
    row = events.iloc[0]
    assert row["timestamp"] == pd.Timestamp("2024-01-02 09:45")
    assert row["symbol"] == "AAA"
    assert row["direction"] == "long"
    assert row["event_type"] == "raw_setup"
    assert row["entry_price"] == 9.1
    assert row["stop_price"] == 8.9
    assert row["stop_distance_pct"] == pytest.approx((0.2 / 9.1) * 100.0)
    assert row["trigger_bar_timestamp"] == pd.Timestamp("2024-01-02 09:45")
    assert row["entry_bar_index_from_open"] == 3.0
    assert row["ema.alert_bar_timestamp"] == pd.Timestamp("2024-01-02 09:40")


def test_event_diagnostics_derive_generic_cohorts_from_standardized_schema() -> None:
    event_frame = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2024-01-02 09:35"),
                "symbol": "AAA",
                "direction": "long",
                "event_type": "accepted_signal",
                "reason": "",
                "entry_price": 100.0,
                "stop_price": 99.0,
                "stop_distance_pct": 1.0,
                "trigger_bar_timestamp": pd.Timestamp("2024-01-02 09:35"),
                "entry_bar_index_from_open": 1.0,
                "ema.alert_bar_timestamp": pd.Timestamp("2024-01-02 09:30"),
                "ema.alert_close_minus_ema": -0.12,
            },
            {
                "timestamp": pd.Timestamp("2024-01-02 09:40"),
                "symbol": "AAA",
                "direction": "long",
                "event_type": "rejected_signal",
                "reason": "max_stop_distance",
                "entry_price": 100.0,
                "stop_price": 98.0,
                "stop_distance_pct": 2.0,
                "trigger_bar_timestamp": pd.Timestamp("2024-01-02 09:40"),
                "entry_bar_index_from_open": 2.0,
                "ema.alert_bar_timestamp": pd.Timestamp("2024-01-02 09:35"),
                "ema.alert_close_minus_ema": 0.08,
            },
        ]
    )

    diagnostics = build_event_diagnostics(trade_count=1, event_frame=event_frame)

    assert diagnostics["event_counts"] == {"accepted_signal": 1, "rejected_signal": 1}
    assert diagnostics["rejection_breakdown"] == {"max_stop_distance": 1}
    assert diagnostics["event_schema_metadata"] == {
        "event_schema_version": "1.0",
        "core_fields_present": ["timestamp", "symbol", "direction", "event_type", "reason"],
        "shared_optional_fields_present": [
            "entry_price",
            "stop_price",
            "stop_distance_pct",
            "trigger_bar_timestamp",
            "entry_bar_index_from_open",
        ],
        "strategy_specific_fields_present": [
            "ema.alert_bar_timestamp",
            "ema.alert_close_minus_ema",
        ],
    }
    assert diagnostics["standardized_event_schema"]["stop_distance_pct"] is True
    assert diagnostics["event_counts_by_trigger_bucket"] == {
        "accepted_signal": {"09:35": 1},
        "rejected_signal": {"09:40": 1},
    }
    assert diagnostics["event_counts_by_entry_bar_index_from_open"] == {
        "accepted_signal": {"1": 1},
        "rejected_signal": {"2": 1},
    }
    assert diagnostics["stop_distance_pct_summary_by_event_type"] == {
        "accepted_signal": {"count": 1, "min": 1.0, "median": 1.0, "max": 1.0},
        "rejected_signal": {"count": 1, "min": 2.0, "median": 2.0, "max": 2.0},
    }


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
    assert "config_changes_passthrough" not in rendered_keys
    assert {"type": "max_hold_bars", "value": 7} in contract
    assert {"type": "trail_after_r", "value": 1.5} in contract
    assert {"type": "symbols", "value": ["SPY"]} in contract
    assert {"type": "validation_start", "value": "2024-01-01"} in contract
    assert {"type": "validation_end", "value": "2024-12-31"} in contract


def test_map_ema_config_changes_to_contract_classifies_unknown_keys_as_missing() -> None:
    contract = map_ema_config_changes_to_contract(
        {
            "vwap_side_gate_enabled": True,
            "vwap_side_consecutive_closes_required": 2,
        }
    )

    assert {
        "type": "missing_primitive",
        "key": "vwap_side_consecutive_closes_required",
        "value": 2,
    } in contract
    assert {"type": "missing_primitive", "key": "vwap_side_gate_enabled", "value": True} in contract
    assert "config_changes_passthrough" not in {primitive["type"] for primitive in contract}


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


def test_validate_ema_runtime_config_rejects_non_positive_max_hold_bars() -> None:
    violations = validate_ema_runtime_config({"max_hold_bars": -72})
    assert any("max_hold_bars=-72" in violation for violation in violations)


def test_validate_ema_runtime_config_explains_zero_trade_cap_as_invalid_not_zero_trades() -> None:
    violations = validate_ema_runtime_config({"max_trades_per_day": 0})
    assert any("max_trades_per_day=0" in violation for violation in violations)
    assert not any("disables trading" in violation for violation in violations)


def test_load_runtime_config_accepts_runtime_config_wrapper(tmp_path: Path) -> None:
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"runtime_config": _tiny_config()}) + "\n")

    loaded = load_runtime_config(str(wrapped), "ema")

    assert loaded["ema_length"] == _tiny_config()["ema_length"]


def test_load_runtime_config_resolves_explicit_data_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "autoresearch-data"
    _write_wide_dataset(data_root / "universes" / "nasdaq2", ["SPY", "AAPL"])
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    config = _tiny_config()
    config["data_universe"] = "nasdaq2"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    loaded = load_runtime_config(str(path), "ema")

    assert loaded["data_universe"] == "nasdaq2"
    assert loaded["data_provenance"] == {
        "data_universe": "nasdaq2",
        "universe_path": str(data_root / "universes" / "nasdaq2"),
        "symbol_count": 2,
        "symbols_hash": "28f6daa9e0fb",
        "start": "2024-01-02",
        "end": "2024-01-02",
        "manifest_path": str(data_root / "universes" / "nasdaq2" / "manifest.json"),
    }


def test_load_runtime_config_rejects_missing_data_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "autoresearch-data"
    (data_root / "universes" / "nasdaq2").mkdir(parents=True)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    config = _tiny_config()
    config["data_universe"] = "nasdaq_missing"
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    loaded = load_runtime_config(str(path), "ema")

    with pytest.raises(
        FileNotFoundError,
        match=(
            "Data universe 'nasdaq_missing' not found.*AUTORESEARCH_DATA_ROOT="
            f"{re.escape(str(data_root))}.*Available universes: nasdaq2"
        ),
    ):
        load_universe_data(loaded)


def test_load_runtime_config_rejects_path_based_market_data_selector(tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy-data"
    _write_wide_dataset(legacy_path, ["SPY"])
    config = _tiny_config()
    config["data" + "_" + "dir"] = str(legacy_path)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    with pytest.raises(
        ValueError,
        match="Path-based market data selectors are not supported.*data_universe",
    ):
        load_runtime_config(str(path), "ema")


def test_load_universe_data_rejects_path_traversal_names() -> None:
    with pytest.raises(ValueError, match="must be a simple universe name"):
        load_universe_data({"data_universe": "../escape"})

    with pytest.raises(ValueError, match="must be a simple universe name"):
        load_universe_data({"data_universe": "nested/universe"})


def test_load_universe_data_rejects_boolean_universe_names() -> None:
    with pytest.raises(ValueError, match="non-empty string or integer universe name"):
        load_universe_data({"data_universe": True})

    with pytest.raises(ValueError, match="non-empty string or integer universe name"):
        load_universe_data({"data_universe": False})


def test_result_json_includes_data_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "autoresearch-data"
    _write_wide_dataset(data_root / "universes" / "nasdaq2", ["AAA", "BBB"])
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    config = _tiny_config()
    config["data_universe"] = "nasdaq2"
    config_path = tmp_path / "runtime.json"
    config_path.write_text(json.dumps({"runtime_config": config}) + "\n")

    subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "backtest" / "runner.py"),
            "--strategy",
            "ema",
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        env={**os.environ, "AUTORESEARCH_DATA_ROOT": str(data_root)},
        capture_output=True,
        text=True,
        check=True,
    )

    payload = json.loads((tmp_path / "out" / "result.json").read_text())

    assert payload["data_provenance"]["data_universe"] == "nasdaq2"
    assert payload["data_provenance"]["symbol_count"] == 2


def test_load_runtime_config_resolves_data_universe_for_orb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "autoresearch-data"
    _write_wide_dataset(data_root / "universes" / "nasdaq8", ["SPY"])
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    config = {
        "data_universe": "nasdaq8",
        "symbols": None,
        "validation_start": "2024-01-01",
        "validation_end": "2024-01-02",
        "or_minutes": 30,
        "timeframe_minutes": 5,
        "rr_ratio": 2.0,
    }
    path = tmp_path / "orb.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    loaded = load_runtime_config(str(path), "orb")

    assert loaded["data_provenance"]["data_universe"] == "nasdaq8"


def test_load_runtime_config_contract_payload_merges_orb_defaults_for_data_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "autoresearch-data"
    _write_wide_dataset(data_root / "universes" / "nasdaq143", ["SPY"])
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    contract = [{"type": "opening_range", "or_minutes": 15}]
    path = tmp_path / "orb-contract.json"
    path.write_text(json.dumps(contract) + "\n")

    loaded = load_runtime_config(str(path), "orb")

    assert loaded["or_minutes"] == 15
    assert loaded["timeframe_minutes"] == 5
    assert loaded["data_provenance"]["data_universe"] == "nasdaq143"


def test_config_hash_for_data_universe_ignores_machine_specific_paths() -> None:
    left = {
        "ema_length": 5,
        "data_universe": "nasdaq8",
        "data_provenance": {
            "data_universe": "nasdaq8",
            "universe_path": "/root/autoresearch-data/universes/nasdaq8",
            "manifest_path": "/root/autoresearch-data/universes/nasdaq8/manifest.json",
            "symbol_count": 8,
            "symbols_hash": "abc123",
        },
    }
    right = {
        "ema_length": 5,
        "data_universe": "nasdaq8",
        "data_provenance": {
            "data_universe": "nasdaq8",
            "universe_path": "/Users/me/autoresearch-data/universes/nasdaq8",
            "manifest_path": "/Users/me/autoresearch-data/universes/nasdaq8/manifest.json",
            "symbol_count": 8,
            "symbols_hash": "abc123",
        },
    }

    assert _config_hash(left) == _config_hash(right)


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
    if family in {"ema", "orb"}:
        config = {"data_universe": "tiny_ema_data", **config}
    path = tmp_path / f"{family}.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    with pytest.raises(ValueError, match=f"Config validation failed.*{re.escape(expected)}"):
        load_runtime_config(str(path), family)


def test_load_runtime_config_rejects_unknown_ema_runtime_keys(tmp_path: Path) -> None:
    config = {
        **_tiny_config(),
        "vwap_side_gate_enabled": True,
        "vwap_side_consecutive_closes_required": 2,
    }
    path = tmp_path / "ema.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    with pytest.raises(
        ValueError,
        match="Config validation failed.*Unsupported EMA runtime config keys: "
        "vwap_side_consecutive_closes_required, vwap_side_gate_enabled",
    ):
        load_runtime_config(str(path), "ema")


def test_load_runtime_config_rejects_unimplemented_ema_feature_gates(tmp_path: Path) -> None:
    config = {
        **_tiny_config(),
        "opening_info_intensity_gate_enabled": True,
    }
    path = tmp_path / "ema.json"
    path.write_text(json.dumps({"runtime_config": config}) + "\n")

    with pytest.raises(
        ValueError,
        match="Config validation failed.*opening_info_intensity_gate_enabled=true is not implemented",
    ):
        load_runtime_config(str(path), "ema")


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
            "range_shift_lookback='20': must be an integer",
        ),
        (
            "orb",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "or_minutes": "30",
            },
            "or_minutes='30': must be an integer",
        ),
        (
            "orb",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "rr_ratio": True,
            },
            "rr_ratio=True: must be numeric",
        ),
        (
            "ema",
            {
                "validation_start": "2024-01-01",
                "validation_end": "2024-01-02",
                "gap_pct": True,
            },
            "gap_pct=True: must be numeric",
        ),
    ],
)
def test_load_runtime_config_rejects_malformed_numeric_types(
    tmp_path: Path, family: str, config: dict, expected: str
) -> None:
    if family in {"ema", "orb"}:
        config = {"data_universe": "tiny_ema_data", **config}
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
    assert payload["metrics_file"] == str(tmp_path / "metrics.json")
    assert "metrics" not in payload
    assert "strategy_diagnostics" not in payload
    metrics_payload = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics_payload["trade_count"] == 0
    assert metrics_payload["profit_factor"] == 0.0
    assert metrics_payload["diagnostics"] == {}
    assert metrics_payload["exit_reason_counts"] == {}
    assert json.loads((tmp_path / "diagnostics.json").read_text()) == build_event_diagnostics(
        trade_count=0,
        event_frame=pd.DataFrame(),
        event_counts={},
        rejection_breakdown={},
    )
