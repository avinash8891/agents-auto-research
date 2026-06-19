DESCRIPTION_FOR_RESEARCH = """OPENING RANGE BREAKOUT (ORB) STRATEGY

Mechanics:
- Computes the Opening Range (OR) from the first N minutes of trading (configurable, default 30 min).
- OR high = highest high during OR window; OR low = lowest low.
- Long entry: first bar that breaks above OR high (next-bar open after breakout).
- Short entry: first bar that breaks below OR low.
- Stop loss: opposite side of the opening range (long stop = OR low, short stop = OR high).
- Target = entry + risk-reward ratio * risk distance (default RR=2).
- Exits: target hit, stop hit, optional time stop (opt-in, off by default),
  max hold bars, volatility trailing stop, failed breakout reversal,
  opposite-side break.
- Regime classification: each day is classified as wide-OR, narrow-OR,
  trend-day, chop-day, or normal based on OR width and intraday behavior.
- Regime gating: can skip or require specific regime types.
- Universe filter: stocks-in-play (top-N by first-30-min dollar volume or
  relative volume) or explicit symbol list.
- Relative volume (RVOL) gate: optional filter requiring volume above
  trailing baseline before taking entries.

To understand what the engine supports and what can be changed,
READ THE SOURCE CODE. Do not guess parameter names.

Source code for signal mechanics (use these to verify hypotheses):
- strategies/orb/signals.py: OR computation, breakout detection, entry/stop/target calc
- strategies/orb/exits.py: exit logic (stop, target, time stop, trailing stop, failed breakout)
- strategies/orb/regime_filter.py: regime classification (wide/narrow OR, trend/chop day)
- strategies/orb/runner.py: main backtest orchestration, universe filtering"""
