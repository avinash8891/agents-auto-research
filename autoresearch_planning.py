"""Planning logic for round-scoped autoresearch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoresearch_artifacts import read_research_artifacts
from autoresearch_logging import get_logger
from autoresearch_state import BacktestResultRecord
from strategy_family import StrategyFamily
from trace_sdk import trace

log = get_logger(__name__)

# DEFAULT_CONFIG_ORDER is kept for backward compatibility with any
# external imports — the live path now reads from family.default_variants.
DEFAULT_CONFIG_ORDER = [
    "configs/variants/orb_spy_only.yaml",
    "configs/variants/orb_stocks_in_play.yaml",
    "configs/variants/orb_trailing_stop.yaml",
    "configs/variants/orb_trend_filter.yaml",
]

THESIS_FAMILY: dict[str, str] = {}
COMBINATION_RULES: dict[tuple[str, str], str] = {}


# ── Variant discovery ─────────────────────────────────────────────


def list_known_variant_configs(root: Path, family: StrategyFamily) -> list[str]:
    """Inventory variant configs for this family.

    This helper is informational only; scheduling is queue/research-driven.
    """
    known: list[str] = []
    for config in family.default_variants:
        if (root / config).exists():
            known.append(config)
    variants_dir = root / "configs" / "variants"
    if variants_dir.exists():
        for path in sorted(variants_dir.glob("*.yaml")):
            rel = path.relative_to(root).as_posix()
            if path.name == "README.keep":
                continue
            if family.variant_prefix and not path.stem.startswith(family.variant_prefix):
                continue
            if rel not in known:
                known.append(rel)
    return known


def pending_configs(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> list[str]:
    return []


def thesis_statuses(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for result in results:
        if not result.config:
            continue
        statuses.setdefault(result.config, {})
        statuses[result.config].update(
            {
                "status": result.status,
                "last_metric": result.metric,
                "last_timestamp": result.timestamp,
                "description": result.description,
            }
        )
    return statuses


# ── Combinations ──────────────────────────────────────────────────


def thesis_family_for(config: str, family: StrategyFamily, proposals_dir: Path, root: Path) -> str:
    """Determine the thesis family for a config path."""
    slug = family.slug_from_config(config)
    thesis_family_by_slug = family.thesis_family_by_slug or {}
    if slug in thesis_family_by_slug:
        return thesis_family_by_slug[slug]
    return "unknown"


def generate_combination_candidates(
    root: Path,
    family: StrategyFamily,
    proposals_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> list[str]:
    """Winner-combination exploitation is disabled.

    Research must test isolated mechanisms from the family baseline instead of
    stacking previously kept configs.
    """
    return []


# ── Termination + finish summary ─────────────────────────────────


def should_terminate(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    research_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> bool:
    research = read_research_artifacts(research_dir, root, job=job)
    if not research:
        return False
    latest = research[-1]
    if latest.get("status") != "completed":
        return False
    generated = latest.get("generated_configs")
    if generated:
        return False
    if latest.get("new_theses_generated", 0):
        return False
    if latest.get("suggested_theses"):
        return False
    return bool(latest.get("findings"))


# ── Research-next-action waterfall + plan_next_action ────────────


def _serialize_path(root: Path, path: Path) -> str:
    """Prefer a root-relative path, but preserve absolute paths when needed."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _running_state(config: str, family: StrategyFamily, source: str) -> dict[str, Any]:
    """Build the standard `state=running` dict for one of the planning
    waterfall's branches. All five 'pick this config next' branches in
    select_research_next_action share this exact shape."""
    return {
        "state": "running",
        "current_thesis": {
            "config": config,
            "status": "ready_to_run",
            "selected_thesis_id": Path(config).stem,
        },
        "next_action": {
            "type": "run_experiment",
            "config": config,
            "benchmark_command": family.benchmark_command(config),
            "requires_trade_analysis": True,
            "source": source,
            "selected_thesis_id": Path(config).stem,
        },
        "blockers": [],
    }


def _baseline_branch(
    root: Path,
    family: StrategyFamily,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> dict[str, Any] | None:
    if results:
        return None
    baseline_config = family.baseline_config_path
    if not (root / baseline_config).exists():
        return None
    state = _running_state(baseline_config, family, source="baseline")
    state["research_round"] = 0
    state["selected_config_path"] = baseline_config
    state["selected_thesis_id"] = "baseline"
    job_part = f"job-{job}" if job is not None else "job-unknown"
    state["backtest_target_path"] = f"runtime/jobs/{job_part}/research/round-0-baseline/backtest"
    state["current_thesis"]["selected_thesis_id"] = "baseline"
    state["next_action"]["selected_thesis_id"] = "baseline"
    return state


def _thesis_queue_branch(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> dict[str, Any] | None:
    return None


def _combination_branch(
    root: Path,
    family: StrategyFamily,
    proposals_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> dict[str, Any] | None:
    combos = generate_combination_candidates(root, family, proposals_dir, results, job=job)
    if not combos:
        return None
    return _running_state(combos[0], family, source="combination_phase")


def select_research_next_action(
    code_root: Path,
    runtime_root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    proposals_dir: Path,
    research_dir: Path,
    results: list[BacktestResultRecord],
    job: int | None = None,
) -> dict[str, Any]:
    baseline = _baseline_branch(code_root, family, results, job=job)
    if baseline is not None:
        return baseline
    if should_terminate(runtime_root, family, run_queue_dir, research_dir, results, job=job):
        return _finished_state()
    return _blocked_for_research_state(runtime_root, research_dir)


def _finished_state() -> dict[str, Any]:
    return {
        "state": "finished",
        "next_action": {
            "type": "terminated",
            "reason": "Research completed with no further justified theses.",
        },
        "blockers": [],
        "finished_reason": "research_completed_no_new_theses",
    }


def _blocked_for_research_state(root: Path, research_dir: Path) -> dict[str, Any]:
    return {
        "state": "blocked",
        "next_action": {
            "type": "research",
            "reason": "All candidates and ideas exhausted; research subagent will generate next thesis.",
            "requires_subagent": True,
            "artifact_dir": _serialize_path(root, research_dir),
        },
        "blockers": [
            {
                "kind": "research_required",
                "detail": "Research subagent will generate the next thesis one at a time.",
            }
        ],
    }


def build_research_failure_state(
    root: Path,
    research_dir: Path,
    detail: str,
) -> dict[str, Any]:
    return {
        "state": "interrupted",
        "next_action": {
            "type": "terminated",
            "reason": detail,
            "artifact_dir": _serialize_path(root, research_dir),
        },
        "blockers": [
            {
                "kind": "research_failed",
                "detail": detail,
            }
        ],
    }


def plan_next_action(
    state: dict[str, Any],
    results: list[BacktestResultRecord],
    code_root: Path,
    runtime_root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    proposals_dir: Path,
    research_dir: Path,
) -> dict[str, Any]:
    # Respect forced baseline reruns — don't overwrite them
    if state.get("next_action", {}).get("baseline_rerun_for_commit"):
        return state
    # Brand-new job policy: baseline always runs first for the family.
    if not results:
        baseline = _baseline_branch(code_root, family, results, job=state.get("job"))
        if baseline is not None:
            state.update(baseline)
            state.pop("finished_reason", None)
            state.pop("research_stop_reasoning", None)
            return state

    raw_job = state.get("job")
    try:
        job = int(raw_job) if raw_job is not None else None
    except (TypeError, ValueError):
        job = None
    state.update(
        select_research_next_action(
            code_root,
            runtime_root,
            family,
            run_queue_dir,
            proposals_dir,
            research_dir,
            results,
            job=job,
        )
    )
    if state.get("state") == "running":
        state.pop("finished_reason", None)
        state.pop("research_stop_reasoning", None)
    return state


# ── Forced baseline rerun ────────────────────────────────────────


def check_baseline_rerun(
    root: Path,
    family: StrategyFamily,
    baseline_tracker: Any,
    current_commit: str,
    results: list[BacktestResultRecord],
) -> dict[str, Any] | None:
    """Check if baseline needs rerunning. Returns next_action dict or None."""
    last_checkpoint = baseline_tracker.latest()
    if not last_checkpoint:
        return None

    if last_checkpoint.code_commit == current_commit:
        return None

    reason = f"code changed {last_checkpoint.code_commit} -> {current_commit}"

    # Coerce both timestamps to epoch-ms for comparison.
    # BacktestResultRecord.timestamp is now ISO-8601 UTC str (rule J).
    # checkpoint.timestamp may be ISO string or legacy int; coerce both.
    from autoresearch_state import coerce_timestamp_to_epoch_ms

    checkpoint_ts_ms = coerce_timestamp_to_epoch_ms(last_checkpoint.timestamp)
    already_reran = any(
        r.asi.get("baseline_rerun_for_commit") == current_commit
        and coerce_timestamp_to_epoch_ms(r.timestamp) > checkpoint_ts_ms
        for r in results
    )
    if already_reran:
        return None

    baseline_config = family.baseline_config_path
    trace("BASELINE", f"forcing rerun: {reason}")
    log.info(f"BASELINE_RERUN {reason}")
    return {
        "type": "run_experiment",
        "config": baseline_config,
        "benchmark_command": family.benchmark_command(baseline_config),
        "requires_trade_analysis": True,
        "source": "baseline",
        "baseline_rerun_for_commit": current_commit,
        "rerun_reason": reason,
    }
