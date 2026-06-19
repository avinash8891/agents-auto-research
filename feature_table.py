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
    # ponytail: curated OHLCV priors only. RVOL follows participation/conviction
    # literature (Chordia, Roll, Subrahmanyam 2001); ATR/ADX are Wilder 1978
    # volatility/trend measures; trailing return follows Jegadeesh-Titman 1993
    # short-horizon relative-strength priors; session_phase captures intraday
    # periodicity documented by Andersen-Bollerslev 1997.
    "rvol",
    "gap_atr",
    "or_width_pctile",
    "dist_to_ema_pct",
    "dist_to_ema_atr",
    "vol_of_vol",
    "adx_14",
    "trailing_5d_return",
    "xs_rank_gap_pct",
    "xs_rank_rvol",
    "session_phase",
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
    daily_volume: np.ndarray
    daily_range_pct: np.ndarray

    @classmethod
    def from_bars(cls, bars: pd.DataFrame) -> "SymbolBars":
        daily = _daily_bars(bars)
        daily_open = pd.to_numeric(daily.get("open"), errors="coerce").to_numpy(dtype=float)
        daily_high = pd.to_numeric(daily.get("high"), errors="coerce").to_numpy(dtype=float)
        daily_low = pd.to_numeric(daily.get("low"), errors="coerce").to_numpy(dtype=float)
        daily_close = pd.to_numeric(daily.get("close"), errors="coerce").to_numpy(dtype=float)
        daily_volume = pd.to_numeric(daily.get("volume"), errors="coerce").to_numpy(dtype=float)
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
            daily_volume=daily_volume,
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
    """Load regime labels exported by the regime-detection repo.

    Contract: each row labels date D using day D's own (full-session) data —
    the parquet must NOT be pre-lagged. The feature table applies the
    one-trading-day lag itself at join time (`_prior_regime_record` joins the
    latest label strictly BEFORE the entry day). A pre-lagged parquet would be
    double-lagged: safe against look-ahead but one day staler than intended.
    """
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
    atr_pct = _atr_pct(symbol_data, day_idx, period=14)
    rvol = _rvol(symbol_bars, entry_ts)
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
        "rvol": rvol,
        "gap_atr": _ratio(gap_pct, atr_pct),
        "vol_pctile_20d": _vol_pctile_20d(symbol_data.daily_range_pct, day_idx),
        "vol_of_vol": _vol_of_vol(symbol_data.daily_range_pct, day_idx),
        "adx_14": _adx(symbol_data, day_idx, period=14),
        "trailing_5d_return": _trailing_return(symbol_data.daily_close, day_idx, sessions=5),
        "xs_rank_gap_pct": _xs_rank(
            "gap_pct",
            symbol,
            bars_by_symbol,
            entry_ts,
        ),
        "xs_rank_rvol": _xs_rank(
            "rvol",
            symbol,
            bars_by_symbol,
            entry_ts,
        ),
        "session_phase": _session_phase(entry_ts),
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
    row["dist_to_ema_atr"] = _ratio(row.get("dist_to_ema_pct", np.nan), atr_pct)
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
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    grouped = bars.groupby("date", sort=True)
    volume = grouped["volume"].sum().values if "volume" in bars.columns else np.nan
    return pd.DataFrame(
        {
            "date": grouped["date"].first().values,
            "open": grouped["open"].first().values,
            "high": grouped["high"].max().values,
            "low": grouped["low"].min().values,
            "close": grouped["close"].last().values,
            "volume": volume,
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


def _ratio(numerator: float, denominator: float) -> float:
    if not np.isfinite(numerator) or not np.isfinite(denominator) or denominator == 0:
        return np.nan
    return float(numerator / denominator)


def _time_of_day_min(entry_ts: pd.Timestamp) -> int:
    local = entry_ts.tz_convert("America/New_York")
    return int((local.hour * 60 + local.minute) - (9 * 60 + 30))


def _session_phase(entry_ts: pd.Timestamp) -> str:
    minute = _time_of_day_min(entry_ts)
    if minute < 60:
        return "open"
    if minute < 150:
        return "mid"
    if minute < 270:
        return "lunch"
    return "close"


def _rvol(symbol_bars: pd.DataFrame, entry_ts: pd.Timestamp) -> float:
    if "volume" not in symbol_bars or symbol_bars.empty:
        return np.nan
    entry_day = entry_ts.tz_convert("America/New_York").date()
    entry_minute = _time_of_day_min(entry_ts)
    local_times = symbol_bars["timestamp"].dt.tz_convert("America/New_York")
    minutes = (local_times.dt.hour * 60 + local_times.dt.minute) - (9 * 60 + 30)
    volume = pd.to_numeric(symbol_bars["volume"], errors="coerce")
    usable = symbol_bars.assign(_minute=minutes, _volume=volume)
    current = usable[
        (usable["date"] == entry_day)
        & (usable["timestamp"] < entry_ts)
        & (usable["_minute"] < entry_minute)
    ]["_volume"].sum()
    prior = (
        usable[
            (usable["date"] < entry_day)
            & (usable["_minute"] < entry_minute)
            & np.isfinite(usable["_volume"])
        ]
        .groupby("date", sort=True)["_volume"]
        .sum()
        .tail(20)
    )
    if prior.empty or not np.isfinite(current):
        return np.nan
    baseline = float(prior.mean())
    return _ratio(float(current), baseline)


def _atr_pct(data: SymbolBars, day_idx: int, *, period: int) -> float:
    if day_idx <= 0:
        return np.nan
    true_ranges = _true_ranges(data.daily_high, data.daily_low, data.daily_close)[:day_idx]
    window = true_ranges[max(0, len(true_ranges) - period) :]
    window = window[np.isfinite(window)]
    prior_close = data.daily_close[day_idx - 1]
    if window.size == 0:
        return np.nan
    return _pct(float(window.mean()), float(prior_close))


def _true_ranges(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    out = np.full(len(close), np.nan, dtype=float)
    for idx in range(len(close)):
        if idx == 0:
            out[idx] = high[idx] - low[idx]
        else:
            out[idx] = max(
                high[idx] - low[idx],
                abs(high[idx] - close[idx - 1]),
                abs(low[idx] - close[idx - 1]),
            )
    return out


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


def _vol_of_vol(daily_range_pct: np.ndarray, day_idx: int) -> float:
    prior = daily_range_pct[:day_idx]
    if prior.size < 3:
        return np.nan
    changes = np.diff(prior)
    window = changes[max(0, changes.size - 20) :]
    window = window[np.isfinite(window)]
    if window.size < 2:
        return np.nan
    return float(np.std(window, ddof=1))


def _trailing_return(close: np.ndarray, day_idx: int, *, sessions: int) -> float:
    if day_idx < sessions + 1:
        return np.nan
    start = close[day_idx - sessions - 1]
    end = close[day_idx - 1]
    return _pct(float(end - start), float(start))


def _adx(data: SymbolBars, day_idx: int, *, period: int) -> float:
    if day_idx < period + 1:
        return np.nan
    high = data.daily_high[:day_idx]
    low = data.daily_low[:day_idx]
    close = data.daily_close[:day_idx]
    tr = _true_ranges(high, low, close)
    plus_dm = np.zeros(len(close), dtype=float)
    minus_dm = np.zeros(len(close), dtype=float)
    for idx in range(1, len(close)):
        up = high[idx] - high[idx - 1]
        down = low[idx - 1] - low[idx]
        plus_dm[idx] = up if up > down and up > 0 else 0.0
        minus_dm[idx] = down if down > up and down > 0 else 0.0
    atr = pd.Series(tr).rolling(period).mean()
    plus = 100.0 * pd.Series(plus_dm).rolling(period).mean() / atr
    minus = 100.0 * pd.Series(minus_dm).rolling(period).mean() / atr
    dx = (abs(plus - minus) / (plus + minus) * 100.0).replace([np.inf, -np.inf], np.nan)
    adx = dx.rolling(period).mean().iloc[-1]
    return float(adx) if np.isfinite(adx) else np.nan


def _xs_rank(
    feature: str,
    symbol: str,
    bars_by_symbol: dict[str, SymbolBars],
    entry_ts: pd.Timestamp,
) -> float:
    values: dict[str, float] = {}
    entry_day = entry_ts.tz_convert("America/New_York").date()
    for candidate, data in bars_by_symbol.items():
        day_idx = int(np.searchsorted(data.daily_dates, entry_day, side="left"))
        has_current_day = day_idx < len(data.daily_dates) and data.daily_dates[day_idx] == entry_day
        if not has_current_day:
            continue
        if feature == "gap_pct":
            value = (
                _pct(
                    data.daily_open[day_idx] - data.daily_close[day_idx - 1],
                    data.daily_close[day_idx - 1],
                )
                if day_idx > 0
                else np.nan
            )
        elif feature == "rvol":
            value = _rvol(data.bars, entry_ts)
        else:
            value = np.nan
        if np.isfinite(value):
            values[candidate] = float(value)
    if symbol not in values or len(values) < 2:
        return np.nan
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    rank = [item[0] for item in ordered].index(symbol)
    return float(rank / (len(ordered) - 1))


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
            "rvol": "float64",
            "gap_atr": "float64",
            "or_width_pctile": "float64",
            "dist_to_ema_pct": "float64",
            "dist_to_ema_atr": "float64",
            "vol_of_vol": "float64",
            "adx_14": "float64",
            "trailing_5d_return": "float64",
            "xs_rank_gap_pct": "float64",
            "xs_rank_rvol": "float64",
            "session_phase": "str",
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
