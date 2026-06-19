from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now
from persistence_utils import write_text_atomic

AGENT_FEATURE_REGISTRY = Path("runtime") / "agent_features.jsonl"
_STATIC_FORMULA_NAMES = frozenset(
    {
        "abs",
        "adx_14",
        "bars_since_open",
        "day_of_week",
        "days_to_econ_release",
        "days_to_fomc",
        "dist_to_ema_atr",
        "dist_to_ema_pct",
        "gap_atr",
        "gap_pct",
        "is_earnings_window",
        "max",
        "min",
        "or_width_pctile",
        "overnight_move_pct",
        "prior_day_range_pct",
        "rolling_mean",
        "rolling_rank",
        "rolling_std",
        "rvol",
        "session_phase",
        "stop_distance_pct",
        "time_of_day_min",
        "trailing_5d_return",
        "vol_of_vol",
        "vol_pctile_20d",
        "xs_rank_gap_pct",
        "xs_rank_rvol",
    }
)


class AgentFeatureRegistryError(RuntimeError):
    pass


def _path(root: Path) -> Path:
    return root / AGENT_FEATURE_REGISTRY


def _load(root: Path) -> list[dict[str, Any]]:
    path = _path(root)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _write(root: Path, entries: list[dict[str, Any]]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries))


def _formula_names(formula: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", formula))


def active_agent_feature_columns(root: Path, family_name: str) -> frozenset[str]:
    columns = []
    for entry in _load(root):
        family = (entry.get("families") or {}).get(family_name) or {}
        if family.get("status") in {"exploratory", "validated"}:
            columns.append(str(entry.get("column")))
    return frozenset(columns)


def register_agent_feature(
    root: Path,
    *,
    column: str,
    formula: str,
    required_data: list[str],
    family_name: str,
    thesis_id: str,
) -> None:
    entries = _load(root)
    active_columns = active_agent_feature_columns(root, family_name)
    unknown = _formula_names(formula) - _STATIC_FORMULA_NAMES - active_columns - {column}
    if unknown:
        raise AgentFeatureRegistryError(f"unknown dependency: {sorted(unknown)}")
    for entry in entries:
        if entry.get("column") != column:
            continue
        if entry.get("formula") != formula:
            raise AgentFeatureRegistryError(f"formula conflict for {column}")
        families = entry.setdefault("families", {})
        families[family_name] = {
            "status": "exploratory",
            "requesting_thesis_id": thesis_id,
            "requesting_thesis_verdict": "build_passed",
        }
        _write(root, entries)
        return
    entries.append(
        {
            "column": column,
            "formula": formula,
            "required_data": list(required_data),
            "requesting_thesis_id": thesis_id,
            "families": {
                family_name: {
                    "status": "exploratory",
                    "requesting_thesis_id": thesis_id,
                    "requesting_thesis_verdict": "build_passed",
                }
            },
            "created_by": "agent",
            "created_at": timestamp_now(),
        }
    )
    _write(root, entries)
