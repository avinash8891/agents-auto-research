"""Research round driver for autoresearch.

Drives the research-conductor loop: invoking the conductor, validating
proposed theses, compiling them, queueing multi-variant probes, logging
research-round outcomes, and translating the conductor result into the
next state for the controller.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from artifact_io import write_json_artifact
from autoresearch_constants import (
    DISCORD_BODY_MAX_CHARS,
    DISCORD_COLOR_DISCARD,
    DISCORD_COLOR_ERROR,
    DISCORD_COLOR_SUCCESS,
    DISCORD_HTTP_TIMEOUT_SECONDS,
    MAX_RESEARCH_ROUNDS,
    MAX_VALIDATION_RETRIES,
    MAX_VALIDATION_RETRIES_COMPILE,
    MAX_VALIDATION_RETRIES_STAGE_1,
    MAX_VALIDATION_RETRIES_STAGE_2,
)
from autoresearch_logging import get_logger
from autoresearch_orchestration import (
    build_missing_primitives_for_state as _orchestration_build_missing_primitives_for_state,
)
from autoresearch_paths import resolve_config_path
from autoresearch_planning import build_research_failure_state
from autoresearch_runtime_paths import research_round_root
from autoresearch_state import (
    BacktestResultRecord,
    read_state,
    write_state,
)
from backtest.runtime_config import load_runtime_config
from family_research_spec import resolve_research_resolution_context
from persistence_utils import utc_now_iso8601 as iso8601_utc_now
from persistence_utils import write_text_atomic as _write_text_atomic
from research_memory import latest_thesis_details as _latest_thesis_details
from research_types import ConductorResult, ResearchThesis
from strategy_family import StrategyFamily
from trace_adapters import emit_halo_event, emit_recursive_improve_event, emit_reflexio_event
from trace_adapters.halo import build_halo_export_package, build_halo_payload
from trace_adapters.recursive_improve import (
    build_recursive_improve_export_package,
    build_recursive_improve_payload,
)
from trace_adapters.reflexio import build_reflexio_export_package
from trace_quality_history import QualityHistory
from trace_rule_proposals import RuleProposalRegistry
from trace_sdk import (
    begin_hypothesis,
    end_hypothesis,
    get_event_file,
    record_event,
    trace,
)

if TYPE_CHECKING:
    from autoresearch_controller import AutoresearchController

log = get_logger(__name__)
_QUALITY_HISTORY = QualityHistory()
_RULE_PROPOSALS = RuleProposalRegistry()


def _record_event_fail_open(**kwargs: Any) -> None:
    try:
        record_event(**kwargs)
    except Exception as exc:
        log.debug("trace event emission failed: %s", exc)


def _prepare_thesis_for_validation(
    thesis: dict[str, Any],
    *,
    strategy_family: str,
    prior_theses: list[dict[str, Any]] | None = None,
    allow_schema_only_code_change_fallback: bool = False,
):
    from compiler_pipeline import operationalize_thesis
    from thesis_validator import (
        ThesisValidationError,
        normalize_thesis_payload,
        validate_thesis_dict,
    )

    raw_thesis = dict(thesis)
    raw_thesis["strategy_family"] = strategy_family
    if raw_thesis.get("requires_code_change") and not raw_thesis.get("requested_primitives"):
        operationalized = operationalize_thesis(dict(raw_thesis))
        missing = operationalized.get("missing_primitives") or []
        if missing and not operationalized.get("requested_primitives"):
            operationalized["requested_primitives"] = missing
        raw_thesis = operationalized
    try:
        validated = validate_thesis_dict(raw_thesis, prior_theses=prior_theses)
    except ThesisValidationError:
        if not (allow_schema_only_code_change_fallback and raw_thesis.get("requires_code_change")):
            raise
        validated = ResearchThesis.model_validate(normalize_thesis_payload(raw_thesis))
    return raw_thesis, validated


# ── Discord notification ──────────────────────────────────────────


def notify_discord(
    title: str, body: str, *, webhook: str = "", color: int = DISCORD_COLOR_ERROR
) -> None:
    """Send a Discord embed notification. Fire-and-forget, never raises."""
    if not webhook:
        return
    try:
        payload = json.dumps(
            {
                "embeds": [
                    {
                        "title": title,
                        "description": body[:DISCORD_BODY_MAX_CHARS],
                        "color": color,
                    }
                ]
            }
        ).encode()
        req = urllib.request.Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "AutoresearchBot/1.0"},
            method="POST",
        )
        # webhook URL comes from AUTORESEARCH_DISCORD_WEBHOOK_<FAMILY>
        # env var (rule 2). The operator controls the env, so url-scheme
        # exposure is bounded by the deployment surface, not user input.
        urllib.request.urlopen(
            req, timeout=DISCORD_HTTP_TIMEOUT_SECONDS
        )  # noqa: S310  # nosec B310
        trace("DISCORD", f"OK title='{title[:60]}'")
    except (urllib.error.URLError, OSError) as exc:
        trace("DISCORD", f"FAILED title='{title[:60]}' error={exc}")


# ── Persistence + state mutators ─────────────────────────────────


def accumulate_job_usage(state_path: Path, round_usage: dict[str, Any]) -> None:
    """Accumulate round token usage into job totals in state JSON."""
    state = read_state(state_path)
    job_usage = state.get("job_usage") or {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
        "estimated_total_tokens": 0,
        "cost_usd": 0.0,
        "rounds": 0,
    }
    total = round_usage.get("total", {})
    job_usage["input_tokens"] += total.get("input_tokens", 0)
    job_usage["output_tokens"] += total.get("output_tokens", 0)
    job_usage["total_tokens"] += total.get("total_tokens", 0)
    job_usage["cached_input_tokens"] = job_usage.get("cached_input_tokens", 0) + total.get(
        "cached_input_tokens", 0
    )
    job_usage["estimated_input_tokens"] = job_usage.get("estimated_input_tokens", 0) + total.get(
        "estimated_input_tokens", 0
    )
    job_usage["estimated_output_tokens"] = job_usage.get("estimated_output_tokens", 0) + total.get(
        "estimated_output_tokens", 0
    )
    job_usage["estimated_total_tokens"] = job_usage.get("estimated_total_tokens", 0) + total.get(
        "estimated_total_tokens", 0
    )
    job_usage["cost_usd"] += total.get("cost_usd", 0.0)
    job_usage["rounds"] += 1
    state["job_usage"] = job_usage
    write_state(state_path, state)


def log_research_round(
    db_path: Path,
    state_path: Path,
    *,
    round_number: int,
    thesis_id: str,
    hypothesis_id: str = "",
    outcome: str,
    config_changes: dict[str, Any] | None = None,
    hypothesis: str = "",
    mechanism: str = "",
    mechanism_dimension: str = "",
    thesis_details: dict[str, Any] | None = None,
    validation_failure_reason: str = "",
    usage: dict[str, Any] | None = None,
) -> None:
    """Log every research round outcome to canonical persistence."""
    from backtest_run_db import BacktestRunDB

    db = BacktestRunDB(db_path)
    state = read_state(state_path)
    attempt_number = 1
    if outcome.startswith("rejected_attempt_"):
        try:
            attempt_number = int(outcome.rsplit("_", 1)[-1])
        except ValueError:
            attempt_number = 1
    db.log_research_round(
        state_path,
        round_number=round_number,
        thesis_id=thesis_id,
        hypothesis_id=hypothesis_id or thesis_id,
        outcome=outcome,
        usage=usage,
    )
    db.add_research_thesis_attempt(
        {
            "research_round_id": f"job-{state.get('job', 0)}-round-{round_number}",
            "attempt_number": attempt_number,
            "thesis_id": thesis_id,
            "strategy_family": state.get("family", ""),
            "config_changes": config_changes or {},
            "validator_status": outcome,
            "mechanism_dimension": mechanism_dimension,
            "hypothesis": hypothesis,
            "mechanism": mechanism,
            "thesis_details": thesis_details or {},
            "validation_failure_reason": validation_failure_reason,
            "selected_for_execution": 1 if outcome == "compiled" else 0,
            "created_at_utc": iso8601_utc_now(),
        }
    )


# ── Pure helpers ──────────────────────────────────────────────────


def results_to_dicts(results: list[BacktestResultRecord]) -> list[dict[str, Any]]:
    """Convert ExperimentRecords to plain dicts for formatting."""
    result_dicts: list[dict[str, Any]] = []
    for r in results:
        d: dict[str, Any] = {
            "config": r.config,
            "metric": r.metric,
            "status": r.status,
            "description": r.description,
            "job": r.job,
        }
        ta = r.asi.get("trade_analysis", {})
        if ta:
            for key in (
                "trade_count",
                "profit_factor",
                "max_drawdown",
                "pct_profitable_windows",
                "avg_sharpe_across_windows",
                "win_rate",
                "exit_mix",
                "regime_expectancy",
                "why",
                "regime_insight",
                "trade_count_insight",
                "mechanism_analysis",
            ):
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


def load_baseline_config(root: Path, family: StrategyFamily) -> dict[str, Any] | None:
    """Load the baseline config for variant generation."""
    base_path = root / "configs" / family.base_config_filename
    if not base_path.exists():
        return None
    try:
        return yaml.safe_load(base_path.read_text())
    except (yaml.YAMLError, OSError) as exc:
        log.error(
            f"BASELINE_CONFIG_LOAD_FAILED path={base_path}: {exc} "
            f"| hint=fix the family baseline config before generating variants"
        )
        raise ValueError(f"BASELINE_CONFIG_LOAD_FAILED path={base_path}: {exc}") from exc


def _resolve_runtime_config_for_record(
    controller: "AutoresearchController", latest: BacktestResultRecord
) -> dict[str, Any]:
    config_path = resolve_config_path(
        latest.config,
        code_root=controller.root,
        runtime_root=controller.runtime_root,
        execution_root=controller.ctx.execution_root,
    )
    try:
        return load_runtime_config(str(config_path), controller.family.name)
    except Exception as exc:
        trace(
            "LOOP",
            f"runtime_config_resolution_failed path={config_path} error={exc.__class__.__name__}",
        )
        log.warning("RUNTIME_CONFIG_RESOLUTION_FAILED path=%s error=%s", config_path, exc)
        return {}


def queue_variants(
    root: Path,
    run_queue_dir: Path,
    variants: list[dict[str, Any]],
    thesis: Any,  # ResearchThesis
    primary_contract: Any,  # BacktestContract
    baseline_config: dict[str, Any],
    *,
    experiments_dir: Path | None = None,
    job: int | None = None,
    created_for_commit: str = "",
) -> None:
    raise RuntimeError(
        "variant backtest queueing is no longer supported; each research round may select at most one backtest"
    )


# ── Conductor invocation ──────────────────────────────────────────


def _backfill_artifact_files_from_latest_dir(
    controller: "AutoresearchController",
    latest: BacktestResultRecord,
    trades_file: str,
    strategy_events_file: str,
    diagnostics_file: str,
) -> tuple[str, str, str]:
    """If ctx didn't carry the latest run's artifact files, look for them
    inside the latest result's artifact_dir on disk."""
    artifact_dir_raw = str((latest.asi or {}).get("artifact_dir") or "")
    if not artifact_dir_raw:
        return trades_file, strategy_events_file, diagnostics_file
    root = controller.root.resolve()
    artifact_dir = Path(artifact_dir_raw)
    if not artifact_dir.is_absolute():
        artifact_dir = root / artifact_dir
    try:
        artifact_dir = artifact_dir.resolve()
    except OSError:
        return trades_file, strategy_events_file, diagnostics_file
    if not artifact_dir.is_relative_to(root):
        return trades_file, strategy_events_file, diagnostics_file
    if not artifact_dir.exists():
        return trades_file, strategy_events_file, diagnostics_file
    if not trades_file:
        csvs = list(artifact_dir.glob("*trades.csv"))
        if csvs:
            trades_file = str(csvs[0])
    if not strategy_events_file:
        events_files = list(artifact_dir.glob("*strategy_events.parquet")) or list(
            artifact_dir.glob("*strategy_events.csv")
        )
        if events_files:
            preferred_prefix = f"{latest.config}."
            preferred_stem = f"{Path(latest.config).stem}."
            preferred = [
                path
                for path in events_files
                if path.name.startswith(preferred_prefix) or path.name.startswith(preferred_stem)
            ]
            strategy_events_file = str((preferred or events_files)[0])
    if not diagnostics_file:
        diag_jsons = list(artifact_dir.glob("*diagnostics.json"))
        if diag_jsons:
            diagnostics_file = str(diag_jsons[0])
    return trades_file, strategy_events_file, diagnostics_file


def _existing_artifact_file(controller: "AutoresearchController", raw_path: Any) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        return ""
    root = controller.root.resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return ""
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return ""
    return str(candidate)


def _artifact_files_from_latest_record(
    controller: "AutoresearchController",
    latest: BacktestResultRecord,
    trades_file: str,
    strategy_events_file: str,
    diagnostics_file: str,
) -> tuple[str, str, str]:
    """Use canonical persisted file paths before falling back to directory scans."""
    asi = latest.asi or {}
    if not trades_file:
        trades_file = _existing_artifact_file(controller, asi.get("trades_file"))
    if not strategy_events_file:
        strategy_events_file = _existing_artifact_file(controller, asi.get("strategy_events_file"))
    if not diagnostics_file:
        diagnostics_file = _existing_artifact_file(controller, asi.get("diagnostics_file"))
    return trades_file, strategy_events_file, diagnostics_file


def _resolve_conductor_inputs(
    controller: "AutoresearchController",
    results: list[BacktestResultRecord],
    *,
    current_job: int | None = None,
) -> tuple[str, str, str, dict[str, Any]]:
    """Gather the four inputs the conductor needs from the most recent
    experiment: trades file, strategy events file, diagnostics file, and
    a small `latest_outcome` dict the prompt templates use."""
    if current_job is None:
        trades_file = controller.ctx.latest_trades_file
        strategy_events_file = controller.ctx.latest_strategy_events_file
        diagnostics_file = controller.ctx.latest_diagnostics_file
    else:
        trades_file = ""
        strategy_events_file = ""
        diagnostics_file = ""
    scoped_results = results
    if current_job is not None:
        scoped_results = [result for result in results if result.job == current_job]
    latest = controller.latest_result(scoped_results)
    if latest and (not trades_file or not strategy_events_file or not diagnostics_file):
        trades_file, strategy_events_file, diagnostics_file = _artifact_files_from_latest_record(
            controller, latest, trades_file, strategy_events_file, diagnostics_file
        )
    if latest and (not trades_file or not strategy_events_file or not diagnostics_file):
        trades_file, strategy_events_file, diagnostics_file = (
            _backfill_artifact_files_from_latest_dir(
                controller, latest, trades_file, strategy_events_file, diagnostics_file
            )
        )
    latest_outcome: dict[str, Any] = {}
    if latest:
        latest_outcome["thesis_id"] = latest.asi.get("thesis_id") or Path(latest.config).parent.name
        latest_outcome["metric"] = latest.metric
        latest_outcome["decision"] = latest.status
        latest_outcome["config_path"] = latest.config
        runtime_config = _resolve_runtime_config_for_record(controller, latest)
        latest_outcome["resolution_context"] = resolve_research_resolution_context(
            controller.family.name,
            runtime_config,
        )
        ta = latest.asi.get("trade_analysis", {})
        for key in (
            "trade_count",
            "profit_factor",
            "max_drawdown",
            "pct_profitable_windows",
            "avg_sharpe_across_windows",
        ):
            if ta.get(key) is not None:
                latest_outcome[key] = ta[key]
        verdict = ta.get("verdict")
        if isinstance(verdict, dict):
            verdict_status = verdict.get("status")
            verdict_summary = verdict.get("summary")
            if verdict_status is not None:
                verdict_status = str(verdict_status)
                latest_outcome["verdict_status"] = verdict_status
            if verdict_summary is not None:
                verdict_summary = str(verdict_summary)
                latest_outcome["verdict_summary"] = verdict_summary
            if verdict_status and verdict_summary:
                latest_outcome["research_feedback"] = _research_feedback_from_verdict(
                    verdict_status,
                    verdict_summary,
                )
    if latest_outcome:
        thesis_id = latest_outcome.get("thesis_id")
        if thesis_id:
            prior = _latest_thesis_details(controller.runtime_root, str(thesis_id))
            if prior:
                latest_outcome["previous_thesis"] = prior
    return trades_file, strategy_events_file, diagnostics_file, latest_outcome


def _research_feedback_from_verdict(verdict_status: str, verdict_summary: str) -> str:
    prefix = f"{verdict_status}:"
    verdict_summary = verdict_summary.rstrip()
    terminal = "" if verdict_summary.endswith((".", "!", "?")) else "."
    if verdict_summary.startswith(prefix):
        feedback = f"Previous candidate was {verdict_summary}{terminal}"
    else:
        feedback = f"Previous candidate was {verdict_status}: {verdict_summary}{terminal}"
    if verdict_status == "invalid_noop_config":
        feedback += (
            " If this was a threshold/gating thesis, revise the threshold so it changes "
            "behavior or abandon the mechanism."
        )
    return feedback


def _check_parsed_for_terminal(result: ConductorResult | None) -> dict[str, Any] | None:
    """Inspect the conductor result for terminal conditions before thesis validation.

    Returns an outer result dict to short-circuit the validation-retry loop, or None
    to continue to validation.
    """
    if result is None:
        return {
            "status": "parse_failed",
            "generated_config": None,
            "should_stop": False,
            "validation_failure_reason": "research conductor returned no parseable thesis",
        }
    if result.status == "conductor_error":
        error = result.error or result.reasoning or "unknown conductor error"
        validation_failure_reason = f"research conductor failed: {error}"
        if result.validation_reason:
            validation_failure_reason = f"{validation_failure_reason}: {result.validation_reason}"
        outer: dict[str, Any] = {
            "status": "conductor_error",
            "generated_config": None,
            "should_stop": False,
            "validation_failure_reason": validation_failure_reason,
        }
        if result.validation_reason:
            outer["validation_reason"] = result.validation_reason
        _record_event_fail_open(
            source_module="autoresearch_research",
            category="conductor",
            action="conductor_error",
            summary=validation_failure_reason,
            payload={
                "error_code": "conductor_error",
                "error": str(error),
                "validation_reason": result.validation_reason,
            },
        )
        return outer
    if result.status == "should_stop":
        reasoning = result.reasoning or "research conductor recommends stopping"
        return {
            "status": "completed",
            "generated_config": None,
            "should_stop": True,
            "reasoning": reasoning,
        }
    return None


def _structured_validation_failure(*, source: str, message: str) -> dict[str, str]:
    """Normalize free-form rejection text into stable routing/trace metadata."""
    lower = message.lower()
    if "overlap" in lower or "duplicate" in lower:
        code = f"{source}_duplicate_or_overlap"
    elif "unsupported" in lower or "not allowed" in lower or "unknown" in lower:
        code = f"{source}_unsupported_field"
    elif "missing" in lower or "required" in lower:
        code = f"{source}_missing_required_field"
    elif "invalid" in lower:
        code = f"{source}_invalid_payload"
    else:
        code = f"{source}_rejected"
    return {
        "source": source,
        "code": code,
        "message": message,
    }


def _log_validation_rejection(
    controller: "AutoresearchController",
    research_round: int,
    attempt: int,
    raw_thesis: dict[str, Any],
    thesis_id: str,
    reason: str,
    *,
    exc: Exception | None = None,
    stage: str = "stage_1",
) -> None:
    rejection_feedback = f"Thesis '{thesis_id}' rejected by validator: {reason}"
    structured_rejection = _structured_validation_failure(
        source="validator",
        message=reason,
    )
    # Persist a machine-readable rejection.json next to the thesis. Survives
    # process restart; read by per-round prompt and rejection-pattern tools.
    if exc is not None and thesis_id:
        try:
            from rejection_artifact import persist_rejection

            state = controller.read_state()
            job_value = state.get("job") if isinstance(state, dict) else None
            if job_value is not None:
                persist_rejection(
                    controller.root,
                    job=int(job_value),
                    round_number=research_round,
                    thesis_id=thesis_id,
                    stage=stage,  # type: ignore[arg-type]
                    exc=exc,
                )
        except Exception as persist_exc:  # noqa: BLE001
            # Persistence is best-effort; never block the validation flow on a
            # filesystem error. Existing event logging still records the rejection.
            log.warning(f"failed to persist rejection.json: {persist_exc}")
    log.warning(f"THESIS REJECTED (will retry with feedback): {rejection_feedback}")
    trace("LOOP", f"thesis rejected, retrying: {rejection_feedback}")
    _record_event_fail_open(
        source_module="autoresearch_research",
        category="validation",
        action="validation_error",
        summary=rejection_feedback,
        payload={
            "error_code": structured_rejection["code"],
            "rejection": structured_rejection,
            "research_round": research_round,
            "attempt": attempt + 1,
            "thesis_id": thesis_id,
            "reason": reason,
        },
    )
    controller.log_research_round(
        round_number=research_round,
        thesis_id=thesis_id,
        hypothesis_id=thesis_id,
        outcome=f"rejected_attempt_{attempt+1}",
        config_changes=raw_thesis.get("config_changes"),
        hypothesis=raw_thesis.get("hypothesis", ""),
        mechanism=raw_thesis.get("mechanism", ""),
        mechanism_dimension=raw_thesis.get("mechanism_dimension", ""),
        thesis_details={
            key: raw_thesis.get(key)
            for key in (
                "dimension_novelty",
                "evidence",
                "expected_effects",
                "disqualifiers",
                "why_not_overfit",
                "requires_code_change",
                "required_diagnostics",
                "required_diagnostic_specs",
                "new_dimension_name",
                "why_existing_dimensions_do_not_fit",
                "mechanism_family_definition",
                "expected_reuse_across_future_theses",
            )
            if key in raw_thesis
        }
        | {"structured_rejection": structured_rejection},
        validation_failure_reason=reason,
    )
    _RULE_PROPOSALS.create_proposal(
        title=f"Round {research_round} rejected thesis {thesis_id}",
        rationale=reason,
        evidence_event_ids=[],
        expected_impact="reduce repeated validator failures",
        proposed_rule=(
            f"Reject or revise theses matching validator failure pattern for {thesis_id}"
        ),
    )


def _on_ready_to_run(
    controller: "AutoresearchController",
    research_round: int,
    contract: Any,
    raw_thesis: dict[str, Any],
    thesis_id: str,
    conductor_result: "ConductorResult",
    should_stop: bool,
) -> dict[str, Any]:
    """Wire the selected round thesis into the controller."""
    runtime_root = getattr(controller, "runtime_root", None) or controller.root
    round_root = research_round_root(
        runtime_root, int(controller.read_state().get("job")), research_round
    )
    config_path = (round_root / "selected_config.json").relative_to(runtime_root).as_posix()
    controller.ctx.current_contract = contract
    latest_db = controller.backtest_run_db.latest(1)
    controller.ctx.parent_backtest_run_id = latest_db[0].run_id if latest_db else ""
    return {
        "status": "completed",
        "generated_config": config_path,
        "generated_config_needs_build": False,
        "generated_thesis_id": thesis_id,
        "contract_id": contract.contract_id,
        "thesis_id": thesis_id,
        "thesis": raw_thesis,
        "should_stop": should_stop,
        "reasoning": conductor_result.reasoning,
    }


def _per_stage_budget_exhausted(stage_1: int, stage_2: int, compile_n: int) -> bool:
    """Return True if any stage's per-failure counter has hit its budget."""
    return (
        stage_1 >= MAX_VALIDATION_RETRIES_STAGE_1
        or stage_2 >= MAX_VALIDATION_RETRIES_STAGE_2
        or compile_n >= MAX_VALIDATION_RETRIES_COMPILE
    )


def _try_one_validation_attempt(
    controller: "AutoresearchController",
    research_round: int,
    attempt: int,
    conductor_result: ConductorResult,
    prior_theses: Any,
) -> tuple[dict[str, Any] | None, str | None, str]:
    """One pass of the conductor-validate-compile retry loop.

    Returns (result, retry_feedback, failed_stage).
    - `result` is not None on success or terminal state.
    - `retry_feedback` is set on failure for the next conductor call.
    - `failed_stage` is "stage_1" / "stage_2" / "compile" on failure, "" on success.
    """
    from compiler_pipeline import compile_research_thesis
    from thesis_validator import ThesisValidationError, validate_stage_2

    assert conductor_result.thesis is not None
    raw_thesis = conductor_result.thesis
    thesis_id = raw_thesis.get("thesis_id", "unknown")

    # If the conductor attached a validator_challenge, persist it before any
    # validation work. Logged for human review; does not alter the decision.
    challenge_payload = raw_thesis.get("validator_challenge")
    if isinstance(challenge_payload, dict):
        try:
            from rejection_artifact import write_challenge

            state = controller.read_state()
            job_value = state.get("job") if isinstance(state, dict) else None
            if job_value is not None:
                write_challenge(controller.root, job=int(job_value), payload=challenge_payload)
        except Exception as challenge_exc:  # noqa: BLE001
            log.warning(f"failed to persist validator_challenge: {challenge_exc}")

    # Stage 1: structural / pre-compile validation.
    try:
        raw_thesis, validated = _prepare_thesis_for_validation(
            raw_thesis,
            strategy_family=controller.family.name,
            prior_theses=prior_theses,
        )
        thesis_id = raw_thesis.get("thesis_id", "unknown")
        log.info(
            f"RESEARCH_RAW thesis_id={thesis_id} "
            f"config_changes={json.dumps(raw_thesis.get('config_changes', 'MISSING'))}"
        )
    except (ThesisValidationError, ValueError) as exc:
        _log_validation_rejection(
            controller,
            research_round,
            attempt,
            raw_thesis,
            thesis_id,
            str(exc),
            exc=exc,
            stage="stage_1",
        )
        return None, f"Thesis '{thesis_id}' rejected by validator: {exc}", "stage_1"

    # Compile.
    try:
        round_root = research_round_root(
            controller.root, int(controller.read_state().get("job")), research_round
        )
        contract = compile_research_thesis(validated, controller.root, artifact_root=round_root)
    except (ThesisValidationError, ValueError) as exc:
        _log_validation_rejection(
            controller,
            research_round,
            attempt,
            raw_thesis,
            thesis_id,
            str(exc),
            exc=exc,
            stage="compile",
        )
        return None, f"Thesis '{thesis_id}' rejected at compile: {exc}", "compile"

    # Stage 2: post-compile semantic rules.
    try:
        contract = validate_stage_2(contract)
    except (ThesisValidationError, ValueError) as exc:
        _log_validation_rejection(
            controller,
            research_round,
            attempt,
            raw_thesis,
            thesis_id,
            str(exc),
            exc=exc,
            stage="stage_2",
        )
        return None, f"Thesis '{thesis_id}' rejected by stage_2 validator: {exc}", "stage_2"

    result, feedback = _dispatch_compiled_contract(
        controller,
        research_round,
        attempt,
        conductor_result,
        raw_thesis,
        validated,
        contract,
        thesis_id,
    )
    # _dispatch_compiled_contract handles the contract-status-not-ready case as
    # a "compile" rejection.
    return result, feedback, "" if result is not None else "compile"


def _dispatch_compiled_contract(
    controller: "AutoresearchController",
    research_round: int,
    attempt: int,
    conductor_result: "ConductorResult",
    raw_thesis: dict[str, Any],
    validated: Any,
    contract: Any,
    thesis_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    should_stop = conductor_result.should_stop
    if contract.status == "needs_code":
        return {
            "status": "completed",
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": thesis_id,
            "thesis_id": thesis_id,
            "should_stop": should_stop,
            "reasoning": conductor_result.reasoning,
            "thesis": raw_thesis,
        }, None
    if contract.status == "ready_to_run":
        return (
            _on_ready_to_run(
                controller,
                research_round,
                contract,
                raw_thesis,
                thesis_id,
                conductor_result,
                should_stop,
            ),
            None,
        )
    feedback = (
        f"Thesis '{thesis_id}' rejected: status={contract.status}, "
        f"missing={contract.missing_primitives}"
    )
    # Construct a synthetic exception so persistence still records a structured
    # rejection at the compile boundary. The compile rejection_code is explicit.
    compile_exc = ValueError(feedback)
    setattr(compile_exc, "rejection_code", "compile_rejected")
    setattr(
        compile_exc,
        "evidence",
        {
            "contract_status": contract.status,
            "missing_primitives": list(contract.missing_primitives),
        },
    )
    _log_validation_rejection(
        controller,
        research_round,
        attempt,
        raw_thesis,
        thesis_id,
        feedback,
        exc=compile_exc,
        stage="compile",
    )
    return None, feedback


def _exhausted_retries_result(
    conductor_result: ConductorResult | None, rejection_feedback: str
) -> dict[str, Any]:
    thesis_id = (
        conductor_result.thesis.get("thesis_id", "unknown")
        if conductor_result and conductor_result.thesis
        else "unknown"
    )
    log.error(
        f"THESIS REJECTED after {MAX_VALIDATION_RETRIES} attempts: {rejection_feedback} "
        f"| hint=the conductor produced a thesis that failed validation 3 times in a row; "
        f"review the validation_failure_reason above and refine the conductor system prompt or "
        f"add a counterexample to thesis_validator.load_prior_theses"
    )
    trace(
        "LOOP",
        f"thesis rejected after {MAX_VALIDATION_RETRIES} attempts: {rejection_feedback}",
    )
    return {
        "status": "thesis_rejected",
        "generated_config": None,
        "generated_config_needs_build": False,
        "generated_thesis_id": thesis_id,
        "validation_failure_reason": rejection_feedback,
        "should_stop": False,
        "reasoning": conductor_result.reasoning if conductor_result else "",
    }


def _call_conductor(
    research_round: int,
    attempt: int,
    *,
    trades_file: str,
    strategy_events_file: str,
    diagnostics_file: str,
    experiment_results: Any,
    latest_outcome: dict[str, Any],
    family_name: str,
    rejection_feedback: str,
    agent_reflexions: dict[str, str] | None = None,
    current_job: int | None = None,
) -> ConductorResult | None:
    """One conductor HTTP/SDK call with the per-attempt log preamble."""
    from research_conductor import run_research_conductor_sync

    label = f"round={research_round}" + (
        f" attempt={attempt+1} (retry with feedback)" if attempt else ""
    )
    boundary = (
        f"INPUT_BOUNDARY job={current_job} round={research_round} attempt={attempt + 1} "
        f"family={family_name} trades={'YES' if trades_file else 'NO'} "
        f"events={'YES' if strategy_events_file else 'NO'} "
        f"diagnostics={'YES' if diagnostics_file else 'NO'} "
        f"rejection_feedback={'YES' if rejection_feedback else 'NO'}"
    )
    log.info(f"CONDUCTOR {boundary}")
    log.info(f"CONDUCTOR starting {label} trades={'YES' if trades_file else 'NO'}")
    trace("CONDUCTOR", boundary)
    trace("CONDUCTOR", f"START {label}")
    return run_research_conductor_sync(
        trades_file=trades_file,
        experiment_results=experiment_results,
        latest_outcome=latest_outcome,
        research_round=research_round,
        family_name=family_name,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        rejection_feedback=rejection_feedback,
        agent_reflexions=agent_reflexions,
        current_job=current_job,
    )


def execute_research_sdk(controller: "AutoresearchController") -> dict[str, Any]:
    """Drive research using the research conductor.

    Calls the conductor, validates the proposed thesis, compiles it.
    If validation rejects the thesis, calls the conductor AGAIN with
    the rejection reason so it can propose something different.
    """
    from agent_formatters import format_experiment_results_summary
    from thesis_validator import load_prior_theses

    state = controller.read_state()
    raw_job = state.get("job")
    try:
        current_job = int(raw_job) if raw_job is not None else None
    except (TypeError, ValueError):
        current_job = None
    active_round = state.get("research_round_in_progress")
    try:
        research_round = int(active_round) if active_round is not None else None
    except (TypeError, ValueError):
        research_round = None
    if research_round is None or research_round < 1:
        research_round = state.get("research_round", 0) + 1
    results = controller.read_results()
    result_dicts = results_to_dicts(results)
    if current_job is not None:
        result_dicts = [result for result in result_dicts if result.get("job") == current_job]
    experiment_results = format_experiment_results_summary(result_dicts)
    prior_theses = load_prior_theses(controller.root)
    trace("LOOP", f"loaded {len(prior_theses)} prior theses for overlap detection")
    trades_file, strategy_events_file, diagnostics_file, latest_outcome = _resolve_conductor_inputs(
        controller,
        results,
        current_job=current_job,
    )
    state["research_round_in_progress"] = research_round
    state["activity"] = _research_activity(research_round=research_round, phase="conductor_running")
    controller.write_state(state)

    from improvement_flags import reflexion_enabled

    rejection_feedback = str(state.get("rejection_feedback") or "")
    agent_reflexions: dict[str, str] = {}
    if reflexion_enabled():
        from improvement_reflexion import build_reflexion_feedback

        rejection_feedback = build_reflexion_feedback(controller, research_round, agent="conductor")
        agent_reflexions = {
            agent: feedback
            for agent in ("analyst", "web-researcher")
            if (feedback := build_reflexion_feedback(controller, research_round, agent=agent))
        }
    conductor_result: ConductorResult | None = None
    # Per-stage failure counters. Loop exits when any stage's budget is hit.
    stage_1_failures = 0
    stage_2_failures = 0
    compile_failures = 0
    attempt = 0
    while not _per_stage_budget_exhausted(stage_1_failures, stage_2_failures, compile_failures):
        conductor_result = _call_conductor(
            research_round,
            attempt,
            trades_file=trades_file,
            strategy_events_file=strategy_events_file,
            diagnostics_file=diagnostics_file,
            experiment_results=experiment_results,
            latest_outcome=latest_outcome,
            family_name=controller.family.name,
            rejection_feedback=rejection_feedback,
            agent_reflexions=agent_reflexions,
            current_job=current_job,
        )
        terminal = _check_parsed_for_terminal(conductor_result)
        if terminal is not None:
            return terminal
        result, retry_feedback, failed_stage = _try_one_validation_attempt(
            controller, research_round, attempt, conductor_result, prior_theses
        )
        if result is not None:
            return result
        if failed_stage == "stage_1":
            stage_1_failures += 1
        elif failed_stage == "stage_2":
            stage_2_failures += 1
        elif failed_stage == "compile":
            compile_failures += 1
        rejection_feedback = retry_feedback or rejection_feedback
        attempt += 1
    return _exhausted_retries_result(conductor_result, rejection_feedback)


def execute_research_one(controller: "AutoresearchController") -> dict[str, Any]:
    """Drive research using SDK agents."""
    return execute_research_sdk(controller)


# ── Round orchestration ──────────────────────────────────────────


def _research_activity(*, research_round: int, phase: str) -> dict[str, Any]:
    return {"type": "research", "phase": phase, "round": research_round}


def _thesis_meta_from_result(result: dict[str, Any], family_name: str) -> dict[str, Any]:
    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
    thesis_meta = result.get("thesis")
    if isinstance(thesis_meta, dict):
        return thesis_meta
    return {
        "thesis_id": thesis_id,
        "strategy_family": family_name,
        "config_changes": result.get("config_changes") or {},
        "hypothesis": result.get("hypothesis") or result.get("reasoning", ""),
        "mechanism": result.get("mechanism") or result.get("reasoning", ""),
        "mechanism_dimension": result.get("mechanism_dimension") or "",
    }


def _thesis_quality_dimension_scores(thesis_meta: dict[str, Any]) -> dict[str, float]:
    if not thesis_meta:
        return {}

    closest_prior = thesis_meta.get("closest_prior_theses_considered")
    if not isinstance(closest_prior, list):
        closest_prior = []
    requested_primitives = thesis_meta.get("requested_primitives")
    if not isinstance(requested_primitives, list):
        requested_primitives = []

    orthogonality_defense = str(thesis_meta.get("orthogonality_defense") or "").strip()
    evidence_strength = str(thesis_meta.get("evidence_strength") or "").strip()
    thesis_role = str(thesis_meta.get("thesis_role") or "").strip()
    falsification = str(thesis_meta.get("falsification_or_alternative") or "").strip()
    requires_code_change = bool(thesis_meta.get("requires_code_change"))

    dimension_scores = {
        "prior_comparison": 1.0 if closest_prior else 0.0,
        "orthogonality_defense": 1.0 if orthogonality_defense else 0.0,
        "evidence_strength_labeled": 1.0 if evidence_strength else 0.0,
        "falsification_discipline": 1.0 if falsification else 0.0,
        "thesis_role_labeled": 1.0 if thesis_role else 0.0,
    }
    if thesis_role == "winning_cluster_follow_up":
        dimension_scores["follow_up_honesty"] = 1.0 if orthogonality_defense else 0.0
    elif thesis_role:
        dimension_scores["follow_up_honesty"] = 1.0
    if requires_code_change:
        dimension_scores["code_change_contract"] = 1.0 if requested_primitives else 0.0
    return dimension_scores


def _classify_round_outcome(result: dict[str, Any]) -> str:
    from eval_metrics import (
        OUTCOME_COMPILED,
        OUTCOME_CONDUCTOR_ERROR,
        OUTCOME_NEEDS_CODE,
        OUTCOME_REJECTED,
        OUTCOME_STOPPED,
    )

    if result.get("should_stop"):
        return OUTCOME_STOPPED
    if result.get("generated_config_needs_build"):
        return OUTCOME_NEEDS_CODE
    if result.get("generated_config"):
        return OUTCOME_COMPILED
    if result.get("validation_failure_reason"):
        return OUTCOME_REJECTED
    return OUTCOME_CONDUCTOR_ERROR


def _record_round_quality_and_bridges(
    controller: "AutoresearchController",
    research_round: int,
    result: dict[str, Any],
    round_usage: dict[str, Any],
) -> None:
    outcome = _classify_round_outcome(result)
    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
    thesis_meta = _thesis_meta_from_result(result, controller.family.name)
    reasoning = result.get("reasoning", "")
    validation_failure_reason = result.get("validation_failure_reason", "")
    dimension_scores = {
        k: 1.0 if k == outcome else 0.0
        for k in ("compiled", "needs_code", "stopped", "rejected", "conductor_error")
    }
    dimension_scores.update(_thesis_quality_dimension_scores(thesis_meta))
    overall_score = 1.0 if outcome in {"compiled", "stopped"} else 0.0
    artifact_paths = []
    if result.get("generated_config"):
        artifact_paths.append(str(controller.root / result["generated_config"]))
    quality_event = _QUALITY_HISTORY.append_run(
        summary=f"research round {research_round} outcome={outcome}",
        run_label=f"round-{research_round}",
        dimension_scores=dimension_scores,
        overall_score=overall_score,
        artifact_paths=artifact_paths,
    )
    payload_kwargs = {
        "research_round": research_round,
        "thesis_id": thesis_id,
        "outcome": outcome,
        "family": controller.family.name,
        "reasoning": reasoning,
        "validation_failure_reason": validation_failure_reason,
        "usage": round_usage,
        "quality": quality_event,
    }
    canonical_trace_path = get_event_file()
    reflexio_package = build_reflexio_export_package(
        **payload_kwargs,
        canonical_trace_path=canonical_trace_path,
    )
    for emit_fn, build_fn, label in [
        (emit_halo_event, build_halo_payload, "HALO"),
        (emit_recursive_improve_event, build_recursive_improve_payload, "recursive improve"),
    ]:
        emit_fn(
            action="research_round",
            summary=f"{label} round {research_round}",
            payload=build_fn(**payload_kwargs),
        )
    emit_reflexio_event(
        action="research_round",
        summary=f"reflexio round {research_round}",
        payload=reflexio_package["files"]["reflexio-event.json"],
    )
    state = controller.read_state()
    runtime_root = getattr(controller, "runtime_root", None) or controller.root
    round_root = research_round_root(runtime_root, int(state.get("job")), research_round)
    _write_adapter_exports(round_root, **payload_kwargs)


def _write_export_package(export_root: Path, directory_name: str, package: dict[str, Any]) -> None:
    target_dir = export_root / directory_name
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in package.get("files", {}).items():
        _write_text_atomic(
            target_dir / filename,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )
    _write_text_atomic(
        target_dir / "package.json",
        json.dumps(package, indent=2, sort_keys=True) + "\n",
    )


def _write_adapter_exports(
    artifact_root: Path,
    *,
    research_round: int,
    thesis_id: str,
    outcome: str,
    family: str,
    reasoning: str,
    validation_failure_reason: str,
    usage: dict[str, Any],
    quality: Any,
) -> None:
    kwargs = dict(
        research_round=research_round,
        thesis_id=thesis_id,
        outcome=outcome,
        family=family,
        reasoning=reasoning,
        validation_failure_reason=validation_failure_reason,
        usage=usage,
        quality=quality,
    )
    export_root = artifact_root / "trace_exports" / f"round-{research_round:03d}-{thesis_id}"
    for dir_name, build_fn in [
        ("halo", build_halo_export_package),
        ("recursive_improve", build_recursive_improve_export_package),
        ("reflexio", build_reflexio_export_package),
    ]:
        adapter_kwargs = dict(kwargs)
        if dir_name in {"recursive_improve", "reflexio"}:
            adapter_kwargs["canonical_trace_path"] = get_event_file()
        _write_export_package(export_root, dir_name, build_fn(**adapter_kwargs))


def _write_research_round_artifacts(
    controller: "AutoresearchController",
    *,
    research_round: int,
    result: dict[str, Any],
    round_usage: dict[str, Any],
) -> Path:
    state = controller.read_state()
    raw_job = state.get("job")
    try:
        job = int(raw_job)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"job id is required for research round artifact path; got {raw_job!r}"
        ) from exc
    runtime_root = getattr(controller, "runtime_root", None) or controller.root
    round_root = research_round_root(runtime_root, job, research_round)
    round_root.mkdir(parents=True, exist_ok=True)
    for dirname in ("conductor", "analyst", "validator", "compiler"):
        (round_root / dirname).mkdir(parents=True, exist_ok=True)
    (round_root / "attempts" / "attempt-1").mkdir(parents=True, exist_ok=True)
    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id")
    write_json_artifact(
        round_root / "round.json",
        {
            "job_id": job,
            "round_number": research_round,
            "strategy_family": controller.family.name,
            "selected_thesis_id": thesis_id,
            "outcome": _classify_round_outcome(result),
            "run_id": result.get("run_id"),
            "created_at": iso8601_utc_now(),
            "usage": round_usage if round_usage else None,
        },
    )
    write_json_artifact(
        round_root / "links.json",
        {
            "generated_config_path": result.get("generated_config"),
            "selected_thesis_path": (
                f"runtime/jobs/job-{job}/research/round-{research_round}/selected_thesis.json"
                if thesis_id
                else None
            ),
            "selected_contract_path": (
                f"runtime/jobs/job-{job}/research/round-{research_round}/selected_contract.json"
                if thesis_id and result.get("generated_config")
                else None
            ),
            "related_backtest_run_artifact_path": result.get("related_backtest_run_artifact_path"),
            "related_trace_export_path": (
                f"runtime/jobs/job-{job}/research/round-{research_round}/trace_exports"
            ),
            "related_builder_request_path": (
                f"runtime/jobs/job-{job}/research/round-{research_round}/builder_request"
                if thesis_id and result.get("generated_config_needs_build")
                else None
            ),
        },
    )
    return round_root


def _safe_hook(name: str, fn, *args, **kwargs):
    """Run an instrumentation hook fail-open for *external* flakiness only.

    Per CLAUDE.md error policy: deterministic errors (TypeError, KeyError,
    AttributeError, ImportError, AssertionError, ValueError from our own
    code) propagate loud — those are bugs we want to notice. Only OS,
    subprocess, and timeout errors are tolerated as instrumentation noise.
    """
    import subprocess

    try:
        return fn(*args, **kwargs)
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        log.error(
            f"IMPROVEMENT_HOOKS {name} raised {type(exc).__name__}: {exc}; "
            f"continuing. Action: investigate {name} logs."
        )
        return None


def _run_improvement_hooks(
    controller: "AutoresearchController",
    research_round: int,
    result: dict[str, Any],
) -> None:
    """Dispatch HALO mining + auto-apply + Ratchet — all flag-gated.

    Each arrow short-circuits when its flag is off, so flag-off behavior
    is byte-identical to the pre-improvement code path.
    """
    from improvement_flags import (
        halo_apply_enabled,
        halo_enabled,
        ratchet_enabled,
        recursive_improve_enabled,
    )

    halo_report = None
    if halo_enabled():
        from improvement_halo import run_halo_after_round
        from trace_sdk import get_event_file

        halo_dir = controller.root / "improvement_reports" / "halo"
        halo_report = _safe_hook(
            "HALO_mining", run_halo_after_round, research_round, get_event_file(), halo_dir
        )

    apply_decision: dict | None = None
    if halo_report is not None and halo_apply_enabled():
        from improvement_halo_apply import apply_halo_report

        apply_decision = _safe_hook("HALO_apply", apply_halo_report, halo_report, controller.root)
        if isinstance(apply_decision, dict):
            log.info(
                f"HALO_APPLY round={research_round} status={apply_decision.get('status')} "
                f"reason={apply_decision.get('reason', '')}"
            )
        else:
            apply_decision = None

    if ratchet_enabled():
        from eval_harness import EVAL_RESULTS_DIRNAME
        from improvement_ratchet import record_round_decision

        eval_result_path = None
        if isinstance(apply_decision, dict) and apply_decision.get("eval_result_path"):
            try:
                candidate_eval_path = Path(str(apply_decision["eval_result_path"])).resolve(
                    strict=False
                )
                eval_results_root = (Path(controller.root) / EVAL_RESULTS_DIRNAME).resolve(
                    strict=False
                )
                if candidate_eval_path.is_relative_to(eval_results_root):
                    eval_result_path = candidate_eval_path
                else:
                    log.warning(
                        "RATCHET ignoring eval_result_path outside eval_results: "
                        f"{candidate_eval_path}"
                    )
            except (OSError, RuntimeError, TypeError, ValueError):
                log.warning(
                    "RATCHET ignoring invalid eval_result_path: "
                    f"{apply_decision.get('eval_result_path')!r}"
                )
        _safe_hook(
            "Ratchet",
            record_round_decision,
            controller,
            research_round,
            _classify_round_outcome(result),
            eval_result_path=eval_result_path,
            apply_decision=apply_decision,
        )

    if recursive_improve_enabled():
        from improvement_recursive_improve import run_scheduled_recursive_improve_reports

        state = controller.read_state()
        raw_job = state.get("job")
        try:
            job = int(raw_job) if raw_job is not None else None
        except (TypeError, ValueError):
            job = None
        report_result = _safe_hook(
            "RecursiveImproveReports",
            run_scheduled_recursive_improve_reports,
            controller.root,
            research_round=research_round,
            job=job,
        )
        if report_result is not None:
            log.info(
                "RECURSIVE_IMPROVE_REPORTS round=%s status=%s output_dir=%s reason=%s",
                research_round,
                getattr(report_result, "status", ""),
                getattr(report_result, "output_dir", ""),
                getattr(report_result, "reason", ""),
            )


def _record_rejection_rule_if_needed(research_round: int, result: dict[str, Any]) -> None:
    validation_failure_reason = result.get("validation_failure_reason")
    if not validation_failure_reason:
        return
    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
    _RULE_PROPOSALS.create_proposal(
        title=f"Round {research_round} rejected thesis {thesis_id}",
        rationale=validation_failure_reason,
        evidence_event_ids=[],
        expected_impact="reduce repeated rejected research outcomes",
        proposed_rule=f"Prevent repeated rejection path for thesis {thesis_id}",
    )


def _close_run(
    controller: "AutoresearchController",
    state: dict[str, Any],
    title: str,
    body: str,
    color: int,
) -> None:
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    notify_discord(title, body, webhook=controller.family.discord_webhook, color=color)


def _handle_max_rounds_reached(
    controller: "AutoresearchController", state: dict[str, Any]
) -> dict[str, Any]:
    state["state"] = "finished"
    state["finished_reason"] = "max_research_rounds_reached"
    state.pop("research_round_in_progress", None)
    state.pop("activity", None)
    log.info(f"LOOP_STOP finished: max research rounds ({MAX_RESEARCH_ROUNDS}) reached")
    best = state.get("current_best", {})
    _close_run(
        controller,
        state,
        f"✅ {controller.family.name.upper()} FINISHED — max rounds",
        f"**Rounds:** {MAX_RESEARCH_ROUNDS}\n"
        f"**Best config:** `{best.get('config', '?')}`\n"
        f"**Best PF:** {best.get('metric', '?')}",
        DISCORD_COLOR_SUCCESS,
    )
    return state


def _handle_should_stop(
    controller: "AutoresearchController", state: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    state["state"] = "finished"
    state["finished_reason"] = "research_recommends_stop"
    state["research_stop_reasoning"] = result.get("reasoning", "")
    state["research_round"] = result.get("research_round", state.get("research_round", 0))
    state.pop("research_round_in_progress", None)
    state.pop("activity", None)
    log.info("LOOP_STOP finished: research recommends stop")
    best = state.get("current_best", {})
    _close_run(
        controller,
        state,
        f"✅ {controller.family.name.upper()} FINISHED — conductor says stop",
        f"**Best config:** `{best.get('config', '?')}`\n"
        f"**Best PF:** {best.get('metric', '?')}\n\n"
        "Research conductor recommends stopping.",
        DISCORD_COLOR_SUCCESS,
    )
    return state


def _handle_needs_code(
    controller: "AutoresearchController", state: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    thesis_id = result.get("generated_thesis_id", "unknown")
    thesis = result.get("thesis", {})
    log.warning(f"LOOP_HALT thesis={thesis_id} requires code change")
    thesis_payload = dict(thesis)
    thesis_payload.setdefault("thesis_id", thesis_id)
    thesis_payload.setdefault("strategy_family", controller.family.name)
    thesis_payload["requires_code_change"] = True
    # Halt bookkeeping is unconditional: the conductor has already decided this
    # thesis needs a code change. Validation/compilation below is a best-effort
    # builder-artifact materialization step; any failure must not skip the halt
    # state mutation or the operator notification at _close_run.
    state["state"] = "halted"
    state["research_round"] = result.get("research_round", state.get("research_round", 0))
    state.pop("research_round_in_progress", None)
    state.pop("activity", None)
    state["halted_reason"] = "requires_code_change"
    state["halted_thesis_id"] = thesis_id
    state["halted_thesis"] = thesis
    try:
        _, validated = _prepare_thesis_for_validation(
            thesis_payload,
            strategy_family=controller.family.name,
            prior_theses=None,
            allow_schema_only_code_change_fallback=True,
        )
    except (ValidationError, ValueError) as exc:
        log.warning(
            "LOOP_HALT thesis=%s validation failed; skipping compile: %s",
            thesis_id,
            exc,
        )
        validated = None
    if validated is not None:
        from compiler_pipeline import compile_research_thesis

        try:
            compile_research_thesis(
                validated, controller.root, artifact_root=controller.job_runtime_root
            )
        except Exception as exc:
            log.warning(
                "LOOP_HALT thesis=%s could not materialize builder artifacts: %s",
                thesis_id,
                exc,
            )
    best = state.get("current_best", {})
    _close_run(
        controller,
        state,
        f"🔧 {controller.family.name.upper()} needs code change — attempting auto-build",
        f"**Thesis:** `{thesis_id}`\n"
        f"**Best PF:** {best.get('metric', '?')}\n\n"
        f"**Hypothesis:** {thesis.get('hypothesis', '(no details captured)')}\n\n"
        f"**Mechanism:** {thesis.get('mechanism', '')}\n\n"
        f"**Config changes:** `{json.dumps(thesis.get('config_changes', {}))}`",
        DISCORD_COLOR_DISCARD,
    )
    return state


def _handle_success(
    controller: "AutoresearchController",
    state: dict[str, Any],
    result: dict[str, Any],
    research_round: int,
) -> dict[str, Any]:
    gen_config = result["generated_config"]
    thesis_id = result.get("thesis_id", "unknown")
    trace("LOOP", f"research produced config: {gen_config} thesis={thesis_id}")
    state["state"] = "running"
    state["research_round"] = research_round
    state.pop("research_round_in_progress", None)
    state["activity"] = {
        "type": "experiment",
        "phase": "pending_backtest",
        "round": research_round,
        "config": gen_config,
        "thesis_id": thesis_id,
    }
    state["current_thesis"] = {
        "config": gen_config,
        "status": "ready_to_run",
        "selected_thesis_id": thesis_id,
    }
    state["selected_thesis_id"] = thesis_id
    state["selected_config_path"] = gen_config
    state["backtest_target_path"] = (
        f"runtime/jobs/job-{state.get('job')}/research/round-{research_round}/backtest"
    )
    state["next_action"] = {
        "type": "run_experiment",
        "config": gen_config,
        "benchmark_command": controller.family.benchmark_command(gen_config),
        "requires_trade_analysis": True,
        "source": "research_conductor",
        "research_round": research_round,
        "selected_thesis_id": thesis_id,
    }
    state["blockers"] = []
    state.pop("rejection_feedback", None)
    controller.write_state(state)
    log.info(f"HEARTBEAT research generated {thesis_id} -> {gen_config}")
    return state


def _handle_round_failure(
    controller: "AutoresearchController",
    state: dict[str, Any],
    result: dict[str, Any],
    research_round: int,
) -> dict[str, Any]:
    reason = (
        result.get("validation_failure_reason") or result.get("reasoning") or "no thesis generated"
    )
    trace("LOOP", f"research round {research_round} produced no config: {reason}")
    log.warning(f"HEARTBEAT research round {research_round} failed: {reason}")
    state["research_round"] = research_round
    state.pop("research_round_in_progress", None)
    state.pop("activity", None)
    state.update(
        build_research_failure_state(
            controller.root,
            controller.research_dir,
            f"round {research_round} failed: {reason}",
        )
    )
    controller.write_state(state)
    return state


def _invoke_conductor_round(
    controller: "AutoresearchController", research_round: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one conductor pass. Returns (result, round_usage). Also writes
    round-usage into state for downstream log_experiment_result use."""
    from research_conductor import get_round_usage, reset_round_usage

    log.info(f"HEARTBEAT research_blocked round={research_round}, invoking research subagent")
    begin_hypothesis(f"research-round-{research_round}")
    reset_round_usage()
    result = controller.execute_research_one()
    round_usage = get_round_usage()
    trace("USAGE", f"round={research_round} {json.dumps(round_usage)}")
    controller._accumulate_job_usage(round_usage)
    state = controller.read_state()
    state["_last_round_usage"] = round_usage
    state["family"] = controller.family.name
    controller.write_state(state)
    end_hypothesis(decision="research_complete")
    return result, round_usage


def run_research(controller: "AutoresearchController", state: dict[str, Any]) -> dict[str, Any]:
    """Run one research round. Returns updated state dict."""
    from trace_sdk import begin_round

    ensure_job_metadata = getattr(controller, "_ensure_job_metadata", None)
    if ensure_job_metadata is not None:
        ensure_job_metadata()
    research_round = state.get("research_round", 0) + 1
    begin_round(research_round)
    if research_round > MAX_RESEARCH_ROUNDS:
        return _handle_max_rounds_reached(controller, state)

    result, round_usage = _invoke_conductor_round(controller, research_round)
    result["research_round"] = research_round
    state = controller.read_state()  # refresh after _invoke_conductor_round mutated state
    _write_research_round_artifacts(
        controller,
        research_round=research_round,
        result=result,
        round_usage=round_usage,
    )

    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
    thesis_meta = _thesis_meta_from_result(result, controller.family.name)
    controller.log_research_round(
        round_number=research_round,
        thesis_id=thesis_id,
        hypothesis_id=thesis_id,
        outcome=_classify_round_outcome(result),
        config_changes=thesis_meta.get("config_changes"),
        hypothesis=thesis_meta.get("hypothesis", ""),
        mechanism=thesis_meta.get("mechanism", ""),
        mechanism_dimension=thesis_meta.get("mechanism_dimension", ""),
        thesis_details={
            key: thesis_meta.get(key)
            for key in (
                "dimension_novelty",
                "evidence",
                "expected_effects",
                "disqualifiers",
                "why_not_overfit",
                "requires_code_change",
                "requested_primitives",
                "required_diagnostics",
                "required_diagnostic_specs",
                "closest_prior_theses_considered",
                "orthogonality_defense",
                "evidence_strength",
                "thesis_role",
                "falsification_or_alternative",
                "new_dimension_name",
                "why_existing_dimensions_do_not_fit",
                "mechanism_family_definition",
                "expected_reuse_across_future_theses",
            )
            if key in thesis_meta
        },
        validation_failure_reason=result.get("validation_failure_reason")
        or result.get("reasoning", ""),
        usage=round_usage,
    )
    _record_rejection_rule_if_needed(research_round, result)
    try:
        _record_round_quality_and_bridges(controller, research_round, result, round_usage)
    except Exception as exc:
        log.warning("_record_round_quality_and_bridges failed (non-fatal): %s", exc)
    _run_improvement_hooks(controller, research_round, result)

    if result.get("should_stop"):
        return _handle_should_stop(controller, state, result)
    if result.get("generated_config_needs_build"):
        state = _handle_needs_code(controller, state, result)
        return _orchestration_build_missing_primitives_for_state(
            controller,
            state,
            thesis_id,
            result.get("thesis", {}),
            research_round=research_round,
        )
    if result.get("generated_config"):
        return _handle_success(controller, state, result, research_round)
    return _handle_round_failure(controller, state, result, research_round)
