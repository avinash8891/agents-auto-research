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


def normalize_ema_contract(contract: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in primitive.items() if v is not None} for primitive in contract]


def compile_ema_contract(contract: list[dict[str, Any]]) -> CompilationResult:
    normalized = normalize_ema_contract(contract)
    if not normalized:
        return CompilationResult("invalid_contract", {}, ["empty_contract"], normalized)

    seen = {primitive.get("type") for primitive in normalized}
    missing = sorted(REQUIRED_TYPES - seen)
    if missing:
        return CompilationResult("invalid_contract", {}, missing, normalized)

    # Defaults match transcript-grounded baseline (ema_base.yaml).
    # Keep the research window bounded when a raw primitive contract is run
    # directly from ema-contracts/ instead of a pre-rendered runtime config.
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

    # Validate before returning
    from strategies.ema.validate import validate_ema_runtime_config

    violations = validate_ema_runtime_config(runtime)
    if violations:
        return CompilationResult(
            "rejected_at_compile", runtime, violations, normalized
        )

    return CompilationResult("ready_to_run", runtime, [], normalized)
