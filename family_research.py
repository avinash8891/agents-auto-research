from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from experiment_schema import canonical_regimes, supported_config_keys


@dataclass(frozen=True)
class FamilyResearchSpec:
    strategy_label: str
    one_thesis_label: str
    config_schema: str
    research_questions: tuple[str, ...]
    config_rules: tuple[str, ...]
    prompt_focus: tuple[str, ...]
    thesis_json_hint: str
    allowed_config_keys: frozenset[str]


_CANONICAL_REGIMES = ", ".join(f'"{name}"' for name in canonical_regimes())
_ORB_SUPPORTED_KEYS = "\n".join(
    f"  - {key}" for key in supported_config_keys() if key != "_research_source"
)

ORB_CONFIG_SCHEMA = f"""
SUPPORTED CONFIG KEYS (backtest_orb.py + orb_signals.py + exits.py):

All config_changes keys MUST come from this list:
{_ORB_SUPPORTED_KEYS}

Data & dates (do NOT change these):
  data_dir: "data"
  symbols: null
  discovery_start/end, validation_start/end, holdout_start/end

Universe selection:
  universe_mode: null
  stocks_in_play_top_n: 20
  stocks_in_play_ranking: "first30_dollar_volume"

Opening range & entry:
  or_minutes: 30
  timeframe_minutes: 5
  rr_ratio: 2.0
  max_one_entry_per_day: true
  long_only: false

Entry filters:
  use_volume_filter: false
  use_trend_filter: false
  use_wide_or_filter: false
  use_follow_through: false
  wide_or_mult: 1.5
  narrow_or_mult: 0.5
  trend_ema_period: 20
  volume_lookback: 20
  use_rvol_gate: false
  rvol_threshold: 1.5
  rvol_lookback_days: 20
  rvol_baseline_stat: "mean"
  rvol_computation: "cumulative_or_window"
  rvol_min_history_days: 10

Exit controls:
  max_hold_bars: 78
  slippage_pct: 0.05
  use_time_stop: false
  time_stop_hour: 12
  time_stop_minute: 0
  use_failed_breakout_exit: false
  use_volatility_trail: false
  vol_trail_atr_mult: 1.0
  use_opposite_break_exit: false
  conservative_sl_fill: false

Regime gating:
  skip_regimes: []
  require_regimes: []

Available regime names: {_CANONICAL_REGIMES}
"""

FAMILY_RESEARCH_SPECS: dict[str, FamilyResearchSpec] = {
    "orb": FamilyResearchSpec(
        strategy_label="Opening Range Breakout (ORB)",
        one_thesis_label="Opening Range Breakout (ORB)",
        config_schema=ORB_CONFIG_SCHEMA,
        research_questions=(
            "What structural improvements to opening range breakout strategies are documented in literature?",
            "What regime filters have been shown to improve mean-reversion or breakout strategies?",
            "What exit mechanisms beyond fixed stop/target improve intraday momentum strategies?",
            "What universe selection methods improve intraday breakout strategy performance?",
        ),
        config_rules=(
            "ONLY use ORB supported config keys.",
            "Do not invent config keys; unsupported keys are silently ignored.",
            "Prefer structural improvements over micro-tuning.",
        ),
        prompt_focus=(
            "universe selection",
            "opening range filters",
            "regime gating",
            "intraday breakout exits",
        ),
        thesis_json_hint='"family": "universe" or "entry" or "exit" or "regime"',
        allowed_config_keys=frozenset(supported_config_keys()) - {"_research_source"},
    ),
}


def get_family_research_spec(name: str) -> FamilyResearchSpec:
    if name in FAMILY_RESEARCH_SPECS:
        return FAMILY_RESEARCH_SPECS[name]
    from strategies import STRATEGIES

    return STRATEGIES[name].research_spec


def infer_family_from_dir_name(dirname: str) -> str:
    return "ema" if "ema" in dirname else "orb"


def validate_family_config_changes(family_name: str, thesis: dict[str, Any]) -> dict[str, Any]:
    spec = get_family_research_spec(family_name)
    config_changes = thesis.get("config_changes") or {}
    invalid = sorted(set(config_changes) - spec.allowed_config_keys)
    if not invalid:
        return thesis
    sanitized = dict(thesis)
    sanitized["requires_code_change"] = True
    sanitized["invalid_config_keys"] = invalid
    sanitized["code_change_idea"] = sanitized.get("code_change_idea") or {
        "idea": f"{family_name} thesis requires unsupported runtime keys",
        "what_code_needs": f"Add {', '.join(invalid)} support to the {family_name} family compiler/runtime or reformulate the thesis.",
        "evidence": [f"Unsupported keys proposed for {family_name}: {', '.join(invalid)}"],
    }
    sanitized["config_changes"] = {}
    return sanitized
