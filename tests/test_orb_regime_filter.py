from __future__ import annotations

import pandas as pd

from strategies.orb.regime_filter import apply_regime_gate, classify_regimes

def _single_symbol_intraday_frames(
    daily_ranges: list[float],
    opening_range_widths: list[float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    or_high_rows: list[dict[str, object]] = []
    or_low_rows: list[dict[str, object]] = []
    start = pd.Timestamp("2024-01-02")

    for day_offset, daily_range in enumerate(daily_ranges):
        day = start + pd.Timedelta(days=day_offset)
        half_range = daily_range / 2
        or_width = (
            opening_range_widths[day_offset]
            if opening_range_widths is not None
            else daily_range / 4
        )

        for minute in (0, 390):
            ts = day + pd.Timedelta(hours=9, minutes=30 + minute)
            rows.append(
                {
                    "timestamp": ts,
                    "open": 100.0,
                    "high": 100.0 + half_range,
                    "low": 100.0 - half_range,
                    "close": 100.0,
                }
            )
            or_high_rows.append({"timestamp": ts, "AAA": 100.0 + or_width / 2})
            or_low_rows.append({"timestamp": ts, "AAA": 100.0 - or_width / 2})

    raw = pd.DataFrame(rows).set_index("timestamp")
    open_ = raw[["open"]].rename(columns={"open": "AAA"})
    high = raw[["high"]].rename(columns={"high": "AAA"})
    low = raw[["low"]].rename(columns={"low": "AAA"})
    close = raw[["close"]].rename(columns={"close": "AAA"})
    or_high = pd.DataFrame(or_high_rows).set_index("timestamp")
    or_low = pd.DataFrame(or_low_rows).set_index("timestamp")
    return open_, high, low, close, or_high, or_low


def test_high_vol_regime_lags_same_day_full_session_volatility_before_gating() -> None:
    open_, high, low, close, or_high, or_low = _single_symbol_intraday_frames(
        [10.0] * 9 + [1.0, 100.0]
    )

    regimes = classify_regimes(
        open_,
        high,
        low,
        close,
        or_high=or_high,
        or_low=or_low,
        atr_period=1,
        atr_lookback=10,
    )
    trade_date = pd.Timestamp("2024-01-12")
    trades = pd.DataFrame(
        [
            {
                "entry_date": trade_date + pd.Timedelta(hours=10),
                "symbol": "AAA",
                "pnl_pct": 0.01,
            }
        ]
    )

    kept = apply_regime_gate(trades, regimes, skip_regimes={"high-vol"})

    assert not regimes["high-vol"].at[trade_date.date(), "AAA"]
    assert len(kept) == 1


def test_wide_or_regime_uses_same_day_opening_range_because_it_is_known_at_entry() -> None:
    open_, high, low, close, or_high, or_low = _single_symbol_intraday_frames(
        [1.0] * 11,
        opening_range_widths=[1.0] * 10 + [10.0],
    )

    regimes = classify_regimes(
        open_,
        high,
        low,
        close,
        or_high=or_high,
        or_low=or_low,
        atr_period=1,
        atr_lookback=10,
    )
    trade_date = pd.Timestamp("2024-01-12")
    trades = pd.DataFrame(
        [
            {
                "entry_date": trade_date + pd.Timedelta(hours=10),
                "symbol": "AAA",
                "pnl_pct": 0.01,
            }
        ]
    )

    kept = apply_regime_gate(trades, regimes, skip_regimes={"wide-OR"})

    assert regimes["wide-OR"].at[trade_date.date(), "AAA"]
    assert kept.empty


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
