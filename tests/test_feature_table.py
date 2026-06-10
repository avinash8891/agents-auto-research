from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from feature_table import (
    ENTRY_TIME_COLUMNS,
    OUTCOME_COLUMNS,
    build_feature_table,
    feature_table_path,
    load_feature_table,
    load_regime_labels,
)

EXPECTED_COLUMNS = [
    "trade_id",
    "symbol",
    "side",
    "entry_ts",
    "time_of_day_min",
    "day_of_week",
    "bars_since_open",
    "gap_pct",
    "prior_day_range_pct",
    "overnight_move_pct",
    "or_width_pctile",
    "dist_to_ema_pct",
    "vol_pctile_20d",
    "regime_label",
    "stop_distance_pct",
    "entry_bar_range_pct",
    "out_pnl",
    "out_pnl_pct",
    "out_mae",
    "out_mfe",
    "out_exit_reason",
    "out_hold_bars",
    "out_is_loss",
]


def _bars_df() -> pd.DataFrame:
    rows = []
    for day, base in [
        ("2024-01-02", 100.0),
        ("2024-01-03", 101.0),
        ("2024-01-04", 102.0),
    ]:
        for minute, close in [(0, base + 0.2), (5, base + 0.6), (390, base + 1.0)]:
            ts = pd.Timestamp(f"{day} 14:30:00", tz="UTC") + pd.Timedelta(minutes=minute)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": "AAA",
                    "open": base,
                    "high": close + 0.4,
                    "low": close - 0.4,
                    "close": close,
                    "volume": 1000 + minute,
                }
            )
    return pd.DataFrame(rows)


def _trades_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "direction": "long",
                "entry_date": pd.Timestamp("2024-01-04 14:35:00", tz="UTC"),
                "entry_price": 102.6,
                "stop": 101.6,
                "pnl": -2.0,
                "pnl_pct": -0.02,
                "mae": 0.03,
                "mfe": 0.01,
                "exit_reason": "stop",
                "hold_bars": 3,
            }
        ]
    )


def _write_regime_labels(data_root: Path, **extra_columns: object) -> None:
    data_root.mkdir()
    payload = {
        "date": [pd.Timestamp("2024-01-04").date()],
        "regime_label": ["risk_off"],
    }
    for key, value in extra_columns.items():
        payload[key] = [value]
    pd.DataFrame(payload).to_parquet(data_root / "regime_labels.parquet", index=False)


def test_build_feature_table_emits_exact_entry_time_and_outcome_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert list(table.columns) == EXPECTED_COLUMNS
    assert set(table.columns) == ENTRY_TIME_COLUMNS | OUTCOME_COLUMNS
    assert ENTRY_TIME_COLUMNS.isdisjoint(OUTCOME_COLUMNS)
    row = table.iloc[0]
    assert row["trade_id"] == "AAA:2024-01-04T14:35:00+00:00"
    assert row["entry_ts"] == pd.Timestamp("2024-01-04 14:35:00", tz="UTC")
    assert row["time_of_day_min"] == 5
    assert row["day_of_week"] == 3
    assert row["bars_since_open"] == 1
    assert row["side"] == "long"
    assert row["regime_label"] == "risk_off"
    assert pd.isna(row["or_width_pctile"])
    assert row["dist_to_ema_pct"] == pytest.approx(0.6218, abs=0.0001)
    assert row["stop_distance_pct"] == pytest.approx((1.0 / 102.6) * 100.0)
    assert row["entry_bar_range_pct"] == pytest.approx((0.8 / 102.6) * 100.0)
    assert bool(row["out_is_loss"]) is True


def test_build_feature_table_writes_deterministic_parquet_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    first = tmp_path / "first.parquet"
    second = tmp_path / "second.parquet"

    build_feature_table(_trades_df(), _bars_df(), events=[], family="ema").to_parquet(
        first, index=False
    )
    build_feature_table(_trades_df(), _bars_df(), events=[], family="ema").to_parquet(
        second, index=False
    )

    assert first.read_bytes() == second.read_bytes()


def test_build_feature_table_requires_external_regime_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    expected = data_root / "regime_labels.parquet"

    with pytest.raises(FileNotFoundError, match=str(expected)):
        build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")


def test_build_feature_table_joins_extra_regime_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root, volatility_regime="high_vol")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert table.loc[0, "volatility_regime"] == "high_vol"
    assert "volatility_regime" not in OUTCOME_COLUMNS
    assert list(load_regime_labels().columns) == ["date", "regime_label", "volatility_regime"]


def test_build_feature_table_ignores_post_entry_bar_poisoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    bars = _bars_df()
    poisoned = bars.copy()
    post_entry = poisoned["timestamp"] > pd.Timestamp("2024-01-04 14:35:00", tz="UTC")
    poisoned.loc[post_entry, ["high", "low", "close", "volume"]] = [9999.0, 1.0, 5000.0, 99]

    clean_table = build_feature_table(_trades_df(), bars, events=[], family="ema")
    poisoned_table = build_feature_table(_trades_df(), poisoned, events=[], family="ema")

    pd.testing.assert_frame_equal(clean_table, poisoned_table)


def test_feature_table_path_and_load_round_trip(tmp_path: Path) -> None:
    table = pd.DataFrame({"trade_id": ["AAA:2024-01-04T14:35:00+00:00"]})
    path = feature_table_path(tmp_path)
    table.to_parquet(path, index=False)

    assert path == tmp_path / "feature_table.parquet"
    pd.testing.assert_frame_equal(load_feature_table(tmp_path), table)
