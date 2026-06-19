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
_ALLOWED_FORMULA_CHARS = re.compile(r"^[A-Za-z0-9_+\-*/()., \t]+$")


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


def active_agent_feature_definitions(root: Path, family_name: str) -> list[dict[str, Any]]:
    definitions = []
    for entry in _load(root):
        family = (entry.get("families") or {}).get(family_name) or {}
        if family.get("status") in {"exploratory", "validated"}:
            definitions.append(dict(entry))
    return definitions


def _agent_feature_columns(entries: list[dict[str, Any]]) -> frozenset[str]:
    return frozenset(str(entry.get("column")) for entry in entries if entry.get("column"))


def _formula_dependencies(formula: str, entries: list[dict[str, Any]]) -> set[str]:
    return _formula_names(formula) & _agent_feature_columns(entries)


def _assert_declarative_formula(formula: str) -> None:
    if not formula.strip() or not _ALLOWED_FORMULA_CHARS.fullmatch(formula):
        raise AgentFeatureRegistryError("formula must use the declarative expression subset")


def _assert_acyclic(entries: list[dict[str, Any]], family_name: str) -> None:
    active = active_agent_feature_columns_from_entries(entries, family_name)
    graph: dict[str, set[str]] = {}
    for entry in entries:
        column = str(entry.get("column") or "")
        if column not in active:
            continue
        graph[column] = _formula_dependencies(str(entry.get("formula") or ""), entries) & active

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(column: str) -> None:
        if column in visiting:
            raise AgentFeatureRegistryError(f"cyclic dependency involving {column}")
        if column in visited:
            return
        visiting.add(column)
        for dependency in graph.get(column, set()):
            visit(dependency)
        visiting.remove(column)
        visited.add(column)

    for column in graph:
        visit(column)


def active_agent_feature_columns_from_entries(
    entries: list[dict[str, Any]], family_name: str
) -> frozenset[str]:
    columns = []
    for entry in entries:
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
    for entry in entries:
        if entry.get("column") == column and entry.get("formula") != formula:
            raise AgentFeatureRegistryError(f"formula conflict for {column}")
    _assert_declarative_formula(formula)
    active_columns = active_agent_feature_columns_from_entries(entries, family_name)
    unknown = _formula_names(formula) - _STATIC_FORMULA_NAMES - active_columns - {column}
    if unknown:
        raise AgentFeatureRegistryError(f"unknown dependency: {sorted(unknown)}")
    for entry in entries:
        if entry.get("column") != column:
            continue
        families = entry.setdefault("families", {})
        families[family_name] = {
            "status": "exploratory",
            "requesting_thesis_id": thesis_id,
            "requesting_thesis_verdict": "build_passed",
        }
        _assert_acyclic(entries, family_name)
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
    _assert_acyclic(entries, family_name)
    _write(root, entries)


def mark_agent_features_validated(root: Path, *, family_name: str, thesis_id: str) -> None:
    entries = _load(root)
    changed = False
    for entry in entries:
        families = entry.get("families") if isinstance(entry.get("families"), dict) else {}
        family = families.get(family_name) if isinstance(families, dict) else None
        if not isinstance(family, dict):
            continue
        if family.get("requesting_thesis_id") != thesis_id:
            continue
        if family.get("status") in {"exploratory", "validated"}:
            family["status"] = "validated"
            family["validated_thesis_id"] = thesis_id
            family["validated_at"] = timestamp_now()
            changed = True
    if changed:
        _write(root, entries)


def prune_agent_feature(
    root: Path,
    *,
    column: str,
    family_name: str,
    reason: str = "pruned",
) -> None:
    entries = _load(root)
    to_deactivate = {column}
    changed = True
    while changed:
        changed = False
        for entry in entries:
            current = str(entry.get("column") or "")
            if current in to_deactivate:
                continue
            families = entry.get("families") if isinstance(entry.get("families"), dict) else {}
            family = families.get(family_name) if isinstance(families, dict) else None
            if not isinstance(family, dict) or family.get("status") not in {
                "exploratory",
                "validated",
            }:
                continue
            if _formula_dependencies(str(entry.get("formula") or ""), entries) & to_deactivate:
                to_deactivate.add(current)
                changed = True

    wrote = False
    for entry in entries:
        current = str(entry.get("column") or "")
        if current not in to_deactivate:
            continue
        families = entry.setdefault("families", {})
        family = families.setdefault(family_name, {})
        if family.get("status") == "inactive":
            continue
        family["status"] = "inactive"
        family["inactive_reason"] = "inactive_dependency" if current != column else reason
        family["inactive_at"] = timestamp_now()
        wrote = True
    if wrote:
        _write(root, entries)
