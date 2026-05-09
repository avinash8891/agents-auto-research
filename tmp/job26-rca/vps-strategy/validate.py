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
    alert_close_vs_ema = config.get("requires_engine_change__require_alert_close_vs_ema_confirmation")
    if alert_close_vs_ema is not None:
        if not isinstance(alert_close_vs_ema, dict):
            violations.append(
                "requires_engine_change__require_alert_close_vs_ema_confirmation="
                f"{alert_close_vs_ema!r}: must be an object or null"
            )
        else:
            enabled = alert_close_vs_ema.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__require_alert_close_vs_ema_confirmation.enabled="
                    f"{enabled!r}: must be a boolean"
                )
            direction = alert_close_vs_ema.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__require_alert_close_vs_ema_confirmation.direction="
                    f"{direction!r}: must be one of 'short', 'long', 'both'"
                )
            excluding = alert_close_vs_ema.get("apply_to_entry_buckets_excluding", None)
            if excluding is not None and not (
                isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)
            ):
                violations.append(
                    "requires_engine_change__require_alert_close_vs_ema_confirmation.apply_to_entry_buckets_excluding="
                    f"{excluding!r}: must be a list of bucket strings like ['09:30']"
                )
            require_leq = alert_close_vs_ema.get("require_alert_close_leq_ema", None)
            if require_leq is not None and not isinstance(require_leq, bool):
                violations.append(
                    "requires_engine_change__require_alert_close_vs_ema_confirmation.require_alert_close_leq_ema="
                    f"{require_leq!r}: must be a boolean"
                )

    secondary_override = config.get(
        "requires_engine_change__alert_close_vs_ema_secondary_geometry_override"
    )
    if secondary_override is not None:
        if not isinstance(secondary_override, dict):
            violations.append(
                "requires_engine_change__alert_close_vs_ema_secondary_geometry_override="
                f"{secondary_override!r}: must be an object or null"
            )
        else:
            enabled = secondary_override.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__alert_close_vs_ema_secondary_geometry_override.enabled="
                    f"{enabled!r}: must be a boolean"
                )
            direction = secondary_override.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__alert_close_vs_ema_secondary_geometry_override.direction="
                    f"{direction!r}: must be one of 'short', 'long', 'both'"
                )
            excluding = secondary_override.get("apply_to_entry_buckets_excluding", None)
            if excluding is not None and not (
                isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)
            ):
                violations.append(
                    "requires_engine_change__alert_close_vs_ema_secondary_geometry_override.apply_to_entry_buckets_excluding="
                    f"{excluding!r}: must be a list of bucket strings like ['09:30']"
                )
            override_when = secondary_override.get("override_when_alert_close_gt_ema", None)
            if override_when is not None and not isinstance(override_when, bool):
                violations.append(
                    "requires_engine_change__alert_close_vs_ema_secondary_geometry_override.override_when_alert_close_gt_ema="
                    f"{override_when!r}: must be a boolean"
                )
            max_close_loc = secondary_override.get("require_close_location_in_range_leq", None)
            if max_close_loc is not None and not _is_number_value(max_close_loc):
                violations.append(
                    "requires_engine_change__alert_close_vs_ema_secondary_geometry_override.require_close_location_in_range_leq="
                    f"{max_close_loc!r}: must be a number"
                )

    bearish_alert_candle = config.get(
        "requires_engine_change__require_bearish_alert_candle_confirmation"
    )
    if bearish_alert_candle is not None:
        if not isinstance(bearish_alert_candle, dict):
            violations.append(
                "requires_engine_change__require_bearish_alert_candle_confirmation="
                f"{bearish_alert_candle!r}: must be an object or null"
            )
        else:
            enabled = bearish_alert_candle.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__require_bearish_alert_candle_confirmation.enabled="
                    f"{enabled!r}: must be a boolean"
                )
            direction = bearish_alert_candle.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__require_bearish_alert_candle_confirmation.direction="
                    f"{direction!r}: must be one of 'short', 'long', 'both'"
                )
            excluding = bearish_alert_candle.get("apply_to_entry_buckets_excluding", None)
            if excluding is not None and not (
                isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)
            ):
                violations.append(
                    "requires_engine_change__require_bearish_alert_candle_confirmation.apply_to_entry_buckets_excluding="
                    f"{excluding!r}: must be a list of bucket strings like ['09:30']"
                )
            require_lt_open = bearish_alert_candle.get("require_alert_close_lt_open", None)
            if require_lt_open is not None and not isinstance(require_lt_open, bool):
                violations.append(
                    "requires_engine_change__require_bearish_alert_candle_confirmation.require_alert_close_lt_open="
                    f"{require_lt_open!r}: must be a boolean"
                )

    alert_candle_close_location = config.get(
        "requires_engine_change__require_alert_candle_close_location_confirmation"
    )
    if alert_candle_close_location is not None:
        if not isinstance(alert_candle_close_location, dict):
            violations.append(
                "requires_engine_change__require_alert_candle_close_location_confirmation="
                f"{alert_candle_close_location!r}: must be an object or null"
            )
        else:
            enabled = alert_candle_close_location.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__require_alert_candle_close_location_confirmation.enabled="
                    f"{enabled!r}: must be a boolean"
                )
            direction = alert_candle_close_location.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__require_alert_candle_close_location_confirmation.direction="
                    f"{direction!r}: must be one of 'short', 'long', 'both'"
                )
            excluding = alert_candle_close_location.get("apply_to_entry_buckets_excluding", None)
            if excluding is not None and not (
                isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)
            ):
                violations.append(
                    "requires_engine_change__require_alert_candle_close_location_confirmation.apply_to_entry_buckets_excluding="
                    f"{excluding!r}: must be a list of bucket strings like ['09:30']"
                )
            max_loc = alert_candle_close_location.get("max_close_location_in_range", None)
            if max_loc is not None and not _is_number_value(max_loc):
                violations.append(
                    "requires_engine_change__require_alert_candle_close_location_confirmation.max_close_location_in_range="
                    f"{max_loc!r}: must be numeric in [0, 1]"
                )
            max_wick = alert_candle_close_location.get("max_upper_wick_share", None)
            if max_wick is not None and not _is_number_value(max_wick):
                violations.append(
                    "requires_engine_change__require_alert_candle_close_location_confirmation.max_upper_wick_share="
                    f"{max_wick!r}: must be numeric in [0, 1]"
                )

    alert_candle_body_dominance = config.get(
        "requires_engine_change__require_alert_candle_body_dominance_confirmation"
    )
    if alert_candle_body_dominance is not None:
        if not isinstance(alert_candle_body_dominance, dict):
            violations.append(
                "requires_engine_change__require_alert_candle_body_dominance_confirmation="
                f"{alert_candle_body_dominance!r}: must be an object or null"
            )
        else:
            enabled = alert_candle_body_dominance.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__require_alert_candle_body_dominance_confirmation.enabled="
                    f"{enabled!r}: must be a boolean"
                )
            direction = alert_candle_body_dominance.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__require_alert_candle_body_dominance_confirmation.direction="
                    f"{direction!r}: must be one of 'short', 'long', 'both'"
                )
            excluding = alert_candle_body_dominance.get("apply_to_entry_buckets_excluding", None)
            if excluding is not None and not (
                isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)
            ):
                violations.append(
                    "requires_engine_change__require_alert_candle_body_dominance_confirmation.apply_to_entry_buckets_excluding="
                    f"{excluding!r}: must be a list of bucket strings like ['09:30']"
                )
            min_body_pct = alert_candle_body_dominance.get("min_body_pct", None)
            if min_body_pct is not None and not _is_number_value(min_body_pct):
                violations.append(
                    "requires_engine_change__require_alert_candle_body_dominance_confirmation.min_body_pct="
                    f"{min_body_pct!r}: must be numeric in [0, 1]"
                )
    exhaustion_gate = config.get("requires_engine_change__apply_0930_exhaustion_confirmation_to_0940")
    if exhaustion_gate is not None and not isinstance(exhaustion_gate, bool):
        violations.append(
            "requires_engine_change__apply_0930_exhaustion_confirmation_to_0940="
            f"{exhaustion_gate!r}: must be a boolean"
        )
    opening_gate = config.get("symbol_day_opening_setup_gate_by_0940_raw_setup_enabled")
    if opening_gate is not None and not isinstance(opening_gate, bool):
        violations.append(
            "symbol_day_opening_setup_gate_by_0940_raw_setup_enabled="
            f"{opening_gate!r}: must be a boolean"
        )
    opening_gate_v2 = config.get("requires_engine_change__opening_setup_gate_by_0940_raw_setup")
    if opening_gate_v2 is not None and not isinstance(opening_gate_v2, bool):
        violations.append(
            "requires_engine_change__opening_setup_gate_by_0940_raw_setup="
            f"{opening_gate_v2!r}: must be a boolean"
        )
    bucket_filter = config.get("requires_engine_change__bucket_specific_max_stop_distance_filter")
    if bucket_filter is not None:
        if isinstance(bucket_filter, dict):
            bucket_filter_items = [bucket_filter]
        elif isinstance(bucket_filter, list):
            bucket_filter_items = bucket_filter
        else:
            violations.append(
                "requires_engine_change__bucket_specific_max_stop_distance_filter="
                f"{bucket_filter!r}: must be an object, a list of objects, or null"
            )
            bucket_filter_items = []

        for i, item in enumerate(bucket_filter_items):
            if not isinstance(item, dict):
                violations.append(
                    "requires_engine_change__bucket_specific_max_stop_distance_filter="
                    f"[{i}]={item!r}: must be an object"
                )
                continue
            enabled = item.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__bucket_specific_max_stop_distance_filter="
                    f"[{i}].enabled={enabled!r}: must be a boolean"
                )
            direction = item.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__bucket_specific_max_stop_distance_filter="
                    f"[{i}].direction={direction!r}: must be one of 'short', 'long', 'both'"
                )
            use_alert_ts = item.get("use_alert_bar_timestamp_for_bucket", None)
            if use_alert_ts is not None and not isinstance(use_alert_ts, bool):
                violations.append(
                    "requires_engine_change__bucket_specific_max_stop_distance_filter="
                    f"[{i}].use_alert_bar_timestamp_for_bucket={use_alert_ts!r}: must be a boolean"
                )

            by_bucket = item.get("max_stop_distance_pct_by_entry_bucket", None)
            if by_bucket is not None:
                if not isinstance(by_bucket, dict) or not all(isinstance(k, str) for k in by_bucket.keys()):
                    violations.append(
                        "requires_engine_change__bucket_specific_max_stop_distance_filter="
                        f"[{i}].max_stop_distance_pct_by_entry_bucket={by_bucket!r}: must be an object mapping bucket strings like '09:30' to numeric percents"
                    )
                    continue
                for bucket, max_sd in by_bucket.items():
                    if max_sd is not None and not _is_number_value(max_sd):
                        violations.append(
                            "requires_engine_change__bucket_specific_max_stop_distance_filter="
                            f"[{i}].max_stop_distance_pct_by_entry_bucket[{bucket!r}]={max_sd!r}: must be numeric percent like 0.66"
                        )
                continue
            bucket = item.get("entry_bucket")
            if bucket is not None and not isinstance(bucket, str):
                violations.append(
                    "requires_engine_change__bucket_specific_max_stop_distance_filter="
                    f"[{i}].entry_bucket={bucket!r}: must be a string time like '09:50'"
                )
            max_sd = item.get("max_stop_distance_pct")
            if max_sd is not None and not _is_number_value(max_sd):
                violations.append(
                    "requires_engine_change__bucket_specific_max_stop_distance_filter="
                    f"[{i}].max_stop_distance_pct={max_sd!r}: must be numeric percent like 0.66"
                )

    post0930_filter = config.get("requires_engine_change__post0930_max_stop_distance_filter")
    if post0930_filter is not None:
        if not isinstance(post0930_filter, dict):
            violations.append(
                "requires_engine_change__post0930_max_stop_distance_filter="
                f"{post0930_filter!r}: must be an object or null"
            )
        else:
            enabled = post0930_filter.get("enabled", False)
            if not isinstance(enabled, bool):
                violations.append(
                    "requires_engine_change__post0930_max_stop_distance_filter.enabled="
                    f"{enabled!r}: must be a boolean"
                )
            direction = post0930_filter.get("direction", None)
            if direction is not None and direction not in {"short", "long", "both"}:
                violations.append(
                    "requires_engine_change__post0930_max_stop_distance_filter.direction="
                    f"{direction!r}: must be one of 'short', 'long', 'both'"
                )
            max_sd = post0930_filter.get("max_stop_distance_pct", None)
            if max_sd is not None and not _is_number_value(max_sd):
                violations.append(
                    "requires_engine_change__post0930_max_stop_distance_filter.max_stop_distance_pct="
                    f"{max_sd!r}: must be numeric percent like 0.66"
                )
            excluding = post0930_filter.get("apply_to_entry_buckets_excluding", None)
            if excluding is not None and not (
                isinstance(excluding, list) and all(isinstance(x, str) for x in excluding)
            ):
                violations.append(
                    "requires_engine_change__post0930_max_stop_distance_filter.apply_to_entry_buckets_excluding="
                    f"{excluding!r}: must be a list of bucket strings like ['09:30']"
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
    return violations
