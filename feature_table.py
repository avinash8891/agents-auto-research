from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autoresearch_logging import get_logger
from autoresearch_runtime_paths import research_round_root
from backtest.data_universe import default_data_root
from feature_table_extractors import family_entry_features

log = get_logger(__name__)

# Rule I: bad external trade rows are quarantined row-by-row; above this
# fraction the data is too corrupt to trust and the build stops loudly.
_QUARANTINE_MAX_FRACTION = 0.01

# Ordered source of truth for the feature-table schema. Outcome columns are the
# "out_"-prefixed ones; entry-time columns are the rest. The two frozensets below
# are derived so a new column is added in exactly one place.
_FEATURE_COLUMNS = [
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

OUTCOME_COLUMNS = frozenset(c for c in _FEATURE_COLUMNS if c.startswith("out_"))
ENTRY_TIME_COLUMNS = frozenset(_FEATURE_COLUMNS) - OUTCOME_COLUMNS


@dataclass(frozen=True)
class FeatureTableArtifact:
    """Canonical feature-table artifact for one research round."""

    round_root: Path

    @classmethod
    def for_round(cls, runtime_root: Path, job: int, research_round: int) -> "FeatureTableArtifact":
        return cls(research_round_root(runtime_root, job, research_round))

    @property
    def path(self) -> Path:
        return self.round_root / "feature_table.parquet"

    def load(self) -> pd.DataFrame:
        return pd.read_parquet(self.path)

    def write(self, table: pd.DataFrame) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(self.path, index=False)


@dataclass(frozen=True)
class SymbolBars:
    """One symbol's bars plus precomputed arrays for O(log n) per-trade lookups."""

    bars: pd.DataFrame
    daily: pd.DataFrame
    timestamps: pd.Series
    bar_dates: np.ndarray
    daily_dates: np.ndarray
    daily_open: np.ndarray
    daily_high: np.ndarray
    daily_low: np.ndarray
    daily_close: np.ndarray
    daily_range_pct: np.ndarray

    @classmethod
    def from_bars(cls, bars: pd.DataFrame) -> "SymbolBars":
        daily = _daily_bars(bars)
        daily_open = pd.to_numeric(daily.get("open"), errors="coerce").to_numpy(dtype=float)
        daily_high = pd.to_numeric(daily.get("high"), errors="coerce").to_numpy(dtype=float)
        daily_low = pd.to_numeric(daily.get("low"), errors="coerce").to_numpy(dtype=float)
        daily_close = pd.to_numeric(daily.get("close"), errors="coerce").to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            daily_range_pct = (daily_high - daily_low) / daily_close
        return cls(
            bars=bars,
            daily=daily,
            timestamps=bars["timestamp"].reset_index(drop=True),
            bar_dates=bars["date"].to_numpy(),
            daily_dates=daily["date"].to_numpy(),
            daily_open=daily_open,
            daily_high=daily_high,
            daily_low=daily_low,
            daily_close=daily_close,
            daily_range_pct=daily_range_pct,
        )


@dataclass(frozen=True)
class _RegimeLookup:
    """Regime labels sorted by date with O(log n) prior-day lookup."""

    dates: np.ndarray
    records: list[dict[str, Any]]
    extra_columns: frozenset[str]


def feature_table_path(round_root: Path) -> Path:
    return FeatureTableArtifact(round_root).path


def load_feature_table(round_root: Path) -> pd.DataFrame:
    return FeatureTableArtifact(round_root).load()


def load_regime_labels() -> pd.DataFrame:
    expected = default_data_root() / "regime_labels.parquet"
    if not expected.exists():
        raise FileNotFoundError(f"Missing regime labels file: {expected}")
    labels = pd.read_parquet(expected)
    if "date" not in labels.columns:
        labels = labels.reset_index().rename(columns={labels.index.name or "index": "date"})
    labels = labels.assign(date=pd.to_datetime(labels["date"]).dt.date)
    if "regime_label" not in labels.columns:
        raise ValueError(f"regime_labels.parquet missing required column: {expected}: regime_label")
    return labels


def build_feature_table(
    trades_df: pd.DataFrame,
    bars_df: pd.DataFrame,
    events: list[dict],
    family: str,
    runtime_config: dict[str, Any] | None = None,
    quarantine_path: Path | None = None,
) -> pd.DataFrame:
    bars = _normalize_bars(bars_df)
    trades = trades_df.copy()
    trades = _quarantine_nonfinite_pnl_trades(trades, quarantine_path=quarantine_path)
    event_stops = _event_stop_prices(events)
    regime_labels = load_regime_labels()
    extra_regime_columns = _extra_regime_columns(regime_labels)
    sorted_labels = regime_labels.sort_values("date", kind="mergesort").reset_index(drop=True)
    regime_lookup = _RegimeLookup(
        dates=sorted_labels["date"].to_numpy(),
        records=sorted_labels.to_dict("records"),
        extra_columns=extra_regime_columns,
    )
    config = runtime_config or {}
    bars_by_symbol = {
        str(symbol): SymbolBars.from_bars(symbol_bars.reset_index(drop=True))
        for symbol, symbol_bars in bars.groupby("symbol", sort=False)
    }
    feature_cache: dict[tuple[Any, ...], Any] = {}

    rows = [
        _feature_row(
            trade,
            bars_by_symbol,
            regime_lookup,
            str(family).lower(),
            config,
            event_stops,
            feature_cache,
        )
        for trade in trades.to_dict("records")
    ]
    columns = _feature_columns(extra_regime_columns)
    out = pd.DataFrame(rows, columns=columns)
    if out.empty:
        out = pd.DataFrame(columns=columns)

    out = _coerce_feature_dtypes(out)
    _assert_leakage_guard(out, extra_entry_columns=extra_regime_columns)
    return out


def _quarantine_nonfinite_pnl_trades(
    trades: pd.DataFrame, *, quarantine_path: Path | None
) -> pd.DataFrame:
    """Drop trade rows without a finite pnl, mirroring rule I quarantine.

    Uses the same column-presence fallback chain as `_feature_row`
    (pnl -> pnl_abs -> pnl_pct). Quarantined rows are logged and written to
    `quarantine_path`; above _QUARANTINE_MAX_FRACTION the build stops loudly.
    """
    if trades.empty:
        return trades
    pnl_column = next(
        (column for column in ("pnl", "pnl_abs", "pnl_pct") if column in trades.columns),
        None,
    )
    if pnl_column is None:
        finite = np.zeros(len(trades), dtype=bool)
    else:
        pnl = pd.to_numeric(trades[pnl_column], errors="coerce")
        finite = np.isfinite(pnl.to_numpy(dtype=float))
    quarantined = trades.loc[~finite]
    if quarantined.empty:
        return trades
    total = len(trades)
    if quarantine_path is not None:
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.write_text(
            quarantined.to_json(orient="records", date_format="iso") or "[]",
            encoding="utf-8",
        )
    log.warning(
        "feature table quarantined %d/%d trades with missing finite pnl%s "
        "| inspect the trades artifact for malformed rows",
        len(quarantined),
        total,
        f"; rows written to {quarantine_path}" if quarantine_path is not None else "",
    )
    if len(quarantined) / total > _QUARANTINE_MAX_FRACTION:
        raise ValueError(
            f"feature table quarantined {len(quarantined)}/{total} trades with "
            f"missing finite pnl (>{_QUARANTINE_MAX_FRACTION:.0%}); "
            "the trades artifact is too corrupt to build features from"
        )
    return trades.loc[finite]


def _feature_row(
    trade: dict[str, Any],
    bars_by_symbol: dict[str, SymbolBars],
    regime_lookup: _RegimeLookup,
    family: str,
    runtime_config: dict[str, Any],
    event_stops: dict[tuple[str, pd.Timestamp], float],
    feature_cache: dict[tuple[Any, ...], Any],
) -> dict[str, Any]:
    symbol = str(trade.get("symbol", ""))
    side = str(trade.get("side") or trade.get("direction") or "").lower()
    entry_local = pd.Timestamp(trade.get("entry_ts", trade.get("entry_date")))
    if entry_local.tzinfo is None:
        entry_local = entry_local.tz_localize("America/New_York")
    entry_ts = entry_local.tz_convert("UTC")
    symbol_data = bars_by_symbol.get(symbol)
    if symbol_data is None:
        symbol_data = SymbolBars.from_bars(
            pd.DataFrame(columns=["symbol", "timestamp", "date", "open", "high", "low", "close"])
        )
    symbol_bars = symbol_data.bars
    entry_pos = int(symbol_data.timestamps.searchsorted(entry_ts, side="left"))
    prior_bars = symbol_bars.iloc[:entry_pos]
    # prior_bars is strictly before entry_ts (searchsorted side="left"), so the
    # entry bar is simply the last prior bar.
    entry_bar = symbol_bars.iloc[entry_pos - 1] if entry_pos > 0 else pd.Series(dtype=object)
    entry_day = entry_ts.tz_convert("America/New_York").date()
    daily_dates = symbol_data.daily_dates
    day_idx = int(np.searchsorted(daily_dates, entry_day, side="left"))
    has_current_day = day_idx < len(daily_dates) and daily_dates[day_idx] == entry_day
    current_open = float(symbol_data.daily_open[day_idx]) if has_current_day else np.nan
    prior_close = float(symbol_data.daily_close[day_idx - 1]) if day_idx > 0 else np.nan
    prior_high = float(symbol_data.daily_high[day_idx - 1]) if day_idx > 0 else np.nan
    prior_low = float(symbol_data.daily_low[day_idx - 1]) if day_idx > 0 else np.nan

    gap_pct = _pct(current_open - prior_close, prior_close)
    prior_day_range_pct = _pct(prior_high - prior_low, prior_close)
    overnight_move_pct = gap_pct
    entry_price = _float_or_nan(trade.get("entry_price", np.nan))
    stop_price = _float_or_nan(trade.get("stop", trade.get("stop_price", np.nan)))
    if not np.isfinite(stop_price):
        stop_price = event_stops.get((symbol, entry_ts), np.nan)
    entry_bar_close = _float_or_nan(entry_bar.get("close", np.nan))
    entry_bar_range_pct = _pct(
        _float_or_nan(entry_bar.get("high", np.nan)) - _float_or_nan(entry_bar.get("low", np.nan)),
        entry_bar_close if np.isfinite(entry_bar_close) else entry_price,
    )

    out_pnl = _float_or_nan(trade.get("pnl", trade.get("pnl_abs", trade.get("pnl_pct", np.nan))))
    out_pnl_pct = _float_or_nan(trade.get("pnl_pct", np.nan))
    if not np.isfinite(out_pnl):
        raise ValueError(f"feature table trade {symbol}:{entry_ts.isoformat()} missing finite pnl")

    row = {
        "trade_id": f"{symbol}:{entry_ts.isoformat()}",
        "symbol": symbol,
        "side": side,
        "entry_ts": entry_ts,
        "time_of_day_min": _time_of_day_min(entry_ts),
        "day_of_week": entry_ts.tz_convert("America/New_York").dayofweek,
        "bars_since_open": max(
            entry_pos - int(np.searchsorted(symbol_data.bar_dates, entry_day, side="left")), 0
        ),
        "gap_pct": gap_pct,
        "prior_day_range_pct": prior_day_range_pct,
        "overnight_move_pct": overnight_move_pct,
        "vol_pctile_20d": _vol_pctile_20d(symbol_data.daily_range_pct, day_idx),
        "regime_label": _regime_label_for_date(regime_lookup, entry_day),
        "stop_distance_pct": _pct(abs(entry_price - stop_price), entry_price),
        "entry_bar_range_pct": entry_bar_range_pct,
        "out_pnl": out_pnl,
        "out_pnl_pct": out_pnl_pct,
        "out_mae": _float_or_nan(trade.get("mae", trade.get("out_mae", np.nan))),
        "out_mfe": _float_or_nan(trade.get("mfe", trade.get("out_mfe", np.nan))),
        "out_exit_reason": str(trade.get("exit_reason", "")),
        "out_hold_bars": _int_or_default(
            trade.get("hold_bars", trade.get("out_hold_bars", -1)),
            -1,
        ),
        "out_is_loss": bool(out_pnl < 0),
    }
    row.update(
        family_entry_features(
            family,
            symbol_bars=symbol_bars,
            prior_bars=prior_bars,
            entry_ts=entry_ts,
            entry_price=entry_price,
            runtime_config=runtime_config,
            feature_cache=feature_cache,
        )
    )
    row.update(_regime_columns_for_date(regime_lookup, entry_day))
    return row


def _event_stop_prices(events: list[dict]) -> dict[tuple[str, pd.Timestamp], float]:
    stops: dict[tuple[str, pd.Timestamp], float] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        symbol = str(event.get("symbol", ""))
        if not symbol:
            continue
        stop_price = _float_or_nan(event.get("stop_price", event.get("stop", np.nan)))
        if not np.isfinite(stop_price):
            continue
        raw_ts = event.get("timestamp", event.get("entry_ts", event.get("entry_date")))
        if raw_ts is None:
            continue
        timestamp = pd.Timestamp(raw_ts)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("America/New_York")
        stops[(symbol, timestamp.tz_convert("UTC"))] = stop_price
    return stops


def _normalize_bars(bars_df: pd.DataFrame) -> pd.DataFrame:
    bars = bars_df.copy()
    if "timestamp" not in bars.columns:
        bars = bars.reset_index().rename(columns={bars.index.name or "index": "timestamp"})
    if "symbol" not in bars.columns:
        bars["symbol"] = "UNKNOWN"
    raw_timestamps = pd.to_datetime(bars["timestamp"])
    if raw_timestamps.dt.tz is None:
        timestamps = raw_timestamps.dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    else:
        timestamps = raw_timestamps.dt.tz_convert("UTC")
    bars = bars.assign(
        timestamp=timestamps,
        date=timestamps.dt.tz_convert("America/New_York").dt.date,
    )
    required = {"open", "high", "low", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars_df missing required columns: {sorted(missing)}")
    return bars.sort_values(["symbol", "timestamp"]).reset_index(drop=True)


def _daily_bars(bars: pd.DataFrame) -> pd.DataFrame:
    if bars.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    grouped = bars.groupby("date", sort=True)
    return pd.DataFrame(
        {
            "date": grouped["date"].first().values,
            "open": grouped["open"].first().values,
            "high": grouped["high"].max().values,
            "low": grouped["low"].min().values,
            "close": grouped["close"].last().values,
        }
    )


def _float_or_nan(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _pct(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return np.nan
    return float(numerator / denominator * 100.0)


def _time_of_day_min(entry_ts: pd.Timestamp) -> int:
    local = entry_ts.tz_convert("America/New_York")
    return int((local.hour * 60 + local.minute) - (9 * 60 + 30))


def _vol_pctile_20d(daily_range_pct: np.ndarray, day_idx: int) -> float:
    prior = daily_range_pct[:day_idx]
    if prior.size == 0:
        return np.nan
    current = prior[-1]
    window = prior[max(prior.size - 21, 0) : prior.size - 1]
    window = window[~np.isnan(window)]
    if window.size == 0 or not np.isfinite(current):
        return np.nan
    return float((window <= current).mean())


def _regime_label_for_date(lookup: _RegimeLookup, entry_day: object) -> str:
    record = _prior_regime_record(lookup, entry_day)
    if record is None:
        return ""
    return str(record["regime_label"])


def _regime_columns_for_date(lookup: _RegimeLookup, entry_day: object) -> dict[str, Any]:
    if not lookup.extra_columns:
        return {}
    record = _prior_regime_record(lookup, entry_day)
    if record is None:
        return {column: np.nan for column in lookup.extra_columns}
    return {column: record[column] for column in lookup.extra_columns}


def _prior_regime_record(lookup: _RegimeLookup, entry_day: object) -> dict[str, Any] | None:
    # lookup.dates is sorted (stable mergesort), so the latest strictly-prior
    # label is the record just before the insertion point of entry_day.
    idx = int(np.searchsorted(lookup.dates, entry_day, side="left"))
    if idx == 0:
        return None
    return lookup.records[idx - 1]


def _extra_regime_columns(labels: pd.DataFrame) -> frozenset[str]:
    return frozenset(
        column
        for column in labels.columns
        if column not in {"date", "regime_label"} and column not in _FEATURE_COLUMNS
    )


def _feature_columns(extra_regime_columns: frozenset[str]) -> list[str]:
    columns = list(_FEATURE_COLUMNS)
    insert_at = columns.index("regime_label") + 1
    for column in sorted(extra_regime_columns):
        columns.insert(insert_at, column)
        insert_at += 1
    return columns


def _int_or_default(value: object, default: int) -> int:
    parsed = _float_or_nan(value)
    if not np.isfinite(parsed):
        return default
    return int(parsed)


def _coerce_feature_dtypes(out: pd.DataFrame) -> pd.DataFrame:
    out = out.assign(entry_ts=pd.to_datetime(out["entry_ts"], utc=True))
    return out.astype(
        {
            "trade_id": "str",
            "symbol": "str",
            "side": "str",
            "time_of_day_min": "int16",
            "day_of_week": "int8",
            "bars_since_open": "int16",
            "gap_pct": "float64",
            "prior_day_range_pct": "float64",
            "overnight_move_pct": "float64",
            "or_width_pctile": "float64",
            "dist_to_ema_pct": "float64",
            "vol_pctile_20d": "float64",
            "regime_label": "str",
            "stop_distance_pct": "float64",
            "entry_bar_range_pct": "float64",
            "out_pnl": "float64",
            "out_pnl_pct": "float64",
            "out_mae": "float64",
            "out_mfe": "float64",
            "out_exit_reason": "str",
            "out_hold_bars": "int16",
            "out_is_loss": "bool",
        }
    )


def _assert_leakage_guard(out: pd.DataFrame, *, extra_entry_columns: frozenset[str]) -> None:
    known = ENTRY_TIME_COLUMNS | OUTCOME_COLUMNS | extra_entry_columns
    missing = set(out.columns) - known
    if missing:
        raise AssertionError(f"feature table columns missing leakage classification: {missing}")
    overlap = ENTRY_TIME_COLUMNS & OUTCOME_COLUMNS
    if overlap:
        raise AssertionError(f"feature table columns classified twice: {overlap}")
    for column in out.columns:
        in_entry = column in ENTRY_TIME_COLUMNS or column in extra_entry_columns
        in_outcome = column in OUTCOME_COLUMNS
        if in_entry == in_outcome:
            raise AssertionError(f"feature table column must be in exactly one set: {column}")
