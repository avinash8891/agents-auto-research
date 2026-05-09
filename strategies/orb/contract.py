from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CANONICAL_PRIMITIVE_ALIASES: dict[str, str] = {
    "stocks_in_play": "universe_stocks_in_play",
    "symbol_universe": "universe_symbols",
    "universe_filter": "universe_filter",
    "narrow_opening_range": "opening_range_narrow",
    "time_stop": "time_stop",
    "trend_alignment": "trend_alignment",
    "relative_opening_volume": "rvol_gate",
    "opening_range": "opening_range",
    "breakout_trigger": "breakout_trigger",
    "regime_filter": "regime_filter",
    "entry_signal": "entry_signal",
    "stop_loss": "stop_loss",
    "target": "target",
    "config_changes_passthrough": "config_changes_passthrough",
}

SUPPORTED_PRIMITIVES: set[str] = {
    "universe_stocks_in_play",
    "universe_symbols",
    "universe_filter",
    "opening_range_narrow",
    "opening_range",
    "rvol_gate",
    "breakout_trigger",
    "time_stop",
    "trend_alignment",
    "regime_filter",
    "entry_signal",
    "stop_loss",
    "target",
    "config_changes_passthrough",
}


@dataclass
class CompilationResult:
    status: str
    runtime_config: dict[str, Any]
    missing_primitives: list[str]
    normalized_contract: list[dict[str, Any]]


def normalize_contract(contract: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for primitive in contract:
        canonical_type = CANONICAL_PRIMITIVE_ALIASES.get(
            primitive.get("type"), primitive.get("type")
        )
        normalized.append({**primitive, "type": canonical_type})
    return normalized


def resolve_contract_support(contract: list[dict[str, Any]]) -> dict[str, Any]:
    contract = normalize_contract(contract)
    missing: list[str] = []
    for primitive in contract:
        primitive_type = primitive.get("type")
        if primitive_type not in SUPPORTED_PRIMITIVES and primitive_type not in missing:
            missing.append(str(primitive_type))
    return {
        "supported": not missing,
        "missing_primitive_types": missing,
    }


def render_contract_to_runtime_config(contract: list[dict[str, Any]]) -> dict[str, Any]:
    contract = normalize_contract(contract)
    rendered: dict[str, Any] = {}
    for primitive in contract:
        primitive_type = primitive["type"]
        if primitive_type == "universe_stocks_in_play":
            rendered["universe_mode"] = "stocks_in_play"
            rendered["stocks_in_play_top_n"] = primitive.get("top_n", 20)
            rendered["stocks_in_play_ranking"] = primitive.get("ranking", "first30_dollar_volume")
        elif primitive_type == "universe_symbols":
            rendered["symbols"] = primitive["symbols"]
        elif primitive_type == "opening_range_narrow":
            rendered["narrow_or_mult"] = primitive["threshold_mult"]
            if primitive.get("mode") == "require":
                rendered["require_regimes"] = ["narrow-OR"]
        elif primitive_type == "opening_range":
            rendered["or_minutes"] = primitive["or_minutes"]
        elif primitive_type == "time_stop":
            rendered["use_time_stop"] = True
            rendered["time_stop_hour"] = primitive.get("hour", primitive.get("time_stop_hour"))
            rendered["time_stop_minute"] = primitive.get(
                "minute", primitive.get("time_stop_minute", 0)
            )
        elif primitive_type == "trend_alignment":
            rendered["use_trend_filter"] = True
            rendered["trend_ema_period"] = primitive["ema_period"]
        elif primitive_type == "rvol_gate":
            rendered["use_rvol_gate"] = True
            rendered["rvol_threshold"] = primitive.get("threshold", primitive.get("rvol_threshold"))
            rendered["rvol_lookback_days"] = primitive.get(
                "lookback_days", primitive.get("rvol_lookback_days")
            )
            baseline_aliases = {"sma": "mean", "ema": "median"}
            raw_baseline = primitive.get(
                "baseline_stat", primitive.get("baseline", primitive.get("rvol_baseline_stat"))
            )
            if raw_baseline is not None:
                rendered["rvol_baseline_stat"] = baseline_aliases.get(raw_baseline, raw_baseline)
            if "min_history_days" in primitive or "rvol_min_history_days" in primitive:
                rendered["rvol_min_history_days"] = primitive.get(
                    "min_history_days", primitive.get("rvol_min_history_days")
                )
            if "computation" in primitive:
                computation = primitive["computation"]
                if computation == "cumulative_or_window_volume_vs_trailing_mean":
                    computation = "cumulative_or_window"
                rendered["rvol_computation"] = computation
        elif primitive_type == "breakout_trigger":
            if "outside_or_bar_policy" in primitive:
                rendered["outside_or_bar_policy"] = primitive["outside_or_bar_policy"]
        elif primitive_type == "universe_filter":
            mode = primitive.get("mode")
            if mode == "stocks_in_play":
                rendered["universe_mode"] = "stocks_in_play"
                rendered["stocks_in_play_top_n"] = primitive.get("top_n", 20)
                rank_by = primitive.get("rank_by", "first30_dollar_volume")
                ranking_map = {
                    "prior_day_dollar_volume_gap": "first30_dollar_volume",
                    "first30_dollar_volume": "first30_dollar_volume",
                    "first30_relative_volume": "first30_relative_volume",
                }
                rendered["stocks_in_play_ranking"] = ranking_map.get(rank_by, rank_by)
        elif primitive_type == "entry_signal":
            rendered["long_only"] = primitive.get("direction") == "long_only"
        elif primitive_type == "stop_loss":
            pass
        elif primitive_type == "target":
            if "rr_ratio" in primitive:
                rendered["rr_ratio"] = primitive["rr_ratio"]
        elif primitive_type == "regime_filter":
            if "require_regimes" in primitive:
                rendered["require_regimes"] = primitive["require_regimes"]
        elif primitive_type == "config_changes_passthrough":
            rendered.update(primitive.get("config_changes", {}))
    return rendered


def compile_contract(contract: list[dict[str, Any]]) -> CompilationResult:
    normalized = normalize_contract(contract)
    if not normalized:
        return CompilationResult(
            status="invalid_contract",
            runtime_config={},
            missing_primitives=["empty_contract"],
            normalized_contract=[],
        )
    support = resolve_contract_support(normalized)
    runtime_config = render_contract_to_runtime_config(normalized) if support["supported"] else {}
    return CompilationResult(
        status="ready_to_run" if support["supported"] else "requires_runtime_support",
        runtime_config=runtime_config,
        missing_primitives=support["missing_primitive_types"],
        normalized_contract=normalized,
    )
