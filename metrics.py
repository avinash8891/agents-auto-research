"""Shared metrics computation for all strategy backtests.

Expects a trades DataFrame with at least: pnl_pct, entry_date, exit_date, exit_reason.
Optional columns for richer diagnostics: direction, entry_price, stop, target.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch_logging import get_logger

log = get_logger(__name__)


def _profit_factor_from_pnl(group: pd.Series | np.ndarray) -> float:
    values = np.asarray(group, dtype=float)
    winners = values[values > 0]
    losers = values[values <= 0]
    gross_profit = float(winners.sum()) if len(winners) else 0.0
    gross_loss = abs(float(losers.sum())) if len(losers) else 0.0
    if gross_loss == 0.0:
        return float("inf") if gross_profit > 0.0 else 0.0
    return round(gross_profit / gross_loss, 4)


def compute_metrics(trades_df: pd.DataFrame) -> dict:
    """Compute standard backtest metrics from a trades DataFrame."""
    pnl_arr = trades_df["pnl_pct"].to_numpy(dtype=float)
    if len(pnl_arr) == 0:
        result = empty_metrics()
        result["diagnostics"] = compute_diagnostics(trades_df)
        if "exit_reason" in trades_df.columns:
            result["exit_reason_counts"] = (
                trades_df["exit_reason"].value_counts().sort_index().to_dict()
            )
        return result
    equity = np.cumsum(pnl_arr)
    drawdown = np.maximum.accumulate(equity) - equity
    diagnostics = compute_diagnostics(trades_df)
    window_metrics = compute_window_metrics(trades_df)
    result = {
        "median_expectancy": round(float(np.median(pnl_arr)), 4),
        "trade_count": int(len(pnl_arr)),
        "profit_factor": _profit_factor_from_pnl(pnl_arr),
        "max_drawdown": round(float(np.max(drawdown)) if len(drawdown) else 0.0, 4),
        "pct_profitable_windows": window_metrics["pct_profitable_windows"],
        "avg_sharpe_across_windows": window_metrics["avg_sharpe_across_windows"],
        "diagnostics": diagnostics,
    }
    if "exit_reason" in trades_df.columns:
        result["exit_reason_counts"] = (
            trades_df["exit_reason"].value_counts().sort_index().to_dict()
        )
    return result


def compute_window_metrics(trades_df: pd.DataFrame) -> dict[str, float]:
    """Compute six-month calendar-window robustness metrics from trade PnL."""
    if (
        trades_df.empty
        or "entry_date" not in trades_df.columns
        or "pnl_pct" not in trades_df.columns
    ):
        return {"pct_profitable_windows": 0.0, "avg_sharpe_across_windows": 0.0}

    df = trades_df[["entry_date", "pnl_pct"]].copy()
    df.loc[:, "entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
    df = df.dropna(subset=["entry_date"])
    if df.empty:
        return {"pct_profitable_windows": 0.0, "avg_sharpe_across_windows": 0.0}

    year = df["entry_date"].dt.year.astype(str)
    half = np.where(df["entry_date"].dt.month <= 6, "H1", "H2")
    window_key = year + "-" + half
    window_pnl = df.groupby(window_key)["pnl_pct"]
    window_returns = window_pnl.sum()
    sharpe_values: list[float] = []
    for _, group in window_pnl:
        values = group.to_numpy(dtype=float)
        std = float(values.std())
        if len(values) > 1 and std > 0:
            sharpe_values.append(float(values.mean() / std * np.sqrt(len(values))))
        else:
            sharpe_values.append(0.0)

    return {
        "pct_profitable_windows": round(float((window_returns > 0).mean()), 4),
        "avg_sharpe_across_windows": round(float(np.mean(sharpe_values)), 4),
    }


def empty_metrics() -> dict:
    return {
        "median_expectancy": 0.0,
        "trade_count": 0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "pct_profitable_windows": 0.0,
        "avg_sharpe_across_windows": 0.0,
        "exit_reason_counts": {},
        "diagnostics": {},
    }


def compute_diagnostics(trades_df: pd.DataFrame) -> dict:
    """Trade-level diagnostic breakdowns for research agent discovery.

    Reveals WHERE and WHEN the strategy wins/loses so the research agent
    can form data-grounded hypotheses about structural filters.
    """
    diag: dict = {}

    # 1. PF by hour of entry
    if "entry_date" in trades_df.columns:
        df = trades_df.assign(entry_hour=pd.to_datetime(trades_df["entry_date"]).dt.hour)
        pf_by_hour = {}
        for hour, group in df.groupby("entry_hour")["pnl_pct"]:
            pf_by_hour[f"{int(hour):02d}:00"] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
                "win_rate": round(float((group > 0).mean()), 3),
            }
        diag["pf_by_entry_hour"] = pf_by_hour

    # 2. PF by direction
    if "direction" in trades_df.columns:
        pf_by_dir = {}
        for d, group in trades_df.groupby("direction")["pnl_pct"]:
            pf_by_dir[d] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
                "win_rate": round(float((group > 0).mean()), 3),
            }
        diag["pf_by_direction"] = pf_by_dir

    # 3. PF by exit reason
    if "exit_reason" in trades_df.columns:
        pf_by_exit = {}
        for reason, group in trades_df.groupby("exit_reason")["pnl_pct"]:
            pf_by_exit[reason] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
                "mean_pnl": round(float(group.mean()), 5),
            }
        diag["pf_by_exit_reason"] = pf_by_exit

    # 4. PF by day of week
    if "entry_date" in trades_df.columns:
        df = trades_df.assign(dow=pd.to_datetime(trades_df["entry_date"]).dt.day_name())
        pf_by_dow = {}
        for dow, group in df.groupby("dow")["pnl_pct"]:
            pf_by_dow[dow] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
            }
        diag["pf_by_day_of_week"] = pf_by_dow

    # 5. PF by year
    if "entry_date" in trades_df.columns:
        df = trades_df.assign(year=pd.to_datetime(trades_df["entry_date"]).dt.year)
        pf_by_year = {}
        for year, group in df.groupby("year")["pnl_pct"]:
            pf_by_year[str(year)] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
            }
        diag["pf_by_year"] = pf_by_year

    # 6. Trade duration
    if "entry_date" in trades_df.columns and "exit_date" in trades_df.columns:
        df = trades_df.copy()
        duration_mins = (
            pd.to_datetime(df["exit_date"]) - pd.to_datetime(df["entry_date"])
        ).dt.total_seconds() / 60
        w_dur = duration_mins[df["pnl_pct"] > 0]
        l_dur = duration_mins[df["pnl_pct"] <= 0]
        diag["avg_duration_minutes"] = {
            "all": round(float(duration_mins.mean()), 1),
            "winners": round(float(w_dur.mean()), 1) if len(w_dur) else 0,
            "losers": round(float(l_dur.mean()), 1) if len(l_dur) else 0,
        }

    # 7. Risk/reward realized vs planned
    if all(c in trades_df.columns for c in ["entry_price", "stop", "target", "pnl_pct"]):
        planned_risk = (trades_df["entry_price"] - trades_df["stop"]).abs()
        planned_reward = (trades_df["target"] - trades_df["entry_price"]).abs()
        planned_rr = (planned_reward / planned_risk.replace(0, np.nan)).dropna()
        actual_winners = trades_df[trades_df["pnl_pct"] > 0]
        actual_losers = trades_df[trades_df["pnl_pct"] <= 0]
        avg_win = float(actual_winners["pnl_pct"].mean()) if len(actual_winners) else 0
        avg_loss = abs(float(actual_losers["pnl_pct"].mean())) if len(actual_losers) else 0.0
        diag["risk_reward_realized"] = {
            "planned_rr": round(float(planned_rr.median()), 2),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "realized_rr": round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
        }

    # 8. Consecutive loss streaks
    pnl = trades_df["pnl_pct"].values
    max_consec_loss = 0
    current_streak = 0
    for p in pnl:
        if p <= 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0
    diag["max_consecutive_losses"] = max_consec_loss

    # 9. PF by symbol (top 10 best, top 10 worst, min 5 trades)
    if "symbol" in trades_df.columns:
        sym_stats = []
        for sym, group in trades_df.groupby("symbol")["pnl_pct"]:
            if len(group) < 5:
                continue
            sym_stats.append(
                {
                    "symbol": sym,
                    "pf": round(_profit_factor_from_pnl(group), 3),
                    "trades": len(group),
                    "win_rate": round(float((group > 0).mean()), 3),
                }
            )
        sym_stats.sort(key=lambda x: x["pf"])
        diag["pf_by_symbol_worst10"] = sym_stats[:10]
        diag["pf_by_symbol_best10"] = sym_stats[-10:][::-1]
        # Aggregate: how many symbols are losing
        losing_syms = [s for s in sym_stats if s["pf"] < 1.0]
        diag["losing_symbols_count"] = len(losing_syms)
        diag["losing_symbols_trades"] = sum(s["trades"] for s in losing_syms)

    # 10. Stop distance analysis (quintile PF)
    if all(c in trades_df.columns for c in ["entry_price", "stop", "pnl_pct"]):
        df = trades_df.copy()
        df["stop_dist_pct"] = ((df["entry_price"] - df["stop"]).abs() / df["entry_price"]) * 100
        try:
            df["stop_q"] = pd.qcut(
                df["stop_dist_pct"],
                5,
                labels=["Q1_tight", "Q2", "Q3", "Q4", "Q5_wide"],
                duplicates="drop",
            )
            pf_by_stop = {}
            for q, group in df.groupby("stop_q")["pnl_pct"]:
                pf_by_stop[str(q)] = {
                    "pf": round(_profit_factor_from_pnl(group), 3),
                    "trades": len(group),
                    "win_rate": round(float((group > 0).mean()), 3),
                    "mean_stop_dist_pct": round(
                        float(df.loc[group.index, "stop_dist_pct"].mean()), 4
                    ),
                }
            diag["pf_by_stop_distance_quintile"] = pf_by_stop
        except ValueError as exc:
            log.warning(
                "STOP_DISTANCE_QUINTILE_UNAVAILABLE error=%s "
                "| hint=stop-distance values need more variation for quintile diagnostics",
                exc,
            )
            diag["pf_by_stop_distance_quintile"] = {
                "status": "unavailable",
                "reason": "not_enough_unique_values",
            }

    # 11. Losing streak clusters (streaks >= 5, with date ranges)
    if "entry_date" in trades_df.columns:
        df = trades_df.assign(entry_dt=pd.to_datetime(trades_df["entry_date"]))
        streaks = []
        start_dt = None
        prev_dt = None
        streak_len = 0
        streak_pnl = 0.0
        for _i, row in df.iterrows():
            if row["pnl_pct"] <= 0:
                if start_dt is None:
                    start_dt = row["entry_dt"]
                streak_len += 1
                streak_pnl += row["pnl_pct"]
                prev_dt = row["entry_dt"]
            else:
                if streak_len >= 5:
                    streaks.append(
                        {
                            "length": streak_len,
                            "start": str(start_dt.date()),
                            "end": str(prev_dt.date()),
                            "cumulative_pnl": round(float(streak_pnl), 5),
                        }
                    )
                start_dt = None
                prev_dt = None
                streak_len = 0
                streak_pnl = 0.0
        # Handle trailing streak
        if streak_len >= 5 and start_dt is not None:
            streaks.append(
                {
                    "length": streak_len,
                    "start": str(start_dt.date()),
                    "end": str(prev_dt.date()),
                    "cumulative_pnl": round(float(streak_pnl), 5),
                }
            )
        streaks.sort(key=lambda x: x["length"], reverse=True)
        diag["losing_streaks_gte5"] = streaks[:10]  # top 10 worst
        diag["losing_streak_count_gte5"] = len(streaks)

    # 12. PF by month (seasonal patterns)
    if "entry_date" in trades_df.columns:
        df = trades_df.assign(month=pd.to_datetime(trades_df["entry_date"]).dt.month_name())
        pf_by_month = {}
        for month, group in df.groupby("month")["pnl_pct"]:
            pf_by_month[month] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
                "win_rate": round(float((group > 0).mean()), 3),
            }
        diag["pf_by_month"] = pf_by_month

    # 13. PF by year x quarter (seasonal stability)
    if "entry_date" in trades_df.columns:
        entry_dt = pd.to_datetime(trades_df["entry_date"])
        df = trades_df.assign(
            entry_dt=entry_dt,
            yq=entry_dt.dt.year.astype(str) + "-Q" + entry_dt.dt.quarter.astype(str),
        )
        pf_by_yq = {}
        for yq, group in df.groupby("yq")["pnl_pct"]:
            pf_by_yq[yq] = {
                "pf": round(_profit_factor_from_pnl(group), 3),
                "trades": len(group),
            }
        diag["pf_by_year_quarter"] = pf_by_yq

    return diag
