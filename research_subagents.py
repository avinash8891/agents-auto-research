from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from agent_token_usage import _accumulate_usage
from autoresearch_paths import resolve_runtime_root
from autoresearch_state import coerce_timestamp_to_epoch_ms
from backtest_run_db import BacktestRunDB
from research_paths import (
    _CONDUCTOR_MODEL,
    _OAUTH_PROXY_URL,
    _ROOT,
    _ensure_oauth_proxy,
    _extract_runner_output_text,
    _get_openai_client,
    _parse_json,
)
from strategy_family import load_family
from trace_sdk import (
    trace,
    trace_agent_prompt,
    trace_agent_response,
    trace_agent_tool_call,
    trace_agent_tool_result,
)
from web_research_cli import WebResearchCliError, run_codex_web_research

ANALYST_READ_FILE_MAX_CHARS = 12_000
ANALYST_RUN_PYTHON_MAX_CHARS = 12_000
ANALYST_RUN_PYTHON_FAILED_CALL_LIMIT = 3
ANALYST_SOURCE_EXCLUDED_FILES = {
    "__init__.py",
    "contract.py",
    "defaults.py",
    "prompt.py",
    "research.py",
    "validate.py",
}


@dataclass
class AnalystRoundIndex:
    family_name: str
    db_path: str
    job_id: int | None
    current_ref: str
    default_ref: str
    manifests_by_ref: dict[str, dict[str, object]]
    ordered_refs: list[str]
    round_ref_map: dict[str, str]


def _resolve_tool_max_chars(value: object, *, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1_000, min(parsed, 20_000))


def _compact_tool_output(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    suffix = (
        f"\n... (truncated tool output, {len(text)} total chars, "
        f"sha256={digest}; use targeted run_python or typed artifact tools if more detail is needed)"
    )
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix, True


class AnalystRunPythonFailureBudget:
    """Soft guard against repeated failed Python attempts inside one analyst run."""

    def __init__(self, *, limit: int = ANALYST_RUN_PYTHON_FAILED_CALL_LIMIT) -> None:
        self.limit = limit
        self.failed_calls = 0
        self.last_error_type = ""

    def before_call_message(self) -> str | None:
        if self.failed_calls < self.limit:
            return None
        return (
            "RUN_PYTHON_BUDGET_EXCEEDED: run_python already failed "
            f"{self.failed_calls} times in this analyst run"
            + (f" (last_error_type={self.last_error_type})." if self.last_error_type else ".")
            + " Stop broad Python retries. Use successful outputs, inspect the manifest/artifact "
            "schema with targeted reads, or return a bounded diagnosis explaining what could not "
            "be computed."
        )

    def record(self, *, status: str, error_type: str) -> None:
        if status == "error" or error_type:
            self.failed_calls += 1
            self.last_error_type = error_type or status
            return
        self.failed_calls = 0
        self.last_error_type = ""


def _analyst_data_root_guidance() -> str:
    data_root = os.environ.get("AUTORESEARCH_DATA_ROOT")
    if data_root:
        return (
            f"Market data root: AUTORESEARCH_DATA_ROOT={data_root}\n"
            f"Universe data lives under: {data_root}/universes/{{DATA_UNIVERSE}}/\n"
            "Typical wide-format files: open.parquet, high.parquet, low.parquet, "
            "close.parquet, volume.parquet.\n"
            f"Do NOT probe {str(_ROOT / 'data')} unless AUTORESEARCH_DATA_ROOT is unset."
        )
    return (
        "AUTORESEARCH_DATA_ROOT is unset in this process.\n"
        "Raw market data is unavailable unless the market data manifest below exposes "
        "an exact existing universe_path.\n"
        "Do NOT probe repo-local data directories or guess paths such as "
        f"{str(_ROOT / 'data')}."
    )


def _load_runtime_config_for_artifact(artifact_file: str) -> tuple[Path | None, dict]:
    artifact_path = Path(artifact_file).expanduser()
    candidates = (
        artifact_path.parent / "config.json",
        artifact_path.parent / "selected_config.json",
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return candidate, {}
        runtime = raw.get("runtime_config") if isinstance(raw, dict) else None
        if isinstance(runtime, dict):
            return candidate, runtime
        return candidate, raw if isinstance(raw, dict) else {}
    return None, {}


def _market_data_manifest(trades_file: str) -> str:
    config_path, runtime_config = _load_runtime_config_for_artifact(trades_file)
    data_universe = runtime_config.get("data_universe")
    provenance = runtime_config.get("data_provenance")
    data_root = os.environ.get("AUTORESEARCH_DATA_ROOT")

    lines = ["MARKET DATA MANIFEST:"]
    if config_path is not None:
        lines.append(f"- runtime_config: {config_path}")
    else:
        lines.append("- runtime_config: not found from artifact path")
    lines.append(f"- data_universe: {data_universe or 'unknown'}")

    universe_path = ""
    manifest_path = ""
    if isinstance(provenance, dict):
        universe_path = str(provenance.get("universe_path") or "")
        manifest_path = str(provenance.get("manifest_path") or "")
    if not universe_path and data_root and data_universe:
        universe_path = str(Path(data_root) / "universes" / str(data_universe))
    if not manifest_path and universe_path:
        manifest_path = str(Path(universe_path) / "manifest.json")
    if universe_path:
        lines.append(f"- universe_path: {universe_path}")
    if manifest_path:
        lines.append(f"- manifest_path: {manifest_path}")

    if universe_path:
        for label in ("open", "high", "low", "close", "volume"):
            path = Path(universe_path) / f"{label}.parquet"
            status = "exists" if path.exists() else "expected"
            lines.append(f"- {label}: {path} ({status})")
        lines.append(
            "- Do NOT run recursive filesystem discovery such as glob('/root/**') "
            "or searches for open.parquet; use the paths above."
        )
    else:
        lines.append("- No exact universe path resolved.")
        lines.append(
            "- Raw market data is unavailable for this analyst call; use trades.csv, "
            "strategy_events.parquet, diagnostics.json, and source code only."
        )
        lines.append(
            "- Do NOT probe repo-local data directories, recursive filesystem paths, "
            "or guessed locations for OHLCV files."
        )
    return "\n".join(lines)


def _render_analyst_resolution_context(resolution_context: dict[str, Any] | None) -> str:
    context = resolution_context or {}
    keys = context.get("resolution_config_keys") or []
    resolved = context.get("resolved_minutes_by_key") or {}
    minimum = context.get("minimum_supported_time_bucket_minutes")
    lines = ["EXECUTION RESOLUTION CONTEXT:"]
    if keys:
        lines.append(f"- resolution_config_keys: {', '.join(str(key) for key in keys)}")
    else:
        lines.append("- resolution_config_keys: none declared")
    if resolved:
        for key, minutes in resolved.items():
            lines.append(f"- {key}: {minutes} minute bars")
    else:
        lines.append("- resolved_minutes_by_key: unavailable")
    lines.append(
        f"- minimum_supported_time_bucket_minutes: {minimum}"
        if minimum
        else "- minimum_supported_time_bucket_minutes: unknown"
    )
    return "\n".join(lines)


def _family_name_from_artifact_path(path: str) -> str:
    for part in Path(path).expanduser().parts:
        if part.endswith("_autoresearch-runs"):
            return part.removesuffix("_autoresearch-runs")
    return ""


def _normalized_existing_path(raw_path: str) -> str:
    if not raw_path:
        return ""
    candidate = Path(raw_path).expanduser()
    try:
        if candidate.exists():
            return str(candidate.resolve())
    except OSError:
        return str(candidate)
    return str(candidate)


def _infer_job_id_from_artifact_path(path: str) -> int | None:
    for part in Path(path).expanduser().parts:
        if not part.startswith("job-"):
            continue
        try:
            return int(part.removeprefix("job-"))
        except ValueError:
            continue
    return None


def _resolve_backtest_db_path(trades_file: str, family_name: str) -> Path | None:
    if not family_name:
        return None
    artifact_path = Path(trades_file).expanduser()
    candidates: list[Path] = []
    runtime_root = resolve_runtime_root(_ROOT)
    candidates.append(runtime_root / f"{family_name}_backtest_runs.db")
    for parent in artifact_path.parents:
        candidates.append(parent / f"{family_name}_backtest_runs.db")
        candidates.append(parent / "runtime" / f"{family_name}_backtest_runs.db")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists():
            return resolved
    return None


def _record_artifacts(record: Any) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    trades_file = _normalized_existing_path(getattr(record, "trades_file", ""))
    if trades_file:
        artifacts["trades_csv"] = trades_file
        metrics_path = Path(trades_file).with_name("metrics.json")
        if metrics_path.exists():
            artifacts["metrics_json"] = str(metrics_path.resolve())
        result_path = Path(trades_file).with_name("result.json")
        if result_path.exists():
            artifacts["result_json"] = str(result_path.resolve())
    strategy_events_file = _normalized_existing_path(getattr(record, "strategy_events_file", ""))
    if strategy_events_file:
        artifacts["strategy_events_parquet"] = strategy_events_file
    diagnostics_file = _normalized_existing_path(getattr(record, "diagnostics_file", ""))
    if diagnostics_file:
        artifacts["diagnostics_json"] = diagnostics_file
    config_file, _ = _load_runtime_config_for_artifact(trades_file) if trades_file else (None, {})
    config_path = str(getattr(record, "config_path", "") or "")
    if config_file is not None:
        artifacts["runtime_config_json"] = str(config_file)
    elif config_path:
        artifacts["runtime_config_json"] = config_path
    return artifacts


def _record_metric(record: Any, primary_metric_name: str) -> float | None:
    metrics = getattr(record, "validation_metrics", {}) or {}
    if primary_metric_name in metrics:
        try:
            return float(metrics[primary_metric_name])
        except (TypeError, ValueError):
            return None
    metrics = getattr(record, "train_metrics", {}) or {}
    if primary_metric_name in metrics:
        try:
            return float(metrics[primary_metric_name])
        except (TypeError, ValueError):
            return None
    return None


def _record_summary(record: Any, primary_metric_name: str) -> dict[str, Any]:
    metric = _record_metric(record, primary_metric_name)
    return {
        "backtest_run_id": getattr(record, "backtest_run_id", ""),
        "research_round_id": getattr(record, "research_round_id", ""),
        "research_round_number": getattr(record, "research_round_number", -1),
        "thesis_id": getattr(record, "thesis_id", ""),
        "config_path": getattr(record, "config_path", ""),
        "metric": metric,
        "status": "keep" if getattr(record, "accepted", False) else "discard",
        "timestamp": getattr(record, "timestamp", ""),
        "job": getattr(record, "job", 0),
        "artifacts_available": sorted(_record_artifacts(record).keys()),
    }


def _build_round_index(
    *,
    trades_file: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    family_name: str = "",
) -> AnalystRoundIndex | None:
    config_path, runtime_config = _load_runtime_config_for_artifact(trades_file)
    resolved_family = (
        family_name
        or str(runtime_config.get("strategy_family") or "")
        or str(runtime_config.get("family") or "")
        or _family_name_from_artifact_path(trades_file)
    )
    db_path = _resolve_backtest_db_path(trades_file, resolved_family)
    if db_path is None:
        return None
    db = BacktestRunDB(db_path)
    primary_metric_name = db.primary_metric_name()
    records = db.all()
    target_trades = _normalized_existing_path(trades_file)
    current_record = next(
        (
            record
            for record in records
            if _normalized_existing_path(record.trades_file) == target_trades
        ),
        None,
    )
    job_id = getattr(current_record, "job", None)
    if job_id is None:
        job_id = _infer_job_id_from_artifact_path(trades_file)
    job_records = [record for record in records if getattr(record, "job", None) == job_id]
    if not job_records:
        return None
    ordered = sorted(job_records, key=lambda record: coerce_timestamp_to_epoch_ms(record.timestamp))
    latest_record = ordered[-1]
    if current_record is None:
        current_record = latest_record
    best_record = None
    kept_records = [record for record in ordered if getattr(record, "accepted", False)]
    if kept_records:
        direction = db.best_direction()
        for record in kept_records:
            candidate = _record_metric(record, primary_metric_name)
            if candidate is None:
                continue
            if best_record is None:
                best_record = record
                continue
            best_value = _record_metric(best_record, primary_metric_name)
            if best_value is None:
                best_record = record
                continue
            if direction == "higher" and candidate > best_value:
                best_record = record
            elif direction != "higher" and candidate < best_value:
                best_record = record
    base_config_filename = (
        load_family(resolved_family).base_config_filename if resolved_family else ""
    )
    baseline_config_path = f"configs/{base_config_filename}" if base_config_filename else ""
    baseline_candidates = [
        record for record in ordered if getattr(record, "config_path", "") == baseline_config_path
    ]
    baseline_record = baseline_candidates[-1] if baseline_candidates else None
    manifests_by_ref: dict[str, dict[str, object]] = {}
    ordered_refs: list[str] = []
    round_ref_map: dict[str, str] = {}
    for idx, record in enumerate(ordered, start=1):
        ref = f"sequence:{idx}"
        ordered_refs.append(ref)
        manifest = {
            "summary": _record_summary(record, primary_metric_name),
            "artifacts": _record_artifacts(record),
        }
        manifests_by_ref[ref] = manifest
        round_number = getattr(record, "research_round_number", -1)
        if isinstance(round_number, int) and round_number >= 0:
            round_ref_map[f"round:{round_number}"] = ref
    aliases = {
        "current": current_record,
        "latest": latest_record,
        "best": best_record,
        "baseline": baseline_record,
    }
    for alias, record in aliases.items():
        if record is None:
            continue
        manifests_by_ref[alias] = {
            "summary": _record_summary(record, primary_metric_name),
            "artifacts": _record_artifacts(record),
        }
    if "baseline" in manifests_by_ref:
        round_ref_map["round:0"] = "baseline"
    default_ref = "baseline" if "baseline" in manifests_by_ref else "current"
    return AnalystRoundIndex(
        family_name=resolved_family,
        db_path=str(db_path),
        job_id=job_id,
        current_ref="current",
        default_ref=default_ref,
        manifests_by_ref=manifests_by_ref,
        ordered_refs=ordered_refs,
        round_ref_map=round_ref_map,
    )


def _resolve_round_ref(index: AnalystRoundIndex | None, round_ref: str) -> str | None:
    if index is None:
        return None
    ref = (round_ref or "").strip() or index.default_ref
    if ref in index.round_ref_map:
        mapped_ref = index.round_ref_map[ref]
        if mapped_ref in index.manifests_by_ref:
            return mapped_ref
    if ref in index.manifests_by_ref:
        return ref
    return None


def _resolve_artifacts_for_ref(
    index: AnalystRoundIndex | None, round_ref: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    if index is None:
        return "", {}, {}
    resolved_ref = _resolve_round_ref(index, round_ref)
    if resolved_ref is None:
        return "", {}, {}
    manifest = index.manifests_by_ref.get(resolved_ref) or {}
    summary = manifest.get("summary") if isinstance(manifest, dict) else {}
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    return (
        resolved_ref,
        dict(artifacts) if isinstance(artifacts, dict) else {},
        dict(summary) if isinstance(summary, dict) else {},
    )


def _discover_strategy_source_files(family_name: str) -> dict[str, str]:
    if not family_name:
        return {}
    strategy_dir = _ROOT / "strategies" / family_name
    if not strategy_dir.exists():
        return {}
    sources: dict[str, str] = {}
    for path in sorted(strategy_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name in ANALYST_SOURCE_EXCLUDED_FILES:
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        key = path.relative_to(strategy_dir).as_posix()
        sources[key] = str(path)
    return sources


def _analysis_manifest(
    *,
    trades_file: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    family_name: str = "",
) -> dict[str, object]:
    config_path, runtime_config = _load_runtime_config_for_artifact(trades_file)
    resolved_family = (
        family_name
        or str(runtime_config.get("strategy_family") or "")
        or str(runtime_config.get("family") or "")
        or _family_name_from_artifact_path(trades_file)
    )
    index = _build_round_index(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        family_name=resolved_family,
    )
    resolved_ref, artifacts, default_summary = _resolve_artifacts_for_ref(
        index, index.default_ref if index is not None else ""
    )
    if not artifacts:
        artifacts = {
            "trades_csv": trades_file,
        }
        metrics_path = Path(trades_file).with_name("metrics.json")
        if metrics_path.exists():
            artifacts["metrics_json"] = str(metrics_path.resolve())
        result_path = Path(trades_file).with_name("result.json")
        if result_path.exists():
            artifacts["result_json"] = str(result_path.resolve())
        if strategy_events_file:
            artifacts["strategy_events_parquet"] = strategy_events_file
        if diagnostics_file:
            artifacts["diagnostics_json"] = diagnostics_file
        if config_path is not None:
            artifacts["runtime_config_json"] = str(config_path)
        resolved_ref = "current"
        default_summary = {
            "thesis_id": Path(trades_file).parent.name,
            "config_path": str(config_path) if config_path is not None else "",
        }

    data_files: dict[str, str] = {}
    runtime_config_source = artifacts.get("trades_csv", trades_file)
    _, runtime_config = _load_runtime_config_for_artifact(runtime_config_source)
    data_universe = runtime_config.get("data_universe")
    provenance = runtime_config.get("data_provenance")
    universe_path = ""
    if isinstance(provenance, dict):
        universe_path = str(provenance.get("universe_path") or "")
    data_root = os.environ.get("AUTORESEARCH_DATA_ROOT")
    if not universe_path and data_root and data_universe:
        universe_path = str(Path(data_root) / "universes" / str(data_universe))
    if universe_path:
        for label in ("open", "high", "low", "close", "volume"):
            path = Path(universe_path) / f"{label}.parquet"
            if path.exists():
                data_files[label] = str(path)

    return {
        "family_name": resolved_family,
        "default_round_ref": resolved_ref,
        "default_scope": "baseline" if resolved_ref == "baseline" else "current",
        "default_round_summary": default_summary,
        "artifacts": artifacts,
        "job_round_index": (
            {
                "db_path": index.db_path,
                "job_id": index.job_id,
                "default_ref": index.default_ref,
                "available_refs": sorted(index.manifests_by_ref.keys()),
                "round_refs": index.round_ref_map,
            }
            if index is not None
            else {}
        ),
        "strategy_sources": _discover_strategy_source_files(resolved_family),
        "market_data_files": data_files,
    }


def _manifest_prompt_block(manifest: dict[str, object]) -> str:
    return "ANALYSIS MANIFEST:\n" + json.dumps(manifest, indent=2, sort_keys=True)


def _analysis_python_prelude(manifest: dict[str, object]) -> str:
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    strategy_sources = manifest.get("strategy_sources") if isinstance(manifest, dict) else {}
    market_data_files = manifest.get("market_data_files") if isinstance(manifest, dict) else {}
    default_round_ref = manifest.get("default_round_ref") if isinstance(manifest, dict) else None
    return "\n".join(
        [
            "from pathlib import Path",
            "from analyst_dataframe_helpers import (",
            "    bucket_trade_performance,",
            "    profit_factor,",
            "    safe_merge_asof,",
            ")",
            f"ANALYSIS_ARTIFACTS = {artifacts!r}",
            f"STRATEGY_SOURCE_FILES = {strategy_sources!r}",
            f"MARKET_DATA_FILES = {market_data_files!r}",
            f"DEFAULT_ROUND_REF = {default_round_ref!r}",
            "TRADES_FILE = Path(ANALYSIS_ARTIFACTS['trades_csv'])",
            "METRICS_FILE = Path(ANALYSIS_ARTIFACTS['metrics_json']) "
            "if 'metrics_json' in ANALYSIS_ARTIFACTS else None",
            "RESULT_FILE = Path(ANALYSIS_ARTIFACTS['result_json']) "
            "if 'result_json' in ANALYSIS_ARTIFACTS else None",
            "EVENTS_FILE = Path(ANALYSIS_ARTIFACTS['strategy_events_parquet']) "
            "if 'strategy_events_parquet' in ANALYSIS_ARTIFACTS else None",
            "DIAGNOSTICS_FILE = Path(ANALYSIS_ARTIFACTS['diagnostics_json']) "
            "if 'diagnostics_json' in ANALYSIS_ARTIFACTS else None",
            "RUNTIME_CONFIG_FILE = Path(ANALYSIS_ARTIFACTS['runtime_config_json']) "
            "if 'runtime_config_json' in ANALYSIS_ARTIFACTS else None",
            "",
        ]
    )


async def _call_analyst(
    trades_file: str,
    focus_question: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    family_name: str = "",
    latest_outcome: dict[str, Any] | None = None,
    resolution_context: dict[str, Any] | None = None,
    reflexion_feedback: str = "",
) -> str:
    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import function_tool
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

    _ensure_oauth_proxy()
    current_trace_id = f"analyst-{focus_question[:40].replace(' ', '_')}"
    manifest = _analysis_manifest(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        family_name=family_name,
    )
    round_index = _build_round_index(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        family_name=str(manifest.get("family_name") or family_name),
    )
    latest_outcome = dict(latest_outcome or {})
    manifest_prompt = _manifest_prompt_block(manifest)
    run_python_failure_budget = AnalystRunPythonFailureBudget()

    @function_tool
    def list_analysis_artifacts() -> str:
        """Return the exact artifacts, strategy source files, and market data paths available."""
        output = json.dumps(manifest, indent=2, sort_keys=True)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "list_analysis_artifacts",
            "",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "list_analysis_artifacts",
            output,
            status="ok",
            duration_ms=0,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def list_job_rounds() -> str:
        """Return the current job's round refs and summaries for cross-round analysis."""
        output = ""
        status = "ok"
        error_type = ""
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "list_job_rounds",
            "",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        if round_index is None:
            status = "error"
            error_type = "NoRoundIndex"
            output = "ERROR: No current-job round index is available for this analyst call."
        else:
            payload = {
                "family_name": round_index.family_name,
                "db_path": round_index.db_path,
                "job_id": round_index.job_id,
                "default_ref": round_index.default_ref,
                "current_ref": round_index.current_ref,
                "round_refs": round_index.round_ref_map,
                "rounds": [
                    {
                        "ref": ref,
                        **(
                            round_index.manifests_by_ref.get(ref, {}).get("summary", {})
                            if isinstance(round_index.manifests_by_ref.get(ref, {}), dict)
                            else {}
                        ),
                    }
                    for ref in round_index.ordered_refs
                ],
            }
            output = json.dumps(payload, indent=2, sort_keys=True)
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "list_job_rounds",
            output,
            status=status,
            error_type=error_type,
            duration_ms=0,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def read_artifact(kind: str, max_chars: int = ANALYST_READ_FILE_MAX_CHARS) -> str:
        """Read a manifest artifact by kind, such as diagnostics_json or runtime_config_json.

        Args:
            kind: Artifact key from ANALYSIS MANIFEST artifacts.
            max_chars: Maximum characters to return. Defaults to a compact audit-safe limit.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        max_chars = _resolve_tool_max_chars(max_chars, default=ANALYST_READ_FILE_MAX_CHARS)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "read_artifact",
            kind,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        try:
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict) or kind not in artifacts:
                raise KeyError(f"Unknown artifact kind: {kind}")
            with open(str(artifacts[kind])) as f:
                content = f.read()
            output, truncated = _compact_tool_output(content, max_chars=max_chars)
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "read_artifact",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def read_round_artifact(
        round_ref: str,
        kind: str,
        max_chars: int = ANALYST_READ_FILE_MAX_CHARS,
    ) -> str:
        """Read an artifact for a specific round ref like baseline, latest, best, round:3, or sequence:2."""
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        max_chars = _resolve_tool_max_chars(max_chars, default=ANALYST_READ_FILE_MAX_CHARS)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "read_round_artifact",
            json.dumps({"round_ref": round_ref, "kind": kind}),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        try:
            if round_index is None:
                raise KeyError("No current-job round index is available")
            _, artifacts, _ = _resolve_artifacts_for_ref(round_index, round_ref)
            if kind not in artifacts:
                raise KeyError(f"Artifact {kind!r} is unavailable for round_ref={round_ref!r}")
            with open(str(artifacts[kind])) as f:
                content = f.read()
            output, truncated = _compact_tool_output(content, max_chars=max_chars)
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "read_round_artifact",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def read_strategy_source(source_name: str, max_chars: int = ANALYST_READ_FILE_MAX_CHARS) -> str:
        """Read a strategy source file by name from ANALYSIS MANIFEST strategy_sources.

        Args:
            source_name: Source key from strategy_sources, for example strategy.py or signals.py.
            max_chars: Maximum characters to return.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        max_chars = _resolve_tool_max_chars(max_chars, default=ANALYST_READ_FILE_MAX_CHARS)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "read_strategy_source",
            source_name,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        try:
            sources = manifest.get("strategy_sources")
            if not isinstance(sources, dict) or source_name not in sources:
                raise KeyError(f"Unknown strategy source: {source_name}")
            with open(str(sources[source_name])) as f:
                content = f.read()
            output, truncated = _compact_tool_output(content, max_chars=max_chars)
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "read_strategy_source",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def run_python(code: str) -> str:
        """Execute Python code locally and return stdout + stderr.

        Args:
            code: Python code to execute. Use print() for output.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "run_python",
            code,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        budget_message = run_python_failure_budget.before_call_message()
        if budget_message is not None:
            status = "error"
            error_type = "RunPythonFailureBudgetExceeded"
            output = budget_message
            trace_agent_tool_result(
                "analyst",
                current_trace_id,
                "run_python",
                output,
                status=status,
                error_type=error_type,
                truncated=truncated,
                duration_ms=int((monotonic() - started) * 1000),
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return output
        try:
            executable_code = _analysis_python_prelude(manifest) + "\n" + code
            result = subprocess.run(
                [sys.executable, "-c", executable_code],
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            if result.returncode != 0:
                status = "error"
                error_type = "NonZeroExit"
                output += f"\nEXIT CODE: {result.returncode}"
            output, truncated = _compact_tool_output(output, max_chars=ANALYST_RUN_PYTHON_MAX_CHARS)
        except subprocess.TimeoutExpired:
            status = "error"
            error_type = "TimeoutExpired"
            output = "ERROR: Code execution timed out (60s limit)"
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        run_python_failure_budget.record(status=status, error_type=error_type)
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "run_python",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    analyst_prompt = f"""You are a quantitative trading analyst. You receive:
1. A path to a CSV file containing raw trades from a backtest
2. A FOCUS QUESTION from the research conductor
3. A strategy_events.parquet with every signal the strategy considered (accepted AND rejected)
4. A diagnostics.json with event counts and rejection breakdown
5. Optional raw OHLCV data, only when the manifest below exposes exact paths:
{_analyst_data_root_guidance()}
{_market_data_manifest(trades_file)}
{manifest_prompt}
CANONICAL LATEST OUTCOME SUMMARY:
{json.dumps(latest_outcome, indent=2) if latest_outcome else "(not provided)"}
{_render_analyst_resolution_context(resolution_context)}
   If no exact universe_path is resolved, do not use raw OHLCV or search for it.

The default preloaded artifact bundle is the baseline anchor for this job when
baseline artifacts exist; otherwise it falls back to the current round.
Use that default anchor for mechanism-discovery questions unless the focus
question is clearly about the latest run, the current best run, or a named
research round. For cross-round questions, use list_job_rounds and
read_round_artifact to fetch the exact comparison artifacts you need.

You MUST ground conclusions in the artifacts you actually read. Trades alone
show what happened; strategy_events show what DIDN'T happen and WHY.
Diagnostics give the high-level rejection breakdown before you dig into
details.
For headline backtest metrics, metrics.json produced by the shared metrics
pipeline is canonical; result.json is the manifest that points to it and to
diagnostics.json. Only recompute headline metrics as a sanity check, and label
any recomputed numbers as derived rather than canonical.

RAW TRADES CSV SCHEMA (one row per completed trade):
  entry_date, exit_date, direction, entry_price, exit_price, stop, target,
  pnl_pct, exit_reason, symbol

STRATEGY EVENTS PARQUET SCHEMA (one row per decision point, read with pd.read_parquet()):
  timestamp, symbol, direction, event_type, reason, entry_price, stop_price

  event_type values: raw_setup, accepted_signal, rejected_signal, order_rejected, executed_trade
  reason values: strategy-specific (read diagnostics.json for the actual reasons
                 used in this backtest)

DIAGNOSTICS JSON: quick summary with event_counts and rejection_breakdown.

WORKFLOW:
1. Start with the default anchor bundle exposed in ANALYSIS MANIFEST:
   read_artifact("result_json") first if present, then read_artifact("metrics_json")
   and read_artifact("diagnostics_json").
2. If the focus question is about latest/current behavior, best-run behavior,
   or longitudinal comparison, call list_job_rounds and then fetch the
   exact artifacts you need with read_round_artifact.
3. Use run_python to execute pandas analysis code on trades and/or events.
4. When the focus question requires market context (volatility, volume,
   trend, gaps, range characteristics), use raw OHLCV only if the manifest
   provides exact paths. If not, state that raw OHLCV is unavailable and
   answer from trades/events/diagnostics/source code.
5. Focus effort on the FOCUS QUESTION. Go deep, not wide.
6. When you find a pattern, quantify it with exact numbers and sample sizes.
7. Each run_python call is stateless. Put imports, path definitions, file reads,
   and calculations in the same run_python call. Do not rely on variables from
   earlier tool calls.

CRITICAL RULES:
- PF = sum(pnl_pct where pnl_pct > 0) / abs(sum(pnl_pct where pnl_pct <= 0))
- Only flag patterns with >50 trades per bucket
- Cite exact numbers
- Do NOT invent data
- Do NOT repeat analyses the focus question doesn't ask for
- Use raw OHLCV only from exact manifest paths. Never guess or probe data directories.
- Do NOT guess source paths. Use read_strategy_source with names from ANALYSIS MANIFEST.
- Do NOT read large source/data files into the chat unless strictly necessary.
  Prefer targeted run_python summaries and print compact tables only.
- Use canonical headline metrics from metrics.json before doing your own cohort
  analysis. If you recompute PF/expectancy/trade_count from raw trades, mark
  those values as derived and explain why the recomputation was needed.
- Treat CANONICAL LATEST OUTCOME SUMMARY as a compact prompt-level restatement
  of the latest backtest metrics. Use it for orientation, but prefer the
  artifact you explicitly read as the authoritative source.
- In run_python, these variables are already defined: TRADES_FILE, METRICS_FILE,
  RESULT_FILE, EVENTS_FILE, DIAGNOSTICS_FILE, RUNTIME_CONFIG_FILE,
  ANALYSIS_ARTIFACTS, STRATEGY_SOURCE_FILES, MARKET_DATA_FILES,
  DEFAULT_ROUND_REF.
- Start the JSON response with audit fields before long tables so required
  deliverables survive truncation.
- Every bucketed metric must include n= or N=. If the focus question specifies
  a minimum sample size, explicitly state which buckets meet it.
- If a join, field, artifact, or code/config lookup is blocked, quantify the
  blocker and give the best viable alternative instead of silently pivoting.
- If the focus question asks for time buckets finer than
  minimum_supported_time_bucket_minutes and no finer-grain raw data is exposed,
  mark feasibility as blocked/partial and restate the analysis at the finest
  supported bar resolution instead of inventing sub-bar buckets.
- Distinguish direct evidence from inference:
  - supported = directly measured from the current artifacts/source code
  - partially_supported = a narrower or proxy-based claim is supported, but the
    full requested claim is not directly provable
  - unsupported_missing_data = the core claim depends on missing fields,
    unavailable raw data, or insufficient resolution
  - unsupported_tool_failure = the core claim could not be evaluated because
    tool execution failed or timed out
- Never present an unsupported claim as if it were measured. If you proceed with
  a proxy or narrower claim, say exactly what is supported and what remains
  unproven.

OUTPUT FORMAT:
Return ONLY a JSON object:
{{
  "feasibility": {{
    "status": "supported/partially_supported/unsupported_missing_data/unsupported_tool_failure",
    "blockers": ["quantified blocker such as missing field or N=0"],
    "best_viable_alternative": "what you analyzed instead, or empty string"
  }},
  "supported_claims": [
    "claims directly supported by current artifacts or source code"
  ],
  "unsupported_claims": [
    "claims the current artifacts do not directly support"
  ],
  "artifacts_used": ["result_json if used", "metrics_json", "trades_csv", "strategy_events_parquet", "diagnostics_json", "runtime_config_json", "market_data_files if used"],
  "files_inspected": ["strategy/config/source files read, or NOT_FOUND"],
  "keys_found": ["config/source keys or fields found, or NOT_FOUND"],
  "focus_answer": "direct answer to the focus question with exact numbers",
  "key_anomalies": [
    {{
      "pattern": "one-line description",
      "numbers": "exact computed values",
      "sample_size": "trades in bucket",
      "suggested_exploit": "specific structural change",
      "confidence": "high/medium/low"
    }}
  ],
  "rejection_insights": [
    {{
      "reason": "rejection reason from strategy_events",
      "count": "number rejected",
      "pattern": "when/where rejections cluster",
      "implication": "what this means for the strategy"
    }}
  ],
  "overall_diagnosis": "2-3 sentence summary",
  "discovery_questions": ["questions needing more data"]
}}

Be brutally honest."""

    user_parts = [
        f"FOCUS QUESTION: {focus_question}",
        f"RAW TRADES FILE: {trades_file}",
    ]
    if strategy_events_file:
        user_parts.append(f"STRATEGY EVENTS FILE: {strategy_events_file}")
    if diagnostics_file:
        user_parts.append(f"DIAGNOSTICS FILE: {diagnostics_file}")
    if reflexion_feedback:
        user_parts.append(
            "AGENT REFLEXION FEEDBACK FROM THE PRIOR ROUND:\n"
            f"{reflexion_feedback}\n"
            "Apply this lesson only if it is relevant to the focus question. Do not repeat "
            "the same tool/path/prompt failure."
        )
    user_parts.append(
        "Load artifacts using read_artifact/read_round_artifact/read_strategy_source and "
        "perform analysis using run_python. Start from the default baseline anchor unless the "
        "question is explicitly about latest/current/best or a named round."
    )
    user_prompt = "\n\n".join(user_parts)
    current_trace_id = trace_agent_prompt(
        "analyst",
        user_prompt,
        analyst_prompt,
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )

    client = _get_openai_client(_OAUTH_PROXY_URL)
    model = OpenAIChatCompletionsModel(model=_CONDUCTOR_MODEL, openai_client=client)
    agent = OAIAgent(
        name="codex-diagnostic-analyst",
        instructions=analyst_prompt,
        tools=[
            list_analysis_artifacts,
            list_job_rounds,
            read_artifact,
            read_round_artifact,
            read_strategy_source,
            run_python,
        ],
        model=model,
    )

    trace(
        "CONDUCTOR",
        f"analyst dispatch focus='{focus_question[:80]}'",
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
    try:
        result = OAIRunner.run_streamed(
            agent,
            user_prompt,
            run_config=OAIRunConfig(tracing_disabled=True),
            max_turns=25,
        )
        async for _ in result.stream_events():
            pass
        output = _extract_runner_output_text(result)
        accumulate_agents_sdk_result_usage(
            "analyst",
            result,
            provider="openai",
            model=_CONDUCTOR_MODEL,
            input_text=f"{analyst_prompt}\n\n{user_prompt}",
            output_text=output,
            trace_id=current_trace_id,
        )
        parsed = _parse_json(output)
        if parsed:
            n_anomalies = len(parsed.get("key_anomalies", []))
            trace(
                "CONDUCTOR",
                f"analyst OK anomalies={n_anomalies}",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            trace_agent_response(
                "analyst",
                current_trace_id,
                output,
                parsed,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return json.dumps(parsed, indent=2)
        trace(
            "CONDUCTOR",
            f"analyst parse failed: {output[:200]}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"ANALYST ERROR: could not parse response: {output[:500]}"
    except Exception as exc:
        trace(
            "CONDUCTOR",
            f"analyst ERROR: {exc}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"ANALYST ERROR: {exc}"


async def _call_web_researcher(query: str, context: str, reflexion_feedback: str = "") -> str:
    web_prompt = """You are a research agent specializing in quantitative trading strategies.
Your ONLY job is to find and report external evidence for the specific question asked.

1. Restate the mechanism being tested in concrete causal terms before searching.
2. Run targeted web searches for evidence that SUPPORTS or FALSIFIES that exact mechanism.
3. Prefer primary sources: academic papers > practitioner research > blogs.
4. Read sources in full. Extract specific claims and data points.
5. Be skeptical. Generic market commentary is weak evidence unless you tie it directly to the mechanism.
6. For every finding, explain the actionable implication for THIS strategy and what observable should be checked in our data.
7. You must actively look for contradiction, boundary conditions, or alternative explanations.
   Do not stop after finding only supporting sources.
8. If direct falsification evidence is weak, say so explicitly and return the
   strongest adjacent contradiction or boundary-condition evidence you found.

OUTPUT FORMAT:
Return a JSON object:
{
  "summary": "2 sentence synthesis; emit this first",
  "mechanism_under_test": "one sentence restatement of the exact mechanism",
  "overall_verdict": "supports|mixed|weak|falsified",
  "strongest_support": "one sentence on the best supporting evidence, or empty string",
  "strongest_contradiction": "one sentence on the best contradiction/boundary-condition evidence, or empty string",
  "findings": [
    {
      "topic": "short label",
      "finding": "specific claim with attribution",
      "source": "URL or null",
      "source_quality": "academic/practitioner/blog/forum",
      "stance": "supports/falsifies/adjacent",
      "actionable_idea": "specific structural change this suggests",
      "data_check": "specific observable to validate in our data"
    }
  ]
}
Return ONLY the JSON object.

Do not cap discovery breadth before research; search broadly enough to find
the strongest evidence. If the response risks exceeding the output budget,
prefer the strongest findings and compact the final JSON rather than returning
invalid JSON. Use concise fields, put "summary" before "findings", and ensure
the final character is "}". If you cannot fit the full result, Return a minimal valid JSON object
with summary, overall_verdict, strongest_support, strongest_contradiction,
and the strongest one or two findings.

Minimum content rules:
- Include at least one non-supportive item:
  - a finding with stance="falsifies", or
  - a finding with stance="adjacent" that clearly states a boundary condition,
    failure mode, or alternative mechanism.
- If all sources only support the mechanism, say that explicitly in
  strongest_contradiction and explain what contradiction you failed to find."""

    user_prompt = f"RESEARCH QUESTION: {query}\n\nCONTEXT: {context}"
    if reflexion_feedback:
        user_prompt += (
            "\n\nAGENT REFLEXION FEEDBACK FROM THE PRIOR ROUND:\n"
            f"{reflexion_feedback}\n"
            "Apply this lesson to improve source selection and specificity."
        )
    trace_id = trace_agent_prompt(
        "web-researcher",
        user_prompt,
        web_prompt,
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )

    trace(
        "CONDUCTOR",
        f"web_search dispatch query='{query[:80]}' api=codex_cli_web_search",
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
    try:
        output, metadata = await asyncio.to_thread(
            run_codex_web_research,
            user_prompt,
            instructions=web_prompt,
            model=_CONDUCTOR_MODEL,
        )
        usage = metadata.get("usage")
        if isinstance(usage, dict):
            usage = {**usage, "usage_source": metadata.get("usage_source", "")}
            _accumulate_usage(
                "web_researcher",
                usage,
                provider="openai",
                model=_CONDUCTOR_MODEL,
                trace_id=trace_id,
            )
        trace(
            "CONDUCTOR",
            "web_search codex_cli completed",
            {
                "exit_code": metadata.get("exit_code"),
                "output_len": metadata.get("output_len"),
                "usage_source": metadata.get("usage_source", ""),
            },
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        parsed = _parse_json(output)
        if parsed:
            n_findings = len(parsed.get("findings", []))
            trace(
                "CONDUCTOR",
                "web_search OK",
                {"findings": n_findings},
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            trace_agent_response(
                "web-researcher",
                trace_id,
                output,
                parsed,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return json.dumps(parsed, indent=2)
        trace(
            "CONDUCTOR",
            "web_search parse failed",
            {
                "output_type": type(output).__name__,
                "output_len": len(output),
                "output_excerpt": output[:200],
            },
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: could not parse: {output[:500]}"
    except WebResearchCliError as exc:
        usage = getattr(exc, "metadata", {}).get("usage")
        if isinstance(usage, dict):
            usage = {**usage, "usage_source": getattr(exc, "metadata", {}).get("usage_source", "")}
            _accumulate_usage(
                "web_researcher",
                usage,
                provider="openai",
                model=_CONDUCTOR_MODEL,
                trace_id=trace_id,
            )
        else:
            accumulate_agents_sdk_result_usage(
                "web_researcher",
                None,
                provider="openai",
                model=_CONDUCTOR_MODEL,
                trace_id=trace_id,
            )
        trace(
            "CONDUCTOR",
            "web_search ERROR",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: {exc}"
