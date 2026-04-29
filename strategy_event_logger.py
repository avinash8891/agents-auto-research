"""Strategy event logger — captures every setup/signal the strategy considered.

Provides evidence beyond trades.csv: rejected signals, filtered setups,
execution failures. Used by the research analyst to reason about what
the strategy missed and why.

Design:
- During backtest: accumulate lightweight tuples (indices + metadata).
  No DataFrames created during the hot loop.
- After backtest: build one DataFrame from accumulated arrays + write parquet.
- Counters tracked incrementally for diagnostics.json.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(slots=True)
class _EventBatch:
    """Lightweight container for a batch of events. No DataFrame overhead."""
    timestamps: np.ndarray     # datetime64 indices
    symbol: str
    direction: str
    event_type: str
    reason: str
    entry_prices: np.ndarray   # float64
    stop_prices: np.ndarray    # float64


class StrategyEventLogger:
    """Accumulates strategy events as raw arrays during backtest,
    builds a single DataFrame at write time.

    Two outputs:
    - strategy_events.parquet: full per-signal detail for the analyst
    - diagnostics.json: aggregate counts for the loop
    """

    def __init__(self) -> None:
        self._event_counts: Counter = Counter()
        self._rejection_counts: Counter = Counter()
        self._batches: list[_EventBatch] = []
        # For multi-symbol batches from record_dataframe
        self._multi_arrays: dict[int, tuple] = {}  # batch_index -> (sym_arr, dir_arr, rsn_arr)

    def record_events(
        self,
        timestamps: pd.DatetimeIndex | np.ndarray,
        mask: np.ndarray,
        symbol: str,
        direction: str,
        event_type: str,
        reason: str = "",
        entry_prices: np.ndarray | None = None,
        stop_prices: np.ndarray | None = None,
    ) -> None:
        """Record events from a boolean mask. Extracts indices, stores raw arrays."""
        indices = np.flatnonzero(mask)
        n = len(indices)
        if n == 0:
            return

        self._event_counts[event_type] += n
        if reason and event_type == "rejected_signal":
            self._rejection_counts[reason] += n

        ts = np.asarray(timestamps)[indices]
        ep = entry_prices[indices] if entry_prices is not None else np.full(n, np.nan)
        sp = stop_prices[indices] if stop_prices is not None else np.full(n, np.nan)
        self._batches.append(_EventBatch(ts, symbol, direction, event_type, reason, ep, sp))

    def record_dataframe(self, df: pd.DataFrame, event_type: str, reason: str = "") -> None:
        """Record events from an existing DataFrame (e.g. daily cap rejections)."""
        n = len(df)
        if n == 0:
            return
        self._event_counts[event_type] += n
        if reason:
            self._rejection_counts[reason] += n

        ts = pd.to_datetime(df["timestamp"], errors="coerce").values if "timestamp" in df.columns else np.full(n, np.datetime64("NaT"))
        ep = pd.to_numeric(df.get("entry_price"), errors="coerce").values if "entry_price" in df.columns else np.full(n, np.nan)
        sp = pd.to_numeric(df.get("stop_price"), errors="coerce").values if "stop_price" in df.columns else np.full(n, np.nan)

        has_multi_sym = "symbol" in df.columns and df["symbol"].nunique() > 1
        has_multi_dir = "direction" in df.columns and df["direction"].nunique() > 1
        has_multi_rsn = "reason" in df.columns and df["reason"].nunique() > 1

        if has_multi_sym or has_multi_dir or has_multi_rsn:
            batch_idx = len(self._batches)
            self._batches.append(_EventBatch(ts, "__multi__", "__multi__", event_type, "__multi__", ep, sp))
            self._multi_arrays[batch_idx] = (
                df["symbol"].values if "symbol" in df.columns else np.full(n, ""),
                df["direction"].values if "direction" in df.columns else np.full(n, ""),
                df["reason"].values if "reason" in df.columns else np.full(n, reason),
            )
        else:
            sym = df["symbol"].iloc[0] if "symbol" in df.columns else ""
            dr = df["direction"].iloc[0] if "direction" in df.columns else ""
            rsn = df["reason"].iloc[0] if "reason" in df.columns else reason
            self._batches.append(_EventBatch(ts, sym, dr, event_type, rsn, ep, sp))

    def log(self, **kwargs: Any) -> None:
        """Log a single event (for low-volume per-trade rejections in exits)."""
        self._event_counts[kwargs.get("event_type", "unknown")] += 1
        if kwargs.get("status") == "rejected" and kwargs.get("reason"):
            self._rejection_counts[kwargs["reason"]] += 1

        ts_raw = kwargs.get("timestamp", "")
        try:
            ts = np.array([np.datetime64(pd.Timestamp(ts_raw))]) if ts_raw else np.array([np.datetime64("NaT")])
        except Exception:
            ts = np.array([np.datetime64("NaT")])

        ep = np.array([kwargs.get("entry_price", np.nan)], dtype=np.float64)
        sp = np.array([kwargs.get("stop_price", np.nan)], dtype=np.float64)
        self._batches.append(_EventBatch(
            ts,
            kwargs.get("symbol", ""),
            kwargs.get("direction", ""),
            kwargs.get("event_type", ""),
            kwargs.get("reason", ""),
            ep, sp,
        ))

    def to_dataframe(self) -> pd.DataFrame:
        """Build one DataFrame from all accumulated batches. Single allocation."""
        if not self._batches:
            return pd.DataFrame()

        total = sum(len(b.timestamps) for b in self._batches)
        ts_all = np.empty(total, dtype="datetime64[ns]")
        ep_all = np.empty(total, dtype=np.float64)
        sp_all = np.empty(total, dtype=np.float64)
        sym_all = np.empty(total, dtype=object)
        dir_all = np.empty(total, dtype=object)
        evt_all = np.empty(total, dtype=object)
        rsn_all = np.empty(total, dtype=object)

        pos = 0
        for i, b in enumerate(self._batches):
            n = len(b.timestamps)
            end = pos + n
            ts_all[pos:end] = b.timestamps
            ep_all[pos:end] = b.entry_prices
            sp_all[pos:end] = b.stop_prices
            evt_all[pos:end] = b.event_type

            if b.symbol == "__multi__" and i in self._multi_arrays:
                s_arr, d_arr, r_arr = self._multi_arrays[i]
                sym_all[pos:end] = s_arr
                dir_all[pos:end] = d_arr
                rsn_all[pos:end] = r_arr
            else:
                sym_all[pos:end] = b.symbol
                dir_all[pos:end] = b.direction
                rsn_all[pos:end] = b.reason

            pos = end

        return pd.DataFrame({
            "timestamp": ts_all,
            "symbol": sym_all,
            "direction": dir_all,
            "event_type": evt_all,
            "reason": rsn_all,
            "entry_price": ep_all,
            "stop_price": sp_all,
        })

    def write_parquet(self, path: str | Path) -> None:
        """Build DataFrame and write to parquet in one shot."""
        df = self.to_dataframe()
        if df.empty:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)

    def build_diagnostics(self, trade_count: int) -> dict:
        """Build diagnostics.json from aggregate counters."""
        return {
            "trade_count": trade_count,
            "event_counts": dict(self._event_counts),
            "rejection_breakdown": dict(self._rejection_counts),
        }

    def write_diagnostics(self, path: str | Path, trade_count: int) -> dict:
        """Build and write diagnostics.json. Returns the diagnostics dict."""
        diag = self.build_diagnostics(trade_count)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(diag, indent=2) + "\n")
        return diag

    def clear(self) -> None:
        self._event_counts.clear()
        self._rejection_counts.clear()
        self._batches.clear()
        self._multi_arrays.clear()
