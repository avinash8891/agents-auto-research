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

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from autoresearch_logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class _EventBatch:
    """Lightweight container for a batch of events. No DataFrame overhead."""

    timestamps: np.ndarray  # datetime64 indices
    symbol: str
    direction: str
    event_type: str
    reason: str
    entry_prices: np.ndarray  # float64
    stop_prices: np.ndarray  # float64
    extras: dict[str, np.ndarray]


_CORE_EVENT_COLUMNS = (
    "timestamp",
    "symbol",
    "direction",
    "event_type",
    "reason",
    "entry_price",
    "stop_price",
)


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

    @staticmethod
    def _normalize_extra_array(values: Any, n: int) -> np.ndarray:
        if values is None:
            return np.full(n, np.nan, dtype=np.float64)
        # Datetime-like scalars (pd.Timestamp / np.datetime64 / NaT) broadcast to
        # a datetime64[ns] column. np.asarray() would otherwise box a Timestamp as
        # object and collapse a NaT scalar to float NaN (its .item() is None).
        # pd.NaT is its own type (not Timestamp/np.datetime64), so it needs the
        # explicit disjunct; pd.Timestamp(pd.NaT).to_datetime64() is NaT.
        if values is pd.NaT or isinstance(values, (pd.Timestamp, np.datetime64)):
            ts = pd.Timestamp(values)
            if ts.tz is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            return np.full(n, ts.to_datetime64(), dtype="datetime64[ns]")
        arr = np.asarray(values)
        if arr.ndim == 0:
            item = arr.item()
            if item is None:
                return np.full(n, np.nan, dtype=np.float64)
            arr = np.full(n, item, dtype=arr.dtype)
        if len(arr) != n:
            raise ValueError(f"event extra length mismatch: expected {n}, got {len(arr)}")
        if np.issubdtype(arr.dtype, np.datetime64):
            return arr.astype("datetime64[ns]")
        if np.issubdtype(arr.dtype, np.floating):
            return arr.astype(np.float64, copy=False)
        if np.issubdtype(arr.dtype, np.integer):
            return arr.astype(np.float64)
        return arr.astype(object, copy=False)

    @classmethod
    def _normalize_extras(cls, extras: dict[str, Any] | None, n: int) -> dict[str, np.ndarray]:
        if not extras:
            return {}
        normalized: dict[str, np.ndarray] = {}
        for key, values in extras.items():
            if key in _CORE_EVENT_COLUMNS:
                continue
            normalized[key] = cls._normalize_extra_array(values, n)
        return normalized

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
        extras: dict[str, Any] | None = None,
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
        sliced_extras = self._normalize_extras(
            {key: np.asarray(values)[indices] for key, values in (extras or {}).items()},
            n,
        )
        self._batches.append(
            _EventBatch(ts, symbol, direction, event_type, reason, ep, sp, sliced_extras)
        )

    def record_dataframe(self, df: pd.DataFrame, event_type: str, reason: str = "") -> None:
        """Record events from an existing DataFrame (e.g. daily cap rejections)."""
        n = len(df)
        if n == 0:
            return
        self._event_counts[event_type] += n
        if reason:
            self._rejection_counts[reason] += n

        ts = (
            pd.to_datetime(df["timestamp"], errors="coerce").values
            if "timestamp" in df.columns
            else np.full(n, np.datetime64("NaT"))
        )
        ep = (
            pd.to_numeric(df.get("entry_price"), errors="coerce").values
            if "entry_price" in df.columns
            else np.full(n, np.nan)
        )
        sp = (
            pd.to_numeric(df.get("stop_price"), errors="coerce").values
            if "stop_price" in df.columns
            else np.full(n, np.nan)
        )
        extra_source = {
            col: df[col].to_numpy(copy=False)
            for col in df.columns
            if col not in _CORE_EVENT_COLUMNS
        }
        if "trigger_bar_timestamp" not in extra_source and "timestamp" in df.columns:
            extra_source["trigger_bar_timestamp"] = ts
        if "stop_distance_pct" not in extra_source:
            valid = np.isfinite(ep) & np.isfinite(sp) & (ep > 0)
            stop_distance_pct = np.full(n, np.nan, dtype=np.float64)
            stop_distance_pct[valid] = np.abs(ep[valid] - sp[valid]) / ep[valid] * 100.0
            extra_source["stop_distance_pct"] = stop_distance_pct
        extras = self._normalize_extras(
            extra_source,
            n,
        )

        has_multi_sym = "symbol" in df.columns and df["symbol"].nunique() > 1
        has_multi_dir = "direction" in df.columns and df["direction"].nunique() > 1
        has_multi_rsn = "reason" in df.columns and df["reason"].nunique() > 1

        if has_multi_sym or has_multi_dir or has_multi_rsn:
            batch_idx = len(self._batches)
            self._batches.append(
                _EventBatch(
                    ts,
                    "__multi__",
                    "__multi__",
                    event_type,
                    "__multi__",
                    ep,
                    sp,
                    extras,
                )
            )
            self._multi_arrays[batch_idx] = (
                df["symbol"].values if "symbol" in df.columns else np.full(n, ""),
                df["direction"].values if "direction" in df.columns else np.full(n, ""),
                df["reason"].values if "reason" in df.columns else np.full(n, reason),
            )
        else:
            sym = df["symbol"].iloc[0] if "symbol" in df.columns else ""
            dr = df["direction"].iloc[0] if "direction" in df.columns else ""
            rsn = df["reason"].iloc[0] if "reason" in df.columns else reason
            self._batches.append(_EventBatch(ts, sym, dr, event_type, rsn, ep, sp, extras))

    def log(self, **kwargs: Any) -> None:
        """Log a single event (for low-volume per-trade rejections in exits)."""
        self._event_counts[kwargs.get("event_type", "unknown")] += 1
        if kwargs.get("status") == "rejected" and kwargs.get("reason"):
            self._rejection_counts[kwargs["reason"]] += 1

        ts_raw = kwargs.get("timestamp", "")
        try:
            ts = (
                np.array([np.datetime64(pd.Timestamp(ts_raw))])
                if ts_raw
                else np.array([np.datetime64("NaT")])
            )
        except Exception as exc:
            log.error(
                "STRATEGY_EVENT_TIMESTAMP_INVALID value=%r error=%s | hint=provide an ISO-8601-compatible timestamp for strategy event logging",
                ts_raw,
                exc,
            )
            ts = np.array([np.datetime64("NaT")])

        entry_price = kwargs.get("entry_price", np.nan)
        stop_price = kwargs.get("stop_price", np.nan)
        ep = np.array([entry_price], dtype=np.float64)
        sp = np.array([stop_price], dtype=np.float64)
        extra_items = {
            key: value
            for key, value in kwargs.items()
            if key not in _CORE_EVENT_COLUMNS
            and key not in {"symbol", "direction", "event_type", "reason"}
        }
        if (
            "stop_distance_pct" not in extra_items
            and np.isfinite(ep[0])
            and np.isfinite(sp[0])
            and ep[0] > 0
        ):
            extra_items["stop_distance_pct"] = abs(ep[0] - sp[0]) / ep[0] * 100.0
        if "trigger_bar_timestamp" not in extra_items:
            extra_items["trigger_bar_timestamp"] = ts[0]
        extras = self._normalize_extras(extra_items, 1)
        self._batches.append(
            _EventBatch(
                ts,
                kwargs.get("symbol", ""),
                kwargs.get("direction", ""),
                kwargs.get("event_type", ""),
                kwargs.get("reason", ""),
                ep,
                sp,
                extras,
            )
        )

    @staticmethod
    def _extra_kind(arr: np.ndarray) -> str:
        if np.issubdtype(arr.dtype, np.datetime64):
            return "datetime"
        if np.issubdtype(arr.dtype, np.floating):
            return "numeric"
        return "object"

    @staticmethod
    def _empty_extra_column(kind: str, total: int) -> np.ndarray:
        if kind == "datetime":
            return np.full(total, np.datetime64("NaT"), dtype="datetime64[ns]")
        if kind == "numeric":
            return np.full(total, np.nan, dtype=np.float64)
        return np.full(total, "", dtype=object)

    @staticmethod
    def _merged_extra_kind(existing: str | None, current: str) -> str:
        if existing is None or existing == current:
            return current
        return "object"

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
        extra_kinds: dict[str, str] = {}
        for batch in self._batches:
            for key, values in batch.extras.items():
                extra_kinds[key] = self._merged_extra_kind(
                    extra_kinds.get(key), self._extra_kind(values)
                )
        extra_columns = {
            key: self._empty_extra_column(kind, total) for key, kind in extra_kinds.items()
        }

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

            for key, column in extra_columns.items():
                if key in b.extras:
                    column[pos:end] = b.extras[key]

            pos = end

        payload: dict[str, Any] = {
            "timestamp": ts_all,
            "symbol": sym_all,
            "direction": dir_all,
            "event_type": evt_all,
            "reason": rsn_all,
            "entry_price": ep_all,
            "stop_price": sp_all,
        }
        payload.update(extra_columns)
        return pd.DataFrame(payload)

    def write_parquet(self, path: str | Path) -> None:
        """Build DataFrame and write to parquet in one shot."""
        df = self.to_dataframe()
        if df.empty:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)

    def event_counts_snapshot(self) -> dict[str, int]:
        return dict(self._event_counts)

    def rejection_breakdown_snapshot(self) -> dict[str, int]:
        return dict(self._rejection_counts)
