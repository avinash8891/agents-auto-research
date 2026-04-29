"""Research round driver for autoresearch.

Drives the research-conductor loop: invoking the conductor, validating
proposed theses, compiling them, queueing multi-variant probes, logging
research-round outcomes, and translating the conductor result into the
next state for the controller.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from autoresearch_constants import (
    CONFIG_HASH_LENGTH,
    DISCORD_BODY_MAX_CHARS,
    DISCORD_COLOR_DISCARD,
    DISCORD_COLOR_ERROR,
    DISCORD_COLOR_SUCCESS,
    DISCORD_HTTP_TIMEOUT_SECONDS,
    MAX_RESEARCH_ROUNDS,
    MAX_VALIDATION_RETRIES,
)
from autoresearch_logging import get_logger
from autoresearch_state import (
    ExperimentRecord,
    iso8601_utc_now,
    read_entries,
    read_state,
    write_entries,
    write_state,
)
from strategy_family import StrategyFamily
from trace_logger import (
    begin_hypothesis,
    current_hypothesis_id,
    end_hypothesis,
    get_run_id,
    trace,
)

if TYPE_CHECKING:
    from autoresearch_loop import AutoresearchController

log = get_logger(__name__)


# ── Discord notification ──────────────────────────────────────────


def notify_discord(
    title: str, body: str, *, webhook: str = "", color: int = DISCORD_COLOR_ERROR
) -> None:
    """Send a Discord embed notification. Fire-and-forget, never raises."""
    if not webhook:
        return
    try:
        import urllib.error
        import urllib.request

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


# ── JSONL + state mutators ────────────────────────────────────────


def accumulate_job_usage(state_path: Path, round_usage: dict[str, Any]) -> None:
    """Accumulate round token usage into job totals in state JSON."""
    state = read_state(state_path)
    job_usage = state.get("job_usage") or {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "rounds": 0,
    }
    total = round_usage.get("total", {})
    job_usage["input_tokens"] += total.get("input_tokens", 0)
    job_usage["output_tokens"] += total.get("output_tokens", 0)
    job_usage["total_tokens"] += total.get("total_tokens", 0)
    job_usage["cost_usd"] += total.get("cost_usd", 0.0)
    job_usage["rounds"] += 1
    state["job_usage"] = job_usage
    write_state(state_path, state)


def log_research_round(
    jsonl_path: Path,
    state_path: Path,
    *,
    round_number: int,
    thesis_id: str,
    outcome: str,
    config_changes: dict[str, Any] | None = None,
    hypothesis: str = "",
    mechanism: str = "",
    mechanism_dimension: str = "",
    rejection_reason: str = "",
    usage: dict[str, Any] | None = None,
) -> None:
    """Log every research round outcome to the JSONL."""
    entries = read_entries(jsonl_path)
    state = read_state(state_path)
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
        "timestamp": iso8601_utc_now(),
    }
    entries.append(entry)
    write_entries(jsonl_path, entries)


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
    except (yaml.YAMLError, OSError):
        return None


def queue_variants(
    root: Path,
    run_queue_dir: Path,
    variants: list[dict[str, Any]],
    thesis: Any,  # ResearchThesis
    primary_contract: Any,  # ExperimentContract
    baseline_config: dict[str, Any],
) -> None:
    """Write variant runtime configs so the loop picks them up as queued experiments."""
    for variant in variants:
        label = variant.pop("_variant_label", "variant")
        factor = variant.pop("_variant_factor", 1.0)
        if factor == 1.0:
            continue  # skip the proposed value — it's already the primary

        runtime = {**baseline_config, **variant}
        config_hash = hashlib.sha256(json.dumps(runtime, sort_keys=True).encode()).hexdigest()[
            :CONFIG_HASH_LENGTH
        ]
        variant_id = f"{thesis.thesis_id}_{label}"
        exp_dir = root / "experiments" / config_hash
        exp_dir.mkdir(parents=True, exist_ok=True)

        (exp_dir / "runtime_config.json").write_text(json.dumps(runtime, indent=2) + "\n")
        variant_thesis = thesis.model_dump()
        variant_thesis["thesis_id"] = variant_id
        variant_thesis["_variant_of"] = primary_contract.experiment_id
        variant_thesis["_variant_label"] = label
        variant_thesis["_variant_factor"] = factor
        variant_thesis["config_changes"] = variant
        (exp_dir / "thesis.json").write_text(json.dumps(variant_thesis, indent=2) + "\n")

        run_queue_dir.mkdir(parents=True, exist_ok=True)
        queue_artifact = {
            "thesis_id": variant_id,
            "config": f"experiments/{config_hash}/runtime_config.json",
            "status": "pending",
            "source": "multi_variant_probe",
            "variant_of": primary_contract.experiment_id,
            "variant_label": label,
            "variant_factor": factor,
        }
        (run_queue_dir / f"{variant_id}.json").write_text(
            json.dumps(queue_artifact, indent=2) + "\n"
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
    artifact_dir = controller.root / latest.asi.get("artifact_dir", "")
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
            strategy_events_file = str(events_files[0])
    if not diagnostics_file:
        diag_jsons = list(artifact_dir.glob("*diagnostics.json"))
        if diag_jsons:
            diagnostics_file = str(diag_jsons[0])
    return trades_file, strategy_events_file, diagnostics_file


def _resolve_conductor_inputs(
    controller: "AutoresearchController",
    results: list[ExperimentRecord],
) -> tuple[str, str, str, dict[str, Any]]:
    """Gather the four inputs the conductor needs from the most recent
    experiment: trades file, strategy events file, diagnostics file, and
    a small `latest_outcome` dict the prompt templates use."""
    trades_file = controller.ctx.latest_trades_file
    strategy_events_file = controller.ctx.latest_strategy_events_file
    diagnostics_file = controller.ctx.latest_diagnostics_file
    latest = controller.latest_result(results)
    if not trades_file and latest:
        trades_file, strategy_events_file, diagnostics_file = (
            _backfill_artifact_files_from_latest_dir(
                controller, latest, trades_file, strategy_events_file, diagnostics_file
            )
        )
    latest_outcome: dict[str, Any] = {}
    if latest:
        latest_outcome["thesis_id"] = Path(latest.config).stem
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
    return trades_file, strategy_events_file, diagnostics_file, latest_outcome


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
        return {
            "status": "conductor_error",
            "generated_config": None,
            "should_stop": False,
            "rejection_reason": f"research conductor failed: {error}",
        }
    if not parsed.get("suggested_theses"):
        reasoning = parsed.get("reasoning") or "research conductor returned no suggested_theses"
        return {
            "status": "completed",
            "generated_config": None,
            "should_stop": parsed.get("should_stop", False),
            "reasoning": reasoning,
        }
    return None


def _log_validation_rejection(
    controller: "AutoresearchController",
    research_round: int,
    attempt: int,
    raw_thesis: dict[str, Any],
    thesis_id: str,
    reason: str,
) -> None:
    rejection_feedback = f"Thesis '{thesis_id}' rejected by validator: {reason}"
    log.warning(f"THESIS REJECTED (will retry with feedback): {rejection_feedback}")
    trace("LOOP", f"thesis rejected, retrying: {rejection_feedback}")
    controller.log_research_round(
        round_number=research_round,
        thesis_id=thesis_id,
        outcome=f"rejected_attempt_{attempt+1}",
        config_changes=raw_thesis.get("config_changes"),
        hypothesis=raw_thesis.get("hypothesis", ""),
        mechanism=raw_thesis.get("mechanism", ""),
        mechanism_dimension=raw_thesis.get("mechanism_dimension", ""),
        rejection_reason=reason,
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

    config_path = f"experiments/{contract.experiment_id}/runtime_config.json"
    controller.ctx.current_contract = contract
    latest_db = controller.experiment_db.latest(1)
    controller.ctx.parent_experiment_id = latest_db[0].experiment_id if latest_db else ""

    baseline_config = load_baseline_config(controller.root, controller.family)
    if baseline_config:
        variants = generate_variants(raw_thesis.get("config_changes", {}), baseline_config)
        if len(variants) > 1:
            queue_variants(
                controller.root,
                controller.run_queue_dir,
                variants,
                validated,
                contract,
                baseline_config,
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
        contract = compile_research_thesis(validated, controller.root)
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
) -> dict[str, Any] | None:
    """One conductor HTTP/SDK call with the per-attempt log preamble."""
    from research_conductor import run_research_conductor_sync

    label = f"round={research_round}" + (
        f" attempt={attempt+1} (retry with feedback)" if attempt else ""
    )
    log.info(f"CONDUCTOR starting {label} trades={'YES' if trades_file else 'NO'}")
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
    )


def execute_research_sdk(controller: "AutoresearchController") -> dict[str, Any]:
    """Drive research using the research conductor.

    Calls the conductor, validates the proposed thesis, compiles it.
    If validation rejects the thesis, calls the conductor AGAIN with
    the rejection reason so it can propose something different.
    """
    from agent_orchestrator import format_result_history
    from thesis_validator import load_prior_theses

    state = controller.read_state()
    research_round = state.get("research_round", 0) + 1
    results = controller.read_results()
    experiment_results = format_result_history(results_to_dicts(results))
    prior_theses = load_prior_theses(controller.root)
    trace("LOOP", f"loaded {len(prior_theses)} prior theses for overlap detection")
    trades_file, strategy_events_file, diagnostics_file, latest_outcome = _resolve_conductor_inputs(
        controller, results
    )
    state["research_round"] = research_round
    controller.write_state(state)

    rejection_feedback = ""
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
    if result.get("should_stop"):
        return "stopped"
    if result.get("generated_config_needs_build"):
        return "needs_code"
    if result.get("generated_config"):
        return "compiled"
    if result.get("rejection_reason"):
        return "rejected"
    return "conductor_error"


def _handle_max_rounds_reached(
    controller: "AutoresearchController", state: dict[str, Any]
) -> dict[str, Any]:
    state["state"] = "finished"
    state["finished_reason"] = "max_research_rounds_reached"
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    log.info(f"LOOP_STOP finished: max research rounds ({MAX_RESEARCH_ROUNDS}) reached")
    best = state.get("current_best", {})
    notify_discord(
        f"✅ {controller.family.name.upper()} FINISHED — max rounds",
        f"**Rounds:** {MAX_RESEARCH_ROUNDS}\n"
        f"**Best config:** `{best.get('config', '?')}`\n"
        f"**Best PF:** {best.get('metric', '?')}",
        webhook=controller.family.discord_webhook,
        color=DISCORD_COLOR_SUCCESS,
    )
    return state


def _handle_should_stop(
    controller: "AutoresearchController", state: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    state["state"] = "finished"
    state["finished_reason"] = "research_recommends_stop"
    state["research_stop_reasoning"] = result.get("reasoning", "")
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    log.info("LOOP_STOP finished: research recommends stop")
    best = state.get("current_best", {})
    notify_discord(
        f"✅ {controller.family.name.upper()} FINISHED — conductor says stop",
        f"**Best config:** `{best.get('config', '?')}`\n"
        f"**Best PF:** {best.get('metric', '?')}\n\n"
        "Research conductor recommends stopping.",
        webhook=controller.family.discord_webhook,
        color=DISCORD_COLOR_SUCCESS,
    )
    return state


def _handle_needs_code(
    controller: "AutoresearchController", state: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    thesis_id = result.get("generated_thesis_id", "unknown")
    thesis = result.get("thesis", {})
    log.warning(f"LOOP_HALT thesis={thesis_id} requires code change")
    state["state"] = "halted"
    state["halted_reason"] = "requires_code_change"
    state["halted_thesis_id"] = thesis_id
    state["halted_thesis"] = thesis
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    best = state.get("current_best", {})
    notify_discord(
        f"\U0001f6d1 {controller.family.name.upper()} HALTED — needs code change",
        f"**Thesis:** `{thesis_id}`\n"
        f"**Best PF:** {best.get('metric', '?')}\n\n"
        f"**Hypothesis:** {thesis.get('hypothesis', '(no details captured)')}\n\n"
        f"**Mechanism:** {thesis.get('mechanism', '')}\n\n"
        f"**Config changes:** `{json.dumps(thesis.get('config_changes', {}))}`",
        webhook=controller.family.discord_webhook,
        color=DISCORD_COLOR_DISCARD,
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
    state["state"] = "blocked"
    state["blockers"] = [
        {"kind": "research_required", "detail": f"round {research_round} failed: {reason}"}
    ]
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
    controller.write_state(state)
    end_hypothesis(decision="research_complete")
    return result, round_usage


def run_research(controller: "AutoresearchController", state: dict[str, Any]) -> dict[str, Any]:
    """Run one research round. Returns updated state dict."""
    from trace_logger import begin_round

    research_round = state.get("research_round", 0) + 1
    begin_round(research_round)
    if research_round > MAX_RESEARCH_ROUNDS:
        return _handle_max_rounds_reached(controller, state)

    result, round_usage = _invoke_conductor_round(controller, research_round)
    state = controller.read_state()  # refresh after _invoke_conductor_round mutated state

    thesis_meta = result.get("thesis", {})
    controller.log_research_round(
        round_number=research_round,
        thesis_id=result.get("generated_thesis_id") or result.get("thesis_id") or "none",
        outcome=_classify_round_outcome(result),
        config_changes=thesis_meta.get("config_changes"),
        hypothesis=thesis_meta.get("hypothesis", ""),
        mechanism=thesis_meta.get("mechanism", ""),
        mechanism_dimension=thesis_meta.get("mechanism_dimension", ""),
        rejection_reason=result.get("rejection_reason") or result.get("reasoning", ""),
        usage=round_usage,
    )

    if result.get("should_stop"):
        return _handle_should_stop(controller, state, result)
    if result.get("generated_config_needs_build"):
        return _handle_needs_code(controller, state, result)
    if result.get("generated_config"):
        return _handle_success(controller, state, result, research_round)
    return _handle_round_failure(controller, state, result, research_round)
