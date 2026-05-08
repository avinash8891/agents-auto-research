from __future__ import annotations

from family_research_spec import FamilyResearchSpec
from strategies.orb.schema import canonical_regimes, supported_config_keys

_CANONICAL_REGIMES = ", ".join(f'"{name}"' for name in canonical_regimes())
_ORB_SUPPORTED_KEYS = "\n".join(
    f"  - {key}" for key in supported_config_keys() if key != "_research_source"
)

ORB_CONFIG_SCHEMA = f"""
SUPPORTED CONFIG KEYS (backtest_orb.py + orb_signals.py + exits.py):

All config_changes keys MUST come from this list:
{_ORB_SUPPORTED_KEYS}

Data & dates (do NOT change these):
  data_universe: "nasdaq143"
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

ORB_RESEARCH_SPEC = FamilyResearchSpec(
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
    thesis_json_hint='"strategy_family": "orb"',
    allowed_config_keys=frozenset(supported_config_keys()) - {"_research_source"},
    resolution_config_keys=("timeframe_minutes",),
)
