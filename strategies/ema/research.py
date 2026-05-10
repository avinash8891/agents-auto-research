from __future__ import annotations

from family_research_spec import FamilyResearchSpec

EMA_RESEARCH_SPEC = FamilyResearchSpec(
    strategy_label="5 EMA reversal/pullback",
    one_thesis_label="5 EMA reversal/pullback",
    config_schema="""
SUPPORTED EMA CONFIG CHANGES:

All config_changes keys MUST come from this list:
  - ema_length
  - timeframe_long
  - timeframe_short
  - rr_ratio
  - direction_bias
  - entry_cutoff_time
  - max_trades_per_day
  - gap_filter
  - gap_pct
  - use_range_shift
  - range_shift_lookback

EMA FAMILY RULES:
- ema_length: integer EMA period used for the setup
- timeframe_long: integer minutes for bullish setup timeframe
- timeframe_short: integer minutes for bearish setup timeframe
- rr_ratio: float risk/reward multiple
- direction_bias: "both", "long_only", or "short_only"
- entry_cutoff_time: string HH:MM (e.g. "10:00") — only take entries before this time. null=all day.
- max_trades_per_day: integer — max trades per day across all symbols. null=unlimited.
- gap_filter: boolean — require a minimum opening gap before considering entries.
- gap_pct: float — gap threshold used when gap_filter=true.
- use_range_shift: boolean — enable range-shift filter logic.
- range_shift_lookback: integer — lookback bars for range-shift evaluation when enabled.

IMPORTANT:
- Do NOT emit ORB keys like universe_mode, skip_regimes, use_time_stop, use_rvol_gate, use_follow_through, or use_volatility_trail.
- If a thesis requires filters/exits not expressible through the EMA keys above, set requires_code_change=true and explain what primitive/config support is missing.
- Research should focus on EMA-family hypotheses such as EMA period choice, timeframe selection, risk/reward structure, entry timing, and daily trade limits.
""",
    research_questions=(
        "What EMA-based pullback or reversal entry structures are documented in literature or practitioner research?",
        "What EMA period choices are commonly used for intraday pullback/reversal setups and why?",
        "What timeframe combinations are used for long-vs-short EMA pullback confirmations?",
        "What risk-reward structures are documented for intraday EMA pullback/reversal strategies?",
    ),
    config_rules=(
        "ONLY use ema_length, timeframe_long, timeframe_short, rr_ratio, direction_bias, entry_cutoff_time, max_trades_per_day, gap_filter, gap_pct, use_range_shift, and range_shift_lookback in config_changes.",
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
    thesis_json_hint='"strategy_family": "ema"',
    allowed_config_keys=frozenset(
        {
            "ema_length",
            "timeframe_long",
            "timeframe_short",
            "rr_ratio",
            "direction_bias",
            "entry_cutoff_time",
            "max_trades_per_day",
            "gap_filter",
            "gap_pct",
            "use_range_shift",
            "range_shift_lookback",
        }
    ),
    resolution_config_keys=("timeframe_short", "timeframe_long"),
    # Concept regex map for Stage 2 hypothesis-config alignment scoring.
    # Each key here must also be a member of allowed_config_keys above.
    # Patterns are matched (case-insensitive) against the hypothesis+mechanism.
    key_concepts={
        "entry_cutoff_time": (
            r"entry.{0,10}tim",
            r"cutoff",
            r"time window",
            r"entry window",
            r"morning",
            r"first.{0,5}\d+.{0,5}min",
            r"open.{0,10}bar",
        ),
        "max_trades_per_day": (
            r"max.{0,5}trade",
            r"one.{0,5}trade.{0,10}day",
            r"single.{0,5}trade.{0,10}day",
            r"first.{0,10}trade",
            r"first.{0,10}executed.{0,10}trade",
            r"first.{0,10}setup",
            r"only.{0,10}first",
            r"position limit",
            r"portfolio",
            r"daily.{0,5}cap",
            r"trade.{0,5}capacity",
            r"number of trades",
        ),
        "rr_ratio": (
            r"risk.{0,3}reward",
            r"target.{0,5}ratio",
            r"r.?r.?ratio",
            r"profit target",
        ),
        "gap_filter": (r"gap", r"overnight"),
        "gap_pct": (r"gap",),
        "use_range_shift": (
            r"range.{0,5}shift",
            r"lookback",
            r"adaptive",
            r"context.{0,5}window",
        ),
        "range_shift_lookback": (r"range.{0,5}shift", r"lookback", r"adaptive"),
        "timeframe_short": (r"timeframe", r"bar.{0,3}size", r"resolution", r"5.?min"),
        "timeframe_long": (r"timeframe", r"bar.{0,3}size", r"resolution", r"15.?min"),
        "direction_bias": (r"direction", r"long.only", r"short.only", r"bias"),
    },
)
