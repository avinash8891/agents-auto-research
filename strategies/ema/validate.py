from __future__ import annotations

from typing import Any

from strategies.ema.defaults import _get_ema_defaults
from strategies.validate_utils import _is_int_value, _is_number_value

_RUNTIME_METADATA_KEYS = {
    "allow_unbounded_research_backtest",
    "data_provenance",
    "family",
    "slippage_pct",
}


def supported_ema_runtime_keys() -> frozenset[str]:
    return frozenset(set(_get_ema_defaults()) | _RUNTIME_METADATA_KEYS)


def _validate_supported_keys(config: dict[str, Any]) -> list[str]:
    unknown = sorted(set(config) - supported_ema_runtime_keys())
    if not unknown:
        return []
    return [f"Unsupported EMA runtime config keys: {', '.join(unknown)}"]


def validate_ema_runtime_config(config: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    violations.extend(_validate_supported_keys(config))
    if config.get("opening_info_intensity_gate_enabled"):
        violations.append(
            "opening_info_intensity_gate_enabled=true is not implemented by the EMA strategy"
        )
    ema = config.get("ema_length")
    if ema is not None:
        if not _is_int_value(ema) or ema < 2:
            violations.append(f"ema_length={ema}: must be >= 2 (EMA of 1 is just price)")
        if _is_int_value(ema) and ema > 200:
            violations.append(f"ema_length={ema}: must be <= 200 (intraday bars, not daily)")
    rr = config.get("rr_ratio")
    if rr is not None:
        if not _is_number_value(rr) or float(rr) < 0.5:
            violations.append(
                f"rr_ratio={rr}: must be >= 0.5 (below 0.5 is guaranteed negative edge)"
            )
        if _is_number_value(rr) and float(rr) > 20:
            violations.append(f"rr_ratio={rr}: must be <= 20 (unreachable targets waste entries)")
    tf_short = config.get("timeframe_short")
    tf_long = config.get("timeframe_long")
    if tf_short is not None:
        if not _is_int_value(tf_short) or tf_short < 1:
            violations.append(f"timeframe_short={tf_short}: must be >= 1 minute")
        if _is_int_value(tf_short) and tf_short > 60:
            violations.append(f"timeframe_short={tf_short}: must be <= 60 minutes for intraday")
    if tf_long is not None:
        if not _is_int_value(tf_long) or tf_long < 1:
            violations.append(f"timeframe_long={tf_long}: must be >= 1 minute")
        if _is_int_value(tf_long) and tf_long > 60:
            violations.append(f"timeframe_long={tf_long}: must be <= 60 minutes for intraday")
    if tf_short is not None and tf_long is not None:
        if _is_int_value(tf_short) and _is_int_value(tf_long) and tf_short > tf_long:
            violations.append(
                f"timeframe_short={tf_short} > timeframe_long={tf_long}: short TF must be <= long TF"
            )
    mtpd = config.get("max_trades_per_day")
    if mtpd is not None:
        if not _is_int_value(mtpd):
            violations.append(f"max_trades_per_day={mtpd!r}: must be an integer")
        elif mtpd < 1:
            violations.append(
                f"max_trades_per_day={mtpd}: must be >= 1 (use null for no daily cap)"
            )
        elif mtpd > 20:
            violations.append(
                f"max_trades_per_day={mtpd}: must be <= 20 (transcript says 3-5; >20 effectively disables the cap)"
            )
    bias = config.get("direction_bias")
    valid_biases = {"long_only", "short_only", "both"}
    if bias is not None and bias not in valid_biases:
        violations.append(f"direction_bias='{bias}': must be one of {sorted(valid_biases)}")
    cutoff = config.get("entry_cutoff_time")
    if cutoff is not None and isinstance(cutoff, str):
        try:
            parts = cutoff.split(":")
            hour, minute = int(parts[0]), int(parts[1])
            cutoff_minutes = hour * 60 + minute
            market_open = 9 * 60 + 30
            market_close = 16 * 60
            if cutoff_minutes < market_open:
                violations.append(f"entry_cutoff_time='{cutoff}': before market open (09:30)")
            if cutoff_minutes >= market_close:
                violations.append(f"entry_cutoff_time='{cutoff}': at or after market close (16:00)")
        except (ValueError, IndexError):
            violations.append(f"entry_cutoff_time='{cutoff}': invalid time format (use HH:MM)")
    gap_pct = config.get("gap_pct")
    if gap_pct is not None:
        if not _is_number_value(gap_pct):
            violations.append(f"gap_pct={gap_pct!r}: must be numeric")
        else:
            if float(gap_pct) <= 0:
                violations.append(f"gap_pct={gap_pct}: must be > 0")
            if float(gap_pct) > 0.20:
                violations.append(
                    f"gap_pct={gap_pct}: must be <= 0.20 (20%; higher filters out everything)"
                )
    rsl = config.get("range_shift_lookback")
    if rsl is not None:
        if not _is_int_value(rsl):
            violations.append(f"range_shift_lookback={rsl!r}: must be an integer")
        elif not (5 <= rsl <= 100):
            violations.append(f"range_shift_lookback={rsl}: must be between 5 and 100")
    max_hold = config.get("max_hold_bars")
    if max_hold is not None:
        if not _is_int_value(max_hold):
            violations.append(f"max_hold_bars={max_hold!r}: must be an integer")
        elif max_hold < 1:
            violations.append(f"max_hold_bars={max_hold}: must be >= 1")
        elif max_hold > 390:
            violations.append(f"max_hold_bars={max_hold}: must be <= 390")
    # Boolean levers must be real booleans. bool("any non-empty string") is True,
    # so a descriptive value silently flips the lever on and backtests the wrong
    # thing -- reject it loudly instead. (isinstance bool, not _is_int_value,
    # because bool is an int subclass.)
    gap_filter = config.get("gap_filter")
    if gap_filter is not None and not isinstance(gap_filter, bool):
        violations.append(
            f"gap_filter={gap_filter!r}: must be a boolean (true/false), not a label. "
            "A specific exclusion (e.g. by gap direction / bars since open) is not "
            "expressible as the gap_filter flag."
        )
    use_range_shift = config.get("use_range_shift")
    if use_range_shift is not None and not isinstance(use_range_shift, bool):
        violations.append(f"use_range_shift={use_range_shift!r}: must be a boolean (true/false)")
    gap_exclude = config.get("gap_exclude")
    if gap_exclude is not None and not isinstance(gap_exclude, bool):
        violations.append(f"gap_exclude={gap_exclude!r}: must be a boolean (true/false)")
    gap_exclude_pct = config.get("gap_exclude_pct")
    if gap_exclude_pct is not None:
        if not _is_number_value(gap_exclude_pct) or not (0 < float(gap_exclude_pct) <= 0.20):
            violations.append(f"gap_exclude_pct={gap_exclude_pct!r}: must be a number in (0, 0.20]")
    gap_exclude_direction = config.get("gap_exclude_direction")
    if gap_exclude_direction is not None and gap_exclude_direction not in {"up", "down"}:
        violations.append(
            f"gap_exclude_direction={gap_exclude_direction!r}: must be 'up' or 'down'"
        )
    exclude_first_bars = config.get("exclude_first_bars")
    if exclude_first_bars is not None:
        if isinstance(exclude_first_bars, bool) or not _is_int_value(exclude_first_bars):
            violations.append(f"exclude_first_bars={exclude_first_bars!r}: must be an integer")
        elif exclude_first_bars < 0:
            violations.append(f"exclude_first_bars={exclude_first_bars}: must be >= 0 (0 = off)")
        elif exclude_first_bars > 78:
            violations.append(
                f"exclude_first_bars={exclude_first_bars}: must be <= 78 (one session of 5-min bars)"
            )
    return violations
