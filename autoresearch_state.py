"""State, experiment results, and current.md rendering for autoresearch.

Pure functions. The controller composes these with its own paths.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from persistence_utils import write_text_atomic

from autoresearch_constants import MILLISECONDS_PER_SECOND

# ── Time helpers (rule J: UTC in persistent state) ───────────────


def iso8601_utc_now() -> str:
    """Current time as an ISO-8601 string with explicit UTC offset.

    Project rule J: stored timestamps must be UTC with timezone. Naive
    datetimes (and naive epoch-ms ints) are banned from persistent state.
    """
    return datetime.now(timezone.utc).isoformat()


def coerce_timestamp_to_iso8601_utc(value: Any) -> str | None:
    """Normalize a stored timestamp to ISO-8601 UTC, or None if unparseable.

    Accepts:
      - ISO-8601 string with timezone (already correct shape — passthrough)
      - int / float treated as epoch milliseconds (legacy format, pre-rule-J)
    Returns the ISO-8601 string or None if neither shape applies.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(
            value / MILLISECONDS_PER_SECOND,
            tz=timezone.utc,
        ).isoformat()
    if hasattr(value, "item"):
        scalar = value.item()
        if isinstance(scalar, (int, float)):
            return datetime.fromtimestamp(
                scalar / MILLISECONDS_PER_SECOND,
                tz=timezone.utc,
            ).isoformat()
    return None


def coerce_timestamp_to_epoch_ms(value: Any) -> int:
    """Inverse coercion for ordering / comparison code paths that still
    use integer math (e.g. baseline rerun, latest_result).

    Accepts ISO-8601 strings or epoch-ms ints. Returns 0 if unparseable so
    that downstream code that does `max(..., key=lambda r: r.timestamp)`
    cannot crash on a bad row.
    """
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value).timestamp() * MILLISECONDS_PER_SECOND)
        except ValueError:
            return 0
    return 0


@dataclass
class ExperimentRecord:
    config: str
    metric: float
    status: str
    description: str
    timestamp: str
    asi: dict[str, Any]


@dataclass
class RunContext:
    """Transient cross-method state carried by the controller.

    Replaces the legacy `self._current_*` / `self._latest_*` fields. Fields
    fall into three lifecycles:

    - Per-experiment (set in run_experiment, consumed by log_experiment_result,
      then cleared): current_artifact_dir.
    - Per-research-round (set in execute_research_sdk, consumed by run_experiment
      and log_experiment_result, then cleared): current_contract,
      parent_experiment_id.
    - Cross-iteration (overwritten by each derive_trade_analysis call, read by
      the next research round): latest_trades_file, latest_strategy_events_file,
      latest_diagnostics_file, latest_config_contents.
    """

    current_contract: Any = None
    parent_experiment_id: str = ""
    current_artifact_dir: Path | None = None
    latest_trades_file: str = ""
    latest_strategy_events_file: str = ""
    latest_diagnostics_file: str = ""
    latest_config_contents: dict[str, Any] = field(default_factory=dict)


# ── State JSON ─────────────────────────────────────────────────────


def read_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"state": "running"}
    return json.loads(state_path.read_text())


def write_state(state_path: Path, state: dict[str, Any]) -> None:
    write_text_atomic(state_path, json.dumps(state, indent=2) + "\n")


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
                timestamp=coerce_timestamp_to_iso8601_utc(entry.get("timestamp", 0))
                or "1970-01-01T00:00:00+00:00",
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
    return max(results, key=lambda result: coerce_timestamp_to_epoch_ms(result.timestamp))


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


def _format_latest_lines(latest: ExperimentRecord | None, best: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if latest is not None:
        lines.append(f"- Last completed thesis: `{latest.config}`")
        lines.append(f"- Last result: `{latest.status}` at `{latest.metric}`")
    if best:
        lines.append(f"- Current best: `{best.get('config')}` at `{best.get('metric')}`")
    if not lines:
        lines.append("- No experiments logged yet.")
    return lines


def _format_next_candidates(pending: list[str], next_action: dict[str, Any]) -> list[str]:
    if pending:
        return [f"- `{config}`" for config in pending]
    if next_action.get("type") == "research":
        return ["- Research pass required before new thesis generation."]
    if next_action.get("type") == "generate_theses":
        return ["- Research exists; controller synthesis required before queuing new variants."]
    return ["- None"]


def _format_thesis_status_lines(statuses: dict[str, dict[str, Any]]) -> list[str]:
    return [
        f"- `{config}`: `{meta.get('status', 'unknown')}`" for config, meta in statuses.items()
    ] or ["- None"]


def _format_blocker_lines(blockers: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {blocker['kind']}: {blocker.get('detail', '')}".rstrip() for blocker in blockers
    ] or ["- None"]


def render_current_md(
    state: dict[str, Any], results: list[ExperimentRecord], *, family_name: str | None = None
) -> str:
    best = state.get("current_best", {})
    next_action = state.get("next_action", {})
    chosen = next_action.get("config", next_action.get("type", "none"))
    label = family_name.upper() if family_name else "Autoresearch"
    lines = [
        f"# {label} Autoresearch Current State",
        "",
        "## Current Best",
        f"- `{best.get('config', 'unknown') if best else 'none'}`",
        f"- median_expectancy: `{best.get('metric', 'unknown') if best else 'none'}`",
        "",
        "## Latest Insights",
        *_format_latest_lines(latest_result(results), best),
        "",
        "## Next-Thesis Candidates",
        *_format_next_candidates(state.get("pending_configs", []), next_action),
        "",
        "## Thesis Statuses",
        *_format_thesis_status_lines(state.get("thesis_statuses", {})),
        "",
        "## Chosen Next Thesis",
        f"- `{chosen}`",
        "",
        "## Blockers",
        *_format_blocker_lines(state.get("blockers", [])),
        "",
        "## Execution Control",
        "- Machine-readable controller: `autoresearch.next.json`",
        f"- Current controller state: `{state.get('state')}`",
        "- Experiment outcomes are heartbeats, not stopping points.",
    ]
    return "\n".join(lines) + "\n"


def write_current_md(
    current_md_path: Path,
    state: dict[str, Any],
    results: list[ExperimentRecord],
    *,
    family_name: str | None = None,
) -> None:
    current_md_path.write_text(render_current_md(state, results, family_name=family_name))
