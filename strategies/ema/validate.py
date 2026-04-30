from __future__ import annotations

from typing import Any


def validate_ema_runtime_config(config: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    ema = config.get("ema_length")
    if ema is not None:
        if not isinstance(ema, (int, float)) or int(ema) < 2:
            violations.append(f"ema_length={ema}: must be >= 2 (EMA of 1 is just price)")
        if isinstance(ema, (int, float)) and int(ema) > 200:
            violations.append(f"ema_length={ema}: must be <= 200 (intraday bars, not daily)")
    rr = config.get("rr_ratio")
    if rr is not None:
        if not isinstance(rr, (int, float)) or float(rr) < 0.5:
            violations.append(
                f"rr_ratio={rr}: must be >= 0.5 (below 0.5 is guaranteed negative edge)"
            )
        if isinstance(rr, (int, float)) and float(rr) > 20:
            violations.append(f"rr_ratio={rr}: must be <= 20 (unreachable targets waste entries)")
    tf_short = config.get("timeframe_short")
    tf_long = config.get("timeframe_long")
    if tf_short is not None:
        if not isinstance(tf_short, (int, float)) or int(tf_short) < 1:
            violations.append(f"timeframe_short={tf_short}: must be >= 1 minute")
        if isinstance(tf_short, (int, float)) and int(tf_short) > 60:
            violations.append(f"timeframe_short={tf_short}: must be <= 60 minutes for intraday")
    if tf_long is not None:
        if not isinstance(tf_long, (int, float)) or int(tf_long) < 1:
            violations.append(f"timeframe_long={tf_long}: must be >= 1 minute")
        if isinstance(tf_long, (int, float)) and int(tf_long) > 60:
            violations.append(f"timeframe_long={tf_long}: must be <= 60 minutes for intraday")
    if tf_short is not None and tf_long is not None:
        if isinstance(tf_short, (int, float)) and isinstance(tf_long, (int, float)):
            if int(tf_short) > int(tf_long):
                violations.append(
                    f"timeframe_short={tf_short} > timeframe_long={tf_long}: short TF must be <= long TF"
                )
    mtpd = config.get("max_trades_per_day")
    if mtpd is not None:
        if isinstance(mtpd, (int, float)) and int(mtpd) < 1:
            violations.append(f"max_trades_per_day={mtpd}: must be >= 1 (0 disables trading)")
        if isinstance(mtpd, (int, float)) and int(mtpd) > 20:
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
    if gap_pct is not None and isinstance(gap_pct, (int, float)):
        if float(gap_pct) <= 0:
            violations.append(f"gap_pct={gap_pct}: must be > 0")
        if float(gap_pct) > 0.20:
            violations.append(
                f"gap_pct={gap_pct}: must be <= 0.20 (20%; higher filters out everything)"
            )
    rsl = config.get("range_shift_lookback")
    if rsl is not None:
        if not (5 <= int(rsl) <= 100):
            violations.append(f"range_shift_lookback={rsl}: must be between 5 and 100")
    return violations
