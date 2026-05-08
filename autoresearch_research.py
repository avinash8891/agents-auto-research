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

from autoresearch_constants import (
    DISCORD_BODY_MAX_CHARS,
    DISCORD_COLOR_DISCARD,
    DISCORD_COLOR_ERROR,
    DISCORD_COLOR_SUCCESS,
    DISCORD_HTTP_TIMEOUT_SECONDS,
    MAX_RESEARCH_ROUNDS,
    MAX_VALIDATION_RETRIES,
)
from autoresearch_logging import get_logger
from autoresearch_orchestration import (
    build_missing_primitives_for_state as _orchestration_build_missing_primitives_for_state,
)
from autoresearch_planning import build_research_failure_state
from autoresearch_state import (
    ExperimentRecord,
    read_state,
    write_state,
)
from config_hash import _config_hash
from persistence_utils import utc_now_iso8601 as iso8601_utc_now
from persistence_utils import write_text_atomic as _write_text_atomic
from research_types import ResearchThesis
from strategies import STRATEGIES
from strategy_family import StrategyFamily
from thesis_validator import normalize_thesis_payload
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
    rejection_reason: str = "",
    usage: dict[str, Any] | None = None,
) -> None:
    """Log every research round outcome to canonical persistence."""
    from experiment_db import ExperimentDB

    db = ExperimentDB(db_path)
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
            "rejection_reason": rejection_reason,
            "selected_for_execution": 1 if outcome == "compiled" else 0,
            "created_at_utc": iso8601_utc_now(),
        }
    )


# ── Pure helpers ──────────────────────────────────────────────────


def results_to_dicts(results: list[ExperimentRecord]) -> list[dict[str, Any]]:
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


def queue_variants(
    root: Path,
    run_queue_dir: Path,
    variants: list[dict[str, Any]],
    thesis: Any,  # ResearchThesis
    primary_contract: Any,  # ExperimentContract
    baseline_config: dict[str, Any],
    *,
    experiments_dir: Path | None = None,
    job: int | None = None,
    created_for_commit: str = "",
) -> None:
    """Write variant runtime configs so the loop picks them up as queued experiments."""
    for variant in variants:
        variant = dict(variant)
        label = variant.pop("_variant_label", "variant")
        factor = variant.pop("_variant_factor", 1.0)
        if factor == 1.0:
            continue  # skip the proposed value — it's already the primary

        runtime = {**baseline_config, **variant}
        family_name = getattr(thesis, "strategy_family", None) or getattr(
            primary_contract, "strategy_family", None
        )
        strategy = STRATEGIES.get(family_name) if family_name else None
        if strategy is not None:
            try:
                runtime = strategy.validate_runtime_config_scope(runtime)
                violations = strategy.validate_runtime_config(runtime)
            except ValueError as exc:
                violations = [str(exc)]
            if violations:
                trace(
                    "LOOP",
                    f"skipped invalid variant {thesis.thesis_id}_{label}: {'; '.join(violations)}",
                )
                continue
        config_hash = _config_hash(runtime)
        variant_id = f"{thesis.thesis_id}_{label}"
        exp_dir = (experiments_dir or (root / "experiments")) / config_hash
        exp_dir.mkdir(parents=True, exist_ok=True)

        _write_text_atomic(exp_dir / "runtime_config.json", json.dumps(runtime, indent=2) + "\n")
        variant_thesis = thesis.model_dump()
        variant_thesis["thesis_id"] = variant_id
        variant_thesis["_variant_of"] = primary_contract.experiment_id
        variant_thesis["_variant_label"] = label
        variant_thesis["_variant_factor"] = factor
        variant_thesis["config_changes"] = runtime
        variant_thesis["config_changes_kind"] = "full_runtime"
        _write_text_atomic(exp_dir / "thesis.json", json.dumps(variant_thesis, indent=2) + "\n")

        run_queue_dir.mkdir(parents=True, exist_ok=True)
        queue_artifact = {
            "thesis_id": variant_id,
            "config": (exp_dir / "runtime_config.json").relative_to(root).as_posix(),
            "status": "pending",
            "source": "multi_variant_probe",
            "variant_of": primary_contract.experiment_id,
            "variant_label": label,
            "variant_factor": factor,
        }
        if job is not None:
            queue_artifact["job"] = job
        if created_for_commit:
            queue_artifact["created_for_commit"] = created_for_commit
        _write_text_atomic(
            run_queue_dir / f"{variant_id}.json",
            json.dumps(queue_artifact, indent=2) + "\n",
        )
        trace("LOOP", f"queued variant {variant_id} ({label}) config_hash={config_hash}")


# ── Conductor invocation ──────────────────────────────────────────


def _backfill_artifact_files_from_latest_dir(
    controller: "AutoresearchController",
    latest: ExperimentRecord,
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
    latest: ExperimentRecord,
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
    results: list[ExperimentRecord],
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


def _check_parsed_for_terminal(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Inspect the conductor response for terminal conditions before any
    thesis validation. Returns a result dict if the response should
    short-circuit out of the validation-retry loop, else None."""
    if not parsed:
        return {
            "status": "parse_failed",
            "generated_config": None,
            "should_stop": False,
            "rejection_reason": "research conductor returned no parseable thesis",
        }
    if parsed.get("status") == "conductor_error":
        error = parsed.get("error") or parsed.get("reasoning") or "unknown conductor error"
        validation_reason = str(parsed.get("validation_reason") or "")
        rejection_reason = f"research conductor failed: {error}"
        if validation_reason:
            rejection_reason = f"{rejection_reason}: {validation_reason}"
        result = {
            "status": "conductor_error",
            "generated_config": None,
            "should_stop": False,
            "rejection_reason": rejection_reason,
        }
        if validation_reason:
            result["validation_reason"] = validation_reason
        _record_event_fail_open(
            source_module="autoresearch_research",
            category="conductor",
            action="conductor_error",
            summary=rejection_reason,
            payload={
                "error_code": "conductor_error",
                "error": str(error),
                "validation_reason": validation_reason,
            },
        )
        return result
    if not parsed.get("suggested_theses"):
        reasoning = parsed.get("reasoning") or "research conductor returned no suggested_theses"
        return {
            "status": "completed",
            "generated_config": None,
            "should_stop": parsed.get("should_stop", False),
            "reasoning": reasoning,
        }
    return None


def _structured_rejection_reason(*, source: str, message: str) -> dict[str, str]:
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
) -> None:
    rejection_feedback = f"Thesis '{thesis_id}' rejected by validator: {reason}"
    structured_rejection = _structured_rejection_reason(
        source="validator",
        message=reason,
    )
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
                "new_dimension_name",
                "why_existing_dimensions_do_not_fit",
                "mechanism_family_definition",
                "expected_reuse_across_future_theses",
            )
            if key in raw_thesis
        }
        | {"structured_rejection": structured_rejection},
        rejection_reason=reason,
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
    contract: Any,
    raw_thesis: dict[str, Any],
    validated: Any,
    thesis_id: str,
    parsed: dict[str, Any],
    should_stop: bool,
) -> dict[str, Any]:
    """Wire the contract into the controller and queue any multi-variant
    probes; return the success result dict."""
    from thesis_validator import generate_variants

    config_path = (
        (controller.experiments_dir / contract.experiment_id / "runtime_config.json")
        .relative_to(controller.root)
        .as_posix()
    )
    controller.ctx.current_contract = contract
    latest_db = controller.experiment_db.latest(1)
    controller.ctx.parent_experiment_id = latest_db[0].experiment_id if latest_db else ""

    baseline_config = load_baseline_config(controller.root, controller.family)
    if baseline_config:
        variants = generate_variants(raw_thesis.get("config_changes", {}), baseline_config)
        if len(variants) > 1:
            controller._queue_variants(variants, validated, contract, baseline_config)
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


def _try_one_validation_attempt(
    controller: "AutoresearchController",
    research_round: int,
    attempt: int,
    parsed: dict[str, Any],
    prior_theses: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """One pass of the conductor-validate-compile retry loop.

    Returns (result, retry_feedback). If `result` is not None, exit the
    loop with that result. If `retry_feedback` is not None, retry the
    conductor with that feedback string.
    """
    from compiler_pipeline import compile_research_thesis
    from thesis_validator import ThesisValidationError, validate_thesis_dict

    raw_thesis = parsed["suggested_theses"][0]
    raw_thesis["strategy_family"] = controller.family.name
    thesis_id = raw_thesis.get("thesis_id", "unknown")
    log.info(
        f"RESEARCH_RAW thesis_id={thesis_id} "
        f"config_changes={json.dumps(raw_thesis.get('config_changes', 'MISSING'))}"
    )

    try:
        validated = validate_thesis_dict(raw_thesis, prior_theses=prior_theses)
        contract = compile_research_thesis(
            validated, controller.root, artifact_root=controller.job_runtime_root
        )
    except (ThesisValidationError, ValueError) as exc:
        _log_validation_rejection(
            controller, research_round, attempt, raw_thesis, thesis_id, str(exc)
        )
        return None, f"Thesis '{thesis_id}' rejected by validator: {exc}"
    return _dispatch_compiled_contract(
        controller, research_round, attempt, parsed, raw_thesis, validated, contract, thesis_id
    )


def _dispatch_compiled_contract(
    controller: "AutoresearchController",
    research_round: int,
    attempt: int,
    parsed: dict[str, Any],
    raw_thesis: dict[str, Any],
    validated: Any,
    contract: Any,
    thesis_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    should_stop = parsed.get("should_stop", False)
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
        }, None
    if contract.status == "ready_to_run":
        return (
            _on_ready_to_run(
                controller, contract, raw_thesis, validated, thesis_id, parsed, should_stop
            ),
            None,
        )
    feedback = (
        f"Thesis '{thesis_id}' rejected: status={contract.status}, "
        f"missing={contract.missing_primitives}"
    )
    _log_validation_rejection(controller, research_round, attempt, raw_thesis, thesis_id, feedback)
    return None, feedback


def _exhausted_retries_result(
    parsed: dict[str, Any] | None, rejection_feedback: str
) -> dict[str, Any]:
    thesis_id = (
        parsed["suggested_theses"][0].get("thesis_id", "unknown")
        if parsed and parsed.get("suggested_theses")
        else "unknown"
    )
    log.error(
        f"THESIS REJECTED after {MAX_VALIDATION_RETRIES} attempts: {rejection_feedback} "
        f"| hint=the conductor produced a thesis that failed validation 3 times in a row; "
        f"review the rejection_reason above and refine the conductor system prompt or "
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
        "rejection_reason": rejection_feedback,
        "should_stop": False,
        "reasoning": parsed.get("reasoning", "") if parsed else "",
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
) -> dict[str, Any] | None:
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
    state["research_round"] = research_round
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
    parsed: dict[str, Any] | None = None
    for attempt in range(MAX_VALIDATION_RETRIES):
        parsed = _call_conductor(
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
        terminal = _check_parsed_for_terminal(parsed)
        if terminal is not None:
            return terminal
        result, retry_feedback = _try_one_validation_attempt(
            controller, research_round, attempt, parsed, prior_theses
        )
        if result is not None:
            return result
        rejection_feedback = retry_feedback or rejection_feedback
    return _exhausted_retries_result(parsed, rejection_feedback)


def execute_research_one(controller: "AutoresearchController") -> dict[str, Any]:
    """Drive research using SDK agents."""
    return execute_research_sdk(controller)


# ── Round orchestration ──────────────────────────────────────────


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
    if result.get("rejection_reason"):
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
    reasoning = result.get("reasoning", "")
    rejection_reason = result.get("rejection_reason", "")
    dimension_scores = {
        k: 1.0 if k == outcome else 0.0
        for k in ("compiled", "needs_code", "stopped", "rejected", "conductor_error")
    }
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
        "rejection_reason": rejection_reason,
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
    _write_adapter_exports(controller.root, **payload_kwargs)


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
    root: Path,
    *,
    research_round: int,
    thesis_id: str,
    outcome: str,
    family: str,
    reasoning: str,
    rejection_reason: str,
    usage: dict[str, Any],
    quality: Any,
) -> None:
    kwargs = dict(
        research_round=research_round,
        thesis_id=thesis_id,
        outcome=outcome,
        family=family,
        reasoning=reasoning,
        rejection_reason=rejection_reason,
        usage=usage,
        quality=quality,
    )
    export_root = root / "trace_exports" / f"round-{research_round:03d}-{thesis_id}"
    for dir_name, build_fn in [
        ("halo", build_halo_export_package),
        ("recursive_improve", build_recursive_improve_export_package),
        ("reflexio", build_reflexio_export_package),
    ]:
        adapter_kwargs = dict(kwargs)
        if dir_name in {"recursive_improve", "reflexio"}:
            adapter_kwargs["canonical_trace_path"] = get_event_file()
        _write_export_package(export_root, dir_name, build_fn(**adapter_kwargs))


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
        from eval_harness import EVAL_RESULTS_DIRNAME, latest_eval_result_path
        from improvement_ratchet import record_round_decision

        _safe_hook(
            "Ratchet",
            record_round_decision,
            controller,
            research_round,
            _classify_round_outcome(result),
            eval_result_path=latest_eval_result_path(controller.root / EVAL_RESULTS_DIRNAME),
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
    rejection_reason = result.get("rejection_reason")
    if not rejection_reason:
        return
    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
    _RULE_PROPOSALS.create_proposal(
        title=f"Round {research_round} rejected thesis {thesis_id}",
        rationale=rejection_reason,
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
    state["halted_reason"] = "requires_code_change"
    state["halted_thesis_id"] = thesis_id
    state["halted_thesis"] = thesis
    # Normalize before validation so mechanism_dimension aliases and raw
    # expected_effects/disqualifiers match ResearchThesis schema (mirrors
    # thesis_validator.validate_thesis_dict).
    try:
        validated = ResearchThesis.model_validate(normalize_thesis_payload(thesis_payload))
    except ValidationError as exc:
        log.warning(
            "LOOP_HALT thesis=%s schema validation failed; skipping compile: %s",
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
    state["current_thesis"] = {"config": gen_config, "status": "ready_to_run"}
    state["next_action"] = {
        "type": "run_experiment",
        "config": gen_config,
        "benchmark_command": controller.family.benchmark_command(gen_config),
        "requires_trade_analysis": True,
        "source": "research_conductor",
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
    reason = result.get("rejection_reason") or result.get("reasoning") or "no thesis generated"
    trace("LOOP", f"research round {research_round} produced no config: {reason}")
    log.warning(f"HEARTBEAT research round {research_round} failed: {reason}")
    state["research_round"] = research_round
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
    state = controller.read_state()  # refresh after _invoke_conductor_round mutated state

    thesis_id = result.get("generated_thesis_id") or result.get("thesis_id") or "none"
    thesis_meta = result.get("thesis") or {
        "thesis_id": thesis_id,
        "strategy_family": controller.family.name,
        "config_changes": result.get("config_changes") or {},
        "hypothesis": result.get("hypothesis") or result.get("reasoning", ""),
        "mechanism": result.get("mechanism") or result.get("reasoning", ""),
        "mechanism_dimension": result.get("mechanism_dimension") or "",
    }
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
                "required_diagnostics",
                "new_dimension_name",
                "why_existing_dimensions_do_not_fit",
                "mechanism_family_definition",
                "expected_reuse_across_future_theses",
            )
            if key in thesis_meta
        },
        rejection_reason=result.get("rejection_reason") or result.get("reasoning", ""),
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
