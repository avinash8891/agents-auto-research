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
_ORB_SUPPORTED_KEYS = "\n".join(f"  - {key}" for key in supported_config_keys() if key != "_research_source")

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

EMA_CONFIG_SCHEMA = """
SUPPORTED EMA CONFIG CHANGES:

All config_changes keys MUST come from this list:
  - ema_length
  - timeframe_long
  - timeframe_short
  - rr_ratio
  - direction_bias
  - entry_cutoff_time
  - max_trades_per_day

EMA FAMILY RULES:
- ema_length: integer EMA period used for the setup
- timeframe_long: integer minutes for bullish setup timeframe
- timeframe_short: integer minutes for bearish setup timeframe
- rr_ratio: float risk/reward multiple
- direction_bias: "both", "long_only", or "short_only"
- entry_cutoff_time: string HH:MM (e.g. "10:00") — only take entries before this time. null=all day.
- max_trades_per_day: integer — max trades per day across all symbols. null=unlimited.

IMPORTANT:
- Do NOT emit ORB keys like universe_mode, skip_regimes, use_time_stop, use_rvol_gate, use_follow_through, or use_volatility_trail.
- If a thesis requires filters/exits not expressible through the EMA keys above, set requires_code_change=true and explain what primitive/config support is missing.
- Research should focus on EMA-family hypotheses such as EMA period choice, timeframe selection, risk/reward structure, entry timing, and daily trade limits.
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
    "ema": FamilyResearchSpec(
        strategy_label="5 EMA reversal/pullback",
        one_thesis_label="5 EMA reversal/pullback",
        config_schema=EMA_CONFIG_SCHEMA,
        research_questions=(
            "What EMA-based pullback or reversal entry structures are documented in literature or practitioner research?",
            "What EMA period choices are commonly used for intraday pullback/reversal setups and why?",
            "What timeframe combinations are used for long-vs-short EMA pullback confirmations?",
            "What risk-reward structures are documented for intraday EMA pullback/reversal strategies?",
        ),
        config_rules=(
            "ONLY use ema_length, timeframe_long, timeframe_short, rr_ratio, direction_bias, entry_cutoff_time, and max_trades_per_day in config_changes.",
            "If a thesis depends on time stops, volume gates, universe filters, or regime filters, mark it requires_code_change=true instead of emitting ORB keys.",
            "Prefer hypotheses that can be tested by changing EMA period, timeframe pairing, risk/reward structure, entry timing, or daily trade limits.",
        ),
        prompt_focus=(
            "EMA period selection",
            "timeframe asymmetry",
            "risk-reward structure",
            "direction bias",
            "entry timing window",
            "daily trade frequency",
            "EMA pullback/reversal mechanics",
        ),
        thesis_json_hint='"family": "entry" or "exit"',
        allowed_config_keys=frozenset({"ema_length", "timeframe_long", "timeframe_short", "rr_ratio", "direction_bias", "entry_cutoff_time", "max_trades_per_day", "gap_filter", "gap_pct", "use_range_shift", "range_shift_lookback"}),
    ),
}


def get_family_research_spec(name: str) -> FamilyResearchSpec:
    return FAMILY_RESEARCH_SPECS[name]


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
