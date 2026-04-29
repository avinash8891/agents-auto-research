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
from autoresearch_state import ExperimentRecord
from compiler_pipeline import compile_proposal_artifact
from strategy_family import StrategyFamily
from trace_logger import trace

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

def list_known_variant_configs(root: Path) -> list[str]:
    known: list[str] = []
    for config in DEFAULT_CONFIG_ORDER:
        if (root / config).exists():
            known.append(config)
    variants_dir = root / "configs" / "variants"
    if variants_dir.exists():
        for path in sorted(variants_dir.glob("*.yaml")):
            rel = path.relative_to(root).as_posix()
            if rel not in known and path.name != "README.keep":
                known.append(rel)
    return known


def pending_configs(root: Path, results: list[ExperimentRecord]) -> list[str]:
    attempted = {result.config for result in results if result.config}
    return [config for config in list_known_variant_configs(root) if config not in attempted]


def thesis_statuses(
    root: Path,
    run_queue_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for config in list_known_variant_configs(root):
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

def parse_ideas_backlog(ideas_md_path: Path) -> list[dict[str, Any]]:
    """Parse autoresearch.ideas.md and return candidate thesis dicts."""
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
            slug = line[3:line.index("`", 3)]
            config_path = f"configs/variants/orb_{slug}.yaml"
            candidates.append({
                "slug": slug,
                "config": config_path,
                "family": current_family or THESIS_FAMILY.get(slug, "unknown"),
                "source": "ideas_backlog",
            })
    return candidates


def generate_theses_from_ideas(
    root: Path,
    ideas_md_path: Path,
    run_queue_dir: Path,
    proposals_dir: Path,
    results: list[ExperimentRecord],
) -> list[str]:
    """Generate thesis artifacts for untested candidates from ideas backlog."""
    attempted = {r.config for r in results if r.config}
    existing_thesis_configs = {a.get("config") for a in read_run_queue(run_queue_dir, root)}
    candidates = parse_ideas_backlog(ideas_md_path)

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

def thesis_family_for(config: str, proposals_dir: Path, root: Path) -> str:
    """Determine the thesis family for a config path."""
    slug = Path(config).stem.removeprefix("orb_")
    if slug in THESIS_FAMILY:
        return THESIS_FAMILY[slug]
    for artifact in read_thesis_artifacts(proposals_dir, root):
        if artifact.get("thesis_id") == slug:
            return artifact.get("family", "unknown")
    return "unknown"


def generate_combination_candidates(
    root: Path,
    proposals_dir: Path,
    results: list[ExperimentRecord],
) -> list[str]:
    """Generate combination configs from 2+ independent winners."""
    kept = [r for r in results if r.status == "keep" and r.config != "configs/orb_base.yaml"]
    if len(kept) < 2:
        return []

    attempted = {r.config for r in results if r.config}
    generated: list[str] = []

    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            family_a = thesis_family_for(a.config, proposals_dir, root)
            family_b = thesis_family_for(b.config, proposals_dir, root)
            pair = (family_a, family_b)
            rule = COMBINATION_RULES.get(pair) or COMBINATION_RULES.get(
                (family_b, family_a), "disallowed"
            )
            if rule != "allowed":
                continue

            slug_a = Path(a.config).stem.removeprefix("orb_")
            slug_b = Path(b.config).stem.removeprefix("orb_")
            combo_slug = f"{slug_a}_x_{slug_b}"
            combo_config = f"configs/variants/orb_{combo_slug}.yaml"

            if combo_config in attempted:
                continue
            if (root / combo_config).exists():
                continue

            path_a = root / a.config
            path_b = root / b.config
            if not path_a.exists() or not path_b.exists():
                continue
            try:
                cfg_a = yaml.safe_load(path_a.read_text()) or {}
                cfg_b = yaml.safe_load(path_b.read_text()) or {}
            except Exception:
                continue

            combo_path = root / combo_config
            combo_path.parent.mkdir(parents=True, exist_ok=True)

            if isinstance(cfg_a, list) and isinstance(cfg_b, list):
                merged: Any = [*cfg_a, *cfg_b]
                combo_config = f"contracts/{combo_slug}.json"
                combo_path = root / combo_config
                combo_path.parent.mkdir(parents=True, exist_ok=True)
                combo_path.write_text(json.dumps(merged, indent=2) + "\n")
            else:
                merged = {**cfg_a, **cfg_b}
                merged["_combination"] = {
                    "source_a": a.config,
                    "source_b": b.config,
                    "family_a": family_a,
                    "family_b": family_b,
                }
                combo_path.write_text(yaml.dump(merged, default_flow_style=False))

            proposal = {
                "thesis_id": combo_slug,
                "hypothesis": f"Combination of {slug_a} ({family_a}) + {slug_b} ({family_b})",
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
            (proposals_dir / f"{combo_slug}.json").write_text(
                json.dumps(proposal, indent=2) + "\n"
            )
            if isinstance(merged, list):
                compile_proposal_artifact(proposal, root)
            generated.append(combo_config)

    return generated


# ── Termination + finish summary ─────────────────────────────────

def should_terminate(
    root: Path,
    run_queue_dir: Path,
    research_dir: Path,
    results: list[ExperimentRecord],
) -> bool:
    if pending_configs(root, results):
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


def build_finish_summary(
    results: list[ExperimentRecord],
    completed_research: list[dict[str, Any]],
) -> dict[str, Any]:
    kept = [r.config for r in results if r.status == "keep" and r.config != "configs/orb_base.yaml"]
    discarded = [r.config for r in results if r.status == "discard"]
    combos = [r.config for r in results if "_x_" in Path(r.config).stem]
    return {
        "total_experiments": len(results),
        "kept": kept,
        "discarded": discarded,
        "combinations_tested": combos,
        "research_passes": len(completed_research),
    }


# ── Research-next-action waterfall + plan_next_action ────────────

def select_research_next_action(
    root: Path,
    family: StrategyFamily,
    run_queue_dir: Path,
    proposals_dir: Path,
    ideas_md_path: Path,
    research_dir: Path,
    results: list[ExperimentRecord],
) -> dict[str, Any]:
    if not results:
        baseline_config = f"configs/{family.base_config_filename}"
        baseline_path = root / baseline_config
        if baseline_path.exists():
            return {
                "state": "running",
                "current_thesis": {"config": baseline_config, "status": "ready_to_run"},
                "next_action": {
                    "type": "run_experiment",
                    "config": baseline_config,
                    "benchmark_command": family.benchmark_command(baseline_config),
                    "requires_trade_analysis": True,
                    "source": "baseline",
                },
                "blockers": [],
            }

    thesis_queue = queue_from_thesis_artifacts(run_queue_dir, root, results)
    if thesis_queue:
        next_config = thesis_queue[0]
        return {
            "state": "running",
            "current_thesis": {"config": next_config, "status": "ready_to_run"},
            "next_action": {
                "type": "run_experiment",
                "config": next_config,
                "benchmark_command": family.benchmark_command(next_config),
                "requires_trade_analysis": True,
                "source": "thesis_artifact",
            },
            "blockers": [],
        }

    combo_configs = generate_combination_candidates(root, proposals_dir, results)
    if combo_configs:
        next_config = combo_configs[0]
        return {
            "state": "running",
            "current_thesis": {"config": next_config, "status": "ready_to_run"},
            "next_action": {
                "type": "run_experiment",
                "config": next_config,
                "benchmark_command": family.benchmark_command(next_config),
                "requires_trade_analysis": True,
                "source": "combination_phase",
            },
            "blockers": [],
        }

    ideas_configs = generate_theses_from_ideas(
        root, ideas_md_path, run_queue_dir, proposals_dir, results
    )
    if ideas_configs:
        next_config = ideas_configs[0]
        return {
            "state": "running",
            "current_thesis": {"config": next_config, "status": "ready_to_run"},
            "next_action": {
                "type": "run_experiment",
                "config": next_config,
                "benchmark_command": family.benchmark_command(next_config),
                "requires_trade_analysis": True,
                "source": "ideas_backlog",
            },
            "blockers": [],
        }

    if should_terminate(root, run_queue_dir, research_dir, results):
        return {
            "state": "finished",
            "next_action": {
                "type": "terminated",
                "reason": "Research completed with no further justified theses.",
            },
            "blockers": [],
            "finished_reason": "research_completed_no_new_theses",
        }

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

    state.update(select_research_next_action(
        root, family, run_queue_dir, proposals_dir, ideas_md_path, research_dir, results,
    ))
    if state.get("state") == "running":
        state.pop("finished_reason", None)
        state.pop("research_stop_reasoning", None)
    return state


# ── Forced baseline rerun ────────────────────────────────────────

BASELINE_RERUN_INTERVAL = 5


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

    needs_rerun = False
    reason = ""
    if last_checkpoint.code_commit != current_commit:
        needs_rerun = True
        reason = f"code changed {last_checkpoint.code_commit} -> {current_commit}"
    else:
        experiments_since = sum(
            1 for r in results if r.timestamp > last_checkpoint.timestamp
        )
        if experiments_since >= BASELINE_RERUN_INTERVAL:
            needs_rerun = True
            reason = f"periodic rerun ({experiments_since} experiments since last baseline)"

    if not needs_rerun:
        return None

    already_reran = any(
        r.asi.get("baseline_rerun_for_commit") == current_commit
        and r.timestamp > last_checkpoint.timestamp
        for r in results
    )
    if already_reran:
        return None

    baseline_config = f"configs/{family.base_config_filename}"
    trace("BASELINE", f"forcing rerun: {reason}")
    print(f"BASELINE_RERUN {reason}")
    return {
        "type": "run_experiment",
        "config": baseline_config,
        "benchmark_command": family.benchmark_command(baseline_config),
        "requires_trade_analysis": True,
        "source": "baseline",
        "baseline_rerun_for_commit": current_commit,
        "rerun_reason": reason,
    }
