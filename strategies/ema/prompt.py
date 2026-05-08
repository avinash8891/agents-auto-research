DESCRIPTION_FOR_RESEARCH = """5 EMA PULLBACK/REVERSAL STRATEGY

Mechanics:
- Uses an exponential moving average (EMA) on intraday bars.
- BEARISH (short) setups use a shorter timeframe (e.g. 5min bars).
- BULLISH (long) setups use a longer timeframe (e.g. 15min bars).
- Entry occurs when price pulls back to the EMA and reverses.
- Entry is at the alert candle's extreme (break level), not next-bar open.
- Stop is at the alert candle's opposite extreme.
- Target = entry + risk-reward ratio * risk distance.
- Each timeframe is self-contained (no cross-timeframe merging).
- Grounded in practitioner transcripts: primarily a short-selling strategy,
  entries concentrated in first 30 minutes after open.

To understand what the engine supports and what can be changed,
READ THE SOURCE CODE. Do not guess parameter names.

Source code for signal mechanics (use these to verify hypotheses):
- strategies/ema/signals.py: signal generation, alert candle detection, EMA computation,
  daily reset logic, ema_alert_carry() stateful loop
- strategies/ema/exits.py: exit logic (stop/target/timeout)
- strategies/ema/strategy.py: entry filters, main backtest orchestration"""
