from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

EVENT_SCHEMA_VERSION = "1.0"

CORE_EVENT_SCHEMA_COLUMNS = (
    "timestamp",
    "symbol",
    "direction",
    "event_type",
    "reason",
)

SHARED_OPTIONAL_EVENT_SCHEMA_COLUMNS = (
    "entry_price",
    "stop_price",
    "target_price",
    "stop_distance_pct",
    "trigger_bar_timestamp",
    "entry_bar_index_from_open",
)
STANDARD_EVENT_SCHEMA_COLUMNS = (
    *CORE_EVENT_SCHEMA_COLUMNS,
    *SHARED_OPTIONAL_EVENT_SCHEMA_COLUMNS,
)


def _event_counts_from_frame(event_frame: pd.DataFrame) -> dict[str, int]:
    if event_frame.empty or "event_type" not in event_frame.columns:
        return {}
    return {
        str(event_type): int(count)
        for event_type, count in event_frame["event_type"].value_counts(dropna=False).items()
    }


def _rejection_breakdown_from_frame(event_frame: pd.DataFrame) -> dict[str, int]:
    if (
        event_frame.empty
        or "event_type" not in event_frame.columns
        or "reason" not in event_frame.columns
    ):
        return {}
    rejected = event_frame.loc[event_frame["event_type"] == "rejected_signal", "reason"]
    if rejected.empty:
        return {}
    return {
        str(reason): int(count)
        for reason, count in rejected.fillna("").astype(str).value_counts().items()
        if str(reason)
    }


def _bucket_counts(
    event_frame: pd.DataFrame,
    column: str,
) -> dict[str, dict[str, int]]:
    if (
        event_frame.empty
        or column not in event_frame.columns
        or "event_type" not in event_frame.columns
    ):
        return {}
    timestamps = pd.to_datetime(event_frame[column], errors="coerce")
    valid = timestamps.notna()
    if not valid.any():
        return {}
    rows = event_frame.loc[valid, ["event_type"]].copy()
    rows["bucket"] = timestamps.loc[valid].dt.strftime("%H:%M")
    grouped = rows.groupby(["event_type", "bucket"]).size()
    out: dict[str, dict[str, int]] = {}
    for (event_type, bucket), count in grouped.items():
        out.setdefault(str(event_type), {})[str(bucket)] = int(count)
    return out


def _entry_bar_counts(event_frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    column = "entry_bar_index_from_open"
    if (
        event_frame.empty
        or column not in event_frame.columns
        or "event_type" not in event_frame.columns
    ):
        return {}
    values = pd.to_numeric(event_frame[column], errors="coerce")
    valid = values.notna()
    if not valid.any():
        return {}
    rows = event_frame.loc[valid, ["event_type"]].copy()
    rows[column] = values.loc[valid].astype(int)
    grouped = rows.groupby(["event_type", column]).size()
    out: dict[str, dict[str, int]] = {}
    for (event_type, bar_index), count in grouped.items():
        out.setdefault(str(event_type), {})[str(int(bar_index))] = int(count)
    return out


def _stop_distance_summaries(event_frame: pd.DataFrame) -> dict[str, dict[str, float | int]]:
    column = "stop_distance_pct"
    if (
        event_frame.empty
        or column not in event_frame.columns
        or "event_type" not in event_frame.columns
    ):
        return {}
    values = pd.to_numeric(event_frame[column], errors="coerce")
    valid = values.notna() & np.isfinite(values)
    if not valid.any():
        return {}
    rows = event_frame.loc[valid, ["event_type"]].copy()
    rows[column] = values.loc[valid].astype(float)
    out: dict[str, dict[str, float | int]] = {}
    for event_type, group in rows.groupby("event_type"):
        series = group[column]
        out[str(event_type)] = {
            "count": int(series.count()),
            "min": float(series.min()),
            "median": float(series.median()),
            "max": float(series.max()),
        }
    return out


def _present_columns(event_frame: pd.DataFrame, columns: tuple[str, ...]) -> list[str]:
    if event_frame.empty:
        return []
    return [column for column in columns if column in event_frame.columns]


def _strategy_specific_fields_present(event_frame: pd.DataFrame) -> list[str]:
    if event_frame.empty:
        return []
    shared = set(STANDARD_EVENT_SCHEMA_COLUMNS)
    return sorted(str(column) for column in event_frame.columns if column not in shared)


def validate_event_frame_schema(event_frame: pd.DataFrame) -> None:
    if event_frame.empty:
        return
    missing = [column for column in CORE_EVENT_SCHEMA_COLUMNS if column not in event_frame.columns]
    if missing:
        raise ValueError(
            "STRATEGY_EVENT_SCHEMA_INVALID: missing core event columns: " + ", ".join(missing)
        )


def build_event_diagnostics(
    *,
    trade_count: int,
    event_frame: pd.DataFrame | None = None,
    event_counts: Mapping[str, int] | None = None,
    rejection_breakdown: Mapping[str, int] | None = None,
) -> dict[str, object]:
    event_frame = event_frame if event_frame is not None else pd.DataFrame()
    resolved_event_counts = (
        dict(event_counts) if event_counts is not None else _event_counts_from_frame(event_frame)
    )
    resolved_rejection_breakdown = (
        dict(rejection_breakdown)
        if rejection_breakdown is not None
        else _rejection_breakdown_from_frame(event_frame)
    )
    return {
        "trade_count": int(trade_count),
        "event_counts": resolved_event_counts,
        "rejection_breakdown": resolved_rejection_breakdown,
        "event_schema_metadata": {
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "core_fields_present": _present_columns(event_frame, CORE_EVENT_SCHEMA_COLUMNS),
            "shared_optional_fields_present": _present_columns(
                event_frame, SHARED_OPTIONAL_EVENT_SCHEMA_COLUMNS
            ),
            "strategy_specific_fields_present": _strategy_specific_fields_present(event_frame),
        },
        "standardized_event_schema": {
            column: bool(not event_frame.empty and column in event_frame.columns)
            for column in STANDARD_EVENT_SCHEMA_COLUMNS
        },
        "event_counts_by_trigger_bucket": _bucket_counts(event_frame, "trigger_bar_timestamp"),
        "event_counts_by_entry_bar_index_from_open": _entry_bar_counts(event_frame),
        "stop_distance_pct_summary_by_event_type": _stop_distance_summaries(event_frame),
    }


def write_event_diagnostics(
    path: str | Path,
    *,
    trade_count: int,
    event_frame: pd.DataFrame | None = None,
    event_counts: Mapping[str, int] | None = None,
    rejection_breakdown: Mapping[str, int] | None = None,
) -> dict[str, object]:
    diagnostics = build_event_diagnostics(
        trade_count=trade_count,
        event_frame=event_frame,
        event_counts=event_counts,
        rejection_breakdown=rejection_breakdown,
    )
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(diagnostics, indent=2) + "\n")
    return diagnostics
