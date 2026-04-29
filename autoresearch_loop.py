#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from artifact_store import read_json_artifacts
from autoresearch_state import (
    ExperimentRecord,
    deduplicate_entries as _state_deduplicate_entries,
    direction as _state_direction,
    is_better as _state_is_better,
    best_result as _state_best_result,
    latest_result as _state_latest_result,
    promote_missing_known_results as _state_promote_missing_known_results,
    read_entries as _state_read_entries,
    read_results as _state_read_results,
    read_state as _state_read_state,
    render_current_md as _state_render_current_md,
    write_current_md as _state_write_current_md,
    write_entries as _state_write_entries,
    write_state as _state_write_state,
)
from trace_logger import (
    trace, trace_benchmark, trace_state_change, trace_ssh,
    begin_hypothesis, end_hypothesis, current_hypothesis_id, get_run_id,
)
from compiler_pipeline import compile_proposal_artifact
from experiment_db import (
    ExperimentDB, ExperimentResult, BaselineTracker, BaselineCheckpoint,
    build_data_hash, build_config_hash,
)
from strategy_family import StrategyFamily, load_family

ROOT = Path(__file__).resolve().parent
MAX_RESEARCH_ROUNDS = 100  # safeguard: max single-thesis research iterations

def _notify_discord(title: str, body: str, *, webhook: str = "", color: int = 0xFF0000) -> None:
    """Send a Discord embed notification. Fire-and-forget, never raises."""
    if not webhook:
        return
    try:
        import urllib.request
        payload = json.dumps({
            "embeds": [{
                "title": title,
                "description": body[:4000],
                "color": color,
            }]
        }).encode()
        req = urllib.request.Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "AutoresearchBot/1.0"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        trace("DISCORD", f"OK title='{title[:60]}'")
    except Exception as exc:
        trace("DISCORD", f"FAILED title='{title[:60]}' error={exc}")
STATE_PATH = ROOT / "autoresearch.next.json"
JSONL_PATH = ROOT / "autoresearch.jsonl"
CURRENT_MD_PATH = ROOT / "autoresearch.current.md"
IDEAS_MD_PATH = ROOT / "autoresearch.ideas.md"
RUNS_DIR = ROOT / "autoresearch-runs"
RESEARCH_DIR = ROOT / "research"
THESIS_DIR = ROOT / "theses"
DEFAULT_CONFIG_ORDER = [
    "configs/variants/orb_spy_only.yaml",
    "configs/variants/orb_stocks_in_play.yaml",
    "configs/variants/orb_trailing_stop.yaml",
    "configs/variants/orb_trend_filter.yaml",
]


def default_controller_paths(root: Path, family: StrategyFamily) -> tuple[Path, Path, Path, Path, Path]:
    prefix = family.name
    return (
        root / f"{prefix}_autoresearch.next.json",
        root / f"{prefix}_autoresearch.jsonl",
        root / f"{prefix}_autoresearch.current.md",
        root / f"{prefix}_autoresearch.ideas.md",
        root / family.runs_dirname,
    )

# Thesis family categories for combination compatibility
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


class AutoresearchController:
    def __init__(
        self,
        root: Path = ROOT,
        state_path: Path = STATE_PATH,
        jsonl_path: Path = JSONL_PATH,
        current_md_path: Path = CURRENT_MD_PATH,
        ideas_md_path: Path = IDEAS_MD_PATH,
        runs_dir: Path | None = None,
        family: StrategyFamily | None = None,
    ) -> None:
        self.root = root.resolve()
        self.state_path = state_path
        self.jsonl_path = jsonl_path
        self.current_md_path = current_md_path
        self.ideas_md_path = ideas_md_path
        self.family = family or load_family("orb")
        self.runs_dir = runs_dir or (root / self.family.runs_dirname)
        self.research_dir = root / self.family.research_dirname
        self.thesis_dir = root / "theses"
        self.proposals_dir = root / self.family.proposals_dirname
        self.compilations_dir = root / self.family.compilations_dirname
        self.contracts_dir = root / self.family.contracts_dirname
        self.run_queue_dir = root / self.family.run_queue_dirname
        self.experiment_db = ExperimentDB(root / f"{self.family.name}_experiments_db.json")
        self.baseline_tracker = BaselineTracker(root / f"{self.family.name}_baseline_checkpoints.json")

    def read_state(self) -> dict[str, Any]:
        return _state_read_state(self.state_path)

    def write_state(self, state: dict[str, Any]) -> None:
        _state_write_state(self.state_path, state)

    def read_entries(self) -> list[dict[str, Any]]:
        return _state_read_entries(self.jsonl_path)

    def write_entries(self, entries: list[dict[str, Any]]) -> None:
        _state_write_entries(self.jsonl_path, entries)

    def current_commit(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        commit = (result.stdout or "").strip()
        return commit or "unknown"

    def direction(self) -> str:
        return _state_direction(self.read_entries())

    def read_results(self) -> list[ExperimentRecord]:
        return _state_read_results(self.read_entries())

    def read_json_artifacts(self, directory: Path) -> list[dict[str, Any]]:
        artifacts = read_json_artifacts(directory)
        for payload in artifacts:
            artifact_path = payload.get("artifact_path")
            if artifact_path:
                payload["artifact_path"] = Path(artifact_path).relative_to(self.root).as_posix()
        return artifacts

    def read_research_artifacts(self) -> list[dict[str, Any]]:
        return self.read_json_artifacts(self.research_dir)

    def read_thesis_artifacts(self) -> list[dict[str, Any]]:
        return self.read_json_artifacts(self.proposals_dir)

    def is_better(self, candidate: float, current: float | None) -> bool:
        return _state_is_better(self.direction(), candidate, current)

    def best_result(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        return _state_best_result(results, self.direction())

    def latest_result(self, results: list[ExperimentRecord]) -> ExperimentRecord | None:
        return _state_latest_result(results)

    def list_known_variant_configs(self) -> list[str]:
        known: list[str] = []
        for config in DEFAULT_CONFIG_ORDER:
            if (self.root / config).exists():
                known.append(config)
        variants_dir = self.root / "configs" / "variants"
        if variants_dir.exists():
            for path in sorted(variants_dir.glob("*.yaml")):
                rel = path.relative_to(self.root).as_posix()
                if rel not in known and path.name != "README.keep":
                    known.append(rel)
        return known

    def pending_configs(self, results: list[ExperimentRecord]) -> list[str]:
        attempted = {result.config for result in results if result.config}
        return [config for config in self.list_known_variant_configs() if config not in attempted]

    def thesis_statuses(self, results: list[ExperimentRecord]) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        for config in self.list_known_variant_configs():
            statuses[config] = {"status": "pending", "source": "variants_dir"}
        for artifact in self.read_run_queue():
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

    def read_run_queue(self) -> list[dict[str, Any]]:
        return self.read_json_artifacts(self.run_queue_dir)

    def queue_from_thesis_artifacts(self, results: list[ExperimentRecord]) -> list[str]:
        attempted = {result.config for result in results if result.config}
        queued: list[str] = []
        for artifact in self.read_run_queue():
            if artifact.get("status") != "pending":
                continue
            config = artifact.get("config")
            if not config or config in attempted or config in queued:
                continue
            if not (self.root / config).exists():
                continue
            queued.append(config)
        return queued

    def promote_missing_known_results(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_promote_missing_known_results(entries)

    def deduplicate_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _state_deduplicate_entries(entries)

    # ── WS-2: Thesis generation from ideas backlog ──────────────────────

    def parse_ideas_backlog(self) -> list[dict[str, Any]]:
        """Parse autoresearch.ideas.md and return candidate thesis dicts."""
        if not self.ideas_md_path.exists():
            return []
        text = self.ideas_md_path.read_text()
        candidates: list[dict[str, Any]] = []
        current_family: str = ""
        for line in text.splitlines():
            line = line.strip()
            # Detect family headers like "### Universe theses"
            if line.startswith("### ") and "thes" in line.lower():
                family_name = line.replace("### ", "").strip().lower()
                for key in ("universe", "entry", "exit", "regime"):
                    if key in family_name:
                        current_family = key
                        break
                continue
            # Detect candidate bullets like "- `spy_only`"
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

    def generate_theses_from_ideas(self, results: list[ExperimentRecord]) -> list[str]:
        """Generate thesis artifacts for untested candidates from ideas backlog.

        Returns list of config paths for newly generated theses.
        """
        attempted = {r.config for r in results if r.config}
        existing_thesis_configs = {a.get("config") for a in self.read_run_queue()}
        candidates = self.parse_ideas_backlog()

        kept = [r for r in results if r.status == "keep"]
        discarded = [r for r in results if r.status == "discard"]

        generated: list[str] = []
        for candidate in candidates:
            config = candidate["config"]
            if config in attempted or config in existing_thesis_configs:
                continue
            if not (self.root / config).exists():
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
            self.proposals_dir.mkdir(parents=True, exist_ok=True)
            path = self.proposals_dir / f"{candidate['slug']}.json"
            path.write_text(json.dumps(proposal, indent=2) + "\n")
            compile_proposal_artifact(proposal, self.root)
            generated.append(config)
        return generated

    # ── WS-5: Combination phase ───────────────────────────────────────

    def thesis_family_for(self, config: str) -> str:
        """Determine the thesis family for a config path."""
        slug = Path(config).stem.removeprefix("orb_")
        if slug in THESIS_FAMILY:
            return THESIS_FAMILY[slug]
        # Check proposal artifacts for family metadata
        for artifact in self.read_thesis_artifacts():
            if artifact.get("thesis_id") == slug:
                return artifact.get("family", "unknown")
        return "unknown"

    def generate_combination_candidates(self, results: list[ExperimentRecord]) -> list[str]:
        """Generate combination configs from 2+ independent winners.

        Only combines candidates from compatible thesis families.
        Returns list of config paths for newly generated combinations.
        """
        kept = [r for r in results if r.status == "keep" and r.config != "configs/orb_base.yaml"]
        if len(kept) < 2:
            return []

        attempted = {r.config for r in results if r.config}
        generated: list[str] = []

        for i, a in enumerate(kept):
            for b in kept[i + 1:]:
                family_a = self.thesis_family_for(a.config)
                family_b = self.thesis_family_for(b.config)
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
                if (self.root / combo_config).exists():
                    continue  # already exists, will be picked up by list_known_variant_configs

                # Merge configs: load both YAMLs, overlay b onto a
                path_a = self.root / a.config
                path_b = self.root / b.config
                if not path_a.exists() or not path_b.exists():
                    continue
                try:
                    cfg_a = yaml.safe_load(path_a.read_text()) or {}
                    cfg_b = yaml.safe_load(path_b.read_text()) or {}
                except Exception:
                    continue

                combo_path = self.root / combo_config
                combo_path.parent.mkdir(parents=True, exist_ok=True)

                if isinstance(cfg_a, list) and isinstance(cfg_b, list):
                    merged = [*cfg_a, *cfg_b]
                    combo_config = f"contracts/{combo_slug}.json"
                    combo_path = self.root / combo_config
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
                self.proposals_dir.mkdir(parents=True, exist_ok=True)
                (self.proposals_dir / f"{combo_slug}.json").write_text(
                    json.dumps(proposal, indent=2) + "\n"
                )
                if isinstance(merged, list):
                    compile_proposal_artifact(proposal, self.root)
                generated.append(combo_config)

        return generated

    # ── WS-3: Research stage hook ─────────────────────────────────────

    def emit_research_request(self, results: list[ExperimentRecord]) -> Path:
        """Write a research request artifact for external execution."""
        requests_dir = self.research_dir / "requests"
        requests_dir.mkdir(parents=True, exist_ok=True)
        request_id = f"research-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
        best = self.best_result(results)
        kept = [r.config for r in results if r.status == "keep"]
        discarded = [r.config for r in results if r.status == "discard"]

        request = {
            "request_id": f"{self.family.name}-{request_id}",
            "type": "wide_scan",
            "status": "pending",
            "family": self.family.name,
            "context": {
                "strategy": self.family.name.upper(),
                "current_best": best,
                "kept_theses": kept,
                "discarded_theses": discarded,
            },
            "questions": [
                "What structural improvements to opening range breakout strategies are documented in literature?",
                "What regime filters have been shown to improve mean-reversion or breakout strategies?",
                "What exit mechanisms beyond fixed stop/target improve intraday momentum strategies?",
                "What universe selection methods improve intraday breakout strategy performance?",
            ],
        }
        path = requests_dir / f"{request_id}.json"
        path.write_text(json.dumps(request, indent=2) + "\n")
        return path

    def check_research_completion(self) -> list[dict[str, Any]]:
        """Return completed research artifacts (status=completed)."""
        completed: list[dict[str, Any]] = []
        if not self.research_dir.exists():
            return completed
        for path in sorted(self.research_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "completed":
                payload["artifact_path"] = path.relative_to(self.root).as_posix()
                completed.append(payload)
        return completed

    def has_pending_research_request(self) -> bool:
        """Check if there is already a pending research request."""
        requests_dir = self.research_dir / "requests"
        if not requests_dir.exists():
            return False
        for path in requests_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if payload.get("status") == "pending":
                return True
        return False

    # ── WS-4: Parse real benchmark output ─────────────────────────────

    def parse_result_json(self, output: str) -> dict[str, Any] | None:
        """Find RESULT_JSON line in output, read and return the JSON file."""
        match = re.search(r"^RESULT_JSON (.+)$", output, flags=re.MULTILINE)
        if not match:
            return None
        result_path = Path(match.group(1).strip())
        if not result_path.exists():
            return None
        try:
            return json.loads(result_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def parse_benchmark_details(self, output: str) -> dict[str, Any]:
        """Extract metrics from result.json written by backtest."""
        result_json = self.parse_result_json(output)
        if result_json:
            details: dict[str, Any] = {}
            metrics = result_json.get("metrics", {})
            for key in ("trade_count", "profit_factor", "max_drawdown",
                        "pct_profitable_windows", "avg_sharpe_across_windows",
                        "win_rate"):
                if key in metrics:
                    details[key] = metrics[key]
            if result_json.get("diagnostics"):
                details["diagnostics"] = result_json["diagnostics"]
            if result_json.get("trades_file"):
                details["trades_file"] = result_json["trades_file"]
            if result_json.get("strategy_events_file"):
                details["strategy_events_file"] = result_json["strategy_events_file"]
            if result_json.get("diagnostics_file"):
                details["diagnostics_file"] = result_json["diagnostics_file"]
            if result_json.get("strategy_diagnostics"):
                details["strategy_diagnostics"] = result_json["strategy_diagnostics"]
            if result_json.get("git_sha"):
                details["git_sha"] = result_json["git_sha"]
            if result_json.get("config_hash"):
                details["config_hash"] = result_json["config_hash"]
            return details

        # Fallback: should not happen with new backtest, but keeps old runs working
        trace("LOOP", "WARNING: no RESULT_JSON found, falling back to stdout parsing")
        return self._parse_benchmark_details_legacy(output)

    def _parse_benchmark_details_legacy(self, output: str) -> dict[str, Any]:
        """Legacy stdout parsing u2014 only used if result.json is missing."""
        details: dict[str, Any] = {}
        patterns = {
            "trade_count": r"^METRIC trade_count=(\d+)",
            "profit_factor": r"^METRIC profit_factor=([-+]?\d*\.?\d+)",
            "max_drawdown": r"^METRIC max_drawdown=([-+]?\d*\.?\d+)",
            "pct_profitable_windows": r"^METRIC pct_profitable_windows=([-+]?\d*\.?\d+)",
            "avg_sharpe_across_windows": r"^METRIC avg_sharpe_across_windows=([-+]?\d*\.?\d+)",
            "win_rate": r"^METRIC win_rate=([-+]?\d*\.?\d+)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, output, flags=re.MULTILINE)
            if match:
                val = match.group(1)
                details[key] = int(val) if key == "trade_count" else float(val)
        diag_match = re.search(r"^DIAGNOSTICS (.+)$", output, flags=re.MULTILINE)
        if diag_match:
            try:
                details["diagnostics"] = json.loads(diag_match.group(1))
            except json.JSONDecodeError:
                pass
        trades_match = re.search(r"^TRADES_FILE (.+)$", output, flags=re.MULTILINE)
        if trades_match:
            details["trades_file"] = trades_match.group(1).strip()
        return details

    def select_research_next_action(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        # 0. No results at all -> run baseline first
        if not results:
            baseline_config = f"configs/{self.family.base_config_filename}"
            baseline_path = self.root / baseline_config
            if baseline_path.exists():
                return {
                    "state": "running",
                    "current_thesis": {"config": baseline_config, "status": "ready_to_run"},
                    "next_action": {
                        "type": "run_experiment",
                        "config": baseline_config,
                        "benchmark_command": self.family.benchmark_command(baseline_config),
                        "requires_trade_analysis": True,
                        "source": "baseline",
                    },
                    "blockers": [],
                }

        # 1. Check existing thesis artifact queue
        thesis_queue = self.queue_from_thesis_artifacts(results)
        if thesis_queue:
            next_config = thesis_queue[0]
            return {
                "state": "running",
                "current_thesis": {"config": next_config, "status": "ready_to_run"},
                "next_action": {
                    "type": "run_experiment",
                    "config": next_config,
                    "benchmark_command": self.family.benchmark_command(next_config),
                    "requires_trade_analysis": True,
                    "source": "thesis_artifact",
                },
                "blockers": [],
            }

        # 2. Try generating combination candidates from independent winners
        combo_configs = self.generate_combination_candidates(results)
        if combo_configs:
            next_config = combo_configs[0]
            return {
                "state": "running",
                "current_thesis": {"config": next_config, "status": "ready_to_run"},
                "next_action": {
                    "type": "run_experiment",
                    "config": next_config,
                    "benchmark_command": self.family.benchmark_command(next_config),
                    "requires_trade_analysis": True,
                    "source": "combination_phase",
                },
                "blockers": [],
            }

        # 3. Try generating theses from ideas backlog
        ideas_configs = self.generate_theses_from_ideas(results)
        if ideas_configs:
            next_config = ideas_configs[0]
            return {
                "state": "running",
                "current_thesis": {"config": next_config, "status": "ready_to_run"},
                "next_action": {
                    "type": "run_experiment",
                    "config": next_config,
                    "benchmark_command": self.family.benchmark_command(next_config),
                    "requires_trade_analysis": True,
                    "source": "ideas_backlog",
                },
                "blockers": [],
            }

        # 4. All sources exhausted -> finish only after a completed research pass
        if self.should_terminate(results):
            return {
                "state": "finished",
                "next_action": {
                    "type": "terminated",
                    "reason": "Research completed with no further justified theses.",
                },
                "blockers": [],
                "finished_reason": "research_completed_no_new_theses",
            }

        # 5. Otherwise blocked for research
        # Termination decisions (should_stop, max_rounds, no thesis) are handled
        # by execute_once, not here. This just signals that research is needed.
        return {
            "state": "blocked",
            "next_action": {
                "type": "research",
                "reason": "All candidates and ideas exhausted; research subagent will generate next thesis.",
                "requires_subagent": True,
                "artifact_dir": self.research_dir.relative_to(self.root).as_posix(),
            },
            "blockers": [
                {
                    "kind": "research_required",
                    "detail": "Research subagent will generate the next thesis one at a time.",
                }
            ],
        }

    def should_terminate(self, results: list[ExperimentRecord] | None = None) -> bool:
        current_results = results if results is not None else self.read_results()
        if self.pending_configs(current_results):
            return False
        if self.queue_from_thesis_artifacts(current_results):
            return False
        research = self.read_research_artifacts()
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

    def _build_finish_summary(self, results: list[ExperimentRecord]) -> dict[str, Any]:
        kept = [r.config for r in results if r.status == "keep" and r.config != "configs/orb_base.yaml"]
        discarded = [r.config for r in results if r.status == "discard"]
        combos = [r.config for r in results if "_x_" in Path(r.config).stem]
        research_passes = len(self.check_research_completion())
        return {
            "total_experiments": len(results),
            "kept": kept,
            "discarded": discarded,
            "combinations_tested": combos,
            "research_passes": research_passes,
        }

    def plan_next_action(self, state: dict[str, Any], results: list[ExperimentRecord]) -> dict[str, Any]:
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
                "benchmark_command": self.family.benchmark_command(next_config),
                "requires_trade_analysis": True,
            }
            state["blockers"] = []
            return state

        state.update(self.select_research_next_action(results))
        if state.get("state") == "running":
            state.pop("finished_reason", None)
            state.pop("research_stop_reasoning", None)
        return state

    def render_current_md(self, state: dict[str, Any], results: list[ExperimentRecord]) -> str:
        return _state_render_current_md(state, results)

    def write_current_md(self, state: dict[str, Any], results: list[ExperimentRecord]) -> None:
        _state_write_current_md(self.current_md_path, state, results)

    def reconcile_state(self) -> dict[str, Any]:
        entries = self.read_entries()
        reconciled_entries = self.promote_missing_known_results(entries)
        reconciled_entries = self.deduplicate_entries(reconciled_entries)
        if reconciled_entries != entries:
            self.write_entries(reconciled_entries)
        state = self.read_state()
        results = self.read_results()
        best = self.best_result(results)
        if best:
            state["current_best"] = best
        state["pending_configs"] = self.pending_configs(results)
        state["thesis_statuses"] = self.thesis_statuses(results)

        latest = self.latest_result(results)
        heartbeat = state.setdefault("heartbeat", {})
        if latest is not None:
            heartbeat["last_completed_thesis"] = latest.config
            heartbeat["last_result"] = latest.status
            heartbeat["last_metric"] = latest.metric
            heartbeat["current_best"] = state.get("current_best", {})

        state = self.plan_next_action(state, results)
        old_state_val = state.get("state", "unknown")
        self.write_state(state)
        self.write_current_md(state, results)
        next_action = state.get("next_action", {})
        trace("RECONCILE", f"state={old_state_val} best={best} next_action_type={next_action.get('type')}")
        return state

    def artifact_dir_for(self, config: str) -> Path:
        state = self.read_state()
        job = state.get("job", 0)
        path = self.runs_dir.resolve() / f"job-{job}" / Path(config).stem
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sanitize_duplicate_entries(self, config: str) -> None:
        filtered: list[dict[str, Any]] = []
        slug = Path(config).stem
        for entry in self.read_entries():
            if entry.get("type") == "config":
                filtered.append(entry)
                continue
            asi = entry.get("asi") or {}
            same_config = asi.get("config") == config
            low_information = entry.get("description") == f"loop: {slug}"
            if same_config and low_information:
                continue
            filtered.append(entry)
        self.write_entries(filtered)

    def _accumulate_job_usage(self, round_usage: dict[str, Any]) -> None:
        """Accumulate round token usage into job totals in state JSON."""
        state = self.read_state()
        job_usage = state.get("job_usage") or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "rounds": 0}
        total = round_usage.get("total", {})
        job_usage["input_tokens"] += total.get("input_tokens", 0)
        job_usage["output_tokens"] += total.get("output_tokens", 0)
        job_usage["total_tokens"] += total.get("total_tokens", 0)
        job_usage["cost_usd"] += total.get("cost_usd", 0.0)
        job_usage["rounds"] += 1
        state["job_usage"] = job_usage
        self.write_state(state)

    def log_research_round(
        self,
        *,
        round_number: int,
        thesis_id: str,
        outcome: str,  # "compiled", "needs_code", "rejected", "failed", "stopped"
        config_changes: dict[str, Any] | None = None,
        hypothesis: str = "",
        mechanism: str = "",
        mechanism_dimension: str = "",
        rejection_reason: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Log every research round outcome to the JSONL, regardless of whether it produced a runnable experiment."""
        entries = self.read_entries()
        state = self.read_state()
        entry = {
            "type": "research_round",
            "job": state.get("job"),
            "round": round_number,
            "run_id": get_run_id(),
            "hypothesis_id": current_hypothesis_id(),
            "thesis_id": thesis_id,
            "outcome": outcome,
            "config_changes": config_changes or {},
            "hypothesis": hypothesis,
            "mechanism": mechanism,
            "mechanism_dimension": mechanism_dimension,
            "rejection_reason": rejection_reason,
            "usage": usage,
            "timestamp": int(time.time() * 1000),
        }
        entries.append(entry)
        self.write_entries(entries)

    def log_experiment_result(
        self,
        *,
        config: str,
        metric: float,
        decision: str,
        output: str,
        analysis: dict[str, Any],
    ) -> None:
        self.sanitize_duplicate_entries(config)
        # Reuse artifact dir from derive_trade_analysis if available
        artifact_dir = getattr(self, "_current_artifact_dir", None) or self.artifact_dir_for(config)
        self._current_artifact_dir = None  # reset for next hypothesis
        (artifact_dir / "benchmark_output.txt").write_text(output)
        (artifact_dir / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")

        # Load thesis metadata (thesis_id, config_changes) from the
        # experiment directory so the results table shows WHAT was tried.
        contract = getattr(self, "_current_contract", None)
        thesis_id = contract.thesis_id if contract else Path(config).stem
        config_changes: dict[str, Any] = {}
        thesis_json_path = self.root / "experiments" / (contract.experiment_id if contract else Path(config).stem) / "thesis.json"
        if thesis_json_path.exists():
            try:
                tj = json.loads(thesis_json_path.read_text())
                thesis_id = tj.get("thesis_id", thesis_id)
                config_changes = tj.get("config_changes", {})
            except Exception:
                pass

        asi = {
            "job": self.read_state().get("job"),
            "run_id": get_run_id(),
            "hypothesis_id": current_hypothesis_id(),
            "hypothesis": Path(config).stem,
            "config": config,
            "artifact_dir": artifact_dir.relative_to(self.root).as_posix(),
            "trade_analysis": analysis.get("trade_analysis", {}),
            "insights": analysis.get("insights", []),
            "next_candidates": analysis.get("next_candidates", []),
            "next_thesis_suggestion": analysis.get("next_thesis_suggestion", ""),
            "why_not_data_fit": analysis.get("why_not_data_fit"),
            "insight_brief": analysis.get("insight_brief", ""),
            "thesis_id": thesis_id,
            "config_changes": config_changes,
        }
        # Tag baseline reruns for drift detection
        rerun_commit = self.read_state().get("next_action", {}).get("baseline_rerun_for_commit")
        if rerun_commit:
            asi["baseline_rerun_for_commit"] = rerun_commit
        details = self.parse_benchmark_details(output)
        entries = self.read_entries()
        next_run = 1 + len([entry for entry in entries if entry.get("type") != "config"])
        state = self.read_state()
        entry = {
            "run": next_run,
            "job": state.get("job"),
            "run_id": get_run_id(),
            "hypothesis_id": current_hypothesis_id(),
            "commit": self.current_commit(),
            "metric": metric,
            "metrics": details,
            "status": decision,
            "description": f"strict-native loop: {Path(config).stem}",
            "timestamp": int(time.time() * 1000),
            "segment": 0,
            "confidence": None,
            "asi": asi,
        }
        entries.append(entry)
        self.write_entries(entries)
        trace_benchmark(config, metric, decision, details)

        # Write to structured experiment database
        contract = getattr(self, "_current_contract", None)
        verdict = analysis.get("trade_analysis", {}).get("verdict", {})
        runtime_config = getattr(self, "_latest_config_contents", {}) or {}
        self.experiment_db.add(ExperimentResult(
            experiment_id=contract.experiment_id if contract else entry["run_id"],
            thesis_id=contract.thesis_id if contract else Path(config).stem,
            config_path=config,
            runtime_config=runtime_config,
            code_commit=self.current_commit(),
            data_hash=build_data_hash(runtime_config),
            train_metrics={},
            validation_metrics=details,
            trade_count=details.get("trade_count", 0),
            trades_file=details.get("trades_file", ""),
            strategy_events_file=details.get("strategy_events_file", ""),
            diagnostics_file=details.get("diagnostics_file", ""),
            strategy_diagnostics=details.get("strategy_diagnostics", {}),
            accepted=decision == "keep",
            rejection_reason=verdict.get("summary", "") if decision != "keep" else "",
            verdict_status=verdict.get("status", "none"),
            verdict_summary=verdict.get("summary", ""),
            parent_experiment_id=getattr(self, "_parent_experiment_id", ""),
            timestamp=int(time.time() * 1000),
            family=self.family.name,
            hypothesis=contract.hypothesis if contract else "",
            mechanism=contract.mechanism if contract else "",
            job=state.get("job", 0),
            usage=state.get("_last_round_usage", {}),
        ))

    def run_command(self, command: str) -> tuple[int, str]:
        import sys
        try:
            trace("COMMAND", f"START: {command}")
            print(f"RUN_COMMAND start: {command[:80]}", flush=True)
            sys.stdout.flush()
            result = subprocess.run(
                command, shell=True, cwd=self.root,
                capture_output=True, text=True, timeout=1800,
                stdin=subprocess.DEVNULL,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            trace_ssh(command, result.returncode, stdout, stderr)
            print(f"RUN_COMMAND done: exit={result.returncode}", flush=True)
            sys.stdout.flush()
            output = stdout + stderr
            return int(result.returncode), output
        except subprocess.TimeoutExpired:
            trace("COMMAND", f"TIMEOUT (1800s): {command[:100]}")
            print(f"COMMAND TIMEOUT (1800s): {command[:100]}", flush=True)
            return 1, "TIMEOUT"
        except Exception as exc:
            trace("COMMAND", f"ERROR: {exc}")
            print(f"RUN_COMMAND error: {exc}", flush=True)
            return 1, str(exc)

    def primary_metric_name(self) -> str:
        """Read the primary metric name from the JSONL config header."""
        for entry in self.read_entries():
            if entry.get("type") == "config":
                return entry.get("metricName", "median_expectancy")
        return "median_expectancy"

    def parse_metric(self, output: str, name: str = "median_expectancy") -> float | None:
        result_json = self.parse_result_json(output)
        if result_json:
            val = result_json.get("metrics", {}).get(name)
            return float(val) if val is not None else None
        # Legacy fallback
        match = re.search(rf"^METRIC {re.escape(name)}=([-+]?\d*\.?\d+)", output, flags=re.MULTILINE)
        return float(match.group(1)) if match else None

    def evaluate_metric(self, metric: float) -> str:
        result = subprocess.run(
            [
                "python3",
                "autoresearch_helper.py",
                "evaluate",
                "--jsonl",
                self.jsonl_path.name,
                "--metric",
                str(metric),
                "--direction",
                "higher",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        text = (result.stdout or "") + (result.stderr or "")
        return "keep" if "DECISION: keep" in text else "discard"

    def derive_trade_analysis(
        self, config: str, metric: float, decision: str, output: str = ""
    ) -> dict[str, Any]:
        best = self.best_result(self.read_results())
        details = self.parse_benchmark_details(output)

        # Load full config contents for analyst context
        config_contents: dict[str, Any] = {}
        config_path = self.root / config
        if config_path.exists():
            try:
                if config_path.suffix in (".yaml", ".yml"):
                    import yaml
                    raw = yaml.safe_load(config_path.read_text())
                else:
                    raw = json.loads(config_path.read_text())
                if isinstance(raw, dict) and "runtime_config" in raw:
                    config_contents = raw["runtime_config"]
                elif isinstance(raw, dict):
                    config_contents = raw
                else:
                    from ema_contract import compile_ema_contract
                    config_contents = compile_ema_contract(raw).runtime_config
            except Exception:
                pass

        # Trades file path comes from result.json (backtest writes it to the run dir)
        trades_file = details.get("trades_file", "")
        self._latest_trades_file = trades_file
        self._latest_strategy_events_file = details.get("strategy_events_file", "")
        self._latest_diagnostics_file = details.get("diagnostics_file", "")
        self._latest_config_contents = config_contents

        # Build analysis record
        trade_analysis: dict[str, Any] = {
            "what_changed_vs_baseline": f"{Path(config).stem} evaluated independently.",
            "primary_metric_improved": decision == "keep",
            **details,
        }
        result = {
            "trade_analysis": trade_analysis,
            "insights": [f"metric={metric}", f"decision={decision}"],
            "next_candidates": [],
            "why_not_data_fit": "Independent thesis evaluation only.",
        }
        return result

    def execute_research_sdk(self) -> dict[str, Any]:
        """Drive research using the research conductor.

        Calls the conductor, validates the proposed thesis, compiles it.
        If validation rejects the thesis, calls the conductor AGAIN with
        the rejection reason so it can propose something different.

        AlphaAgent-inspired guardrails:
        - Config-key overlap detection (reject parameter variations)
        - Hypothesis-config alignment scoring (reject mismatched stories)
        - Multi-variant probing (test 3 values for continuous params)
        """
        from research_conductor import run_research_conductor_sync
        from agent_orchestrator import format_result_history
        from thesis_validator import (
            validate_thesis_dict, ThesisValidationError,
            load_prior_theses, generate_variants,
        )
        from compiler_pipeline import compile_research_thesis

        state = self.read_state()
        research_round = state.get("research_round", 0) + 1
        results = self.read_results()

        result_dicts = self._results_to_dicts(results)
        experiment_results = format_result_history(result_dicts)

        # Load prior theses for overlap detection (Guardrail 1)
        prior_theses = load_prior_theses(self.root)
        trace("LOOP", f"loaded {len(prior_theses)} prior theses for overlap detection")

        # Resolve trades / events / diagnostics files
        trades_file = getattr(self, "_latest_trades_file", "") or ""
        strategy_events_file = getattr(self, "_latest_strategy_events_file", "") or ""
        diagnostics_file = getattr(self, "_latest_diagnostics_file", "") or ""
        latest = self.latest_result(results)
        if not trades_file and latest:
            artifact_dir = self.root / latest.asi.get("artifact_dir", "")
            if artifact_dir.exists():
                csvs = list(artifact_dir.glob("*trades.csv"))
                if csvs:
                    trades_file = str(csvs[0])
                events_files = list(artifact_dir.glob("*strategy_events.parquet")) or list(artifact_dir.glob("*strategy_events.csv"))
                if events_files:
                    strategy_events_file = str(events_files[0])
                diag_jsons = list(artifact_dir.glob("*diagnostics.json"))
                if diag_jsons:
                    diagnostics_file = str(diag_jsons[0])

        latest_outcome: dict[str, Any] = {}
        if latest:
            latest_outcome["thesis_id"] = Path(latest.config).stem
            latest_outcome["metric"] = latest.metric
            latest_outcome["decision"] = latest.status
            ta = latest.asi.get("trade_analysis", {})
            for key in ("trade_count", "profit_factor", "max_drawdown",
                        "pct_profitable_windows", "avg_sharpe_across_windows"):
                if ta.get(key) is not None:
                    latest_outcome[key] = ta[key]

        state["research_round"] = research_round
        self.write_state(state)

        # First call is normal. On validation rejection, retry with feedback
        # so the conductor can fix its mistake. 3 attempts total.
        MAX_VALIDATION_RETRIES = 3
        rejection_feedback = ""
        thesis_id = "unknown"

        for attempt in range(MAX_VALIDATION_RETRIES):
            label = f"round={research_round}" + (f" attempt={attempt+1} (retry with feedback)" if attempt else "")
            print(f"CONDUCTOR starting {label} trades={"YES" if trades_file else "NO"}")
            trace("CONDUCTOR", f"START {label}")

            parsed = run_research_conductor_sync(
                trades_file=trades_file,
                experiment_results=experiment_results,
                latest_outcome=latest_outcome,
                research_round=research_round,
                family_name=self.family.name,
                strategy_events_file=strategy_events_file,
                diagnostics_file=diagnostics_file,
                rejection_feedback=rejection_feedback,
            )

            if not parsed:
                return {
                    "status": "parse_failed",
                    "generated_config": None,
                    "should_stop": False,
                    "rejection_reason": "research conductor returned no parseable thesis",
                }

            if parsed.get("status") == "conductor_error":
                error = parsed.get("error") or parsed.get("reasoning") or "unknown conductor error"
                return {
                    "status": "conductor_error",
                    "generated_config": None,
                    "should_stop": False,
                    "rejection_reason": f"research conductor failed: {error}",
                }

            should_stop = parsed.get("should_stop", False)
            theses = parsed.get("suggested_theses", [])
            if not theses:
                reasoning = parsed.get("reasoning") or "research conductor returned no suggested_theses"
                return {
                    "status": "completed",
                    "generated_config": None,
                    "should_stop": should_stop,
                    "reasoning": reasoning,
                }

            raw_thesis = theses[0]
            raw_thesis["strategy_family"] = self.family.name
            thesis_id = raw_thesis.get("thesis_id", "unknown")
            print(f"RESEARCH_RAW thesis_id={thesis_id} config_changes={json.dumps(raw_thesis.get("config_changes", "MISSING"))}")

            # Validate with prior theses (Guardrail 1 + 2)
            try:
                validated = validate_thesis_dict(raw_thesis, prior_theses=prior_theses)
                contract = compile_research_thesis(validated, self.root)
            except (ThesisValidationError, ValueError) as exc:
                rejection_feedback = f"Thesis '{thesis_id}' rejected by validator: {exc}"
                print(f"THESIS REJECTED (will retry with feedback): {rejection_feedback}")
                trace("LOOP", f"thesis rejected, retrying: {rejection_feedback}")
                # Log rejected attempt to JSONL
                self.log_research_round(
                    round_number=research_round,
                    thesis_id=thesis_id,
                    outcome=f"rejected_attempt_{attempt+1}",
                    config_changes=raw_thesis.get("config_changes"),
                    hypothesis=raw_thesis.get("hypothesis", ""),
                    mechanism=raw_thesis.get("mechanism", ""),
                    mechanism_dimension=raw_thesis.get("mechanism_dimension", ""),
                    rejection_reason=str(exc),
                )
                continue

            if contract.status == "needs_code":
                return {
                    "status": "completed",
                    "generated_config": None,
                    "generated_config_needs_build": True,
                    "generated_thesis_id": thesis_id,
                    "thesis_id": thesis_id,
                    "should_stop": should_stop,
                    "reasoning": parsed.get("reasoning", ""),
                    "thesis": raw_thesis,
                }

            if contract.status == "ready_to_run":
                config_path = f"experiments/{contract.experiment_id}/runtime_config.json"
                self._current_contract = contract
                latest_db = self.experiment_db.latest(1)
                self._parent_experiment_id = latest_db[0].experiment_id if latest_db else ""

                # --- Guardrail 3: Multi-variant probing ---
                # Generate conservative/aggressive variants and queue them
                # so the loop runs all three after the primary config.
                baseline_config = self._load_baseline_config()
                if baseline_config:
                    variants = generate_variants(
                        raw_thesis.get("config_changes", {}),
                        baseline_config,
                    )
                    if len(variants) > 1:
                        self._queue_variants(
                            variants, validated, contract, baseline_config,
                        )
                        trace("LOOP", f"queued {len(variants)-1} variant(s) for {thesis_id}")

                return {
                    "status": "completed",
                    "generated_config": config_path,
                    "generated_config_needs_build": False,
                    "generated_thesis_id": thesis_id,
                    "experiment_id": contract.experiment_id,
                    "thesis_id": thesis_id,
                    "should_stop": should_stop,
                    "reasoning": parsed.get("reasoning", ""),
                }

            # Unexpected contract status
            rejection_feedback = f"Thesis '{thesis_id}' rejected: status={contract.status}, missing={contract.missing_primitives}"
            print(f"THESIS REJECTED (will retry with feedback): {rejection_feedback}")
            trace("LOOP", f"thesis rejected, retrying: {rejection_feedback}")
            self.log_research_round(
                round_number=research_round,
                thesis_id=thesis_id,
                outcome=f"rejected_attempt_{attempt+1}",
                config_changes=raw_thesis.get("config_changes"),
                hypothesis=raw_thesis.get("hypothesis", ""),
                mechanism=raw_thesis.get("mechanism", ""),
                mechanism_dimension=raw_thesis.get("mechanism_dimension", ""),
                rejection_reason=rejection_feedback,
            )

        # Exhausted retries
        print(f"THESIS REJECTED after {MAX_VALIDATION_RETRIES} attempts: {rejection_feedback}")
        trace("LOOP", f"thesis rejected after {MAX_VALIDATION_RETRIES} attempts: {rejection_feedback}")
        return {
            "status": "thesis_rejected",
            "generated_config": None,
            "generated_config_needs_build": False,
            "generated_thesis_id": thesis_id,
            "rejection_reason": rejection_feedback,
            "should_stop": False,
            "reasoning": parsed.get("reasoning", "") if parsed else "",
        }

    def _load_baseline_config(self) -> dict[str, Any] | None:
        """Load the baseline config for variant generation."""
        base_path = self.root / "configs" / self.family.base_config_filename
        if not base_path.exists():
            return None
        try:
            return yaml.safe_load(base_path.read_text())
        except Exception:
            return None

    def _queue_variants(
        self,
        variants: list[dict[str, Any]],
        thesis: "ResearchThesis",
        primary_contract: "ExperimentContract",
        baseline_config: dict[str, Any],
    ) -> None:
        """Write variant runtime configs so the loop picks them up as queued experiments."""
        import hashlib
        for variant in variants:
            label = variant.pop("_variant_label", "variant")
            factor = variant.pop("_variant_factor", 1.0)
            if factor == 1.0:
                continue  # skip the proposed value — it's already the primary

            runtime = {**baseline_config, **variant}
            config_hash = hashlib.sha256(
                json.dumps(runtime, sort_keys=True).encode()
            ).hexdigest()[:12]
            variant_id = f"{thesis.thesis_id}_{label}"
            exp_dir = self.root / "experiments" / config_hash
            exp_dir.mkdir(parents=True, exist_ok=True)

            # Write runtime config
            (exp_dir / "runtime_config.json").write_text(
                json.dumps(runtime, indent=2) + "\n"
            )
            # Write thesis with variant metadata
            variant_thesis = thesis.model_dump()
            variant_thesis["thesis_id"] = variant_id
            variant_thesis["_variant_of"] = primary_contract.experiment_id
            variant_thesis["_variant_label"] = label
            variant_thesis["_variant_factor"] = factor
            variant_thesis["config_changes"] = variant
            (exp_dir / "thesis.json").write_text(
                json.dumps(variant_thesis, indent=2) + "\n"
            )

            # Queue it via run_queue artifact
            self.run_queue_dir.mkdir(parents=True, exist_ok=True)
            queue_artifact = {
                "thesis_id": variant_id,
                "config": f"experiments/{config_hash}/runtime_config.json",
                "status": "pending",
                "source": "multi_variant_probe",
                "variant_of": primary_contract.experiment_id,
                "variant_label": label,
                "variant_factor": factor,
            }
            (self.run_queue_dir / f"{variant_id}.json").write_text(
                json.dumps(queue_artifact, indent=2) + "\n"
            )
            trace("LOOP", f"queued variant {variant_id} ({label}) config_hash={config_hash}")

    def _results_to_dicts(self, results: list) -> list[dict[str, Any]]:
        """Convert ExperimentRecords to plain dicts for formatting."""
        result_dicts: list[dict[str, Any]] = []
        for r in results:
            d: dict[str, Any] = {
                "config": r.config,
                "metric": r.metric,
                "status": r.status,
                "description": r.description,
            }
            ta = r.asi.get("trade_analysis", {})
            if ta:
                for key in ("trade_count", "profit_factor", "max_drawdown",
                            "pct_profitable_windows", "avg_sharpe_across_windows",
                            "win_rate", "exit_mix", "regime_expectancy",
                            "why", "regime_insight", "trade_count_insight",
                            "mechanism_analysis"):
                    if ta.get(key) is not None:
                        d[key] = ta[key]
            if r.asi.get("thesis_id"):
                d["thesis_id"] = r.asi["thesis_id"]
            if r.asi.get("config_changes"):
                d["config_changes"] = r.asi["config_changes"]
            if r.asi.get("insights"):
                d["insights"] = r.asi["insights"]
            if r.asi.get("next_thesis_suggestion"):
                d["next_thesis_suggestion"] = r.asi["next_thesis_suggestion"]
            if r.asi.get("why_not_data_fit"):
                d["why_not_data_fit"] = r.asi["why_not_data_fit"]
            insight_brief = r.asi.get("insight_brief") or ta.get("insight_brief")
            if insight_brief:
                d["insight_brief"] = insight_brief
            result_dicts.append(d)
        return result_dicts

    def execute_research_one(self) -> dict[str, Any]:
        """Drive research using SDK agents."""
        return self.execute_research_sdk()

    def _check_baseline_rerun(self) -> dict[str, Any] | None:
        """Check if baseline needs rerunning. Returns next_action dict or None."""
        BASELINE_RERUN_INTERVAL = 5
        last_checkpoint = self.baseline_tracker.latest()
        current_commit = self.current_commit()

        if not last_checkpoint:
            return None

        needs_rerun = False
        reason = ""
        if last_checkpoint.code_commit != current_commit:
            needs_rerun = True
            reason = f"code changed {last_checkpoint.code_commit} -> {current_commit}"
        else:
            _results = self.read_results()
            experiments_since = sum(
                1 for r in _results if r.timestamp > last_checkpoint.timestamp
            )
            if experiments_since >= BASELINE_RERUN_INTERVAL:
                needs_rerun = True
                reason = f"periodic rerun ({experiments_since} experiments since last baseline)"

        if not needs_rerun:
            return None

        results = self.read_results()
        already_reran = any(
            r.asi.get("baseline_rerun_for_commit") == current_commit
            and r.timestamp > last_checkpoint.timestamp
            for r in results
        )
        if already_reran:
            return None

        baseline_config = f"configs/{self.family.base_config_filename}"
        trace("BASELINE", f"forcing rerun: {reason}")
        print(f"BASELINE_RERUN {reason}")
        return {
            "type": "run_experiment",
            "config": baseline_config,
            "benchmark_command": self.family.benchmark_command(baseline_config),
            "requires_trade_analysis": True,
            "source": "baseline",
            "baseline_rerun_for_commit": current_commit,
            "rerun_reason": reason,
        }

    def _resolve_next_action(self) -> dict[str, Any]:
        """Decide what to do next. Returns a state dict with state/next_action/blockers.

        Priority order:
        0. Resume halted thesis (code was implemented, runtime_config now exists)
        1. Forced baseline rerun (code changed or periodic)
        2. reconcile_state() discovery (pending configs, thesis queue, combos, ideas)
        3. Blocked for research
        """
        # Resume a halted thesis if the missing config keys now exist in base yaml
        state = self.read_state()
        halted_id = state.get("halted_thesis_id")
        if halted_id and state.get("halted_reason") == "requires_code_change":
            raw_thesis = state.get("halted_thesis", {})
            config_changes = raw_thesis.get("config_changes", {})
            if config_changes:
                import yaml as _yaml
                base = _yaml.safe_load((self.root / "configs" / self.family.base_config_filename).read_text())
                missing = set(config_changes) - set(base)
                if not missing:
                    runtime = {**base, **config_changes}
                    exp_dir = self.root / "experiments" / halted_id
                    exp_dir.mkdir(parents=True, exist_ok=True)
                    config_path = f"experiments/{halted_id}/runtime_config.json"
                    (self.root / config_path).write_text(json.dumps(runtime, indent=2) + "\n")
                    state["state"] = "running"
                    state["current_thesis"] = {"config": config_path, "status": "ready_to_run"}
                    state["next_action"] = {
                        "type": "run_experiment",
                        "config": config_path,
                        "benchmark_command": self.family.benchmark_command(config_path),
                        "requires_trade_analysis": True,
                        "source": "resumed_halted_thesis",
                    }
                    state["blockers"] = []
                    state.pop("halted_thesis_id", None)
                    state.pop("halted_reason", None)
                    state.pop("halted_thesis", None)
                    self.write_state(state)
                    trace("LOOP", f"resumed halted thesis={halted_id}")
                    return state


        baseline_action = self._check_baseline_rerun()
        if baseline_action:
            state = self.read_state()
            state["state"] = "running"
            state["next_action"] = baseline_action
            state["blockers"] = []
            self.write_state(state)
            return state

        return self.reconcile_state()

    def _run_research(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run one research round.  Returns updated state dict.

        Outcomes:
          success  -> state=running, next_action has compiled config
          terminal -> state=finished (should_stop / max rounds) or halted (needs code)
          failure  -> parse fail or validation reject after internal retry
                      Stays blocked so the loop advances to the next research round.
        """
        research_round = state.get("research_round", 0) + 1
        from trace_logger import begin_round
        begin_round(research_round)
        if research_round > MAX_RESEARCH_ROUNDS:
            state["state"] = "finished"
            state["finished_reason"] = "max_research_rounds_reached"
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            print(f"LOOP_STOP finished: max research rounds ({MAX_RESEARCH_ROUNDS}) reached")
            best = state.get("current_best", {})
            _notify_discord(
                f"\u2705 {self.family.name.upper()} FINISHED u2014 max rounds",
                f"**Rounds:** {MAX_RESEARCH_ROUNDS}\n**Best config:** `{best.get('config', '?')}`\n**Best PF:** {best.get('metric', '?')}",
                webhook=self.family.discord_webhook, color=0x00CC00,
            )
            return state

        print(f"HEARTBEAT research_blocked round={research_round}, invoking research subagent")
        begin_hypothesis(f"research-round-{research_round}")
        from research_conductor import reset_round_usage, get_round_usage
        reset_round_usage()
        result = self.execute_research_one()
        round_usage = get_round_usage()
        trace("USAGE", f"round={research_round} {json.dumps(round_usage)}")
        self._accumulate_job_usage(round_usage)
        # Save round usage to state so log_experiment_result can attach it
        state = self.read_state()
        state["_last_round_usage"] = round_usage
        self.write_state(state)
        end_hypothesis(decision="research_complete")

        # Log every research round outcome
        _thesis = result.get("thesis", {})
        _thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
        if result.get("should_stop"):
            _outcome = "stopped"
        elif result.get("generated_config_needs_build"):
            _outcome = "needs_code"
        elif result.get("generated_config"):
            _outcome = "compiled"
        elif result.get("rejection_reason"):
            _outcome = "rejected"
        else:
            _outcome = "conductor_error"
        self.log_research_round(
            round_number=research_round,
            thesis_id=_thesis_id,
            outcome=_outcome,
            config_changes=_thesis.get("config_changes"),
            hypothesis=_thesis.get("hypothesis", ""),
            mechanism=_thesis.get("mechanism", ""),
            mechanism_dimension=_thesis.get("mechanism_dimension", ""),
            rejection_reason=result.get("rejection_reason") or result.get("reasoning", ""),
            usage=round_usage,
        )

        # --- Terminal: conductor says stop ---
        if result.get("should_stop"):
            state["state"] = "finished"
            state["finished_reason"] = "research_recommends_stop"
            state["research_stop_reasoning"] = result.get("reasoning", "")
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            print("LOOP_STOP finished: research recommends stop")
            best = state.get("current_best", {})
            _notify_discord(
                f"\u2705 {self.family.name.upper()} FINISHED \u2014 conductor says stop",
                f"**Best config:** `{best.get('config', '?')}`\n**Best PF:** {best.get('metric', '?')}\n\nResearch conductor recommends stopping.",
                webhook=self.family.discord_webhook, color=0x00CC00,
            )
            return state

        # --- Terminal: thesis needs code change ---
        if result.get("generated_config_needs_build"):
            thesis_id = result.get("generated_thesis_id", "unknown")
            thesis = result.get("thesis", {})
            print(f"LOOP_HALT thesis={thesis_id} requires code change")
            state["state"] = "halted"
            state["halted_reason"] = "requires_code_change"
            state["halted_thesis_id"] = thesis_id
            state["halted_thesis"] = thesis
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            best = state.get("current_best", {})
            hyp = thesis.get("hypothesis", "(no details captured)")
            mech = thesis.get("mechanism", "")
            _notify_discord(
                f"\U0001f6d1 {self.family.name.upper()} HALTED — needs code change",
                f"**Thesis:** `{thesis_id}`\n**Best PF:** {best.get('metric', '?')}\n\n"
                f"**Hypothesis:** {hyp}\n\n"
                f"**Mechanism:** {mech}\n\n"
                f"**Config changes:** `{json.dumps(thesis.get('config_changes', {}))}`",
                webhook=self.family.discord_webhook, color=0xFF4500,
            )
            return state

        # --- Success: valid config produced ---
        gen_config = result.get("generated_config")
        if gen_config:
            thesis_id = result.get("thesis_id", "unknown")
            trace("LOOP", f"research produced config: {gen_config} thesis={thesis_id}")
            state["state"] = "running"
            state["research_round"] = research_round
            state["current_thesis"] = {"config": gen_config, "status": "ready_to_run"}
            state["next_action"] = {
                "type": "run_experiment",
                "config": gen_config,
                "benchmark_command": self.family.benchmark_command(gen_config),
                "requires_trade_analysis": True,
                "source": "research_conductor",
            }
            state["blockers"] = []
            self.write_state(state)
            print(f"HEARTBEAT research generated {thesis_id} -> {gen_config}")
            return state

        # --- Failure: conductor produced nothing usable this round ---
        # Stay blocked so the loop calls us again with an incremented round.
        # The conductor gets a fresh prompt and may propose differently.
        reason = result.get("rejection_reason") or result.get("reasoning") or "no thesis generated"
        trace("LOOP", f"research round {research_round} produced no config: {reason}")
        print(f"HEARTBEAT research round {research_round} failed: {reason}")
        state["research_round"] = research_round  # record so next call increments
        state["state"] = "blocked"
        state["blockers"] = [{"kind": "research_required",
                              "detail": f"round {research_round} failed: {reason}"}]
        self.write_state(state)
        return state
    def _run_experiment(self, state: dict[str, Any]) -> int:
        """Run a single experiment (backtest + evaluate + log). Returns exit code."""
        next_action = state["next_action"]
        config = next_action["config"]

        # Compute config hash and create per-run output dir
        import hashlib
        config_path_full = self.root / config
        if config_path_full.exists():
            if config_path_full.suffix in (".yaml", ".yml"):
                import yaml
                _cfg = yaml.safe_load(config_path_full.read_text())
            else:
                _cfg = json.loads(config_path_full.read_text())
            if isinstance(_cfg, dict) and "runtime_config" in _cfg:
                _cfg = _cfg["runtime_config"]
            config_hash = hashlib.sha256(
                json.dumps(_cfg, sort_keys=True).encode()
            ).hexdigest()[:12]
        else:
            config_hash = hashlib.sha256(config.encode()).hexdigest()[:12]

        state = self.read_state()
        job = state.get("job", 0)
        run_output_dir = self.runs_dir.resolve() / f"job-{job}" / config_hash
        run_output_dir.mkdir(parents=True, exist_ok=True)
        self._current_artifact_dir = run_output_dir

        if config_path_full.exists():
            import shutil
            shutil.copy2(config_path_full, run_output_dir / "config.json")

        command = self.family.benchmark_command(config, output_dir=str(run_output_dir))
        if not command:
            print("LOOP_STOP missing_benchmark_command")
            return 1

        hyp_name = Path(config).stem if config else "unknown"
        begin_hypothesis(hyp_name)
        trace("LOOP", f"BENCHMARK START: {command}")
        print(f"HEARTBEAT running {command}")
        code, output = self.run_command(command)

        if code != 0:
            state["state"] = "blocked"
            state["blockers"] = [{"kind": "command_failed", "detail": command, "exit_code": code}]
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            print(f"LOOP_STOP state=blocked exit_code={code}")
            _notify_discord(
                f"\u26a0\ufe0f {self.family.name.upper()} BLOCKED \u2014 backtest failed",
                f"**Command:** `{command[:200]}`\n**Exit code:** {code}",
                webhook=self.family.discord_webhook, color=0xFFA500,
            )
            return code

        metric = self.parse_metric(output, name=self.primary_metric_name())
        if metric is None:
            state["state"] = "blocked"
            state["blockers"] = [{"kind": "metric_parse_failed", "detail": command}]
            self.write_state(state)
            self.write_current_md(state, self.read_results())
            print("LOOP_STOP state=blocked metric_parse_failed")
            _notify_discord(
                f"\u26a0\ufe0f {self.family.name.upper()} BLOCKED \u2014 metric parse failed",
                f"**Command:** `{command[:200]}`\nCould not extract metric from output.",
                webhook=self.family.discord_webhook, color=0xFFA500,
            )
            return 1

        details = self.parse_benchmark_details(output)
        decision = self.evaluate_metric(metric)
        trace("LOOP", f"METRIC parsed: {metric} decision={decision} config={config}")

        # Evaluate against thesis predictions (if we have a contract)
        verdict = None
        contract = getattr(self, "_current_contract", None)
        if contract and contract.expected_effects:
            try:
                from experiment_evaluator import evaluate_experiment
                from research_types import ResearchThesis

                results = self.read_results()
                baseline_result = results[0] if results else None
                baseline_metrics: dict[str, Any] = {}
                if baseline_result:
                    bta = baseline_result.asi.get("trade_analysis", {})
                    for k in ("trade_count", "profit_factor", "max_drawdown",
                              "pct_profitable_windows", "avg_sharpe_across_windows",
                              "median_expectancy"):
                        if bta.get(k) is not None:
                            baseline_metrics[k] = bta[k]

                candidate_metrics = dict(details)
                candidate_metrics["median_expectancy"] = metric

                thesis_for_eval = ResearchThesis(
                    thesis_id=contract.thesis_id,
                    strategy_family=contract.strategy_family,
                    hypothesis=contract.hypothesis,
                    mechanism=contract.mechanism,
                    expected_effects=contract.expected_effects,
                    disqualifiers=contract.disqualifiers,
                    required_diagnostics=contract.required_diagnostics,
                )

                verdict = evaluate_experiment(
                    thesis=thesis_for_eval,
                    baseline_metrics=baseline_metrics,
                    candidate_metrics=candidate_metrics,
                    experiment_id=contract.experiment_id,
                    strategy_diagnostics=details.get("strategy_diagnostics"),
                )
                trace("EVAL", f"verdict={verdict.status} passed={verdict.passed_effects} failed={verdict.failed_effects} dq={verdict.triggered_disqualifiers}")
                print(f"VERDICT {verdict.status}: {verdict.summary}")

                experiment_dir = self.root / "experiments" / contract.experiment_id
                if experiment_dir.exists():
                    (experiment_dir / "verdict.json").write_text(
                        verdict.model_dump_json(indent=2) + "\n"
                    )

                if verdict.status == "rejected":
                    decision = "discard"
                elif verdict.status == "accepted" and decision == "discard":
                    trace("EVAL", "thesis accepted despite metric threshold")

            except Exception as exc:
                trace("EVAL", f"evaluation error: {exc}")
                print(f"EVAL error (non-fatal): {exc}")

        self._current_contract = None

        analysis = self.derive_trade_analysis(config, metric, decision, output=output)
        if verdict:
            analysis["trade_analysis"]["verdict"] = verdict.model_dump()
        self.log_experiment_result(config=config, metric=metric, decision=decision, output=output, analysis=analysis)

        # Record baseline checkpoint
        is_baseline_run = next_action.get("source") == "baseline"
        if is_baseline_run:
            runtime_cfg = getattr(self, "_latest_config_contents", {}) or {}
            new_checkpoint = BaselineCheckpoint(
                code_commit=self.current_commit(),
                data_hash=build_data_hash(runtime_cfg),
                config_hash=build_config_hash(runtime_cfg),
                metrics=details,
                timestamp=int(time.time() * 1000),
                round_number=len(self.baseline_tracker.all_checkpoints()),
            )
            drift = self.baseline_tracker.check_drift(new_checkpoint)
            self.baseline_tracker.record(new_checkpoint)
            trace("BASELINE", f"checkpoint recorded commit={self.current_commit()}")
            if drift["drifted"]:
                drift_details = [d for d in drift["details"] if d.get("severity") == "critical"]
                trace("BASELINE", f"DRIFT DETECTED: {drift_details}")
                state = self.read_state()
                state["baseline_drift"] = drift
                self.write_state(state)

            # Clear the baseline_rerun_for_commit flag so plan_next_action
            # doesn't short-circuit on the stale flag and loop forever.
            persisted = self.read_state()
            na = persisted.get("next_action", {})
            if na.get("baseline_rerun_for_commit"):
                na.pop("baseline_rerun_for_commit", None)
                persisted["next_action"] = na
                self.write_state(persisted)

        end_hypothesis(decision=decision, metric=metric)

        # After experiment, reconcile to find next work
        state = self.reconcile_state()
        trace("LOOP", f"ITERATION DONE thesis={config} metric={metric} decision={decision} verdict={verdict.status if verdict else 'none'} next={state.get('next_action', {}).get('type')}")
        best = state.get("current_best", {})
        verdict_str = verdict.status if verdict else "none"
        emoji = "\u2705" if decision == "keep" else "\u274c" if decision == "discard" else "\U0001f504"
        _notify_discord(
            f"{emoji} {self.family.name.upper()} \u2014 {Path(config).stem}",
            f"**PF:** {metric}  |  **Decision:** {decision}  |  **Verdict:** {verdict_str}\n"
            f"**Best so far:** `{Path(best.get('config', '?')).stem}` PF={best.get('metric', '?')}",
            webhook=self.family.discord_webhook, color=0x00CC00 if decision == "keep" else 0xFF4500,
        )
        print(
            f"HEARTBEAT complete thesis={config} result={decision} metric={metric} "
            f"verdict={verdict.status if verdict else 'none'} "
            f"next_action={state.get('next_action', {}).get('type')}"
        )
        return 0

    def execute_once(self) -> int:
        """Run one iteration of the autoresearch loop.

        Flow:
        1. Resolve what to do next (baseline rerun, pending config, or research)
        2. If blocked for research: run conductor, compile thesis, wire config
        3. If running: execute the experiment (backtest + evaluate)
        """
        trace("LOOP", "=== execute_once START ===")

        state = self._resolve_next_action()
        trace("LOOP", f"state={state.get('state')} blockers={[b.get('kind') for b in state.get('blockers', [])]}")

        # Handle blocked state: invoke research conductor
        if state.get("state") == "blocked":
            blockers = state.get("blockers", [])
            if any(b.get("kind") == "research_required" for b in blockers):
                state = self._run_research(state)

        # Terminal states
        if state.get("state") != "running":
            print(f"LOOP_STOP state={state.get('state')}")
            return 0

        # We have a config to run
        return self._run_experiment(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run autoresearch controller")
    parser.add_argument("--family", default="orb", help="Strategy family to run (orb or ema)")
    args = parser.parse_args()

    family = load_family(args.family)
    state_path, jsonl_path, current_md_path, ideas_md_path, runs_dir = default_controller_paths(ROOT, family)
    controller = AutoresearchController(
        family=family,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=current_md_path,
        ideas_md_path=ideas_md_path,
        runs_dir=runs_dir,
    )
    # Increment job number on each loop start
    state = controller.read_state()
    job = state.get("job", 0) + 1
    state["job"] = job
    state["research_round"] = 0  # reset round counter for clean job isolation
    state["job_usage"] = None    # reset token usage for new job
    controller.write_state(state)

    from trace_logger import get_log_file, get_session_id, set_family
    set_family(args.family, job=job)
    trace("MAIN", f"Autoresearch loop starting family={args.family} job={job} session={get_session_id()} log={get_log_file()}")
    while True:
        code = controller.execute_once()
        if code != 0:
            return code
        state = controller.read_state()
        current = state.get("state")
        if current in ("finished", "interrupted", "halted"):
            return 0
        # "blocked" with research_required is handled by execute_once on next
        # iteration. Other blocked states (command_failed, etc.) also terminate
        # via non-zero return code from execute_once.


if __name__ == "__main__":
    raise SystemExit(main())
