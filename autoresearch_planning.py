"""Planning logic for autoresearch.

Decides what experiment runs next: variant configs, thesis-queue artifacts,
combinations of independent winners, ideas-backlog candidates, baseline
reruns, or research blocking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from autoresearch_artifacts import (
    queue_from_thesis_artifacts,
    read_research_artifacts,
    read_run_queue,
    read_thesis_artifacts,
)
from autoresearch_constants import BASELINE_RERUN_INTERVAL
from autoresearch_logging import get_logger
from autoresearch_state import ExperimentRecord
from compiler_pipeline import compile_proposal_artifact
from strategy_family import StrategyFamily
from trace_logger import trace

log = get_logger(__name__)

# DEFAULT_CONFIG_ORDER is kept for backward compatibility with any
# external imports — the live path now reads from family.default_variants.
DEFAULT_CONFIG_ORDER = [
    "configs/variants/orb_spy_only.yaml",
    "configs/variants/orb_stocks_in_play.yaml",
    "configs/variants/orb_trailing_stop.yaml",
    "configs/variants/orb_trend_filter.yaml",
]

THESIS_FAMILY: dict[str, str] = {
    "spy_only": "universe",
    "stocks_in_play": "universe",
    "stocks_in_play_universe": "universe",
    "relative_volume_stocks_in_play": "universe",
    "top_10_dollar_volume_stocks_in_play": "universe",
    "high_relative_volume_filter": "entry",
    "trend_alignment_filter": "entry",
    "follow_through": "entry",
    "trailing_stop": "exit",
    "time_stop": "exit",
    "failed_breakout_exit": "exit",
    "volatility_trail": "exit",
    "trend_filter": "regime",
    "skip_chop": "regime",
    "skip_low_vol": "regime",
    "trend_day_only": "regime",
}

COMBINATION_RULES: dict[tuple[str, str], str] = {
    ("universe", "exit"): "allowed",
    ("universe", "entry"): "allowed",
    ("universe", "regime"): "allowed",
    ("entry", "exit"): "allowed",
    ("entry", "regime"): "allowed",
    ("exit", "regime"): "allowed",
    ("universe", "universe"): "disallowed",
    ("entry", "entry"): "disallowed",
    ("exit", "exit"): "review_required",
    ("regime", "regime"): "review_required",
}


# ── Variant discovery ─────────────────────────────────────────────


def list_known_variant_configs(root: Path, family: StrategyFamily) -> list[str]:
    """Variant configs for this family: the family's seed list (those
    that exist on disk) followed by every other yaml in configs/variants/."""
    known: list[str] = []
    for config in family.default_variants:
        if (root / config).exists():
            known.append(config)
    variants_dir = root / "configs" / "variants"
    if variants_dir.exists():
        for path in sorted(variants_dir.glob("*.yaml")):
            rel = path.relative_to(root).as_posix()
            if rel not in known and path.name != "README.keep":
                known.append(rel)
    return known


def pending_configs(
    root: Path, family: StrategyFamily, results: list[ExperimentRecord]
) -> list[str]:
    attempted = {result.config for result in results if result.config}
    return [
        config for config in list_known_variant_configs(root, family) if config not in attempted
    ]


def thesis_statuses(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for config in list_known_variant_configs(root, family):
        statuses[config] = {"status": "pending", "source": "variants_dir"}
    for artifact in read_run_queue(run_queue_dir, root):
        config = artifact.get("config")
        if not config:
            continue
        statuses.setdefault(config, {})
        statuses[config].update(
            {
                "status": artifact.get("status", statuses[config].get("status", "pending")),
                "source": "run_queue",
                "thesis_id": artifact.get("thesis_id"),
                "artifact_path": artifact.get("artifact_path"),
            }
        )
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


# ── Ideas backlog ─────────────────────────────────────────────────


def parse_ideas_backlog(ideas_md_path: Path, family: StrategyFamily) -> list[dict[str, Any]]:
    """Parse autoresearch.ideas.md and return candidate thesis dicts.
    Variant config paths are built from family.variant_config_path so
    each family gets its own prefix."""
    if not ideas_md_path.exists():
        return []
    text = ideas_md_path.read_text()
    candidates: list[dict[str, Any]] = []
    current_family: str = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("### ") and "thes" in line.lower():
            family_name = line.replace("### ", "").strip().lower()
            for key in ("universe", "entry", "exit", "regime"):
                if key in family_name:
                    current_family = key
                    break
            continue
        if line.startswith("- `") and "`" in line[3:]:
            slug = line[3 : line.index("`", 3)]
            candidates.append(
                {
                    "slug": slug,
                    "config": family.variant_config_path(slug),
                    "family": current_family or THESIS_FAMILY.get(slug, "unknown"),
                    "source": "ideas_backlog",
                }
            )
    return candidates


def generate_theses_from_ideas(
    root: Path,
    family: StrategyFamily,
    ideas_md_path: Path,
    run_queue_dir: Path,
    proposals_dir: Path,
    results: list[ExperimentRecord],
) -> list[str]:
    """Generate thesis artifacts for untested candidates from ideas backlog."""
    attempted = {r.config for r in results if r.config}
    existing_thesis_configs = {a.get("config") for a in read_run_queue(run_queue_dir, root)}
    candidates = parse_ideas_backlog(ideas_md_path, family)

    kept = [r for r in results if r.status == "keep"]
    discarded = [r for r in results if r.status == "discard"]

    generated: list[str] = []
    for candidate in candidates:
        config = candidate["config"]
        if config in attempted or config in existing_thesis_configs:
            continue
        if not (root / config).exists():
            continue
        proposal = {
            "thesis_id": candidate["slug"],
            "hypothesis": f"{candidate['slug']} ({candidate['family']} thesis)",
            "family": candidate["family"],
            "source": "controller_synthesis",
            "evidence": [
                f"Kept: {[r.config for r in kept]}",
                f"Discarded: {[r.config for r in discarded]}",
                f"Candidate from ideas backlog, family={candidate['family']}",
            ],
            "primitive_contract": [],
        }
        proposals_dir.mkdir(parents=True, exist_ok=True)
        path = proposals_dir / f"{candidate['slug']}.json"
        path.write_text(json.dumps(proposal, indent=2) + "\n")
        compile_proposal_artifact(proposal, root)
        generated.append(config)
    return generated


# ── Combinations ──────────────────────────────────────────────────


def thesis_family_for(config: str, family: StrategyFamily, proposals_dir: Path, root: Path) -> str:
    """Determine the thesis family for a config path."""
    slug = family.slug_from_config(config)
    if slug in THESIS_FAMILY:
        return THESIS_FAMILY[slug]
    for artifact in read_thesis_artifacts(proposals_dir, root):
        if artifact.get("thesis_id") == slug:
            return artifact.get("family", "unknown")
    return "unknown"


def _load_two_configs(root: Path, config_a: str, config_b: str) -> tuple[Any, Any] | None:
    """Load two YAML configs from disk; return (cfg_a, cfg_b) or None on
    any I/O or parse failure."""
    path_a = root / config_a
    path_b = root / config_b
    if not path_a.exists() or not path_b.exists():
        return None
    try:
        cfg_a = yaml.safe_load(path_a.read_text()) or {}
        cfg_b = yaml.safe_load(path_b.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return None
    return cfg_a, cfg_b


def _merge_combo_configs(
    cfg_a: Any,
    cfg_b: Any,
    *,
    a: ExperimentRecord,
    b: ExperimentRecord,
    family_a: str,
    family_b: str,
    combo_slug: str,
    root: Path,
    combo_config_yaml: str,
) -> tuple[Any, str]:
    """Materialize the merged combo on disk. Returns (merged, final_combo_config_path).

    For list-shaped (primitive contract) configs, writes a JSON contract
    under contracts/. For dict-shaped configs, writes a YAML overlay
    under configs/variants/.
    """
    if isinstance(cfg_a, list) and isinstance(cfg_b, list):
        merged: Any = [*cfg_a, *cfg_b]
        final_path = f"contracts/{combo_slug}.json"
        out = root / final_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(merged, indent=2) + "\n")
        return merged, final_path
    merged = {**cfg_a, **cfg_b}
    merged["_combination"] = {
        "source_a": a.config,
        "source_b": b.config,
        "family_a": family_a,
        "family_b": family_b,
    }
    out = root / combo_config_yaml
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.dump(merged, default_flow_style=False))
    return merged, combo_config_yaml


def _write_combination_proposal(
    proposals_dir: Path,
    *,
    family: StrategyFamily,
    combo_slug: str,
    a: ExperimentRecord,
    b: ExperimentRecord,
    family_a: str,
    family_b: str,
    merged: Any,
    root: Path,
) -> None:
    proposal = {
        "thesis_id": combo_slug,
        "hypothesis": (
            f"Combination of {family.slug_from_config(a.config)} ({family_a}) + "
            f"{family.slug_from_config(b.config)} ({family_b})"
        ),
        "family": "combination",
        "source": "combination_phase",
        "evidence": [
            f"{a.config} kept at {a.metric}",
            f"{b.config} kept at {b.metric}",
            f"Families {family_a}+{family_b} are compatible (orthogonal mechanisms)",
        ],
        "primitive_contract": merged if isinstance(merged, list) else [],
    }
    proposals_dir.mkdir(parents=True, exist_ok=True)
    (proposals_dir / f"{combo_slug}.json").write_text(json.dumps(proposal, indent=2) + "\n")
    if isinstance(merged, list):
        compile_proposal_artifact(proposal, root)


def _try_combine_pair(
    root: Path,
    family: StrategyFamily,
    proposals_dir: Path,
    a: ExperimentRecord,
    b: ExperimentRecord,
    attempted: set[str],
) -> str | None:
    """Try to materialize a combination of two kept theses.
    Returns the combo config path on success, None if the pair is
    incompatible or the combo already exists.
    """
    family_a = thesis_family_for(a.config, family, proposals_dir, root)
    family_b = thesis_family_for(b.config, family, proposals_dir, root)
    rule = COMBINATION_RULES.get((family_a, family_b)) or COMBINATION_RULES.get(
        (family_b, family_a), "disallowed"
    )
    if rule != "allowed":
        return None
    slug_a = family.slug_from_config(a.config)
    slug_b = family.slug_from_config(b.config)
    combo_slug = f"{slug_a}_x_{slug_b}"
    combo_config_yaml = family.variant_config_path(combo_slug)
    if combo_config_yaml in attempted or (root / combo_config_yaml).exists():
        return None
    cfgs = _load_two_configs(root, a.config, b.config)
    if cfgs is None:
        return None
    merged, final_combo_config = _merge_combo_configs(
        *cfgs,
        a=a,
        b=b,
        family_a=family_a,
        family_b=family_b,
        combo_slug=combo_slug,
        root=root,
        combo_config_yaml=combo_config_yaml,
    )
    _write_combination_proposal(
        proposals_dir,
        family=family,
        combo_slug=combo_slug,
        a=a,
        b=b,
        family_a=family_a,
        family_b=family_b,
        merged=merged,
        root=root,
    )
    return final_combo_config


def generate_combination_candidates(
    root: Path,
    family: StrategyFamily,
    proposals_dir: Path,
    results: list[ExperimentRecord],
) -> list[str]:
    """Generate combination configs from 2+ independent winners.
    Excludes the family's baseline config from the kept set so that
    the baseline is not treated as one of the two thesis winners."""
    baseline = family.baseline_config_path
    kept = [r for r in results if r.status == "keep" and r.config != baseline]
    if len(kept) < 2:
        return []
    attempted = {r.config for r in results if r.config}
    generated: list[str] = []
    for i, a in enumerate(kept):
        for b in kept[i + 1 :]:
            combo = _try_combine_pair(root, family, proposals_dir, a, b, attempted)
            if combo is not None:
                generated.append(combo)
    return generated


# ── Termination + finish summary ─────────────────────────────────


def should_terminate(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    research_dir: Path,
    results: list[ExperimentRecord],
) -> bool:
    if pending_configs(root, family, results):
        return False
    if queue_from_thesis_artifacts(run_queue_dir, root, results):
        return False
    research = read_research_artifacts(research_dir, root)
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


def _running_state(config: str, family: StrategyFamily, source: str) -> dict[str, Any]:
    """Build the standard `state=running` dict for one of the planning
    waterfall's branches. All five 'pick this config next' branches in
    select_research_next_action share this exact shape."""
    return {
        "state": "running",
        "current_thesis": {"config": config, "status": "ready_to_run"},
        "next_action": {
            "type": "run_experiment",
            "config": config,
            "benchmark_command": family.benchmark_command(config),
            "requires_trade_analysis": True,
            "source": source,
        },
        "blockers": [],
    }


def _baseline_branch(
    root: Path, family: StrategyFamily, results: list[ExperimentRecord]
) -> dict[str, Any] | None:
    if results:
        return None
    baseline_config = f"configs/{family.base_config_filename}"
    if not (root / baseline_config).exists():
        return None
    return _running_state(baseline_config, family, source="baseline")


def _thesis_queue_branch(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, Any] | None:
    queue = queue_from_thesis_artifacts(run_queue_dir, root, results)
    if not queue:
        return None
    return _running_state(queue[0], family, source="thesis_artifact")


def _combination_branch(
    root: Path,
    family: StrategyFamily,
    proposals_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, Any] | None:
    combos = generate_combination_candidates(root, family, proposals_dir, results)
    if not combos:
        return None
    return _running_state(combos[0], family, source="combination_phase")


def _ideas_branch(
    root: Path,
    family: StrategyFamily,
    ideas_md_path: Path,
    run_queue_dir: Path,
    proposals_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, Any] | None:
    configs = generate_theses_from_ideas(
        root, family, ideas_md_path, run_queue_dir, proposals_dir, results
    )
    if not configs:
        return None
    return _running_state(configs[0], family, source="ideas_backlog")


def select_research_next_action(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    proposals_dir: Path,
    ideas_md_path: Path,
    research_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, Any]:
    """Pick the next experiment to run, in priority order:

    1. Baseline if no results yet.
    2. Pending thesis-queue artifact.
    3. Combination of independent winners.
    4. Ideas-backlog candidate.
    5. Termination if research has confirmed nothing more to try.
    6. Block for research otherwise.
    """
    for branch in (
        _baseline_branch(root, family, results),
        _thesis_queue_branch(root, family, run_queue_dir, results),
        _combination_branch(root, family, proposals_dir, results),
        _ideas_branch(root, family, ideas_md_path, run_queue_dir, proposals_dir, results),
    ):
        if branch is not None:
            return branch
    if should_terminate(root, family, run_queue_dir, research_dir, results):
        return _finished_state()
    return _blocked_for_research_state(root, research_dir)


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
            "artifact_dir": research_dir.relative_to(root).as_posix(),
        },
        "blockers": [
            {
                "kind": "research_required",
                "detail": "Research subagent will generate the next thesis one at a time.",
            }
        ],
    }


def plan_next_action(
    state: dict[str, Any],
    results: list[ExperimentRecord],
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    proposals_dir: Path,
    ideas_md_path: Path,
    research_dir: Path,
) -> dict[str, Any]:
    # Respect forced baseline reruns — don't overwrite them
    if state.get("next_action", {}).get("baseline_rerun_for_commit"):
        return state
    pending = state.get("pending_configs", [])
    if pending:
        state.pop("finished_reason", None)
        state.pop("research_stop_reasoning", None)
    if pending:
        next_config = pending[0]
        state["state"] = "running"
        state["current_thesis"] = {"config": next_config, "status": "ready_to_run"}
        state["next_action"] = {
            "type": "run_experiment",
            "config": next_config,
            "benchmark_command": family.benchmark_command(next_config),
            "requires_trade_analysis": True,
        }
        state["blockers"] = []
        return state

    state.update(
        select_research_next_action(
            root,
            family,
            run_queue_dir,
            proposals_dir,
            ideas_md_path,
            research_dir,
            results,
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
    results: list[ExperimentRecord],
) -> dict[str, Any] | None:
    """Check if baseline needs rerunning. Returns next_action dict or None."""
    last_checkpoint = baseline_tracker.latest()
    if not last_checkpoint:
        return None

    # Coerce the checkpoint timestamp to int epoch-ms for comparison with
    # ExperimentRecord.timestamp (which read_results normalizes to int).
    # Post-rule-J the checkpoint stores ISO strings; coerce here so the
    # comparison stays valid for both legacy and new files.
    from autoresearch_state import coerce_timestamp_to_epoch_ms

    checkpoint_ts_ms = coerce_timestamp_to_epoch_ms(last_checkpoint.timestamp)
    needs_rerun = False
    reason = ""
    if last_checkpoint.code_commit != current_commit:
        needs_rerun = True
        reason = f"code changed {last_checkpoint.code_commit} -> {current_commit}"
    else:
        experiments_since = sum(1 for r in results if r.timestamp > checkpoint_ts_ms)
        if experiments_since >= BASELINE_RERUN_INTERVAL:
            needs_rerun = True
            reason = f"periodic rerun ({experiments_since} experiments since last baseline)"

    if not needs_rerun:
        return None

    already_reran = any(
        r.asi.get("baseline_rerun_for_commit") == current_commit and r.timestamp > checkpoint_ts_ms
        for r in results
    )
    if already_reran:
        return None

    baseline_config = f"configs/{family.base_config_filename}"
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
