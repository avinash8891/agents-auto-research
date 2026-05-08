"""Experiment runner, parsing, and result logging for autoresearch.

Owns the path from `next_action.config` to a logged experiment record:
shell out via run_command, parse RESULT_JSON / metrics, decide keep/discard,
optionally evaluate against a thesis contract, and persist to the structured
ExperimentDB.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import yaml

from autoresearch_constants import (
    COMMAND_NOTIFICATION_TRUNCATION,
    COMMAND_PREVIEW_TRUNCATION,
    COMMAND_TIMEOUT_SECONDS,
    COMMAND_TIMEOUT_TRUNCATION,
    DISCORD_COLOR_DISCARD,
    DISCORD_COLOR_SUCCESS,
    DISCORD_COLOR_WARNING,
)
from autoresearch_logging import get_logger
from autoresearch_planning import build_research_failure_state
from autoresearch_state import (
    read_state,
    write_state,
)
from config_hash import _config_hash, _git_sha
from experiment_db import (
    BaselineCheckpoint,
    ExperimentResult,
    build_config_hash,
    build_data_hash,
)
from persistence_utils import utc_now_iso8601 as iso8601_utc_now
from persistence_utils import write_json_atomic, write_text_atomic
from trace_sdk import (
    begin_hypothesis,
    end_hypothesis,
    trace,
    trace_benchmark,
    trace_ssh,
)

if TYPE_CHECKING:
    from autoresearch_controller import AutoresearchController

log = get_logger(__name__)


class ResultJsonError(RuntimeError):
    """Raised when a RESULT_JSON marker exists but the referenced payload is invalid."""


# ── Shell out ─────────────────────────────────────────────────────


def run_command(root: Path, command: str) -> tuple[int, str]:
    try:
        trace("COMMAND", f"START: {command}")
        log.info(f"RUN_COMMAND start: {command[:COMMAND_PREVIEW_TRUNCATION]}")
        _safe_stdout_flush()
        args = shlex.split(command)
        result = subprocess.run(  # noqa: S602  # nosec B602
            args,
            shell=False,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        trace_ssh(command, result.returncode, stdout, stderr)
        log.info(f"RUN_COMMAND done: exit={result.returncode}")
        _safe_stdout_flush()
        return int(result.returncode), stdout + stderr
    except ValueError as exc:
        trace("COMMAND", f"ERROR: {exc}")
        log.error(
            f"RUN_COMMAND error: {exc} "
            f"| hint=fix the benchmark command quoting or command assembly; "
            f"see TRACE COMMAND for the failing line"
        )
        return 1, str(exc)
    except subprocess.TimeoutExpired:
        trace(
            "COMMAND",
            f"TIMEOUT ({COMMAND_TIMEOUT_SECONDS}s): {command[:COMMAND_TIMEOUT_TRUNCATION]}",
        )
        log.error(
            f"COMMAND TIMEOUT ({COMMAND_TIMEOUT_SECONDS}s): "
            f"{command[:COMMAND_TIMEOUT_TRUNCATION]} "
            f"| hint=run the command manually with /usr/bin/time and either "
            f"raise COMMAND_TIMEOUT_SECONDS in autoresearch_constants.py "
            f"or address the slow path"
        )
        return 1, "TIMEOUT"
    except (subprocess.SubprocessError, OSError) as exc:
        trace("COMMAND", f"ERROR: {exc}")
        log.error(
            f"RUN_COMMAND error: {exc} "
            f"| hint=verify the cwd and that the script path is reachable; "
            f"see TRACE COMMAND for the failing line"
        )
        return 1, str(exc)


def _safe_stdout_flush() -> None:
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        return
    except ValueError as exc:
        if "closed file" in str(exc).lower():
            return
        raise
    except OSError as exc:
        if getattr(exc, "errno", None) == 32:
            return
        raise


# ── Output parsing ────────────────────────────────────────────────


def parse_result_json(output: str, *, allow_inline_json: bool = False) -> dict[str, Any] | None:
    """Find RESULT_JSON line in output.

    Inline JSON payloads are only accepted when explicitly opted in, to keep
    modern runners fail-closed on missing RESULT_JSON markers.
    """
    match = re.search(r"^RESULT_JSON (.+)$", output, flags=re.MULTILINE)
    if match:
        result_path = Path(match.group(1).strip())
        if not result_path.exists():
            msg = f"RESULT_JSON path does not exist: {result_path}"
            log.error(
                f"RESULT_JSON error: {msg} | hint=backtest printed a stale or wrong result path"
            )
            raise ResultJsonError(msg)
        try:
            return json.loads(result_path.read_text())
        except json.JSONDecodeError as exc:
            msg = f"RESULT_JSON malformed JSON at {result_path}: {exc}"
            log.error(f"RESULT_JSON error: {msg} | hint=fix the result writer before rerunning")
            raise ResultJsonError(msg) from exc
        except OSError as exc:
            msg = f"RESULT_JSON unreadable at {result_path}: {exc}"
            log.error(f"RESULT_JSON error: {msg} | hint=check file permissions and run-output dir")
            raise ResultJsonError(msg) from exc

    if not allow_inline_json:
        return None
    stripped = output.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def parse_benchmark_details(output: str, *, allow_legacy: bool = False) -> dict[str, Any]:
    """Extract metrics from result.json written by backtest.

    Legacy stdout parsing is only available when explicitly enabled.
    """
    result_json = parse_result_json(output)
    if result_json:
        details: dict[str, Any] = {}
        metrics = result_json.get("metrics", {})
        if metrics:
            details["train_metrics"] = dict(metrics)
        for key in (
            "trade_count",
            "profit_factor",
            "max_drawdown",
            "pct_profitable_windows",
            "avg_sharpe_across_windows",
            "win_rate",
        ):
            if key in metrics:
                details[key] = metrics[key]
        for key in (
            "diagnostics",
            "trades_file",
            "strategy_events_file",
            "diagnostics_file",
            "strategy_diagnostics",
            "git_sha",
            "config_hash",
        ):
            if result_json.get(key):
                details[key] = result_json[key]
        return details

    if not allow_legacy:
        raise ResultJsonError("RESULT_JSON marker missing; legacy stdout parsing is disabled")
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
        except json.JSONDecodeError as exc:
            log.error(
                f"DIAGNOSTICS parse error: {exc} "
                f"| hint=backtest emitted a malformed DIAGNOSTICS JSON line"
            )
            raise ValueError(f"malformed DIAGNOSTICS line: {exc}") from exc
    trades_match = re.search(r"^TRADES_FILE (.+)$", output, flags=re.MULTILINE)
    if trades_match:
        details["trades_file"] = trades_match.group(1).strip()
    return details


def primary_metric_name(entries: list[dict[str, Any]]) -> str:
    """Read the primary metric name from exported session entries."""
    if not entries:
        return "profit_factor"
    for entry in entries:
        if entry.get("type") == "config":
            return entry.get("metricName", "profit_factor")
    return "profit_factor"


def parse_metric(
    output: str, name: str = "profit_factor", *, allow_legacy: bool = False
) -> float | None:
    result_json = parse_result_json(output)
    if result_json:
        val = result_json.get("metrics", {}).get(name)
        return float(val) if val is not None else None
    if not allow_legacy:
        return None
    match = re.search(rf"^METRIC {re.escape(name)}=([-+]?\d*\.?\d+)", output, flags=re.MULTILINE)
    return float(match.group(1)) if match else None


def evaluate_metric(root: Path, db_name: str, metric: float, *, job_id: int | None = None) -> str:
    from experiment_db import ExperimentDB

    db = ExperimentDB(root / db_name)
    return db.evaluate_metric(metric, job_id=job_id)


# ── Trade analysis (sets transient controller fields) ────────────


def derive_trade_analysis(
    controller: "AutoresearchController",
    config: str,
    metric: float,
    decision: str,
    output: str = "",
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
                from strategies import STRATEGIES

                family_name = controller.family.name
                config_contents = STRATEGIES[family_name].compile_contract(raw).runtime_config
        except OSError as exc:
            log.error(
                f"CONFIG_READ error config={config}: {exc} "
                f"| hint=the experiment config exists but cannot be read"
            )
            raise

    trade_analysis: dict[str, Any] = {
        "what_changed_vs_baseline": f"{Path(config).stem} evaluated independently.",
        "primary_metric_improved": decision == "keep",
        **details,
    }
    controller.ctx.latest_trades_file = details.get("trades_file", "")
    controller.ctx.latest_strategy_events_file = details.get("strategy_events_file", "")
    controller.ctx.latest_diagnostics_file = details.get("diagnostics_file", "")
    controller.ctx.latest_config_contents = config_contents
    return {
        "trade_analysis": trade_analysis,
        "insights": [f"metric={metric}", f"decision={decision}"],
        "next_candidates": [],
        "why_not_data_fit": "Independent thesis evaluation only.",
        "runtime_config": config_contents,
    }


# ── Artifact + entry helpers ─────────────────────────────────────


def artifact_dir_for(state_path: Path, runs_dir: Path, config: str) -> Path:
    state = read_state(state_path)
    job = state.get("job", 0)
    config_path = Path(config)
    slug = (
        config_path.parent.name
        if config_path.name == "runtime_config.json" and config_path.parent.name
        else config_path.stem
    )
    path = runs_dir.resolve() / f"job-{job}" / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_duplicate_entries(db: Any, config: str) -> None:
    slug = Path(config).stem
    removable_ids: list[str] = []
    records = db.all()
    for record in records:
        description = getattr(record, "_description_export", f"strict-native loop: {slug}")
        same_config = record.config_path == config
        low_information = description == f"loop: {slug}"
        if same_config and low_information:
            removable_ids.append(record.experiment_id)
    if not removable_ids:
        return
    db.import_entries(
        [
            entry
            for entry in db.export_entries()
            if entry.get("type") == "config" or entry.get("run_id") not in removable_ids
        ]
    )


def _resolve_artifact_dir(
    controller: "AutoresearchController", config: str, artifact_dir: Path | None = None
) -> Path:
    """Pick the run-output directory: the one set in run_experiment if
    present, otherwise compute a fresh per-job dir."""
    if artifact_dir is not None:
        return artifact_dir
    return artifact_dir_for(controller.state_path, controller.runs_dir, config)


def _read_thesis_metadata(
    controller: "AutoresearchController", config: str
) -> tuple[str, dict[str, Any]]:
    """Look for an experiments/<id>/thesis.json sidecar and return the
    (thesis_id, config_changes) override pair. Falls back to (config-stem, {})."""
    contract = controller.ctx.current_contract
    thesis_id = contract.thesis_id if contract else Path(config).stem
    config_changes: dict[str, Any] = {}
    experiment_slug = contract.experiment_id if contract else Path(config).parent.name
    thesis_json_path = controller.root / "experiments" / experiment_slug / "thesis.json"
    if thesis_json_path.exists():
        try:
            tj = json.loads(thesis_json_path.read_text())
            thesis_id = tj.get("thesis_id", thesis_id)
            config_changes = tj.get("config_changes", {})
        except json.JSONDecodeError as exc:
            log.error(
                f"THESIS_METADATA_MALFORMED path={thesis_json_path}: {exc} "
                f"| hint=repair or delete the malformed thesis sidecar JSON"
            )
            raise ValueError(f"THESIS_METADATA_MALFORMED path={thesis_json_path}: {exc}") from exc
        except OSError as exc:
            log.error(
                f"THESIS_METADATA_READ error path={thesis_json_path}: {exc} "
                f"| hint=the thesis sidecar exists but cannot be read"
            )
            raise
    return thesis_id, config_changes


def _contract_from_sidecar(controller: "AutoresearchController", config: str) -> Any | None:
    contract = controller.ctx.current_contract
    if contract is not None:
        return contract
    experiment_slug = Path(config).parent.name
    thesis_json_path = controller.root / "experiments" / experiment_slug / "thesis.json"
    if not thesis_json_path.exists():
        return None
    try:
        payload = json.loads(thesis_json_path.read_text())
    except json.JSONDecodeError as exc:
        log.error(
            f"THESIS_METADATA_MALFORMED path={thesis_json_path}: {exc} "
            f"| hint=repair or delete the malformed thesis sidecar JSON"
        )
        raise ValueError(f"THESIS_METADATA_MALFORMED path={thesis_json_path}: {exc}") from exc
    except OSError as exc:
        log.error(
            f"THESIS_METADATA_READ error path={thesis_json_path}: {exc} "
            f"| hint=the thesis sidecar exists but cannot be read"
        )
        raise
    return SimpleNamespace(
        experiment_id=experiment_slug,
        thesis_id=payload.get("thesis_id", experiment_slug),
        hypothesis=payload.get("hypothesis", ""),
        mechanism=payload.get("mechanism", ""),
    )


def _resolve_identity(contract: Any | None, config: str) -> str:
    return (
        contract.thesis_id if contract and getattr(contract, "thesis_id", "") else Path(config).stem
    )


def _analysis_identity(controller: "AutoresearchController", config: str) -> str:
    return _resolve_identity(_contract_from_sidecar(controller, config), config)


def _serialize_artifact_dir(controller: "AutoresearchController", artifact_dir: Path) -> str:
    """Prefer a repo-relative artifact path, but keep absolute paths valid.

    The controller accepts an absolute runs_dir, so result logging must not
    assume every artifact directory lives under controller.root.
    """
    try:
        return artifact_dir.relative_to(controller.root).as_posix()
    except ValueError:
        return artifact_dir.as_posix()


def _build_asi_dict(
    controller: "AutoresearchController",
    *,
    config: str,
    artifact_dir: Path,
    analysis: dict[str, Any],
    thesis_id: str,
    config_changes: dict[str, Any],
    next_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = controller.ctx.current_contract
    identity = _resolve_identity(contract, config)
    state = controller.read_state()
    run_id = f"job-{state.get('job', 0)}-run-1-{identity}"
    asi = {
        "job": state.get("job"),
        "run_id": run_id,
        "hypothesis_id": identity,
        "hypothesis": identity,
        "config": config,
        "artifact_dir": _serialize_artifact_dir(controller, artifact_dir),
        "trade_analysis": analysis.get("trade_analysis", {}),
        "insights": analysis.get("insights", []),
        "next_candidates": analysis.get("next_candidates", []),
        "next_thesis_suggestion": analysis.get("next_thesis_suggestion", ""),
        "why_not_data_fit": analysis.get("why_not_data_fit"),
        "insight_brief": analysis.get("insight_brief", ""),
        "thesis_id": thesis_id,
        "config_changes": config_changes,
    }
    if next_action is None:
        next_action = controller.read_state().get("next_action", {})
    rerun_commit = next_action.get("baseline_rerun_for_commit")
    if rerun_commit and next_action.get("source") == "baseline":
        asi["baseline_rerun_for_commit"] = rerun_commit
    return asi


def _build_db_record(
    controller: "AutoresearchController",
    *,
    config: str,
    decision: str,
    details: dict[str, Any],
    analysis: dict[str, Any],
    runtime_config: dict[str, Any] | None = None,
    fallback_experiment_id: str,
    state: dict[str, Any],
) -> ExperimentResult:
    contract = _contract_from_sidecar(controller, config)
    verdict = analysis.get("trade_analysis", {}).get("verdict", {})
    if runtime_config is None:
        runtime_config = getattr(controller.ctx, "latest_config_contents", {}) or {}
    train_metrics = details.get("train_metrics", {})
    if not isinstance(train_metrics, dict):
        train_metrics = {}
    verdict_status = verdict.get("status")
    verdict_summary = verdict.get("summary", "")
    if not verdict_status:
        verdict_status = "accepted" if decision == "keep" else "rejected"
    if not verdict_summary:
        verdict_summary = (
            "kept by primary metric improvement"
            if decision == "keep"
            else "rejected by primary metric comparison"
        )
    duplicate = _find_duplicate_artifact_output(
        controller,
        runtime_config=runtime_config,
        details=details,
        state=state,
    )
    if duplicate is not None:
        decision = "discard"
        verdict_status = (
            "invalid_duplicate_result"
            if duplicate.runtime_config == runtime_config
            else "invalid_noop_config"
        )
        verdict_summary = _invalid_duplicate_result_summary(duplicate, details, runtime_config)
    code_commit = _executed_code_commit(controller, details)
    record = ExperimentResult(
        experiment_id=contract.experiment_id if contract else fallback_experiment_id,
        thesis_id=contract.thesis_id if contract else Path(config).stem,
        config_path=config,
        runtime_config=runtime_config,
        code_commit=code_commit,
        data_hash=build_data_hash(runtime_config),
        train_metrics=train_metrics,
        validation_metrics=details,
        trade_count=details.get("trade_count", 0),
        trades_file=details.get("trades_file", ""),
        strategy_events_file=details.get("strategy_events_file", ""),
        diagnostics_file=details.get("diagnostics_file", ""),
        strategy_diagnostics=details.get("strategy_diagnostics", {}),
        accepted=decision == "keep",
        rejection_reason=verdict_summary if decision != "keep" else "N/A",
        verdict_status=verdict_status,
        verdict_summary=verdict_summary,
        parent_experiment_id=controller.ctx.parent_experiment_id,
        timestamp=iso8601_utc_now(),
        family=controller.family.name,
        hypothesis=contract.hypothesis if contract else "",
        mechanism=contract.mechanism if contract else "",
        job=state.get("job", 0),
        usage=state.get("_last_round_usage", {}),
    )
    return record


def _sha256_file(path_value: Any) -> str:
    if not isinstance(path_value, str) or not path_value:
        return ""
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return ""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _coerce_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_duplicate_artifact_output(
    controller: "AutoresearchController",
    *,
    runtime_config: dict[str, Any],
    details: dict[str, Any],
    state: dict[str, Any],
) -> ExperimentResult | None:
    experiment_db = getattr(controller, "experiment_db", None)
    if experiment_db is None or not hasattr(experiment_db, "all"):
        return None
    trades_hash = _sha256_file(details.get("trades_file"))
    diagnostics_hash = _sha256_file(details.get("diagnostics_file"))
    if not trades_hash or not diagnostics_hash:
        return None
    current_job = _coerce_int_or_none(state.get("job", 0) or 0)
    current_trade_count = _coerce_int_or_none(details.get("trade_count", 0) or 0)
    if current_trade_count is None:
        return None
    for previous in reversed(experiment_db.all()):
        if previous.family and previous.family != controller.family.name:
            continue
        if current_job is not None and previous.job != current_job:
            continue
        if previous.trade_count != current_trade_count:
            continue
        if previous.strategy_diagnostics != details.get("strategy_diagnostics", {}):
            continue
        if _sha256_file(previous.trades_file) != trades_hash:
            continue
        if _sha256_file(previous.diagnostics_file) != diagnostics_hash:
            continue
        return previous
    return None


def _zero_rejection_diagnostic_hints(strategy_diagnostics: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    rejection_items = [
        (key, value)
        for key, value in strategy_diagnostics.items()
        if isinstance(key, str) and key.startswith("trade_rejections_due")
    ]
    for key, value in sorted(rejection_items, key=lambda item: item[0]):
        if isinstance(value, bool):
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if numeric_value == 0:
            hints.append(f"{key}=0")
    return hints


def _invalid_duplicate_result_summary(
    duplicate: ExperimentResult,
    details: dict[str, Any],
    runtime_config: dict[str, Any],
) -> str:
    if duplicate.runtime_config == runtime_config:
        summary = (
            "invalid_duplicate_result: identical runtime_config/artifacts as previous "
            f"experiment {duplicate.experiment_id}"
        )
    else:
        summary = (
            "invalid_noop_config: identical trades/diagnostics as previous "
            f"experiment {duplicate.experiment_id} despite different runtime_config"
        )
    parts = [summary]
    strategy_diagnostics = details.get("strategy_diagnostics", {})
    if isinstance(strategy_diagnostics, dict):
        parts.extend(_zero_rejection_diagnostic_hints(strategy_diagnostics))
    trade_count = details.get("trade_count")
    if trade_count is not None:
        parts.append(f"trade_count={trade_count}")
    return "; ".join(parts)


def _executed_code_commit(controller: "AutoresearchController", details: dict[str, Any]) -> str:
    git_sha = details.get("git_sha")
    if isinstance(git_sha, str) and re.fullmatch(r"[0-9a-f]{7,40}", git_sha):
        return git_sha
    return controller.current_commit()


def _build_export_entry(
    controller: "AutoresearchController",
    *,
    config: str,
    metric: float,
    decision: str,
    details: dict[str, Any],
    asi: dict[str, Any],
    next_run: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    contract = _contract_from_sidecar(controller, config)
    identity = _resolve_identity(contract, config)
    run_id = f"job-{state.get('job', 0)}-run-{next_run}-{identity}"
    experiment_id = contract.experiment_id if contract else run_id
    return {
        "run": next_run,
        "job": state.get("job"),
        "run_id": run_id,
        "experiment_id": experiment_id,
        "hypothesis_id": identity,
        "commit": _executed_code_commit(controller, details),
        "metric": metric,
        "metrics": details,
        "status": decision,
        "description": f"strict-native loop: {identity}",
        "timestamp": iso8601_utc_now(),
        "segment": 0,
        "confidence": None,
        "asi": asi,
        "hypothesis": getattr(contract, "hypothesis", "") if contract else "",
        "mechanism": getattr(contract, "mechanism", "") if contract else "",
    }


def _write_run_artifacts(artifact_dir: Path, output: str, analysis: dict[str, Any]) -> None:
    write_text_atomic(artifact_dir / "benchmark_output.txt", output)
    write_json_atomic(artifact_dir / "analysis.json", analysis)


def _artifact_dir_for_executed_commit(
    controller: "AutoresearchController",
    artifact_dir: Path,
    details: dict[str, Any],
) -> Path:
    executed_commit = _executed_code_commit(controller, details)
    current_commit = controller.current_commit()
    if executed_commit == current_commit:
        return artifact_dir

    target_dir = artifact_dir.parent.parent / executed_commit / artifact_dir.name
    if target_dir == artifact_dir:
        return artifact_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    if artifact_dir.exists():
        for child in artifact_dir.iterdir():
            dest = target_dir / child.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(child), str(dest))
        try:
            artifact_dir.rmdir()
            artifact_dir.parent.rmdir()
        except OSError:
            pass
    return target_dir


def log_experiment_result(
    controller: "AutoresearchController",
    *,
    config: str,
    metric: float,
    decision: str,
    output: str,
    analysis: dict[str, Any],
    next_action: dict[str, Any] | None = None,
    artifact_dir: Path | None = None,
) -> None:
    controller.sanitize_duplicate_entries(config)
    artifact_dir = _resolve_artifact_dir(controller, config, artifact_dir=artifact_dir)
    details = parse_benchmark_details(output)
    artifact_dir = _artifact_dir_for_executed_commit(controller, artifact_dir, details)
    _write_run_artifacts(artifact_dir, output, analysis)

    thesis_id, config_changes = _read_thesis_metadata(controller, config)
    runtime_config = analysis.get("runtime_config")
    if not isinstance(runtime_config, dict) or not runtime_config:
        runtime_config = getattr(controller.ctx, "latest_config_contents", {}) or {}
    asi = _build_asi_dict(
        controller,
        config=config,
        artifact_dir=artifact_dir,
        analysis=analysis,
        thesis_id=thesis_id,
        config_changes=config_changes,
        next_action=next_action,
    )
    next_run = 1 + controller.experiment_db.count()
    state = controller.read_state()
    entry = _build_export_entry(
        controller,
        config=config,
        metric=metric,
        decision=decision,
        details=details,
        asi=asi,
        next_run=next_run,
        state=state,
    )
    trace_benchmark(config, metric, decision, details)
    record = _build_db_record(
        controller,
        config=config,
        decision=decision,
        details=details,
        analysis=analysis,
        runtime_config=runtime_config,
        fallback_experiment_id=entry["run_id"],
        state=state,
    )
    setattr(record, "_asi_export", asi)
    setattr(record, "_description_export", entry["description"])
    controller.experiment_db.add(record)


# ── Run experiment orchestrator ──────────────────────────────────


def _compute_run_output_dir(controller: "AutoresearchController", config: str) -> tuple[Path, Path]:
    """Compute the per-job output directory using a config-content hash.
    Returns (run_output_dir, config_path_full)."""
    config_path_full = controller.root / config
    if config_path_full.exists():
        if config_path_full.suffix in (".yaml", ".yml"):
            _cfg = yaml.safe_load(config_path_full.read_text())
        else:
            _cfg = json.loads(config_path_full.read_text())
        if isinstance(_cfg, dict) and "runtime_config" in _cfg:
            _cfg = _cfg["runtime_config"]
        config_hash = _config_hash(_cfg)
    else:
        config_hash = _config_hash({"config_path": config})
    state = controller.read_state()
    job = state.get("job", 0)
    runs_dir = (
        controller.runs_dir
        if controller.runs_dir.is_absolute()
        else controller.root / controller.runs_dir
    )
    current_commit = (
        controller.current_commit() if hasattr(controller, "current_commit") else _git_sha()
    )
    run_output_dir = runs_dir / f"job-{job}" / current_commit / config_hash
    run_output_dir.mkdir(parents=True, exist_ok=True)
    return run_output_dir, config_path_full


def _block_with_command_failed(
    controller: "AutoresearchController",
    state: dict[str, Any],
    command: str,
    code: int,
) -> int:
    from autoresearch_research import notify_discord

    state.update(
        {
            "state": "blocked",
            "next_action": {
                "type": "blocked",
                "reason": "command_failed",
                "command": command,
                "exit_code": code,
            },
            "blockers": [{"kind": "command_failed", "detail": command, "exit_code": code}],
        }
    )
    state.pop("activity", None)
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    log.error(
        f"LOOP_STOP state=blocked exit_code={code} "
        f"| hint=the backtest exited non-zero; inspect the run-output dir "
        f"under runs/job-N/ for stderr, fix the failing command, then "
        f"re-run the loop (state will resume from blocked)"
    )
    notify_discord(
        f"⚠️ {controller.family.name.upper()} BLOCKED — backtest failed",
        f"**Command:** `{command[:COMMAND_NOTIFICATION_TRUNCATION]}`\n**Exit code:** {code}",
        webhook=controller.family.discord_webhook,
        color=DISCORD_COLOR_WARNING,
    )
    return code


def _block_with_metric_parse_failed(
    controller: "AutoresearchController",
    state: dict[str, Any],
    command: str,
) -> int:
    from autoresearch_research import notify_discord

    state.update(
        {
            "state": "blocked",
            "next_action": {
                "type": "blocked",
                "reason": "metric_parse_failed",
                "command": command,
            },
            "blockers": [{"kind": "metric_parse_failed", "detail": command}],
        }
    )
    state.pop("activity", None)
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    log.error(
        "LOOP_STOP state=blocked metric_parse_failed "
        "| hint=the backtest exited 0 but did not emit a `RESULT_JSON <path>` "
        "line on stdout; check that the backtest script writes its result.json "
        "and prints the marker line — see autoresearch_experiment.parse_result_json"
    )
    notify_discord(
        f"⚠️ {controller.family.name.upper()} BLOCKED — metric parse failed",
        f"**Command:** `{command[:COMMAND_NOTIFICATION_TRUNCATION]}`\n"
        "Could not extract metric from output.",
        webhook=controller.family.discord_webhook,
        color=DISCORD_COLOR_WARNING,
    )
    return 1


def _baseline_metrics_from_first_result(controller: "AutoresearchController") -> dict[str, Any]:
    results = controller.read_results()
    baseline_result = results[0] if results else None
    if not baseline_result:
        return {}
    bta = baseline_result.asi.get("trade_analysis", {})
    out: dict[str, Any] = {}
    for k in (
        "trade_count",
        "profit_factor",
        "max_drawdown",
        "pct_profitable_windows",
        "avg_sharpe_across_windows",
        "median_expectancy",
    ):
        if bta.get(k) is not None:
            out[k] = bta[k]
    return out


def _build_thesis_for_eval(contract: Any) -> Any:
    from research_types import ResearchThesis

    return ResearchThesis(
        thesis_id=contract.thesis_id,
        strategy_family=contract.strategy_family,
        hypothesis=contract.hypothesis,
        mechanism=contract.mechanism,
        expected_effects=contract.expected_effects,
        disqualifiers=contract.disqualifiers,
        required_diagnostics=contract.required_diagnostics,
    )


def _persist_verdict(controller: "AutoresearchController", contract: Any, verdict: Any) -> None:
    experiment_dir = controller.root / "experiments" / contract.experiment_id
    if experiment_dir.exists():
        write_text_atomic(
            experiment_dir / "verdict.json",
            verdict.model_dump_json(indent=2) + "\n",
        )


def _evaluate_against_thesis(
    controller: "AutoresearchController",
    contract: Any,
    metric: float,
    decision: str,
    details: dict[str, Any],
) -> tuple[Any | None, str]:
    """Run the thesis-contract evaluator against the result. Returns
    (verdict_or_None, possibly_overridden_decision). Fail-open: any
    evaluator exception is logged and the decision passes through
    unchanged."""
    try:
        from experiment_evaluator import evaluate_experiment

        candidate_metrics = dict(details)
        primary_metric_name = (
            controller.primary_metric_name()
            if hasattr(controller, "primary_metric_name")
            else "profit_factor"
        )
        candidate_metrics[primary_metric_name] = metric
        verdict = evaluate_experiment(
            thesis=_build_thesis_for_eval(contract),
            baseline_metrics=_baseline_metrics_from_first_result(controller),
            candidate_metrics=candidate_metrics,
            experiment_id=contract.experiment_id,
            strategy_diagnostics=details.get("strategy_diagnostics"),
        )
        trace(
            "EVAL",
            f"verdict={verdict.status} passed={verdict.passed_effects} "
            f"failed={verdict.failed_effects} dq={verdict.triggered_disqualifiers}",
        )
        log.info(f"VERDICT {verdict.status}: {verdict.summary}")
        _persist_verdict(controller, contract, verdict)
        if verdict.status == "rejected":
            return verdict, "discard"
        if verdict.status == "accepted" and decision == "discard":
            trace("EVAL", "thesis accepted despite metric threshold")
        return verdict, decision
    except (
        ImportError,
        KeyError,
        TypeError,
        AttributeError,
        ValueError,
        ZeroDivisionError,
        IndexError,
    ) as exc:
        trace("EVAL", f"evaluation error: {exc}")
        log.error(
            f"EVAL error (fatal): {exc} "
            f"| hint=the thesis evaluator hit a deterministic local error; fix the thesis/evaluator "
            f"contract instead of accepting a metric-only result"
        )
        raise


def _record_baseline_checkpoint(
    controller: "AutoresearchController",
    details: dict[str, Any],
    runtime_cfg: dict[str, Any],
) -> None:
    code_commit = _executed_code_commit(controller, details)
    new_checkpoint = BaselineCheckpoint(
        code_commit=code_commit,
        data_hash=build_data_hash(runtime_cfg),
        config_hash=build_config_hash(runtime_cfg),
        metrics=_baseline_checkpoint_metrics(details),
        timestamp=iso8601_utc_now(),
        round_number=len(controller.baseline_tracker.all_checkpoints()),
    )
    drift = controller.baseline_tracker.check_drift(new_checkpoint)
    # Window 2 crash safety: if the process dies after baseline_tracker.record()
    # but before write_state(), the next startup will see an extra checkpoint
    # and redundantly rerun the baseline. This is benign — no data is lost and
    # the rerun produces a valid new checkpoint.
    controller.baseline_tracker.record(new_checkpoint)
    trace("BASELINE", f"checkpoint recorded commit={code_commit}")
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


def _baseline_checkpoint_metrics(details: dict[str, Any]) -> dict[str, float]:
    """Return only scalar numeric metrics that are valid for drift comparison."""
    metrics: dict[str, float] = {}

    def add_metric(name: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        numeric = float(value)
        if math.isnan(numeric):
            return
        metrics.setdefault(name, numeric)

    for key, value in details.items():
        if key == "train_metrics":
            continue
        add_metric(key, value)

    train_metrics = details.get("train_metrics")
    if isinstance(train_metrics, dict):
        for key, value in train_metrics.items():
            add_metric(key, value)

    return metrics


def _send_completion_notification(
    controller: "AutoresearchController",
    config: str,
    metric: float,
    decision: str,
    verdict: Any | None,
    state: dict[str, Any],
) -> None:
    from autoresearch_research import notify_discord

    best = state.get("current_best", {})
    verdict_str = verdict.status if verdict else "none"
    emoji = "✅" if decision == "keep" else "❌" if decision == "discard" else "🔄"
    notify_discord(
        f"{emoji} {controller.family.name.upper()} — {Path(config).stem}",
        f"**PF:** {metric}  |  **Decision:** {decision}  |  **Verdict:** {verdict_str}\n"
        f"**Best so far:** `{Path(best.get('config', '?')).stem}` "
        f"PF={best.get('metric', '?')}",
        webhook=controller.family.discord_webhook,
        color=DISCORD_COLOR_SUCCESS if decision == "keep" else DISCORD_COLOR_DISCARD,
    )
    log.info(
        f"HEARTBEAT complete thesis={config} result={decision} metric={metric} "
        f"verdict={verdict_str} next_action={state.get('next_action', {}).get('type')}"
    )


def _setup_run(controller: "AutoresearchController", config: str) -> tuple[Path, str | None]:
    """Compute the per-run output dir, copy the source config into it,
    and build the benchmark command. Returns (run_output_dir, command);
    command is None if the family did not produce one."""
    run_output_dir, config_path_full = _compute_run_output_dir(controller, config)
    if config_path_full.exists():
        shutil.copy2(config_path_full, run_output_dir / "config.json")
    command = controller.family.benchmark_command(config, output_dir=str(run_output_dir))
    return run_output_dir, command if command else None


def run_experiment(controller: "AutoresearchController", state: dict[str, Any]) -> int:
    """Run a single experiment (backtest + evaluate + log). Returns exit code."""
    next_action = state["next_action"]
    config = next_action["config"]
    state["activity"] = {
        "type": "experiment",
        "phase": "backtest_running",
        "config": config,
        "source": next_action.get("source"),
    }
    controller.write_state(state)

    try:
        run_output_dir, command = _setup_run(controller, config)
        if not command:
            log.error(
                "LOOP_STOP missing_benchmark_command "
                "| hint=family.benchmark_command() returned an empty string; "
                "check StrategyFamily wiring for this family in strategy_family.py"
            )
            return 1

        begin_hypothesis(Path(config).stem if config else "unknown")
        trace("LOOP", f"BENCHMARK START: {command}")
        log.info(f"HEARTBEAT running {command}")
        code, output = controller.run_command(command)
        if code != 0:
            return _block_with_command_failed(controller, controller.read_state(), command, code)

        try:
            metric = controller.parse_metric(output, name=controller.primary_metric_name())
        except ResultJsonError:
            return _block_with_metric_parse_failed(controller, controller.read_state(), command)
        if metric is None:
            return _block_with_metric_parse_failed(controller, controller.read_state(), command)

        try:
            details = controller.parse_benchmark_details(output)
        except (ResultJsonError, ValueError):
            return _block_with_metric_parse_failed(controller, controller.read_state(), command)
        try:
            decision = controller.evaluate_metric(metric)
        except TimeoutError as exc:
            interrupted = build_research_failure_state(
                controller.root,
                controller.research_dir,
                str(exc),
            )
            state = controller.read_state()
            state.update(interrupted)
            state.pop("activity", None)
            controller.write_state(state)
            controller.write_current_md(state, controller.read_results())
            log.error(
                "LOOP_STOP state=interrupted evaluate_metric_timeout "
                "| hint=autoresearch_cli evaluate hung; inspect local Python environment and "
                "retry once the CLI returns within timeout"
            )
            return 1
        trace("LOOP", f"METRIC parsed: {metric} decision={decision} config={config}")

        verdict: Any | None = None
        contract = controller.ctx.current_contract
        if contract and contract.expected_effects:
            verdict, decision = _evaluate_against_thesis(
                controller, contract, metric, decision, details
            )

        analysis = controller.derive_trade_analysis(config, metric, decision, output=output)
        if verdict:
            analysis["trade_analysis"]["verdict"] = verdict.model_dump()
        if controller.ctx.current_contract is None:
            controller.ctx.parent_experiment_id = ""
            current_state = controller.read_state()
            if current_state.pop("_last_round_usage", None) is not None:
                controller.write_state(current_state)
        controller.log_experiment_result(
            config=config,
            metric=metric,
            decision=decision,
            output=output,
            analysis=analysis,
            next_action=next_action,
            artifact_dir=run_output_dir,
        )

        baseline_source = next_action.get("source") == "baseline"
        if baseline_source:
            runtime_config = analysis.get("runtime_config")
            if not isinstance(runtime_config, dict) or not runtime_config:
                runtime_config = getattr(controller.ctx, "latest_config_contents", {}) or {}
            _record_baseline_checkpoint(controller, details, runtime_config)
        _finalize_experiment(controller, config, metric, decision, verdict)
        return 0
    finally:
        controller.clear_transient_context()
        state = controller.read_state()
        if "_last_round_usage" in state:
            state.pop("_last_round_usage", None)
            controller.write_state(state)


def _finalize_experiment(
    controller: "AutoresearchController",
    config: str,
    metric: float,
    decision: str,
    verdict: Any | None,
) -> None:
    """End the hypothesis, reconcile state, log the iteration trace, and
    send the completion notification."""
    end_hypothesis(decision=decision, metric=metric)
    state = controller.reconcile_state()
    if "activity" in state:
        state.pop("activity", None)
        controller.write_state(state)
        controller.write_current_md(state, controller.read_results())
    trace(
        "LOOP",
        f"ITERATION DONE thesis={config} metric={metric} decision={decision} "
        f"verdict={verdict.status if verdict else 'none'} "
        f"next={state.get('next_action', {}).get('type')}",
    )
    _send_completion_notification(controller, config, metric, decision, verdict, state)
