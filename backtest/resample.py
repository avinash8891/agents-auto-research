from __future__ import annotations

import pandas as pd


def build_timeframe_frame(
    open_: pd.Series,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    timeframe_minutes: int,
) -> pd.DataFrame:
    """Resample 5min bars to the requested timeframe."""
    rule = f"{timeframe_minutes}min"
    frame = pd.DataFrame(
        {
            "open": open_.resample(rule).first(),
            "close": close.resample(rule).last(),
            "high": high.resample(rule).max(),
            "low": low.resample(rule).min(),
        }
    ).dropna()
    return frame
