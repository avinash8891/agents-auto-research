from __future__ import annotations

from typing import Any

from strategies.ema.defaults import _get_ema_defaults


def map_ema_config_changes_to_contract(config_changes: dict[str, Any]) -> list[dict[str, Any]]:
    config_changes = {**_get_ema_defaults(), **config_changes}
    primitive_contract: list[dict[str, Any]] = []
    if "ema_length" in config_changes:
        primitive_contract.append({"type": "ema_length", "value": config_changes["ema_length"]})
    if "timeframe_long" in config_changes:
        primitive_contract.append(
            {"type": "timeframe_long", "minutes": config_changes["timeframe_long"]}
        )
    if "timeframe_short" in config_changes:
        primitive_contract.append(
            {"type": "timeframe_short", "minutes": config_changes["timeframe_short"]}
        )
    if "rr_ratio" in config_changes:
        primitive_contract.append({"type": "risk_reward", "rr_ratio": config_changes["rr_ratio"]})
    if "direction_bias" in config_changes:
        primitive_contract.append(
            {"type": "direction_bias", "value": config_changes["direction_bias"]}
        )
    if "entry_cutoff_time" in config_changes:
        primitive_contract.append(
            {"type": "entry_cutoff", "time": config_changes["entry_cutoff_time"]}
        )
    if "max_trades_per_day" in config_changes:
        primitive_contract.append(
            {"type": "max_trades_per_day", "value": config_changes["max_trades_per_day"]}
        )
    if "gap_filter" in config_changes:
        primitive_contract.append(
            {
                "type": "gap_filter",
                "enabled": bool(config_changes["gap_filter"]),
                "gap_pct": config_changes.get("gap_pct"),
            }
        )
    if "use_range_shift" in config_changes:
        primitive_contract.append(
            {
                "type": "range_shift",
                "enabled": bool(config_changes["use_range_shift"]),
                "lookback": config_changes.get("range_shift_lookback"),
            }
        )
    return primitive_contract
