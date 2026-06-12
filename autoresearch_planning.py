"""Planning logic for round-scoped autoresearch."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from autoresearch_constants import (
    research_engine_plateau_min_skill_gain,
    research_engine_plateau_rounds,
)
from autoresearch_logging import get_logger
from autoresearch_runtime_paths import iter_family_backtest_db_paths
from autoresearch_state import BacktestResultRecord
from research_types import CausalModel
from strategy_family import StrategyFamily
from trace_sdk import trace
from walkforward import WALKFORWARD_TERMINAL_STATUSES

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
    del run_queue_dir, research_dir, results
    config = _load_research_engine_config(root, family)
    plateau_rounds = research_engine_plateau_rounds(config)
    min_skill_gain = research_engine_plateau_min_skill_gain(config)
    model = _load_causal_model(root, family.name)
    if model is None or len(model.accuracy_history) < plateau_rounds:
        return False

    recent = sorted(model.accuracy_history, key=lambda point: point.round_number)[-plateau_rounds:]
    improvements = [max(0.0, right.skill - left.skill) for left, right in zip(recent, recent[1:])]
    max_improvement = max(improvements, default=0.0)
    if max_improvement >= min_skill_gain:
        return False

    if not _latest_screening_pass_rate_is_zero(root, family.name, plateau_rounds, job=job):
        return False
    return True


def _load_research_engine_config(root: Path, family: StrategyFamily) -> dict[str, Any]:
    candidates = [
        root / "configs" / family.base_config_filename,
        Path(__file__).resolve().parent / "configs" / family.base_config_filename,
        Path.cwd() / "configs" / family.base_config_filename,
    ]
    for path in candidates:
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _load_causal_model(root: Path, family_name: str) -> CausalModel | None:
    path = root / f"{family_name}_causal_model.json"
    if not path.exists():
        return None
    return CausalModel.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _latest_screening_pass_rate_is_zero(
    root: Path, family_name: str, plateau_rounds: int, *, job: int | None = None
) -> bool:
    if plateau_rounds <= 0:
        return False
    for db_path in iter_family_backtest_db_paths(root, family=family_name):
        with sqlite3.connect(db_path) as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='screenings'"
            ).fetchone()
            if table_exists is None:
                continue
            columns = {row[1] for row in conn.execute("PRAGMA table_info(screenings)").fetchall()}
            if job is not None and "job_id" not in columns:
                continue
            where = ""
            params: list[int] = [plateau_rounds]
            if job is not None:
                where = "WHERE job_id = ?"
                params = [job, plateau_rounds]
            round_rows = conn.execute(
                f"""
                SELECT DISTINCT round_number
                FROM screenings
                {where}
                ORDER BY round_number DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            round_numbers = sorted(int(row[0]) for row in round_rows)
            if len(round_numbers) < plateau_rounds:
                continue
            placeholders = ",".join("?" for _ in round_numbers)
            # Competitor rows record the alternative hypothesis, not a
            # successful proposal: exclude them or competitor passes keep the
            # plateau stopper from ever firing.
            competitor_filter = (
                "AND COALESCE(is_competitor, 0) = 0" if "is_competitor" in columns else ""
            )
            rows = conn.execute(
                f"""
                SELECT verdict
                FROM screenings
                WHERE round_number IN ({placeholders})
                {"AND job_id = ?" if job is not None else ""}
                {competitor_filter}
                """,
                [*round_numbers, job] if job is not None else round_numbers,
            ).fetchall()
        if rows and all(row[0] != "pass" for row in rows):
            return True
    return False


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
            "type": "run_round",
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
        return _walkforward_state(runtime_root)
    return _blocked_for_research_state(runtime_root, research_dir)


def _walkforward_state(root: Path) -> dict[str, Any]:
    return {
        "state": "running",
        "next_action": {
            "type": "walkforward",
            "reason": "model_plateau",
            "artifact_dir": _serialize_path(root, root / "walkforward"),
        },
        "blockers": [],
        "finished_reason": "model_plateau_pending_walkforward",
    }


def _finished_state() -> dict[str, Any]:
    return {
        "state": "finished",
        "next_action": {
            "type": "terminated",
            "reason": "Model plateau: holdout skill stopped improving and screening pass-rate is zero.",
        },
        "blockers": [],
        "finished_reason": "model_plateau",
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
    if (
        state.get("finished_reason") == "model_plateau_pending_walkforward"
        and state.get("walkforward_status") in WALKFORWARD_TERMINAL_STATUSES
    ):
        state.update(_finished_state())
        state.pop("walkforward_status", None)
        return state
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
    if (
        state.get("state") == "running"
        and state.get("finished_reason") != "model_plateau_pending_walkforward"
    ):
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
        "type": "run_round",
        "config": baseline_config,
        "benchmark_command": family.benchmark_command(baseline_config),
        "requires_trade_analysis": True,
        "source": "baseline",
        "baseline_rerun_for_commit": current_commit,
        "rerun_reason": reason,
    }
