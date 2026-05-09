from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtest.data_universe import load_universe_data
from backtest.filters import (
    _compute_gap_down_days,
    _compute_gap_up_days,
    _exclude_signals_on_days,
    _filter_signals_to_days,
)
from backtest.resample import build_timeframe_frame
from metrics import compute_metrics, empty_metrics
from strategies.base import BaseStrategy
from strategies.ema.contract import compile_ema_contract, map_ema_config_changes_to_contract
from strategies.ema.exits import simulate_trades
from strategies.ema.prompt import DESCRIPTION_FOR_RESEARCH
from strategies.ema.research import EMA_RESEARCH_SPEC
from strategies.ema.validate import validate_ema_runtime_config
from strategy_event_logger import StrategyEventLogger


def _apply_symbol_day_opening_setup_gate_by_0940_raw_setup(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    gate_time: str = "09:40",
    rejection_reason: str = "opening_setup_gate_failed_0940",
) -> set:
    """Reject post-09:40 signals on symbol-days without an early raw_setup.

    The raw_setup stream is the strategy's own generated setup mask before other filters.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        return set()

    try:
        gate_ts = pd.Timestamp(gate_time)
    except Exception:
        gate_ts = pd.Timestamp("09:40")
    gate_cutoff = gate_ts.time()

    idx = frame.index
    raw_setup_mask = signals.entries.values.copy()
    by_0940_mask = raw_setup_mask & (idx.time <= gate_cutoff)
    if not by_0940_mask.any():
        days_with_setup: set = set()
    else:
        day_ids = idx.normalize()
        series = pd.Series(by_0940_mask, index=idx)
        has_setup_by_day = series.groupby(day_ids).any()
        days_with_setup = set(has_setup_by_day[has_setup_by_day].index.date)

    post_gate_mask = idx.time > gate_cutoff
    reject_mask = signals.entries.values & post_gate_mask
    if days_with_setup:
        reject_mask &= np.array([d not in days_with_setup for d in idx.date], dtype=bool)

    if reject_mask.any():
        before = signals.entries.values.copy()
        signals.entries[reject_mask] = False
        signals.entry_price[reject_mask] = np.nan
        signals.stop_price[reject_mask] = np.nan
        _log_filter_rejections(
            event_logger,
            frame,
            before,
            signals.entries.values,
            np.asarray(signals.entry_price),
            np.asarray(signals.stop_price),
            symbol,
            direction,
            rejection_reason,
        )

    return days_with_setup


def _log_filter_rejections(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    before_mask: np.ndarray,
    after_mask: np.ndarray,
    entry_prices: np.ndarray,
    stop_prices: np.ndarray,
    symbol: str,
    direction: str,
    reason: str,
    *,
    extras: dict[str, np.ndarray] | None = None,
) -> None:
    killed = before_mask & ~after_mask
    event_logger.record_events(
        timestamps=frame.index,
        mask=killed,
        symbol=symbol,
        direction=direction,
        event_type="rejected_signal",
        reason=reason,
        entry_prices=entry_prices,
        stop_prices=stop_prices,
        extras=extras,
    )


def _log_raw_setups(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    symbol: str,
    direction: str,
    *,
    extras: dict[str, np.ndarray] | None = None,
) -> None:
    event_logger.record_events(
        timestamps=frame.index,
        mask=signals.entries.values,
        symbol=symbol,
        direction=direction,
        event_type="raw_setup",
        entry_prices=signals.entry_price.values,
        stop_prices=signals.stop_price.values,
        extras=extras,
    )


def _log_accepted_signals(
    event_logger: StrategyEventLogger,
    frame: pd.DataFrame,
    signals,
    symbol: str,
    direction: str,
    *,
    extras: dict[str, np.ndarray] | None = None,
) -> None:
    event_logger.record_events(
        timestamps=frame.index,
        mask=signals.entries.values,
        symbol=symbol,
        direction=direction,
        event_type="accepted_signal",
        entry_prices=signals.entry_price.values,
        stop_prices=signals.stop_price.values,
        extras=extras,
    )


def _compute_alert_bar_feature_arrays(
    frame: pd.DataFrame,
    signals,
    *,
    ema_length: int,
) -> dict[str, np.ndarray]:
    """Compute per-index arrays describing the signal's alert bar, for strategy_events audit."""
    n = len(frame)
    alert_idx = np.asarray(getattr(signals, "alert_bar_idx", np.full(n, -1)))
    try:
        alert_idx = alert_idx.astype(int)
    except Exception:
        alert_idx = np.full(n, -1, dtype=int)

    alert_open = np.full(n, np.nan, dtype=np.float64)
    alert_close = np.full(n, np.nan, dtype=np.float64)
    alert_high = np.full(n, np.nan, dtype=np.float64)
    alert_low = np.full(n, np.nan, dtype=np.float64)
    alert_ema = np.full(n, np.nan, dtype=np.float64)
    alert_ts = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
    trigger_bucket = np.full(n, "", dtype=object)
    alert_bucket = np.full(n, "", dtype=object)

    if not isinstance(frame.index, pd.DatetimeIndex):
        return {
            "alert_bar_open": alert_open,
            "alert_bar_close": alert_close,
            "alert_bar_high": alert_high,
            "alert_bar_low": alert_low,
            "alert_bar_ema": alert_ema,
            "alert_bar_timestamp": alert_ts,
            "trigger_bucket": trigger_bucket,
            "alert_bucket": alert_bucket,
            "alert_close_minus_ema": np.full(n, np.nan, dtype=np.float64),
            "alert_bar_range": np.full(n, np.nan, dtype=np.float64),
            "close_location_in_range": np.full(n, np.nan, dtype=np.float64),
            "upper_wick_share": np.full(n, np.nan, dtype=np.float64),
        }

    ema = pd.Series(frame["close"].values).ewm(span=ema_length, adjust=False).mean().values
    open_arr = pd.to_numeric(frame.get("open"), errors="coerce").to_numpy(dtype=float, copy=False)
    close_arr = pd.to_numeric(frame.get("close"), errors="coerce").to_numpy(dtype=float, copy=False)
    high_arr = pd.to_numeric(frame.get("high"), errors="coerce").to_numpy(dtype=float, copy=False)
    low_arr = pd.to_numeric(frame.get("low"), errors="coerce").to_numpy(dtype=float, copy=False)
    trigger_bucket[:] = np.asarray(frame.index.strftime("%H:%M"), dtype=object)
    ts_arr = frame.index.to_numpy(dtype="datetime64[ns]")

    valid = (alert_idx >= 0) & (alert_idx < n)
    if valid.any():
        dst = np.flatnonzero(valid)
        src = alert_idx[dst]
        alert_open[dst] = open_arr[src]
        alert_close[dst] = close_arr[src]
        alert_high[dst] = high_arr[src]
        alert_low[dst] = low_arr[src]
        alert_ema[dst] = ema[src]
        alert_ts[dst] = ts_arr[src]

    alert_close_minus_ema = alert_close - alert_ema
    alert_range = alert_high - alert_low
    with np.errstate(divide="ignore", invalid="ignore"):
        close_location_in_range = (alert_close - alert_low) / alert_range
        upper_wick_share = (alert_high - np.maximum(alert_open, alert_close)) / alert_range
        lower_wick_share = (np.minimum(alert_open, alert_close) - alert_low) / alert_range
        body_pct = np.abs(alert_close - alert_open) / alert_range
    invalid_range = ~np.isfinite(alert_range) | (alert_range <= 0)
    if invalid_range.any():
        close_location_in_range = close_location_in_range.copy()
        upper_wick_share = upper_wick_share.copy()
        lower_wick_share = lower_wick_share.copy()
        body_pct = body_pct.copy()
        close_location_in_range[invalid_range] = np.nan
        upper_wick_share[invalid_range] = np.nan
        lower_wick_share[invalid_range] = np.nan
        body_pct[invalid_range] = np.nan

    # Persist per-event (trigger, alert) buckets for diagnostics and cohorting.
    # NOTE: The numpy datetime64 values from to_numpy() represent UTC instants without tz;
    # interpret them as UTC and convert to the frame's timezone for bucket strings.
    try:
        tz = frame.index.tz
    except Exception:
        tz = None
    if tz is not None:
        try:
            alert_bucket[:] = (
                pd.to_datetime(alert_ts, errors="coerce", utc=True).tz_convert(tz).strftime("%H:%M").to_numpy()
            )
        except Exception:
            alert_bucket[:] = ""
    return {
        "alert_bar_open": alert_open,
        "alert_bar_close": alert_close,
        "alert_bar_high": alert_high,
        "alert_bar_low": alert_low,
        "alert_bar_ema": alert_ema,
        "alert_bar_timestamp": alert_ts,
        "trigger_bucket": trigger_bucket,
        "alert_bucket": alert_bucket,
        "alert_close_minus_ema": alert_close_minus_ema,
        "alert_bar_range": alert_range,
        "close_location_in_range": close_location_in_range,
        "upper_wick_share": upper_wick_share,
        "lower_wick_share": lower_wick_share,
        "alert_body_pct": body_pct,
    }


def _maybe_apply_require_alert_close_vs_ema_confirmation(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    cfg: dict[str, Any] | None,
    secondary_geometry_override_cfg: dict[str, Any] | None = None,
    ema_length: int,
) -> dict[str, np.ndarray] | None:
    """Reject entries whose alert bar closes on the wrong side of its EMA (by entry bucket).

    Optionally supports a secondary-pass geometry override for cases where alert_close>EMA
    (shorts) or alert_close<EMA (longs): the primary EMA-side confirmation remains a
    high-precision gate, but a selective alert-bar geometry predicate can restore some
    candidates that would otherwise be rejected.
    """
    if not cfg or not isinstance(frame.index, pd.DatetimeIndex) or not isinstance(cfg, dict):
        return None
    if not bool(cfg.get("enabled", False)):
        return None

    direction_cfg = cfg.get("direction", "short")
    if direction_cfg not in {"short", "long", "both"}:
        direction_cfg = "short"
    if direction_cfg != "both" and direction != direction_cfg:
        return None

    require_leq = bool(cfg.get("require_alert_close_leq_ema", True))
    excluding = cfg.get("apply_to_entry_buckets_excluding", ["09:30"])
    if not isinstance(excluding, list) or not all(isinstance(x, str) for x in excluding):
        excluding = ["09:30"]

    extras = _compute_alert_bar_feature_arrays(frame, signals, ema_length=ema_length)
    # Tag downstream accepted_signal rows with which path satisfied the EMA confirmation.
    # Stored as an extras column so strategy_events / diagnostics can cohort performance.
    n = len(frame)
    extras.setdefault(
        "alert_close_vs_ema_confirmation_path",
        np.full(n, "", dtype=object),
    )
    idx_buckets = np.asarray(frame.index.strftime("%H:%M"))
    eligible_bucket_mask = ~np.isin(idx_buckets, np.asarray(excluding, dtype=str))

    alert_close_minus_ema = np.asarray(extras.get("alert_close_minus_ema"))
    rule_pass = np.isfinite(alert_close_minus_ema) & (
        (alert_close_minus_ema <= 0) if require_leq else (alert_close_minus_ema < 0)
    )
    override_pass = np.zeros(len(frame), dtype=bool)
    override_active_mask = np.zeros(len(frame), dtype=bool)
    override_reason = "alert_close_above_ema_geometry_override_failed_reject"
    if secondary_geometry_override_cfg and isinstance(secondary_geometry_override_cfg, dict):
        if bool(secondary_geometry_override_cfg.get("enabled", False)):
            sec_direction = secondary_geometry_override_cfg.get("direction", "short")
            if sec_direction not in {"short", "long", "both"}:
                sec_direction = "short"
            if sec_direction == "both" or sec_direction == direction:
                sec_excluding = secondary_geometry_override_cfg.get(
                    "apply_to_entry_buckets_excluding", ["09:30"]
                )
                if not (
                    isinstance(sec_excluding, list) and all(isinstance(x, str) for x in sec_excluding)
                ):
                    sec_excluding = ["09:30"]
                sec_eligible_bucket_mask = ~np.isin(
                    idx_buckets, np.asarray(sec_excluding, dtype=str)
                )
                if bool(secondary_geometry_override_cfg.get("override_when_alert_close_gt_ema", True)):
                    try:
                        max_close_loc = float(
                            secondary_geometry_override_cfg.get(
                                "require_close_location_in_range_leq", 1.0
                            )
                        )
                    except Exception:
                        max_close_loc = 1.0
                    close_location_in_range = np.asarray(extras.get("close_location_in_range"))
                    alert_close_gt_ema = np.isfinite(alert_close_minus_ema) & (alert_close_minus_ema > 0)
                    override_active_mask = (
                        signals.entries.values & sec_eligible_bucket_mask & alert_close_gt_ema
                    )
                    override_pass = (
                        override_active_mask
                        & np.isfinite(close_location_in_range)
                        & (close_location_in_range <= max_close_loc)
                    )

    reject_mask = signals.entries.values & eligible_bucket_mask & ~rule_pass & ~override_pass
    if not reject_mask.any():
        paths = np.asarray(extras.get("alert_close_vs_ema_confirmation_path"), dtype=object)
        kept = signals.entries.values & eligible_bucket_mask
        if kept.any():
            paths[kept & rule_pass] = "ema_confirm_pass"
            if override_pass.any():
                paths[kept & override_pass] = "ema_geometry_override_pass"
            extras["alert_close_vs_ema_confirmation_path"] = paths
        return extras

    before = signals.entries.values.copy()
    entry_before = signals.entry_price.values.copy()
    stop_before = signals.stop_price.values.copy()
    signals.entries[reject_mask] = False
    signals.entry_price[reject_mask] = np.nan
    signals.stop_price[reject_mask] = np.nan

    # If the override primitive is enabled, use a distinct rejection reason for the
    # subset of signals that were eligible for override but still failed it.
    if override_active_mask.any():
        override_failed_reject = reject_mask & override_active_mask
        if override_failed_reject.any():
            before_override = before.copy()
            after_override = before.copy()
            after_override[override_failed_reject] = False
            _log_filter_rejections(
                event_logger,
                frame,
                before_override,
                after_override,
                entry_before,
                stop_before,
                symbol,
                direction,
                override_reason,
                extras=extras,
            )

        baseline_reject = reject_mask & ~override_active_mask
        if baseline_reject.any():
            before_baseline = before.copy()
            after_baseline = before.copy()
            after_baseline[baseline_reject] = False
            _log_filter_rejections(
                event_logger,
                frame,
                before_baseline,
                after_baseline,
                entry_before,
                stop_before,
                symbol,
                direction,
                "alert_close_above_ema_reject",
                extras=extras,
            )
    else:
        _log_filter_rejections(
            event_logger,
            frame,
            before,
            signals.entries.values,
            entry_before,
            stop_before,
            symbol,
            direction,
            "alert_close_above_ema_reject",
            extras=extras,
        )

    paths = np.asarray(extras.get("alert_close_vs_ema_confirmation_path"), dtype=object)
    kept = signals.entries.values & eligible_bucket_mask
    if kept.any():
        paths[kept & rule_pass] = "ema_confirm_pass"
        if override_pass.any():
            paths[kept & override_pass] = "ema_geometry_override_pass"
        extras["alert_close_vs_ema_confirmation_path"] = paths
    return extras


def _maybe_apply_require_bearish_alert_candle_confirmation(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    cfg: dict[str, Any] | None,
    ema_length: int,
) -> dict[str, np.ndarray] | None:
    """Reject entries whose alert bar is not bearish (close < open) (by entry bucket)."""
    if not cfg or not isinstance(frame.index, pd.DatetimeIndex) or not isinstance(cfg, dict):
        return None
    if not bool(cfg.get("enabled", False)):
        return None

    direction_cfg = cfg.get("direction", "short")
    if direction_cfg not in {"short", "long", "both"}:
        direction_cfg = "short"
    if direction_cfg != "both" and direction != direction_cfg:
        return None

    require_lt_open = bool(cfg.get("require_alert_close_lt_open", True))
    excluding = cfg.get("apply_to_entry_buckets_excluding", ["09:30"])
    if not isinstance(excluding, list) or not all(isinstance(x, str) for x in excluding):
        excluding = ["09:30"]

    extras = _compute_alert_bar_feature_arrays(frame, signals, ema_length=ema_length)
    idx_buckets = np.asarray(frame.index.strftime("%H:%M"))
    eligible_bucket_mask = ~np.isin(idx_buckets, np.asarray(excluding, dtype=str))

    alert_open = np.asarray(extras.get("alert_bar_open"))
    alert_close = np.asarray(extras.get("alert_bar_close"))
    if require_lt_open:
        rule_pass = np.isfinite(alert_open) & np.isfinite(alert_close) & (alert_close < alert_open)
    else:
        rule_pass = np.isfinite(alert_open) & np.isfinite(alert_close) & (alert_close <= alert_open)

    reject_mask = signals.entries.values & eligible_bucket_mask & ~rule_pass
    if not reject_mask.any():
        return extras

    before = signals.entries.values.copy()
    entry_before = signals.entry_price.values.copy()
    stop_before = signals.stop_price.values.copy()
    signals.entries[reject_mask] = False
    signals.entry_price[reject_mask] = np.nan
    signals.stop_price[reject_mask] = np.nan

    _log_filter_rejections(
        event_logger,
        frame,
        before,
        signals.entries.values,
        entry_before,
        stop_before,
        symbol,
        direction,
        "alert_candle_not_bearish",
        extras=extras,
    )
    return extras


def _maybe_apply_require_alert_candle_close_location_confirmation(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    cfg: dict[str, Any] | None,
    ema_length: int,
) -> dict[str, np.ndarray] | None:
    """Reject entries whose alert candle lacks decisive range structure (by entry bucket).

    Uses alert-bar geometry:
      close_location_in_range = (close - low) / (high - low)
      upper_wick_share = (high - max(open, close)) / (high - low)
    """
    if not cfg or not isinstance(frame.index, pd.DatetimeIndex) or not isinstance(cfg, dict):
        return None
    if not bool(cfg.get("enabled", False)):
        return None

    direction_cfg = cfg.get("direction", "short")
    if direction_cfg not in {"short", "long", "both"}:
        direction_cfg = "short"
    if direction_cfg != "both" and direction != direction_cfg:
        return None

    excluding = cfg.get("apply_to_entry_buckets_excluding", ["09:30"])
    if not isinstance(excluding, list) or not all(isinstance(x, str) for x in excluding):
        excluding = ["09:30"]

    try:
        max_close_loc = float(cfg.get("max_close_location_in_range", 1.0))
    except Exception:
        max_close_loc = 1.0
    try:
        max_upper_wick = float(cfg.get("max_upper_wick_share", 1.0))
    except Exception:
        max_upper_wick = 1.0

    extras = _compute_alert_bar_feature_arrays(frame, signals, ema_length=ema_length)
    idx_buckets = np.asarray(frame.index.strftime("%H:%M"))
    eligible_bucket_mask = ~np.isin(idx_buckets, np.asarray(excluding, dtype=str))

    close_loc = np.asarray(extras.get("close_location_in_range"))
    upper_wick = np.asarray(extras.get("upper_wick_share"))

    close_loc_pass = np.isfinite(close_loc) & (close_loc <= max_close_loc)
    upper_wick_pass = np.isfinite(upper_wick) & (upper_wick <= max_upper_wick)

    # Two-stage rejection so diagnostics can count each failure reason independently.
    reject_loc = signals.entries.values & eligible_bucket_mask & ~close_loc_pass
    if reject_loc.any():
        before = signals.entries.values.copy()
        entry_before = signals.entry_price.values.copy()
        stop_before = signals.stop_price.values.copy()
        signals.entries[reject_loc] = False
        signals.entry_price[reject_loc] = np.nan
        signals.stop_price[reject_loc] = np.nan
        _log_filter_rejections(
            event_logger,
            frame,
            before,
            signals.entries.values,
            entry_before,
            stop_before,
            symbol,
            direction,
            "alert_close_location_reject",
            extras=extras,
        )

    reject_wick = signals.entries.values & eligible_bucket_mask & ~upper_wick_pass
    if reject_wick.any():
        before = signals.entries.values.copy()
        entry_before = signals.entry_price.values.copy()
        stop_before = signals.stop_price.values.copy()
        signals.entries[reject_wick] = False
        signals.entry_price[reject_wick] = np.nan
        signals.stop_price[reject_wick] = np.nan
        _log_filter_rejections(
            event_logger,
            frame,
            before,
            signals.entries.values,
            entry_before,
            stop_before,
            symbol,
            direction,
            "alert_upper_wick_reject",
            extras=extras,
        )

    return extras


def _maybe_apply_require_alert_candle_body_dominance_confirmation(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    cfg: dict[str, Any] | None,
    ema_length: int,
) -> dict[str, np.ndarray] | None:
    """Reject entries whose alert candle body is not dominant enough (by entry bucket).

    body_pct = abs(close - open) / (high - low)
    """
    if not cfg or not isinstance(frame.index, pd.DatetimeIndex) or not isinstance(cfg, dict):
        return None
    if not bool(cfg.get("enabled", False)):
        return None

    direction_cfg = cfg.get("direction", "short")
    if direction_cfg not in {"short", "long", "both"}:
        direction_cfg = "short"
    if direction_cfg != "both" and direction != direction_cfg:
        return None

    excluding = cfg.get("apply_to_entry_buckets_excluding", ["09:30"])
    if not isinstance(excluding, list) or not all(isinstance(x, str) for x in excluding):
        excluding = ["09:30"]

    try:
        min_body_pct = float(cfg.get("min_body_pct", 0.0))
    except Exception:
        min_body_pct = 0.0

    extras = _compute_alert_bar_feature_arrays(frame, signals, ema_length=ema_length)
    idx_buckets = np.asarray(frame.index.strftime("%H:%M"))
    eligible_bucket_mask = ~np.isin(idx_buckets, np.asarray(excluding, dtype=str))

    body_pct = np.asarray(extras.get("alert_body_pct"))
    rule_pass = np.isfinite(body_pct) & (body_pct >= min_body_pct)
    reject_mask = signals.entries.values & eligible_bucket_mask & ~rule_pass
    if not reject_mask.any():
        return extras

    before = signals.entries.values.copy()
    entry_before = signals.entry_price.values.copy()
    stop_before = signals.stop_price.values.copy()
    signals.entries[reject_mask] = False
    signals.entry_price[reject_mask] = np.nan
    signals.stop_price[reject_mask] = np.nan

    _log_filter_rejections(
        event_logger,
        frame,
        before,
        signals.entries.values,
        entry_before,
        stop_before,
        symbol,
        direction,
        "alert_body_dominance_reject",
        extras=extras,
    )
    return extras


def _maybe_apply_bucket_specific_max_stop_distance_filter(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    cfg: dict[str, Any] | list[dict[str, Any]] | None,
    extras: dict[str, np.ndarray] | None = None,
) -> None:
    """Reject entries in specific entry-time bucket(s) if stop distance is too wide."""
    if not cfg or not isinstance(frame.index, pd.DatetimeIndex):
        return

    cfg_items: list[dict[str, Any]]
    use_alert_bar_timestamp_for_bucket = False
    if isinstance(cfg, dict):
        # New schema: single object with per-bucket mapping and direction gating.
        use_alert_bar_timestamp_for_bucket = bool(cfg.get("use_alert_bar_timestamp_for_bucket", False))
        if "max_stop_distance_pct_by_entry_bucket" in cfg:
            if not bool(cfg.get("enabled", False)):
                return
            direction_cfg = cfg.get("direction", "both")
            if direction_cfg not in {"short", "long", "both"}:
                direction_cfg = "both"
            if direction_cfg != "both" and direction != direction_cfg:
                return

            mapping = cfg.get("max_stop_distance_pct_by_entry_bucket")
            if not isinstance(mapping, dict):
                return
            cfg_items = []
            for bucket, max_sd in mapping.items():
                if not isinstance(bucket, str):
                    continue
                cfg_items.append(
                    {"enabled": True, "entry_bucket": bucket, "max_stop_distance_pct": max_sd}
                )
        else:
            cfg_items = [cfg]
    elif isinstance(cfg, list):
        cfg_items = [item for item in cfg if isinstance(item, dict)]
    else:
        return

    idx = frame.index
    trigger_buckets = np.asarray(idx.strftime("%H:%M"), dtype=object)
    alert_buckets: np.ndarray | None = None
    if use_alert_bar_timestamp_for_bucket:
        alert_ts = None
        if extras and "alert_bar_timestamp" in extras:
            alert_ts = extras.get("alert_bar_timestamp")
        if alert_ts is None:
            n = len(frame)
            alert_idx = np.asarray(getattr(signals, "alert_bar_idx", np.full(n, -1)))
            try:
                alert_idx = alert_idx.astype(int)
            except Exception:
                alert_idx = np.full(n, -1, dtype=int)
            ts_arr = idx.to_numpy(dtype="datetime64[ns]")
            alert_ts = np.full(n, np.datetime64("NaT"), dtype="datetime64[ns]")
            valid = (alert_idx >= 0) & (alert_idx < n)
            if valid.any():
                dst = np.flatnonzero(valid)
                src = alert_idx[dst]
                alert_ts[dst] = ts_arr[src]
        try:
            alert_buckets = (
                pd.to_datetime(np.asarray(alert_ts), errors="coerce", utc=True)
                .tz_convert(idx.tz)
                .strftime("%H:%M")
                .to_numpy()
            )
        except Exception:
            alert_buckets = np.full(len(frame), "", dtype=object)

    bucket_source = alert_buckets if alert_buckets is not None else trigger_buckets

    stop_dist_pct = (
        np.abs(signals.entry_price.values - signals.stop_price.values) / signals.entry_price.values
    ) * 100

    for item in cfg_items:
        if not bool(item.get("enabled", False)):
            continue
        direction_cfg = item.get("direction", "both")
        if direction_cfg not in {"short", "long", "both"}:
            direction_cfg = "both"
        if direction_cfg != "both" and direction != direction_cfg:
            continue
        bucket = item.get("entry_bucket")
        max_sd = item.get("max_stop_distance_pct")
        if not isinstance(bucket, str) or max_sd is None:
            continue
        try:
            max_sd = float(max_sd)
        except Exception:
            continue

        bucket_mask = bucket_source == bucket
        if not bucket_mask.any():
            continue
        reject_mask = signals.entries.values & bucket_mask & (stop_dist_pct > max_sd)
        if not reject_mask.any():
            continue

        reason = f"bucket_max_stop_distance_exceeded_{bucket.replace(':', '')}"
        before = signals.entries.values.copy()
        entry_before = signals.entry_price.values.copy()
        stop_before = signals.stop_price.values.copy()
        signals.entries[reject_mask] = False
        signals.entry_price[reject_mask] = np.nan
        signals.stop_price[reject_mask] = np.nan
        log_extras: dict[str, np.ndarray] | None = None
        if use_alert_bar_timestamp_for_bucket:
            log_extras = dict(extras) if extras else {}
            log_extras.setdefault("alert_bucket", alert_buckets)
            log_extras.setdefault("trigger_bucket", trigger_buckets)
        _log_filter_rejections(
            event_logger,
            frame,
            before,
            signals.entries.values,
            entry_before,
            stop_before,
            symbol,
            direction,
            reason,
            extras=log_extras if log_extras is not None else extras,
        )


def _maybe_apply_post0930_max_stop_distance_filter(
    frame: pd.DataFrame,
    signals,
    *,
    event_logger: StrategyEventLogger,
    symbol: str,
    direction: str,
    cfg: dict[str, Any] | None,
) -> None:
    """Reject post-09:30 entries if stop distance is too wide.

    Contract primitive: reject signals based on stop_distance_pct conditional on
    post-09:30 entry bucket membership, emitting a dedicated rejection reason
    into strategy_events / diagnostics.
    """
    if not cfg or not isinstance(cfg, dict) or not isinstance(frame.index, pd.DatetimeIndex):
        return
    if not bool(cfg.get("enabled", False)):
        return

    direction_cfg = cfg.get("direction", "short")
    if direction_cfg not in {"short", "long", "both"}:
        direction_cfg = "short"
    if direction_cfg != "both" and direction != direction_cfg:
        return

    max_sd = cfg.get("max_stop_distance_pct")
    if max_sd is None:
        return
    try:
        max_sd_value = float(max_sd)
    except Exception:
        return

    excluding = cfg.get("apply_to_entry_buckets_excluding", ["09:30"])
    if not isinstance(excluding, list) or not all(isinstance(x, str) for x in excluding):
        excluding = ["09:30"]

    idx = frame.index
    idx_buckets = np.asarray(idx.strftime("%H:%M"))

    # Strictly post-09:30 (wall-clock minutes), plus optional exclusions.
    minutes = idx.hour * 60 + idx.minute
    eligible_mask = (minutes > (9 * 60 + 30)) & ~np.isin(idx_buckets, np.asarray(excluding, dtype=str))
    if not eligible_mask.any():
        return

    stop_dist_pct = (
        np.abs(signals.entry_price.values - signals.stop_price.values) / signals.entry_price.values
    ) * 100
    reject_mask = signals.entries.values & eligible_mask & (stop_dist_pct > max_sd_value)
    if not reject_mask.any():
        return

    before = signals.entries.values.copy()
    entry_before = signals.entry_price.values.copy()
    stop_before = signals.stop_price.values.copy()
    signals.entries[reject_mask] = False
    signals.entry_price[reject_mask] = np.nan
    signals.stop_price[reject_mask] = np.nan
    _log_filter_rejections(
        event_logger,
        frame,
        before,
        signals.entries.values,
        entry_before,
        stop_before,
        symbol,
        direction,
        "post0930_max_stop_distance_exceeded",
    )


def run_backtest(config: dict) -> dict:
    from strategies.ema.signals import generate_signals_for_frame

    event_logger = StrategyEventLogger()
    if bool(
        config.get(
            "requires_engine_change__standardize_stop_distance_pct_units_in_events_and_diagnostics",
            False,
        )
    ):
        event_logger.set_stop_distance_pct_unit("percent")

    batch = load_universe_data(config)
    close = batch["close"]
    high = batch["high"]
    low = batch["low"]
    open_ = batch["open"]
    if close.empty:
        return empty_metrics()

    ema_length = int(config["ema_length"])
    tf_long = int(config["timeframe_long"])
    tf_short = int(config["timeframe_short"])
    direction_bias = config.get("direction_bias", "long_only")
    use_range_shift = config.get("use_range_shift", False)
    range_shift_lookback = int(config.get("range_shift_lookback", 20))
    entry_cutoff_time = config.get("entry_cutoff_time", None)
    gap_filter = config.get("gap_filter", False)
    gap_pct = config.get("gap_pct", 0.01)
    gap_exclude = config.get("gap_exclude", False)
    gap_exclude_pct = config.get("gap_exclude_pct", 0.005)
    min_stop_distance_pct = config.get("min_stop_distance_pct", None)
    if min_stop_distance_pct is not None:
        min_stop_distance_pct = float(min_stop_distance_pct)
    max_stop_distance_pct = config.get("max_stop_distance_pct", None)
    if max_stop_distance_pct is not None:
        max_stop_distance_pct = float(max_stop_distance_pct)
    # Transcript: 3-5 trades per day TOTAL (T2: "five trades"; T4: "not more than 3 times a day")
    max_trades_per_day = config.get("max_trades_per_day", None)
    entry_cutoff = pd.Timestamp(entry_cutoff_time).time() if entry_cutoff_time else None
    opening_setup_gate_by_0940_raw_setup_enabled = bool(
        config.get("requires_engine_change__opening_setup_gate_by_0940_raw_setup", False)
        or config.get("symbol_day_opening_setup_gate_by_0940_raw_setup_enabled", False)
    )
    bucket_specific_max_stop_distance_filter_cfg = config.get(
        "requires_engine_change__bucket_specific_max_stop_distance_filter"
    )
    if not isinstance(bucket_specific_max_stop_distance_filter_cfg, (dict, list)):
        bucket_specific_max_stop_distance_filter_cfg = None

    post0930_max_stop_distance_filter_cfg = config.get(
        "requires_engine_change__post0930_max_stop_distance_filter"
    )
    if not isinstance(post0930_max_stop_distance_filter_cfg, dict):
        post0930_max_stop_distance_filter_cfg = None

    all_trades: list[dict] = []

    for symbol in close.columns:
        sym_open = open_[symbol].dropna()
        sym_close = close[symbol].dropna()
        sym_high = high[symbol].dropna()
        sym_low = low[symbol].dropna()

        if sym_close.empty:
            continue

        gap_up_days = _compute_gap_up_days(sym_open, sym_close, gap_pct) if gap_filter else None
        gap_down_days = _compute_gap_down_days(sym_open, sym_close, gap_pct) if gap_filter else None
        gap_exclude_up_days = (
            _compute_gap_up_days(sym_open, sym_close, gap_exclude_pct) if gap_exclude else None
        )

        if direction_bias in {"both", "long_only"}:
            long_frame = build_timeframe_frame(sym_open, sym_close, sym_high, sym_low, tf_long)
            long_signals = generate_signals_for_frame(
                long_frame,
                "long",
                ema_length,
                use_range_shift=use_range_shift,
                range_shift_lookback=range_shift_lookback,
            )

            long_extras = _compute_alert_bar_feature_arrays(
                long_frame, long_signals, ema_length=ema_length
            )
            _log_raw_setups(
                event_logger, long_frame, long_signals, symbol, "long", extras=long_extras
            )

            if entry_cutoff and isinstance(long_frame.index, pd.DatetimeIndex):
                before = long_signals.entries.values.copy()
                mask = long_frame.index.time <= entry_cutoff
                long_signals.entries[~mask] = False
                long_signals.entry_price[~mask] = np.nan
                long_signals.stop_price[~mask] = np.nan
                _log_filter_rejections(
                    event_logger,
                    long_frame,
                    before,
                    long_signals.entries.values,
                    long_signals.entry_price.values,
                    long_signals.stop_price.values,
                    symbol,
                    "long",
                    "entry_cutoff",
                )
            if gap_filter:
                before = long_signals.entries.values.copy()
                _filter_signals_to_days(long_signals, long_frame, gap_down_days)
                _log_filter_rejections(
                    event_logger,
                    long_frame,
                    before,
                    long_signals.entries.values,
                    long_signals.entry_price.values,
                    long_signals.stop_price.values,
                    symbol,
                    "long",
                    "gap_filter",
                )

            if min_stop_distance_pct is not None or max_stop_distance_pct is not None:
                long_stop_dist = (
                    np.abs(long_signals.entry_price - long_signals.stop_price)
                    / long_signals.entry_price
                    * 100
                )
                if min_stop_distance_pct is not None:
                    before = long_signals.entries.values.copy()
                    long_signals.entries[long_stop_dist < min_stop_distance_pct] = False
                    long_signals.entry_price[long_stop_dist < min_stop_distance_pct] = np.nan
                    long_signals.stop_price[long_stop_dist < min_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        long_frame,
                        before,
                        long_signals.entries.values,
                        long_signals.entry_price.values,
                        long_signals.stop_price.values,
                        symbol,
                        "long",
                        "min_stop_distance",
                    )
                if max_stop_distance_pct is not None:
                    before = long_signals.entries.values.copy()
                    long_signals.entries[long_stop_dist > max_stop_distance_pct] = False
                    long_signals.entry_price[long_stop_dist > max_stop_distance_pct] = np.nan
                    long_signals.stop_price[long_stop_dist > max_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        long_frame,
                        before,
                        long_signals.entries.values,
                        long_signals.entry_price.values,
                        long_signals.stop_price.values,
                        symbol,
                        "long",
                        "max_stop_distance",
                    )

            _maybe_apply_post0930_max_stop_distance_filter(
                long_frame,
                long_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="long",
                cfg=post0930_max_stop_distance_filter_cfg,
            )

            _maybe_apply_bucket_specific_max_stop_distance_filter(
                long_frame,
                long_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="long",
                cfg=bucket_specific_max_stop_distance_filter_cfg,
                extras=(
                    long_extras
                    if bool(
                        config.get(
                            "requires_engine_change__log_alert_bar_extras_for_bucket_stop_distance_rejects",
                            False,
                        )
                    )
                    else None
                ),
            )

            _log_accepted_signals(
                event_logger, long_frame, long_signals, symbol, "long", extras=long_extras
            )

            for t in simulate_trades(
                long_frame, long_signals, config, symbol=symbol, event_logger=event_logger
            ):
                t["symbol"] = symbol
                all_trades.append(t)

        if direction_bias in {"both", "short_only"}:
            short_frame = build_timeframe_frame(sym_open, sym_close, sym_high, sym_low, tf_short)
            short_signals = generate_signals_for_frame(
                short_frame,
                "short",
                ema_length,
                use_range_shift=use_range_shift,
                range_shift_lookback=range_shift_lookback,
            )

            short_extras = _compute_alert_bar_feature_arrays(
                short_frame, short_signals, ema_length=ema_length
            )
            _log_raw_setups(
                event_logger, short_frame, short_signals, symbol, "short", extras=short_extras
            )
            days_with_setup_by_0940: set | None = None
            if opening_setup_gate_by_0940_raw_setup_enabled:
                days_with_setup_by_0940 = _apply_symbol_day_opening_setup_gate_by_0940_raw_setup(
                    short_frame,
                    short_signals,
                    event_logger=event_logger,
                    symbol=symbol,
                    direction="short",
                )

            alert_close_vs_ema_cfg = config.get(
                "requires_engine_change__require_alert_close_vs_ema_confirmation"
            )
            secondary_override_cfg = config.get(
                "requires_engine_change__alert_close_vs_ema_secondary_geometry_override"
            )
            maybe_extras = _maybe_apply_require_alert_close_vs_ema_confirmation(
                short_frame,
                short_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="short",
                cfg=alert_close_vs_ema_cfg if isinstance(alert_close_vs_ema_cfg, dict) else None,
                secondary_geometry_override_cfg=secondary_override_cfg
                if isinstance(secondary_override_cfg, dict)
                else None,
                ema_length=ema_length,
            )
            if maybe_extras is not None:
                short_extras = maybe_extras

            bearish_alert_candle_cfg = config.get(
                "requires_engine_change__require_bearish_alert_candle_confirmation"
            )
            maybe_extras = _maybe_apply_require_bearish_alert_candle_confirmation(
                short_frame,
                short_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="short",
                cfg=bearish_alert_candle_cfg if isinstance(bearish_alert_candle_cfg, dict) else None,
                ema_length=ema_length,
            )
            if maybe_extras is not None:
                short_extras = maybe_extras

            alert_candle_close_location_cfg = config.get(
                "requires_engine_change__require_alert_candle_close_location_confirmation"
            )
            maybe_extras = _maybe_apply_require_alert_candle_close_location_confirmation(
                short_frame,
                short_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="short",
                cfg=alert_candle_close_location_cfg
                if isinstance(alert_candle_close_location_cfg, dict)
                else None,
                ema_length=ema_length,
            )
            if maybe_extras is not None:
                short_extras = maybe_extras

            alert_candle_body_dominance_cfg = config.get(
                "requires_engine_change__require_alert_candle_body_dominance_confirmation"
            )
            maybe_extras = _maybe_apply_require_alert_candle_body_dominance_confirmation(
                short_frame,
                short_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="short",
                cfg=alert_candle_body_dominance_cfg
                if isinstance(alert_candle_body_dominance_cfg, dict)
                else None,
                ema_length=ema_length,
            )
            if maybe_extras is not None:
                short_extras = maybe_extras

            if entry_cutoff and isinstance(short_frame.index, pd.DatetimeIndex):
                before = short_signals.entries.values.copy()
                mask = short_frame.index.time <= entry_cutoff
                short_signals.entries[~mask] = False
                short_signals.entry_price[~mask] = np.nan
                short_signals.stop_price[~mask] = np.nan
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    before,
                    short_signals.entries.values,
                    short_signals.entry_price.values,
                    short_signals.stop_price.values,
                    symbol,
                    "short",
                    "entry_cutoff",
                )
            if gap_filter:
                # Short on gap-up days (fade the gap — transcript core rule)
                before = short_signals.entries.values.copy()
                _filter_signals_to_days(short_signals, short_frame, gap_up_days)
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    before,
                    short_signals.entries.values,
                    short_signals.entry_price.values,
                    short_signals.stop_price.values,
                    symbol,
                    "short",
                    "gap_filter",
                )

            if gap_exclude and gap_exclude_up_days:
                before = short_signals.entries.values.copy()
                _exclude_signals_on_days(short_signals, short_frame, gap_exclude_up_days)
                _log_filter_rejections(
                    event_logger,
                    short_frame,
                    before,
                    short_signals.entries.values,
                    short_signals.entry_price.values,
                    short_signals.stop_price.values,
                    symbol,
                    "short",
                    "gap_exclude",
                )

            if min_stop_distance_pct is not None or max_stop_distance_pct is not None:
                short_stop_dist = (
                    np.abs(short_signals.entry_price - short_signals.stop_price)
                    / short_signals.entry_price
                    * 100
                )
                if min_stop_distance_pct is not None:
                    before = short_signals.entries.values.copy()
                    short_signals.entries[short_stop_dist < min_stop_distance_pct] = False
                    short_signals.entry_price[short_stop_dist < min_stop_distance_pct] = np.nan
                    short_signals.stop_price[short_stop_dist < min_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        short_frame,
                        before,
                        short_signals.entries.values,
                        short_signals.entry_price.values,
                        short_signals.stop_price.values,
                        symbol,
                        "short",
                        "min_stop_distance",
                    )
                if max_stop_distance_pct is not None:
                    before = short_signals.entries.values.copy()
                    short_signals.entries[short_stop_dist > max_stop_distance_pct] = False
                    short_signals.entry_price[short_stop_dist > max_stop_distance_pct] = np.nan
                    short_signals.stop_price[short_stop_dist > max_stop_distance_pct] = np.nan
                    _log_filter_rejections(
                        event_logger,
                        short_frame,
                        before,
                        short_signals.entries.values,
                        short_signals.entry_price.values,
                        short_signals.stop_price.values,
                        symbol,
                        "short",
                        "max_stop_distance",
                    )

            _maybe_apply_post0930_max_stop_distance_filter(
                short_frame,
                short_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="short",
                cfg=post0930_max_stop_distance_filter_cfg,
            )

            _maybe_apply_bucket_specific_max_stop_distance_filter(
                short_frame,
                short_signals,
                event_logger=event_logger,
                symbol=symbol,
                direction="short",
                cfg=bucket_specific_max_stop_distance_filter_cfg,
                extras=(
                    short_extras
                    if bool(
                        config.get(
                            "requires_engine_change__log_alert_bar_extras_for_bucket_stop_distance_rejects",
                            False,
                        )
                    )
                    else None
                ),
            )

            _log_accepted_signals(
                event_logger, short_frame, short_signals, symbol, "short", extras=short_extras
            )

            for t in simulate_trades(
                short_frame, short_signals, config, symbol=symbol, event_logger=event_logger
            ):
                t["symbol"] = symbol
                if days_with_setup_by_0940 is not None:
                    try:
                        t["has_raw_setup_by_0940"] = (
                            pd.Timestamp(t["entry_date"]).date() in days_with_setup_by_0940
                        )
                    except Exception:
                        t["has_raw_setup_by_0940"] = False
                all_trades.append(t)

    if not all_trades:
        result = empty_metrics()
        result["_event_logger"] = event_logger
        return result

    trades_df = pd.DataFrame(all_trades)

    if max_trades_per_day and max_trades_per_day > 0:
        trades_df = trades_df.sort_values("entry_date")
        trades_df["_date"] = trades_df["entry_date"].dt.date
        kept = trades_df.groupby("_date").head(max_trades_per_day)
        rejected_idx = trades_df.index.difference(kept.index)
        if len(rejected_idx):
            rej = trades_df.loc[
                rejected_idx, ["entry_date", "symbol", "direction", "entry_price", "stop"]
            ].copy()
            rej = rej.rename(columns={"entry_date": "timestamp", "stop": "stop_price"})
            rej["event_type"] = "rejected_signal"
            rej["reason"] = "max_trades_per_day"
            event_logger.record_dataframe(rej, "rejected_signal", "max_trades_per_day")
        trades_df = kept.drop(columns=["_date"])

    result = compute_metrics(trades_df)
    result["_trades_df"] = trades_df
    result["_event_logger"] = event_logger
    return result


class EMAStrategy(BaseStrategy):
    name = "ema"
    benchmark_script = "backtest_5ema.py"
    description_for_research = DESCRIPTION_FOR_RESEARCH
    research_spec = EMA_RESEARCH_SPEC

    def run(self, config: dict[str, Any]) -> dict[str, Any]:
        return run_backtest(config)

    def validate_runtime_config(self, config: dict[str, Any]) -> list[str]:
        return validate_ema_runtime_config(config)

    def compile_contract(self, contract: list[dict[str, Any]]):
        return compile_ema_contract(contract)

    def map_config_changes_to_contract(
        self, config_changes: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return map_ema_config_changes_to_contract(config_changes)
