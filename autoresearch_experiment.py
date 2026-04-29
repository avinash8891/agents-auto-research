"""Experiment runner, parsing, and result logging for autoresearch.

Owns the path from `next_action.config` to a logged experiment record:
shell out via run_command, parse RESULT_JSON / metrics, decide keep/discard,
optionally evaluate against a thesis contract, and persist to JSONL plus
the structured ExperimentDB.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from autoresearch_state import (
    read_entries,
    read_state,
    write_entries,
    write_state,
)
from experiment_db import (
    BaselineCheckpoint,
    ExperimentResult,
    build_config_hash,
    build_data_hash,
)
from trace_logger import (
    begin_hypothesis,
    current_hypothesis_id,
    end_hypothesis,
    get_run_id,
    trace,
    trace_benchmark,
    trace_ssh,
)

if TYPE_CHECKING:
    from autoresearch_loop import AutoresearchController


COMMAND_TIMEOUT_SECONDS = 1800


# ── Shell out ─────────────────────────────────────────────────────

def run_command(root: Path, command: str) -> tuple[int, str]:
    try:
        trace("COMMAND", f"START: {command}")
        print(f"RUN_COMMAND start: {command[:80]}", flush=True)
        sys.stdout.flush()
        result = subprocess.run(
            command, shell=True, cwd=root,
            capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        trace_ssh(command, result.returncode, stdout, stderr)
        print(f"RUN_COMMAND done: exit={result.returncode}", flush=True)
        sys.stdout.flush()
        return int(result.returncode), stdout + stderr
    except subprocess.TimeoutExpired:
        trace("COMMAND", f"TIMEOUT ({COMMAND_TIMEOUT_SECONDS}s): {command[:100]}")
        print(f"COMMAND TIMEOUT ({COMMAND_TIMEOUT_SECONDS}s): {command[:100]}", flush=True)
        return 1, "TIMEOUT"
    except Exception as exc:
        trace("COMMAND", f"ERROR: {exc}")
        print(f"RUN_COMMAND error: {exc}", flush=True)
        return 1, str(exc)


# ── Output parsing ────────────────────────────────────────────────

def parse_result_json(output: str) -> dict[str, Any] | None:
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


def parse_benchmark_details(output: str) -> dict[str, Any]:
    """Extract metrics from result.json written by backtest."""
    result_json = parse_result_json(output)
    if result_json:
        details: dict[str, Any] = {}
        metrics = result_json.get("metrics", {})
        for key in ("trade_count", "profit_factor", "max_drawdown",
                    "pct_profitable_windows", "avg_sharpe_across_windows",
                    "win_rate"):
            if key in metrics:
                details[key] = metrics[key]
        for key in ("diagnostics", "trades_file", "strategy_events_file",
                    "diagnostics_file", "strategy_diagnostics", "git_sha", "config_hash"):
            if result_json.get(key):
                details[key] = result_json[key]
        return details

    trace("LOOP", "WARNING: no RESULT_JSON found, falling back to stdout parsing")
    return parse_benchmark_details_legacy(output)


def parse_benchmark_details_legacy(output: str) -> dict[str, Any]:
    """Legacy stdout parsing — only used if result.json is missing."""
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


def primary_metric_name(entries: list[dict[str, Any]]) -> str:
    """Read the primary metric name from the JSONL config header."""
    for entry in entries:
        if entry.get("type") == "config":
            return entry.get("metricName", "median_expectancy")
    return "median_expectancy"


def parse_metric(output: str, name: str = "median_expectancy") -> float | None:
    result_json = parse_result_json(output)
    if result_json:
        val = result_json.get("metrics", {}).get(name)
        return float(val) if val is not None else None
    match = re.search(rf"^METRIC {re.escape(name)}=([-+]?\d*\.?\d+)", output, flags=re.MULTILINE)
    return float(match.group(1)) if match else None


def evaluate_metric(root: Path, jsonl_name: str, metric: float) -> str:
    result = subprocess.run(
        [
            "python3", "autoresearch_helper.py", "evaluate",
            "--jsonl", jsonl_name,
            "--metric", str(metric),
            "--direction", "higher",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    text = (result.stdout or "") + (result.stderr or "")
    return "keep" if "DECISION: keep" in text else "discard"


# ── Trade analysis (sets transient controller fields) ────────────

def derive_trade_analysis(
    controller: "AutoresearchController",
    config: str, metric: float, decision: str, output: str = "",
) -> dict[str, Any]:
    details = parse_benchmark_details(output)

    config_contents: dict[str, Any] = {}
    config_path = controller.root / config
    if config_path.exists():
        try:
            if config_path.suffix in (".yaml", ".yml"):
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

    controller._latest_trades_file = details.get("trades_file", "")
    controller._latest_strategy_events_file = details.get("strategy_events_file", "")
    controller._latest_diagnostics_file = details.get("diagnostics_file", "")
    controller._latest_config_contents = config_contents

    trade_analysis: dict[str, Any] = {
        "what_changed_vs_baseline": f"{Path(config).stem} evaluated independently.",
        "primary_metric_improved": decision == "keep",
        **details,
    }
    return {
        "trade_analysis": trade_analysis,
        "insights": [f"metric={metric}", f"decision={decision}"],
        "next_candidates": [],
        "why_not_data_fit": "Independent thesis evaluation only.",
    }


# ── Artifact + JSONL helpers ─────────────────────────────────────

def artifact_dir_for(state_path: Path, runs_dir: Path, config: str) -> Path:
    state = read_state(state_path)
    job = state.get("job", 0)
    path = runs_dir.resolve() / f"job-{job}" / Path(config).stem
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_duplicate_entries(jsonl_path: Path, config: str) -> None:
    filtered: list[dict[str, Any]] = []
    slug = Path(config).stem
    for entry in read_entries(jsonl_path):
        if entry.get("type") == "config":
            filtered.append(entry)
            continue
        asi = entry.get("asi") or {}
        same_config = asi.get("config") == config
        low_information = entry.get("description") == f"loop: {slug}"
        if same_config and low_information:
            continue
        filtered.append(entry)
    write_entries(jsonl_path, filtered)


def log_experiment_result(
    controller: "AutoresearchController",
    *,
    config: str,
    metric: float,
    decision: str,
    output: str,
    analysis: dict[str, Any],
) -> None:
    sanitize_duplicate_entries(controller.jsonl_path, config)
    artifact_dir = getattr(controller, "_current_artifact_dir", None) or artifact_dir_for(
        controller.state_path, controller.runs_dir, config,
    )
    controller._current_artifact_dir = None  # reset for next hypothesis
    (artifact_dir / "benchmark_output.txt").write_text(output)
    (artifact_dir / "analysis.json").write_text(json.dumps(analysis, indent=2) + "\n")

    contract = getattr(controller, "_current_contract", None)
    thesis_id = contract.thesis_id if contract else Path(config).stem
    config_changes: dict[str, Any] = {}
    thesis_json_path = controller.root / "experiments" / (
        contract.experiment_id if contract else Path(config).stem
    ) / "thesis.json"
    if thesis_json_path.exists():
        try:
            tj = json.loads(thesis_json_path.read_text())
            thesis_id = tj.get("thesis_id", thesis_id)
            config_changes = tj.get("config_changes", {})
        except Exception:
            pass

    asi = {
        "job": controller.read_state().get("job"),
        "run_id": get_run_id(),
        "hypothesis_id": current_hypothesis_id(),
        "hypothesis": Path(config).stem,
        "config": config,
        "artifact_dir": artifact_dir.relative_to(controller.root).as_posix(),
        "trade_analysis": analysis.get("trade_analysis", {}),
        "insights": analysis.get("insights", []),
        "next_candidates": analysis.get("next_candidates", []),
        "next_thesis_suggestion": analysis.get("next_thesis_suggestion", ""),
        "why_not_data_fit": analysis.get("why_not_data_fit"),
        "insight_brief": analysis.get("insight_brief", ""),
        "thesis_id": thesis_id,
        "config_changes": config_changes,
    }
    rerun_commit = controller.read_state().get("next_action", {}).get("baseline_rerun_for_commit")
    if rerun_commit:
        asi["baseline_rerun_for_commit"] = rerun_commit
    details = parse_benchmark_details(output)
    entries = controller.read_entries()
    next_run = 1 + len([entry for entry in entries if entry.get("type") != "config"])
    state = controller.read_state()
    entry = {
        "run": next_run,
        "job": state.get("job"),
        "run_id": get_run_id(),
        "hypothesis_id": current_hypothesis_id(),
        "commit": controller.current_commit(),
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
    controller.write_entries(entries)
    trace_benchmark(config, metric, decision, details)

    contract = getattr(controller, "_current_contract", None)
    verdict = analysis.get("trade_analysis", {}).get("verdict", {})
    runtime_config = getattr(controller, "_latest_config_contents", {}) or {}
    controller.experiment_db.add(ExperimentResult(
        experiment_id=contract.experiment_id if contract else entry["run_id"],
        thesis_id=contract.thesis_id if contract else Path(config).stem,
        config_path=config,
        runtime_config=runtime_config,
        code_commit=controller.current_commit(),
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
        parent_experiment_id=getattr(controller, "_parent_experiment_id", ""),
        timestamp=int(time.time() * 1000),
        family=controller.family.name,
        hypothesis=contract.hypothesis if contract else "",
        mechanism=contract.mechanism if contract else "",
        job=state.get("job", 0),
        usage=state.get("_last_round_usage", {}),
    ))


# ── Run experiment orchestrator ──────────────────────────────────

def run_experiment(controller: "AutoresearchController", state: dict[str, Any]) -> int:
    """Run a single experiment (backtest + evaluate + log). Returns exit code."""
    from autoresearch_research import notify_discord

    next_action = state["next_action"]
    config = next_action["config"]

    config_path_full = controller.root / config
    if config_path_full.exists():
        if config_path_full.suffix in (".yaml", ".yml"):
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

    state = controller.read_state()
    job = state.get("job", 0)
    run_output_dir = controller.runs_dir.resolve() / f"job-{job}" / config_hash
    run_output_dir.mkdir(parents=True, exist_ok=True)
    controller._current_artifact_dir = run_output_dir

    if config_path_full.exists():
        shutil.copy2(config_path_full, run_output_dir / "config.json")

    command = controller.family.benchmark_command(config, output_dir=str(run_output_dir))
    if not command:
        print("LOOP_STOP missing_benchmark_command")
        return 1

    hyp_name = Path(config).stem if config else "unknown"
    begin_hypothesis(hyp_name)
    trace("LOOP", f"BENCHMARK START: {command}")
    print(f"HEARTBEAT running {command}")
    code, output = controller.run_command(command)

    if code != 0:
        state["state"] = "blocked"
        state["blockers"] = [{"kind": "command_failed", "detail": command, "exit_code": code}]
        controller.write_state(state)
        controller.write_current_md(state, controller.read_results())
        print(f"LOOP_STOP state=blocked exit_code={code}")
        notify_discord(
            f"⚠️ {controller.family.name.upper()} BLOCKED — backtest failed",
            f"**Command:** `{command[:200]}`\n**Exit code:** {code}",
            webhook=controller.family.discord_webhook, color=0xFFA500,
        )
        return code

    metric = controller.parse_metric(output, name=controller.primary_metric_name())
    if metric is None:
        state["state"] = "blocked"
        state["blockers"] = [{"kind": "metric_parse_failed", "detail": command}]
        controller.write_state(state)
        controller.write_current_md(state, controller.read_results())
        print("LOOP_STOP state=blocked metric_parse_failed")
        notify_discord(
            f"⚠️ {controller.family.name.upper()} BLOCKED — metric parse failed",
            f"**Command:** `{command[:200]}`\nCould not extract metric from output.",
            webhook=controller.family.discord_webhook, color=0xFFA500,
        )
        return 1

    details = controller.parse_benchmark_details(output)
    decision = controller.evaluate_metric(metric)
    trace("LOOP", f"METRIC parsed: {metric} decision={decision} config={config}")

    verdict = None
    contract = getattr(controller, "_current_contract", None)
    if contract and contract.expected_effects:
        try:
            from experiment_evaluator import evaluate_experiment
            from research_types import ResearchThesis

            results = controller.read_results()
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

            experiment_dir = controller.root / "experiments" / contract.experiment_id
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

    controller._current_contract = None

    analysis = controller.derive_trade_analysis(config, metric, decision, output=output)
    if verdict:
        analysis["trade_analysis"]["verdict"] = verdict.model_dump()
    controller.log_experiment_result(config=config, metric=metric, decision=decision, output=output, analysis=analysis)

    is_baseline_run = next_action.get("source") == "baseline"
    if is_baseline_run:
        runtime_cfg = getattr(controller, "_latest_config_contents", {}) or {}
        new_checkpoint = BaselineCheckpoint(
            code_commit=controller.current_commit(),
            data_hash=build_data_hash(runtime_cfg),
            config_hash=build_config_hash(runtime_cfg),
            metrics=details,
            timestamp=int(time.time() * 1000),
            round_number=len(controller.baseline_tracker.all_checkpoints()),
        )
        drift = controller.baseline_tracker.check_drift(new_checkpoint)
        controller.baseline_tracker.record(new_checkpoint)
        trace("BASELINE", f"checkpoint recorded commit={controller.current_commit()}")
        if drift["drifted"]:
            drift_details = [d for d in drift["details"] if d.get("severity") == "critical"]
            trace("BASELINE", f"DRIFT DETECTED: {drift_details}")
            state = controller.read_state()
            state["baseline_drift"] = drift
            controller.write_state(state)

        persisted = controller.read_state()
        na = persisted.get("next_action", {})
        if na.get("baseline_rerun_for_commit"):
            na.pop("baseline_rerun_for_commit", None)
            persisted["next_action"] = na
            write_state(controller.state_path, persisted)

    end_hypothesis(decision=decision, metric=metric)

    state = controller.reconcile_state()
    trace("LOOP", f"ITERATION DONE thesis={config} metric={metric} decision={decision} verdict={verdict.status if verdict else 'none'} next={state.get('next_action', {}).get('type')}")
    best = state.get("current_best", {})
    verdict_str = verdict.status if verdict else "none"
    emoji = "✅" if decision == "keep" else "❌" if decision == "discard" else "🔄"
    notify_discord(
        f"{emoji} {controller.family.name.upper()} — {Path(config).stem}",
        f"**PF:** {metric}  |  **Decision:** {decision}  |  **Verdict:** {verdict_str}\n"
        f"**Best so far:** `{Path(best.get('config', '?')).stem}` PF={best.get('metric', '?')}",
        webhook=controller.family.discord_webhook, color=0x00CC00 if decision == "keep" else 0xFF4500,
    )
    print(
        f"HEARTBEAT complete thesis={config} result={decision} metric={metric} "
        f"verdict={verdict.status if verdict else 'none'} "
        f"next_action={state.get('next_action', {}).get('type')}"
    )
    return 0
