from __future__ import annotations

import pandas as pd

from strategies.orb.regime_filter import apply_regime_gate, classify_regimes


def test_high_vol_regime_is_lagged_to_avoid_same_day_lookahead() -> None:
    index = pd.date_range("2026-01-01", periods=12, freq="D")
    open_ = pd.DataFrame({"SPY": [100.0] * 12}, index=index)
    high = pd.DataFrame({"SPY": [105.0] * 9 + [100.5, 130.0, 105.0]}, index=index)
    low = pd.DataFrame({"SPY": [95.0] * 9 + [99.5, 70.0, 95.0]}, index=index)
    close = pd.DataFrame({"SPY": [100.0] * 12}, index=index)

    regimes = classify_regimes(open_, high, low, close, atr_period=1, atr_lookback=10)

    high_vol = regimes["high-vol"]
    assert bool(high_vol.loc[index[10].date(), "SPY"]) is False
    assert bool(high_vol.loc[index[11].date(), "SPY"]) is True


def test_apply_regime_gate_normalizes_lowercase_regime_aliases() -> None:
    trades = pd.DataFrame(
        {
            "entry_date": [pd.Timestamp("2026-01-02 10:00:00")],
            "symbol": ["SPY"],
            "pnl_pct": [0.01],
        }
    )
    regime_dates = [pd.Timestamp("2026-01-02").date()]
    regime_dict = {
        "wide-OR": pd.DataFrame({"SPY": [True]}, index=regime_dates),
    }

    gated = apply_regime_gate(trades, regime_dict, require_regimes={"wide-or"})

    assert len(gated) == 1
