"""State, JSONL log, results, and current.md rendering for autoresearch.

Pure functions. The controller composes these with its own paths.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExperimentRecord:
    config: str
    metric: float
    status: str
    description: str
    timestamp: int
    asi: dict[str, Any]


# ── State JSON ─────────────────────────────────────────────────────

def read_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"state": "running"}
    return json.loads(state_path.read_text())


def write_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_name(f"{state_path.name}.tmp")
    payload = json.dumps(state, indent=2) + "\n"
    with tmp_path.open("w") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.replace(state_path)


# ── JSONL log ──────────────────────────────────────────────────────

def read_entries(jsonl_path: Path) -> list[dict[str, Any]]:
    if not jsonl_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def write_entries(jsonl_path: Path, entries: list[dict[str, Any]]) -> None:
    jsonl_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


# ── Results ────────────────────────────────────────────────────────

def read_results(entries: list[dict[str, Any]]) -> list[ExperimentRecord]:
    results: list[ExperimentRecord] = []
    for entry in entries:
        if entry.get("type") in ("config", "research_round"):
            continue
        asi = entry.get("asi") or {}
        results.append(
            ExperimentRecord(
                config=asi.get("config", ""),
                metric=entry["metric"],
                status=entry["status"],
                description=entry.get("description", ""),
                timestamp=entry.get("timestamp", 0),
                asi=asi,
            )
        )
    return results


def direction(entries: list[dict[str, Any]]) -> str:
    for entry in entries:
        if entry.get("type") == "config":
            return entry.get("bestDirection", "higher")
    return "higher"


def is_better(direction_str: str, candidate: float, current: float | None) -> bool:
    if current is None:
        return True
    return candidate > current if direction_str == "higher" else candidate < current


def best_result(results: list[ExperimentRecord], direction_str: str) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for result in results:
        if result.status != "keep":
            continue
        if best is None or is_better(direction_str, result.metric, best["metric"]):
            best = {"config": result.config, "metric": result.metric}
    return best or {}


def latest_result(results: list[ExperimentRecord]) -> ExperimentRecord | None:
    if not results:
        return None
    return max(results, key=lambda result: result.timestamp)


# ── Entry reconciliation ──────────────────────────────────────────

def promote_missing_known_results(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # One-time reconciliation disabled for clean runs.
    # Previously auto-promoted stocks_in_play from known session context.
    # Now all variants are discovered through the benchmark loop.
    return entries


def deduplicate_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the richest entry per config. Drop low-info duplicates."""
    config_entries: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    config_order: list[str] = []
    non_experiment: list[dict[str, Any]] = []

    for idx, entry in enumerate(entries):
        if entry.get("type") == "config":
            non_experiment.append(entry)
            continue
        asi = entry.get("asi") or {}
        config = asi.get("config", "")
        if not config:
            non_experiment.append(entry)
            continue
        if config not in config_entries:
            config_entries[config] = []
            config_order.append(config)
        config_entries[config].append((idx, entry))

    deduped: list[dict[str, Any]] = list(non_experiment)
    for config in config_order:
        group = config_entries[config]
        if len(group) == 1:
            deduped.append(group[0][1])
            continue

        def richness(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
            _idx, e = item
            asi = e.get("asi") or {}
            has_trade = 1 if asi.get("trade_analysis") else 0
            has_insights = 1 if asi.get("insights") else 0
            return (has_trade, len(asi), has_insights)

        best_item = max(group, key=richness)
        deduped.append(best_item[1])

    return deduped


# ── current.md rendering ──────────────────────────────────────────

def render_current_md(state: dict[str, Any], results: list[ExperimentRecord]) -> str:
    best = state.get("current_best", {})
    latest = latest_result(results)
    next_action = state.get("next_action", {})
    pending = state.get("pending_configs", [])
    blockers = state.get("blockers", [])
    statuses = state.get("thesis_statuses", {})

    latest_lines: list[str] = []
    if latest is not None:
        latest_lines.append(f"- Last completed thesis: `{latest.config}`")
        latest_lines.append(f"- Last result: `{latest.status}` at `{latest.metric}`")
    if best:
        latest_lines.append(f"- Current best: `{best.get('config')}` at `{best.get('metric')}`")
    if not latest_lines:
        latest_lines.append("- No experiments logged yet.")

    if pending:
        next_candidates = [f"- `{config}`" for config in pending]
    elif next_action.get("type") == "research":
        next_candidates = ["- Research pass required before new thesis generation."]
    elif next_action.get("type") == "generate_theses":
        next_candidates = ["- Research exists; controller synthesis required before queuing new variants."]
    else:
        next_candidates = ["- None"]

    thesis_status_lines = [
        f"- `{config}`: `{meta.get('status', 'unknown')}`"
        for config, meta in statuses.items()
    ] or ["- None"]

    blocker_lines = [f"- {blocker['kind']}: {blocker.get('detail', '')}".rstrip() for blocker in blockers] or ["- None"]
    chosen = next_action.get("config", next_action.get("type", "none"))

    lines = [
        "# ORB Autoresearch Current State",
        "",
        "## Current Best",
        f"- `{best.get('config', 'unknown') if best else 'none'}`",
        f"- median_expectancy: `{best.get('metric', 'unknown') if best else 'none'}`",
        "",
        "## Latest Insights",
        *latest_lines,
        "",
        "## Next-Thesis Candidates",
        *next_candidates,
        "",
        "## Thesis Statuses",
        *thesis_status_lines,
        "",
        "## Chosen Next Thesis",
        f"- `{chosen}`",
        "",
        "## Blockers",
        *blocker_lines,
        "",
        "## Execution Control",
        "- Machine-readable controller: `autoresearch.next.json`",
        f"- Current controller state: `{state.get('state')}`",
        "- Experiment outcomes are heartbeats, not stopping points.",
    ]
    return "\n".join(lines) + "\n"


def write_current_md(current_md_path: Path, state: dict[str, Any], results: list[ExperimentRecord]) -> None:
    current_md_path.write_text(render_current_md(state, results))
