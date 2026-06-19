from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import feature_table_extractors as extractors
from feature_table import (
    ENTRY_TIME_COLUMNS,
    OUTCOME_COLUMNS,
    regime_feature_columns,
    FeatureTableArtifact,
    FeatureTableMissingError,
    build_feature_table,
    feature_table_path,
    load_feature_table,
    load_regime_labels,
)
from feature_table_extractors import family_entry_features

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
        "date": [pd.Timestamp("2024-01-03").date()],
        "regime_label": ["risk_off"],
    }
    for key, value in extra_columns.items():
        payload[key] = [value]
    pd.DataFrame(payload).to_parquet(data_root / "regime_labels.parquet", index=False)


def test_regime_feature_columns_discovers_every_extra_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root, volatility_regime="high_vol", trend_label="up")
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    assert regime_feature_columns() == frozenset(
        {"regime_label", "volatility_regime", "trend_label"}
    )


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
    assert row["dist_to_ema_pct"] == pytest.approx(0.9327, abs=0.0001)
    assert row["stop_distance_pct"] == pytest.approx((1.0 / 102.6) * 100.0)
    assert row["entry_bar_range_pct"] == pytest.approx((0.8 / 102.2) * 100.0)
    assert bool(row["out_is_loss"]) is True


def test_build_feature_table_reuses_orb_opening_widths_for_repeated_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = pd.concat(
        [
            _trades_df().assign(entry_date=pd.Timestamp("2024-01-04 15:05:00", tz="UTC")),
            _trades_df().assign(entry_date=pd.Timestamp("2024-01-04 15:10:00", tz="UTC")),
        ],
        ignore_index=True,
    )
    full_width_days: list[object] = []
    original = extractors._entry_day_orb_width

    def _counting_entry_day_orb_width(day_bars, entry_ts, or_minutes):
        if entry_ts is None:
            full_width_days.append(day_bars["date"].iloc[0])
        return original(day_bars, entry_ts, or_minutes)

    monkeypatch.setattr(extractors, "_entry_day_orb_width", _counting_entry_day_orb_width)

    table = build_feature_table(
        trades,
        _bars_df(),
        events=[],
        family="orb",
        runtime_config={"or_minutes": 30},
    )

    assert len(table) == 2
    assert len(full_width_days) == len(set(full_width_days))


def test_build_feature_table_does_not_use_iterrows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    def fail_iterrows(self):
        raise AssertionError("build_feature_table must not use DataFrame.iterrows")

    monkeypatch.setattr(pd.DataFrame, "iterrows", fail_iterrows)

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert len(table) == 1


def test_build_feature_table_localizes_naive_trade_times_as_new_york(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = _trades_df().assign(entry_date=pd.Timestamp("2024-01-04 09:35:00"))

    table = build_feature_table(trades, _bars_df(), events=[], family="ema")

    row = table.iloc[0]
    assert row["trade_id"] == "AAA:2024-01-04T14:35:00+00:00"
    assert row["entry_ts"] == pd.Timestamp("2024-01-04 14:35:00", tz="UTC")
    assert row["time_of_day_min"] == 5
    assert row["bars_since_open"] == 1


def test_build_feature_table_rejects_missing_trade_pnl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = _trades_df().drop(columns=["pnl", "pnl_pct"])

    with pytest.raises(ValueError, match="missing finite pnl"):
        build_feature_table(trades, _bars_df(), events=[], family="ema")


def _many_trades(count: int) -> pd.DataFrame:
    base = _trades_df()
    frames = [
        base.assign(
            entry_date=pd.Timestamp("2024-01-04 14:35:00", tz="UTC") + pd.Timedelta(minutes=i)
        )
        for i in range(count)
    ]
    return pd.concat(frames, ignore_index=True)


def test_build_feature_table_quarantines_sparse_nonfinite_pnl_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = _many_trades(150)
    trades.loc[7, "pnl"] = float("nan")  # one malformed row out of 150 (<1%)
    quarantine_path = tmp_path / "round" / "feature_table_quarantine.json"

    with caplog.at_level(logging.WARNING):
        table = build_feature_table(
            trades,
            _bars_df(),
            events=[],
            family="ema",
            quarantine_path=quarantine_path,
        )

    assert len(table) == 149
    assert quarantine_path.exists()
    quarantined = json.loads(quarantine_path.read_text())
    assert len(quarantined) == 1
    assert "quarantined 1/150" in caplog.text


def test_build_feature_table_stops_when_quarantine_exceeds_one_percent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = _many_trades(10)
    trades.loc[3, "pnl"] = float("nan")  # 10% malformed: stop and surface

    with pytest.raises(ValueError, match="missing finite pnl"):
        build_feature_table(trades, _bars_df(), events=[], family="ema")


def test_build_feature_table_uses_orb_event_stop_price_when_trade_stop_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = _trades_df().drop(columns=["stop"])
    events = [
        {
            "timestamp": pd.Timestamp("2024-01-04 14:35:00", tz="UTC"),
            "symbol": "AAA",
            "event_type": "executed_trade",
            "stop_price": 101.6,
        }
    ]

    table = build_feature_table(trades, _bars_df(), events=events, family="orb")

    assert table.iloc[0]["stop_distance_pct"] == pytest.approx((1.0 / 102.6) * 100.0)


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


def test_load_regime_labels_uses_default_data_root_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AUTORESEARCH_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    data_root = tmp_path / "autoresearch-data"
    _write_regime_labels(data_root)

    labels = load_regime_labels()

    assert labels["regime_label"].tolist() == ["risk_off"]


def test_load_regime_labels_strips_padded_data_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", f"  {data_root}  ")

    labels = load_regime_labels()

    assert labels["regime_label"].tolist() == ["risk_off"]


def test_load_regime_labels_treats_blank_data_root_env_as_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "   ")
    monkeypatch.setenv("HOME", str(tmp_path))
    data_root = tmp_path / "autoresearch-data"
    _write_regime_labels(data_root)

    labels = load_regime_labels()

    assert labels["regime_label"].tolist() == ["risk_off"]


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


def test_build_feature_table_lags_regime_labels_to_completed_prior_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2024-01-03").date(),
                pd.Timestamp("2024-01-04").date(),
            ],
            "regime_label": ["prior_completed", "same_day_poison"],
            "volatility_regime": ["prior_vol", "same_day_poison"],
        }
    ).to_parquet(data_root / "regime_labels.parquet", index=False)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert table.loc[0, "regime_label"] == "prior_completed"
    assert table.loc[0, "volatility_regime"] == "prior_vol"


def test_build_feature_table_uses_latest_prior_regime_from_unsorted_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2024-01-04").date(),
                pd.Timestamp("2024-01-02").date(),
                pd.Timestamp("2024-01-03").date(),
            ],
            "regime_label": ["same_day_poison", "older_prior", "latest_prior"],
        }
    ).to_parquet(data_root / "regime_labels.parquet", index=False)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert table.loc[0, "regime_label"] == "latest_prior"


def test_build_feature_table_uses_last_duplicate_latest_prior_regime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2024-01-03").date(),
                pd.Timestamp("2024-01-02").date(),
                pd.Timestamp("2024-01-03").date(),
            ],
            "regime_label": ["first_duplicate", "older_prior", "last_duplicate"],
        }
    ).to_parquet(data_root / "regime_labels.parquet", index=False)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert table.loc[0, "regime_label"] == "last_duplicate"


def test_build_feature_table_preserves_numeric_missing_extra_regime_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-04").date()],
            "regime_label": ["risk_on"],
            "volatility_score": [0.73],
        }
    ).to_parquet(data_root / "regime_labels.parquet", index=False)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(_trades_df(), _bars_df(), events=[], family="ema")

    assert pd.isna(table.loc[0, "volatility_score"])
    assert pd.api.types.is_numeric_dtype(table["volatility_score"])


def test_build_feature_table_handles_nan_hold_bars_and_nan_entry_bar_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    trades = _trades_df()
    trades.loc[0, "hold_bars"] = float("nan")
    bars = _bars_df()
    entry_bar = bars["timestamp"] == pd.Timestamp("2024-01-04 14:35:00", tz="UTC")
    bars.loc[entry_bar, "close"] = float("nan")

    table = build_feature_table(trades, bars, events=[], family="ema")

    assert table.loc[0, "out_hold_bars"] == -1
    assert table.loc[0, "entry_bar_range_pct"] == pytest.approx((0.8 / 102.2) * 100.0)


def test_build_feature_table_uses_runtime_ema_length_for_distance_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    table = build_feature_table(
        _trades_df(),
        _bars_df(),
        events=[],
        family="ema",
        runtime_config={"ema_length": 2},
    )

    closes = _bars_df()[_bars_df()["timestamp"] < pd.Timestamp("2024-01-04 14:35:00", tz="UTC")][
        "close"
    ].astype(float)
    ema = closes.ewm(span=2, adjust=False).mean().iloc[-1]
    assert table.loc[0, "dist_to_ema_pct"] == pytest.approx((102.6 - ema) / 102.6 * 100.0)


def test_family_specific_feature_ownership_lives_in_extractor_registry() -> None:
    bars = _bars_df()
    prior_bars = bars[bars["timestamp"] < pd.Timestamp("2024-01-04 14:35:00", tz="UTC")]

    ema_features = family_entry_features(
        "ema",
        symbol_bars=bars,
        prior_bars=prior_bars,
        entry_ts=pd.Timestamp("2024-01-04 14:35:00", tz="UTC"),
        entry_price=102.6,
        runtime_config={"ema_length": 2},
    )
    unknown_features = family_entry_features(
        "unknown",
        symbol_bars=bars,
        prior_bars=prior_bars,
        entry_ts=pd.Timestamp("2024-01-04 14:35:00", tz="UTC"),
        entry_price=102.6,
        runtime_config={},
    )

    assert ema_features["dist_to_ema_pct"] == pytest.approx(
        (102.6 - prior_bars["close"].astype(float).ewm(span=2, adjust=False).mean().iloc[-1])
        / 102.6
        * 100.0
    )
    assert pd.isna(unknown_features["dist_to_ema_pct"])
    assert pd.isna(unknown_features["or_width_pctile"])


def test_build_feature_table_uses_runtime_or_minutes_for_orb_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    rows = []
    for day, first_width, second_width in [
        ("2024-01-02", 10.0, 100.0),
        ("2024-01-03", 10.0, 100.0),
        ("2024-01-04", 2.0, 100.0),
    ]:
        for minute, width in [(0, first_width), (5, second_width), (390, 1.0)]:
            ts = pd.Timestamp(f"{day} 14:30:00", tz="UTC") + pd.Timedelta(minutes=minute)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": "AAA",
                    "open": 100.0,
                    "high": 100.0 + width,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000,
                }
            )
    bars = pd.DataFrame(rows)

    table = build_feature_table(
        _trades_df(),
        bars,
        events=[],
        family="orb",
        runtime_config={"or_minutes": 5},
    )

    assert table.loc[0, "or_width_pctile"] == 0.0


def test_build_feature_table_orb_width_ignores_current_day_post_entry_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    rows = []
    for day, widths in [
        ("2024-01-02", [(0, 10.0), (5, 10.0), (25, 10.0)]),
        ("2024-01-03", [(0, 10.0), (5, 10.0), (25, 10.0)]),
        ("2024-01-04", [(0, 2.0), (5, 2.0), (25, 100.0)]),
    ]:
        for minute, width in widths:
            ts = pd.Timestamp(f"{day} 14:30:00", tz="UTC") + pd.Timedelta(minutes=minute)
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": "AAA",
                    "open": 100.0,
                    "high": 100.0 + width,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000,
                }
            )
    bars = pd.DataFrame(rows)

    table = build_feature_table(
        _trades_df(),
        bars,
        events=[],
        family="orb",
        runtime_config={"or_minutes": 30},
    )

    assert table.loc[0, "or_width_pctile"] == 0.0


def test_build_feature_table_localizes_naive_bars_as_new_york_to_match_naive_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    bars = _bars_df()
    naive_bars = bars.drop(columns=["timestamp"]).assign(
        timestamp=bars["timestamp"].dt.tz_convert("America/New_York").dt.tz_localize(None)
    )
    trades = _trades_df().assign(entry_date=pd.Timestamp("2024-01-04 09:35:00"))

    table = build_feature_table(trades, naive_bars, events=[], family="ema")

    assert table.loc[0, "entry_ts"] == pd.Timestamp("2024-01-04 14:35:00", tz="UTC")
    assert table.loc[0, "bars_since_open"] == 1
    assert table.loc[0, "entry_bar_range_pct"] == pytest.approx((0.8 / 102.2) * 100.0)


def test_build_feature_table_uses_naive_market_time_bars_for_orb_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    rows = []
    for day, width in [
        ("2024-01-02", 10.0),
        ("2024-01-03", 10.0),
        ("2024-01-04", 2.0),
    ]:
        for minute in [0, 5]:
            rows.append(
                {
                    "timestamp": pd.Timestamp(f"{day} 09:30:00") + pd.Timedelta(minutes=minute),
                    "symbol": "AAA",
                    "open": 100.0,
                    "high": 100.0 + width,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000,
                }
            )
    bars = pd.DataFrame(rows)
    trades = _trades_df().assign(entry_date=pd.Timestamp("2024-01-04 09:35:00"))

    table = build_feature_table(
        trades,
        bars,
        events=[],
        family="orb",
        runtime_config={"or_minutes": 5},
    )

    assert table.loc[0, "or_width_pctile"] == 0.0


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


def test_build_feature_table_ignores_entry_stamped_bar_poisoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    _write_regime_labels(data_root)
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    bars = _bars_df()
    poisoned = bars.copy()
    entry_bar = poisoned["timestamp"] == pd.Timestamp("2024-01-04 14:35:00", tz="UTC")
    poisoned.loc[entry_bar, ["high", "low", "close", "volume"]] = [9999.0, 1.0, 5000.0, 99]

    clean_table = build_feature_table(_trades_df(), bars, events=[], family="ema")
    poisoned_table = build_feature_table(_trades_df(), poisoned, events=[], family="ema")

    pd.testing.assert_frame_equal(clean_table, poisoned_table)


def test_feature_table_path_and_load_round_trip(tmp_path: Path) -> None:
    table = pd.DataFrame({"trade_id": ["AAA:2024-01-04T14:35:00+00:00"]})
    path = feature_table_path(tmp_path)
    table.to_parquet(path, index=False)

    assert path == tmp_path / "feature_table.parquet"
    pd.testing.assert_frame_equal(load_feature_table(tmp_path), table)


def test_latest_through_falls_back_to_baseline_when_round_has_no_table(tmp_path: Path) -> None:
    """A research round that proposes no actionable change runs no experiment
    and writes no feature table; the next round's screening must fall back to
    the most recent realized round (baseline), not assume round N-1 exists.
    Regression for round-2 FileNotFoundError on round-1/feature_table.parquet."""
    job = 4
    table = pd.DataFrame({"trade_id": ["AAA:2024-01-04T14:35:00+00:00"]})
    FeatureTableArtifact.for_round(tmp_path, job, 0).write(table)  # baseline only
    # Round 1 wrote nothing; screening at round 2 looks back through round 1.
    resolved = FeatureTableArtifact.latest_through(tmp_path, job, 1)
    assert resolved.round_root == FeatureTableArtifact.for_round(tmp_path, job, 0).round_root
    pd.testing.assert_frame_equal(resolved.load(), table)


def test_latest_through_prefers_most_recent_realized_round(tmp_path: Path) -> None:
    job = 4
    baseline = pd.DataFrame({"trade_id": ["AAA:2024-01-04T14:35:00+00:00"]})
    round2 = pd.DataFrame({"trade_id": ["BBB:2024-02-04T14:35:00+00:00"]})
    FeatureTableArtifact.for_round(tmp_path, job, 0).write(baseline)
    FeatureTableArtifact.for_round(tmp_path, job, 2).write(round2)
    resolved = FeatureTableArtifact.latest_through(tmp_path, job, 2)
    pd.testing.assert_frame_equal(resolved.load(), round2)


def test_load_missing_feature_table_raises_actionable_error(tmp_path: Path) -> None:
    artifact = FeatureTableArtifact.for_round(tmp_path, job=4, research_round=1)
    with pytest.raises(FeatureTableMissingError):
        artifact.load()
    # backward-compatible: existing `except FileNotFoundError` handlers still catch it
    assert issubclass(FeatureTableMissingError, FileNotFoundError)


def test_latest_through_raises_when_no_round_has_a_table(tmp_path: Path) -> None:
    with pytest.raises(FeatureTableMissingError):
        FeatureTableArtifact.latest_through(tmp_path, job=4, research_round=3)
