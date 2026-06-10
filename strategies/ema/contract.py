from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompilationResult:
    status: str
    runtime_config: dict[str, Any]
    missing_primitives: list[str]
    normalized_contract: list[dict[str, Any]]


REQUIRED_TYPES = {"ema_length", "timeframe_long", "timeframe_short", "risk_reward"}
IMPLEMENTED_OPTIONAL_TYPES = {
    "data_universe",
    "symbols",
    "validation_start",
    "validation_end",
    "max_hold_bars",
    "min_stop_distance_pct",
    "max_stop_distance_pct",
    "trail_after_r",
}


def normalize_ema_contract(contract: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in primitive.items() if v is not None} for primitive in contract]


def compile_ema_contract(contract: list[dict[str, Any]]) -> CompilationResult:
    normalized = normalize_ema_contract(contract)
    if not normalized:
        return CompilationResult("invalid_contract", {}, ["empty_contract"], normalized)

    missing_primitives = [
        str(primitive.get("key") or primitive.get("type"))
        for primitive in normalized
        if primitive.get("type") == "missing_primitive"
    ]
    if missing_primitives:
        return CompilationResult("invalid_contract", {}, sorted(missing_primitives), normalized)

    seen = {primitive.get("type") for primitive in normalized}
    missing = sorted(REQUIRED_TYPES - seen)
    if missing:
        return CompilationResult("invalid_contract", {}, missing, normalized)

    implemented_types = (
        REQUIRED_TYPES
        | {
            "direction_bias",
            "range_shift",
            "entry_cutoff",
            "max_trades_per_day",
            "gap_filter",
            "gap_exclude",
        }
        | IMPLEMENTED_OPTIONAL_TYPES
    )
    unsupported_types = sorted(
        str(primitive.get("type"))
        for primitive in normalized
        if primitive.get("type") not in implemented_types
    )
    if unsupported_types:
        return CompilationResult("invalid_contract", {}, unsupported_types, normalized)

    # Defaults match transcript-grounded baseline (ema_base.yaml).
    # Keep the research window bounded when a raw primitive contract is run
    # directly from a job-scoped contract artifact instead of a pre-rendered
    # runtime config.
    from strategies.ema.defaults import _get_ema_defaults

    runtime = {
        "family": "ema",
        **_get_ema_defaults(),
    }
    for primitive in normalized:
        if primitive["type"] == "ema_length":
            runtime["ema_length"] = primitive["value"]
        elif primitive["type"] == "timeframe_long":
            runtime["timeframe_long"] = primitive["minutes"]
        elif primitive["type"] == "timeframe_short":
            runtime["timeframe_short"] = primitive["minutes"]
        elif primitive["type"] == "risk_reward":
            runtime["rr_ratio"] = primitive["rr_ratio"]
        elif primitive["type"] == "direction_bias":
            runtime["direction_bias"] = primitive["value"]
        elif primitive["type"] == "range_shift":
            runtime["use_range_shift"] = primitive.get("enabled", True)
            if "lookback" in primitive:
                runtime["range_shift_lookback"] = primitive["lookback"]
        elif primitive["type"] == "entry_cutoff":
            runtime["entry_cutoff_time"] = primitive["time"]
        elif primitive["type"] == "max_trades_per_day":
            runtime["max_trades_per_day"] = primitive["value"]
        elif primitive["type"] == "gap_filter":
            runtime["gap_filter"] = primitive.get("enabled", True)
            if "gap_pct" in primitive:
                runtime["gap_pct"] = primitive["gap_pct"]
        elif primitive["type"] == "gap_exclude":
            runtime["gap_exclude"] = primitive.get("enabled", True)
            if "gap_exclude_pct" in primitive:
                runtime["gap_exclude_pct"] = primitive["gap_exclude_pct"]
        elif primitive["type"] in {
            *IMPLEMENTED_OPTIONAL_TYPES,
        }:
            runtime[primitive["type"]] = primitive.get("value")

    # Validate before returning
    from strategies.ema.validate import validate_ema_runtime_config

    violations = validate_ema_runtime_config(runtime)
    if violations:
        return CompilationResult("rejected_at_compile", runtime, violations, normalized)

    return CompilationResult("ready_to_run", runtime, [], normalized)


def map_ema_config_changes_to_contract(config_changes: dict[str, Any]) -> list[dict[str, Any]]:
    from strategies.ema.defaults import _get_ema_defaults
    from strategies.ema.validate import supported_ema_runtime_keys

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
    if "gap_exclude" in config_changes:
        primitive_contract.append(
            {
                "type": "gap_exclude",
                "enabled": bool(config_changes["gap_exclude"]),
                "gap_exclude_pct": config_changes.get("gap_exclude_pct"),
            }
        )
    explicitly_mapped_keys = {
        "ema_length",
        "timeframe_long",
        "timeframe_short",
        "rr_ratio",
        "direction_bias",
        "entry_cutoff_time",
        "max_trades_per_day",
        "gap_filter",
        "gap_pct",
        "gap_exclude",
        "gap_exclude_pct",
        "use_range_shift",
        "range_shift_lookback",
    }
    supported = supported_ema_runtime_keys()
    for key, value in config_changes.items():
        if key in explicitly_mapped_keys or key == "family":
            continue
        if key in supported:
            primitive_contract.append({"type": key, "value": value})
        else:
            primitive_contract.append({"type": "missing_primitive", "key": key, "value": value})
    return primitive_contract
