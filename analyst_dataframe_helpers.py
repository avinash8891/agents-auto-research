from __future__ import annotations

from typing import Sequence

import pandas as pd


def profit_factor(values: pd.Series) -> float | None:
    """Return gross profit / gross loss for a PnL series.

    Returns None when there are no losses, because the PF is undefined/infinite
    and should be handled explicitly by the analyst.
    """
    pnl = pd.to_numeric(values, errors="coerce").dropna()
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl <= 0].sum()
    if losses == 0:
        return None
    return float(wins / abs(losses))


def bucket_trade_performance(
    trades: pd.DataFrame,
    *,
    value_col: str,
    bucket_col: str,
    bins: Sequence[float],
    labels: Sequence[str],
    min_count: int = 50,
) -> pd.DataFrame:
    """Summarize trade performance by numeric buckets with PF and expectancy."""
    required = {value_col, bucket_col}
    missing = required - set(trades.columns)
    if missing:
        raise KeyError(f"missing columns: {sorted(missing)}")

    frame = trades[[bucket_col, value_col]].copy()
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame[bucket_col] = pd.to_numeric(frame[bucket_col], errors="coerce")
    frame = frame.dropna(subset=[bucket_col, value_col])
    frame["bucket"] = pd.cut(frame[bucket_col], bins=bins, labels=labels, include_lowest=True)
    frame = frame.dropna(subset=["bucket"])

    rows: list[dict[str, object]] = []
    for bucket, group in frame.groupby("bucket", observed=True):
        count = int(len(group))
        if count < min_count:
            continue
        values = group[value_col]
        rows.append(
            {
                "bucket": str(bucket),
                "trades": count,
                "expectancy": float(values.mean()),
                "median_pnl": float(values.median()),
                "win_rate": float((values > 0).mean()),
                "profit_factor": profit_factor(values),
            }
        )
    return pd.DataFrame(rows)


def safe_merge_asof(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    on: str,
    direction: str = "backward",
    tolerance=None,
    by: str | list[str] | None = None,
) -> pd.DataFrame:
    """Sort inputs before merge_asof to avoid common analyst retry failures."""
    if on not in left.columns:
        raise KeyError(f"left missing asof key: {on}")
    if on not in right.columns:
        raise KeyError(f"right missing asof key: {on}")
    sort_cols = ([by] if isinstance(by, str) else list(by or [])) + [on]
    left_sorted = left.sort_values(sort_cols).reset_index(drop=True)
    right_sorted = right.sort_values(sort_cols).reset_index(drop=True)
    return pd.merge_asof(
        left_sorted,
        right_sorted,
        on=on,
        by=by,
        direction=direction,
        tolerance=tolerance,
    )
