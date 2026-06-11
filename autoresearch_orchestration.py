from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

from autoresearch_paths import serialize_config_path
from autoresearch_runtime_paths import (
    research_round_id,
    research_round_root,
)
from improvement_reflexion import read_validation_failure_reason
from persistence_utils import utc_now_iso8601 as iso8601_utc_now
from persistence_utils import write_text_atomic as _write_text_atomic
from strategies import STRATEGIES
from trace_sdk import trace

if TYPE_CHECKING:
    from autoresearch_controller import AutoresearchController

DETERMINISTIC_BUILDER_ERROR_CODES = frozenset(
    {
        "builder_cli_unavailable",
        "builder_config_validation_failed",
        "builder_implementation_contract_failed",
        "builder_missing_compilation_artifact",
        "builder_missing_generated_config",
        "builder_missing_proposal_artifact",
        "builder_missing_strategy_family",
        "builder_malformed_compilation_artifact",
        "builder_malformed_proposal_artifact",
        "builder_timeout",
        "builder_unknown_strategy_family",
    }
)
RESEARCH_RETRY_BUILDER_ERROR_CODES = frozenset(
    {
        "builder_config_validation_failed",
        "builder_missing_primitive_contract",
    }
)


def _controller_research_artifact_dir(controller: "AutoresearchController") -> str:
    research_dir = getattr(controller, "research_dir", None)
    runtime_root = getattr(controller, "runtime_root", None) or getattr(controller, "root", None)
    if research_dir is None or runtime_root is None:
        raise RuntimeError("Controller must provide job-scoped research_dir for research artifacts")
    resolved = Path(research_dir).resolve()
    runtime_root_resolved = Path(runtime_root).resolve()
    try:
        return resolved.relative_to(runtime_root_resolved).as_posix()
    except ValueError:
        return resolved.as_posix()


def _builder_result_context(builder_result: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in (
        "builder_task",
        "builder_phase",
        "builder_self_check",
        "builder_attempts",
        "implementation_verification_passed",
        "implementation_verification_failures",
        "error_code",
        "generated_config",
        "execution_root",
        "promotion_manifest",
        "promoted_files",
        "seeded_capability",
        "status",
        "validation_passed",
        "usage",
    ):
        if key in builder_result:
            context[key] = builder_result[key]
    return context


def _archive_reactivated_blocker(state: dict[str, Any], *, source: str) -> None:
    blocker: dict[str, Any] = {}
    if state.get("halted_reason"):
        blocker["halted_reason"] = state.get("halted_reason")
    if state.get("halted_thesis_id"):
        blocker["halted_thesis_id"] = state.get("halted_thesis_id")
    if state.get("halted_thesis"):
        blocker["halted_thesis"] = state.get("halted_thesis")
    if state.get("manual_review_theses"):
        blocker["manual_review_theses"] = list(state.get("manual_review_theses") or [])
    if state.get("builder_failed_theses"):
        blocker["builder_failed_theses"] = list(state.get("builder_failed_theses") or [])
    if state.get("next_action"):
        blocker["next_action"] = state.get("next_action")
    if state.get("current_thesis"):
        blocker["current_thesis"] = state.get("current_thesis")
    if not blocker:
        return
    history = state.setdefault("history", {})
    if not isinstance(history, dict):
        history = {}
        state["history"] = history
    history["last_blocker"] = blocker
    state["resume_context"] = {"source": source, "blocker": blocker}


def _activate_builder_config(
    controller: "AutoresearchController",
    state: dict[str, Any],
    thesis_id: str,
    generated_config: str,
    *,
    execution_root: str | None = None,
    research_round: int | None = None,
) -> dict[str, Any]:
    _archive_reactivated_blocker(state, source="builder_activation")
    state["state"] = "running"
    controller.clear_terminal_metadata(state)
    state["activity"] = {
        "type": "round",
        "phase": "pending_backtest",
        "round": research_round,
        "config": generated_config,
        "thesis_id": thesis_id,
        "source": "builder",
    }
    if execution_root:
        state["activity"]["execution_root"] = execution_root
    if research_round is not None:
        state["research_round"] = research_round
    state["current_thesis"] = {
        "config": generated_config,
        "status": "ready_to_run",
        "selected_thesis_id": thesis_id,
        "source": "builder",
    }
    if execution_root:
        state["current_thesis"]["execution_root"] = execution_root
    state["next_action"] = {
        "type": "run_round",
        "config": generated_config,
        "benchmark_command": controller.family.benchmark_command(generated_config),
        "requires_trade_analysis": True,
        "source": "builder",
        "builder_thesis_id": thesis_id,
        "selected_thesis_id": thesis_id,
        "research_round": research_round,
    }
    if execution_root:
        state["next_action"]["execution_root"] = execution_root
    state["blockers"] = []
    state.pop("halted_thesis_id", None)
    state.pop("halted_reason", None)
    state.pop("halted_thesis", None)
    state.pop("manual_review_theses", None)
    state.pop("builder_failed_theses", None)
    state["selected_thesis_id"] = thesis_id
    state["selected_config_path"] = generated_config
    if research_round is not None:
        state["backtest_target_path"] = (
            f"runtime/jobs/job-{state.get('job')}/research/round-{research_round}/backtest"
        )
    controller.ctx.parent_backtest_run_id = ""
    base_root = (
        Path(execution_root)
        if execution_root
        else getattr(controller, "runtime_root", controller.root)
    )
    config_path = base_root / generated_config
    thesis_path = config_path.parent / "selected_thesis.json"
    thesis_payload: dict[str, Any] = {"thesis_id": thesis_id}
    if thesis_path.exists():
        try:
            loaded = json.loads(thesis_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"thesis.json unreadable for {thesis_id}: {exc}") from exc
        if isinstance(loaded, dict):
            thesis_payload = loaded
        else:
            raise RuntimeError(f"thesis.json is not an object for {thesis_id}")
    from research_types import BacktestContract

    controller.ctx.current_contract = BacktestContract.from_sidecar(
        contract_id=thesis_id,
        strategy_family=controller.family.name,
        baseline_config_path=f"configs/{controller.family.base_config_filename}",
        runtime_config={},
        sidecar=thesis_payload,
    )
    controller.write_state(state)
    return state


def _mark_builder_heartbeat_finished(
    state: dict[str, Any],
    thesis_id: str,
    status: str,
) -> None:
    heartbeat = state.setdefault("heartbeat", {})
    heartbeat["builder_status"] = status
    heartbeat["builder_thesis"] = thesis_id
    heartbeat["builder_finished_at"] = iso8601_utc_now()


def _attach_builder_runtime_context(
    state: dict[str, Any],
    builder_result: dict[str, Any],
) -> None:
    context = _builder_result_context(builder_result)
    if not context:
        return
    heartbeat = state.setdefault("heartbeat", {})
    heartbeat["builder_result_context"] = context


def _mark_builder_manual_review(
    controller: "AutoresearchController",
    state: dict[str, Any],
    thesis_id: str,
    thesis: dict[str, Any],
    builder_result: dict[str, Any],
    *,
    research_round: int | None = None,
) -> dict[str, Any]:
    error_code = str(builder_result.get("error_code") or "")
    if error_code in RESEARCH_RETRY_BUILDER_ERROR_CODES:
        state["state"] = "blocked"
        state.pop("activity", None)
        if research_round is not None:
            state["research_round"] = research_round
        controller.clear_terminal_metadata(state)
        reason = f"Builder failed for {thesis_id}: {error_code}"
        feedback = (
            f"Previous thesis '{thesis_id}' could not be built because {error_code}: "
            f"{builder_result.get('reason') or 'unknown builder validation failure'}. "
            "Revise the thesis/config_changes so the generated runtime config is valid, "
            "or abandon this mechanism if the requested config is not implementable."
        )
        state["rejection_feedback"] = feedback
        state["blockers"] = [
            {
                "kind": "research_retry_required",
                "detail": reason,
                "error_code": error_code,
                "builder_context": _builder_result_context(builder_result),
            }
        ]
        state["next_action"] = {
            "type": "research",
            "reason": feedback,
            "reason_code": "research_retry_required",
            "requires_subagent": True,
            "artifact_dir": _controller_research_artifact_dir(controller),
            "failed_thesis_id": thesis_id,
            "error_code": error_code,
            "builder_context": _builder_result_context(builder_result),
        }
        heartbeat = state.setdefault("heartbeat", {})
        raw_builder_status = str(builder_result.get("status") or "error")
        _mark_builder_heartbeat_finished(state, thesis_id, "research_retry_required")
        _attach_builder_runtime_context(state, builder_result)
        heartbeat["blocked_thesis"] = thesis_id
        heartbeat["blocked_builder_status"] = "research_retry_required"
        heartbeat["blocked_builder_result_status"] = raw_builder_status
        heartbeat["blocked_reason"] = feedback
        heartbeat["blocked_error_code"] = error_code
        controller.write_state(state)
        controller.write_current_md(state, controller.read_results())
        return state

    if error_code in DETERMINISTIC_BUILDER_ERROR_CODES:
        builder_failed = list(state.get("builder_failed_theses") or [])
        builder_failed.append(
            {
                "thesis_id": thesis_id,
                "round": (
                    research_round if research_round is not None else state.get("research_round")
                ),
                "thesis": thesis,
                "builder_result": builder_result,
                "builder_context": _builder_result_context(builder_result),
                "timestamp": iso8601_utc_now(),
            }
        )
        state["builder_failed_theses"] = builder_failed
        state["state"] = "blocked"
        state.pop("activity", None)
        if research_round is not None:
            state["research_round"] = research_round
        controller.clear_terminal_metadata(state)
        reason = f"Builder failed for {thesis_id}: {error_code}"
        state["blockers"] = [
            {
                "kind": "builder_failed",
                "detail": reason,
                "error_code": error_code,
                "builder_context": _builder_result_context(builder_result),
            }
        ]
        state["next_action"] = {
            "type": "builder_failed",
            "reason": reason,
            "error_code": error_code,
            "requires_subagent": True,
            "artifact_dir": f"{controller.family.name}-builder-failed",
            "builder_context": _builder_result_context(builder_result),
        }
        heartbeat = state.setdefault("heartbeat", {})
        raw_builder_status = str(builder_result.get("status") or "error")
        _mark_builder_heartbeat_finished(state, thesis_id, "builder_failed")
        _attach_builder_runtime_context(state, builder_result)
        heartbeat["blocked_thesis"] = thesis_id
        heartbeat["blocked_builder_status"] = "builder_failed"
        heartbeat["blocked_builder_result_status"] = raw_builder_status
        heartbeat["blocked_reason"] = reason
        heartbeat["blocked_error_code"] = error_code
        controller.write_state(state)
        controller.write_current_md(state, controller.read_results())
        return state

    manual_review = list(state.get("manual_review_theses") or [])
    manual_review.append(
        {
            "thesis_id": thesis_id,
            "round": research_round if research_round is not None else state.get("research_round"),
            "thesis": thesis,
            "builder_result": builder_result,
            "builder_context": _builder_result_context(builder_result),
            "timestamp": iso8601_utc_now(),
        }
    )
    state["manual_review_theses"] = manual_review
    state["state"] = "blocked"
    state.pop("activity", None)
    if research_round is not None:
        state["research_round"] = research_round
    controller.clear_terminal_metadata(state)
    state["blockers"] = [
        {
            "kind": "manual_review",
            "detail": f"Builder failed for {thesis_id}; thesis marked manual_review for operator follow-up.",
            "builder_context": _builder_result_context(builder_result),
        }
    ]
    state["next_action"] = {
        "type": "manual_review",
        "reason": f"Builder failed for {thesis_id}; operator review required.",
        "requires_subagent": False,
        "artifact_dir": f"{controller.family.name}-manual-review",
        "builder_context": _builder_result_context(builder_result),
    }
    heartbeat = state.setdefault("heartbeat", {})
    raw_builder_status = str(builder_result.get("status") or "error")
    _mark_builder_heartbeat_finished(state, thesis_id, "manual_review")
    _attach_builder_runtime_context(state, builder_result)
    heartbeat["blocked_thesis"] = thesis_id
    heartbeat["blocked_builder_status"] = "manual_review"
    heartbeat["blocked_builder_result_status"] = raw_builder_status
    heartbeat["blocked_reason"] = state["next_action"]["reason"]
    controller.write_state(state)
    controller.write_current_md(state, controller.read_results())
    return state


def _mark_builder_running(
    controller: "AutoresearchController",
    state: dict[str, Any],
    thesis_id: str,
    *,
    research_round: int | None = None,
) -> dict[str, Any]:
    state["state"] = "building"
    controller.clear_terminal_metadata(state)
    state["activity"] = {
        "type": "builder",
        "phase": "builder_running",
        "round": research_round,
        "thesis_id": thesis_id,
    }
    if research_round is not None:
        state["research_round"] = research_round
    state["current_thesis"] = {"thesis_id": thesis_id, "status": "builder_running"}
    state["blockers"] = [
        {
            "kind": "builder_running",
            "detail": f"Builder is running for {thesis_id}.",
        }
    ]
    state["next_action"] = {
        "type": "builder_running",
        "reason": f"Builder is running for {thesis_id}.",
        "builder_thesis_id": thesis_id,
        "requires_subagent": False,
    }
    heartbeat = state.setdefault("heartbeat", {})
    for stale_key in (
        "blocked_builder_status",
        "blocked_builder_result_status",
        "blocked_reason",
        "blocked_thesis",
    ):
        heartbeat.pop(stale_key, None)
    heartbeat["builder_status"] = "running"
    heartbeat["builder_thesis"] = thesis_id
    heartbeat["builder_started_at"] = iso8601_utc_now()
    controller.write_state(state)
    return state


def _refresh_reflexio_export_after_builder(
    root,
    *,
    research_round: int | None,
    thesis_id: str,
    family: str,
    builder_result: dict[str, Any],
    canonical_trace_path: Path | None = None,
) -> None:
    """Refresh the round Reflexio export after builder events are traced.

    Research-round exports are first written before the long builder subprocess.
    Refreshing the same export after builder completion keeps one source of
    truth while allowing the next builder attempt to receive builder-specific
    Reflexion memory.
    """
    if research_round is None:
        return
    root_path = Path(root)
    current = _find_reflexio_export_for_thesis(
        root_path,
        research_round,
        thesis_id,
        artifact_root=root_path,
    )
    if current is None:
        return
    try:
        payload = json.loads(current.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("could not refresh reflexio export after builder: %s", exc)
        return
    if not isinstance(payload, dict):
        return

    from trace_adapters.reflexio import build_reflexio_export_package

    trace_path = canonical_trace_path
    if trace_path is None:
        from trace_sdk import get_event_file

        trace_path = get_event_file()
    episode = payload.get("episode") if isinstance(payload.get("episode"), dict) else {}
    reflection = payload.get("reflection") if isinstance(payload.get("reflection"), dict) else {}
    resources = payload.get("resources") if isinstance(payload.get("resources"), dict) else {}
    outcome = _builder_reflexio_outcome(builder_result)
    package = build_reflexio_export_package(
        research_round=int(episode.get("round") or research_round),
        thesis_id=str(episode.get("thesis_id") or thesis_id),
        outcome=outcome,
        family=str(episode.get("family") or family),
        reasoning=str(reflection.get("reasoning") or ""),
        validation_failure_reason=read_validation_failure_reason(reflection),
        quality=reflection.get("quality") if isinstance(reflection.get("quality"), dict) else {},
        usage=resources.get("usage") if isinstance(resources.get("usage"), dict) else {},
        canonical_trace_path=trace_path,
    )
    target_dir = current.parent
    for filename, content in package.get("files", {}).items():
        _write_text_atomic(
            target_dir / filename,
            json.dumps(content, indent=2, sort_keys=True) + "\n",
        )
    _write_text_atomic(
        target_dir / "package.json",
        json.dumps(package, indent=2, sort_keys=True) + "\n",
    )


def _find_reflexio_export_for_thesis(
    root_path: Path,
    research_round: int,
    thesis_id: str,
    *,
    artifact_root: Path | None = None,
) -> Path | None:
    search_root = artifact_root or root_path
    matches = sorted(
        (search_root / "research" / f"round-{research_round}" / "trace_exports").glob(
            f"round-{research_round:03d}-*/reflexio/reflexio-event.json"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    requested = str(thesis_id)
    for path in matches:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("could not inspect reflexio export for builder refresh: %s", exc)
            continue
        episode = payload.get("episode") if isinstance(payload, dict) else None
        if isinstance(episode, dict) and str(episode.get("thesis_id") or "") == requested:
            return path
    return None


def _builder_reflexio_outcome(builder_result: dict[str, Any]) -> str:
    if builder_result.get("status") == "completed" and builder_result.get("validation_passed"):
        return "compiled"
    return "builder_failed"


def build_missing_primitives_for_state(
    controller: "AutoresearchController",
    state: dict[str, Any],
    thesis_id: str,
    thesis: dict[str, Any],
    *,
    research_round: int | None = None,
) -> dict[str, Any]:
    trace("BUILDER", f"start thesis={thesis_id}")
    trace("LOOP", f"building halted thesis={thesis_id}")
    runtime_root = getattr(controller, "runtime_root", None) or controller.root
    job_runtime_root = getattr(controller, "job_runtime_root", runtime_root)
    raw_job = state.get("job")
    try:
        job = int(raw_job) if raw_job is not None else None
    except (TypeError, ValueError):
        job = None
    round_root = (
        research_round_root(runtime_root, job, int(research_round))
        if research_round is not None and job is not None
        else job_runtime_root
    )
    state = _mark_builder_running(
        controller,
        state,
        thesis_id,
        research_round=research_round,
    )

    try:
        import compiler_pipeline

        if job is None or research_round is None:
            raise ValueError(
                "build_missing_primitives dispatch requires job and research_round; "
                f"got job={raw_job!r}, research_round={research_round!r}"
            )
        round_id = research_round_id(int(job), int(research_round))
        builder_result = compiler_pipeline.build_missing_primitives(
            controller.root,
            thesis_id,
            artifact_root=round_root,
            research_round_id=round_id,
        )
    except (
        Exception
    ) as exc:  # noqa: BLE001 — builder failures must route to manual-review, not crash the loop
        _log.exception("builder raised for thesis=%s", thesis_id)
        builder_result = {
            "status": "error",
            "reason": f"builder exception for {thesis_id}: {type(exc).__name__}: {exc}",
            "generated_config": None,
            "validation_passed": False,
        }
    trace(
        "BUILDER",
        f"finish thesis={thesis_id} status={builder_result.get('status')} "
        f"generated={builder_result.get('generated_config') or ''}",
    )
    try:
        _refresh_reflexio_export_after_builder(
            round_root,
            research_round=research_round,
            thesis_id=thesis_id,
            family=controller.family.name,
            builder_result=builder_result,
        )
    except Exception as exc:  # noqa: BLE001 - improvement export must not block orchestration
        _log.warning("refreshing reflexio export after builder failed: %s", exc)
    if builder_result.get("status") == "completed" and builder_result.get("validation_passed"):
        generated_config = builder_result.get("generated_config")
        execution_root = builder_result.get("execution_root")
        base_root = (
            Path(execution_root)
            if isinstance(execution_root, str)
            else getattr(controller, "runtime_root", controller.root)
        )
        if generated_config and (base_root / generated_config).exists():
            _mark_builder_heartbeat_finished(state, thesis_id, "completed")
            _attach_builder_runtime_context(state, builder_result)
            try:
                state = _activate_builder_config(
                    controller,
                    state,
                    thesis_id,
                    generated_config,
                    execution_root=execution_root if isinstance(execution_root, str) else None,
                    research_round=research_round,
                )
            except Exception as exc:  # noqa: BLE001
                builder_result = {
                    **builder_result,
                    "status": "error",
                    "error_code": "builder_activation_failed",
                    "reason": f"failed to activate builder config for {thesis_id}: {exc}",
                    "validation_passed": False,
                }
                state = _mark_builder_manual_review(
                    controller,
                    state,
                    thesis_id,
                    thesis,
                    builder_result,
                    research_round=research_round,
                )
                trace("LOOP", f"builder activation failed thesis={thesis_id}: {exc}")
                return state
            trace("LOOP", f"builder generated thesis={thesis_id} -> {generated_config}")
            return state
    state = _mark_builder_manual_review(
        controller,
        state,
        thesis_id,
        thesis,
        builder_result,
        research_round=research_round,
    )
    trace("LOOP", f"builder failed thesis={thesis_id}; marked manual_review")
    return state


def try_resume_halted_thesis(controller: "AutoresearchController") -> dict[str, Any] | None:
    """Resume a halted thesis if its runtime config is now satisfiable."""
    state = controller.read_state()
    halted_id = state.get("halted_thesis_id")
    if not halted_id or state.get("halted_reason") != "requires_code_change":
        return None
    raw_thesis = state.get("halted_thesis", {})
    config_changes = raw_thesis.get("config_changes", {})
    if not config_changes:
        return None

    import yaml as _yaml

    base = (
        _yaml.safe_load(
            (controller.root / "configs" / controller.family.base_config_filename).read_text()
        )
        or {}
    )
    if not isinstance(base, dict):
        return None
    if set(config_changes) - set(base):
        return None
    runtime = {**base, **config_changes}
    try:
        runtime = STRATEGIES[controller.family.name].validate_runtime_config_scope(runtime)
    except ValueError:
        return None
    raw_round = state.get("research_round")
    try:
        research_round = int(raw_round)
    except (TypeError, ValueError):
        research_round = -1
    if research_round < 1:
        raise RuntimeError(
            f"halted thesis resume requires a non-baseline research round, got {raw_round!r}"
        )
    runtime_root = getattr(controller, "runtime_root", None) or controller.root
    exp_dir = research_round_root(runtime_root, int(state.get("job")), research_round)
    exp_dir.mkdir(parents=True, exist_ok=True)
    config_abspath = exp_dir / "selected_config.json"
    config_path = serialize_config_path(config_abspath, code_root=runtime_root)
    _write_text_atomic(config_abspath, json.dumps(runtime, indent=2) + "\n")
    resumed_thesis = dict(raw_thesis)
    resumed_thesis.setdefault("thesis_id", halted_id)
    resumed_thesis.setdefault("config_changes", config_changes)
    thesis_sidecar = {
        "contract_id": halted_id,
        "strategy_family": controller.family.name,
        "thesis_id": resumed_thesis.get("thesis_id", halted_id),
        "hypothesis": resumed_thesis.get("hypothesis", ""),
        "mechanism": resumed_thesis.get("mechanism", ""),
        "config_changes": resumed_thesis.get("config_changes", {}),
        "expected_effects": resumed_thesis.get("expected_effects", []),
        "disqualifiers": resumed_thesis.get("disqualifiers", []),
        "required_diagnostics": resumed_thesis.get("required_diagnostics", []),
        "required_diagnostic_specs": resumed_thesis.get("required_diagnostic_specs", []),
    }
    _write_text_atomic(
        exp_dir / "selected_thesis.json",
        json.dumps(thesis_sidecar, indent=2) + "\n",
    )
    from research_types import BacktestContract

    contract = BacktestContract.from_sidecar(
        contract_id=halted_id,
        strategy_family=controller.family.name,
        baseline_config_path=f"configs/{controller.family.base_config_filename}",
        runtime_config=runtime,
        sidecar=thesis_sidecar,
        status="ready_to_run",
    )
    _write_text_atomic(
        exp_dir / "selected_contract.json",
        contract.model_dump_json(indent=2) + "\n",
    )
    controller.ctx.current_contract = contract
    controller.ctx.parent_backtest_run_id = ""
    _archive_reactivated_blocker(state, source="resumed_halted_thesis")
    state["state"] = "running"
    controller.clear_terminal_metadata(state)
    state["current_thesis"] = {"config": config_path, "status": "ready_to_run"}
    state["next_action"] = {
        "type": "run_round",
        "config": config_path,
        "benchmark_command": controller.family.benchmark_command(config_path),
        "requires_trade_analysis": True,
        "source": "resumed_halted_thesis",
        "research_round": research_round,
        "selected_thesis_id": resumed_thesis.get("thesis_id", halted_id),
    }
    state["selected_thesis_id"] = resumed_thesis.get("thesis_id", halted_id)
    state["selected_config_path"] = config_path
    state["backtest_target_path"] = (
        f"runtime/jobs/job-{state.get('job')}/research/round-{research_round}/backtest"
    )
    state["blockers"] = []
    state.pop("halted_thesis_id", None)
    state.pop("halted_reason", None)
    state.pop("halted_thesis", None)
    state.pop("manual_review_theses", None)
    state.pop("builder_failed_theses", None)
    controller.write_state(state)
    trace("LOOP", f"resumed halted thesis={halted_id}")
    return state


def apply_forced_baseline_rerun(
    controller: "AutoresearchController", baseline_action: dict[str, Any]
) -> dict[str, Any]:
    state = controller.read_state()
    _archive_reactivated_blocker(state, source="forced_baseline_rerun")
    state["state"] = "running"
    controller.clear_terminal_metadata(state)
    state["research_round"] = 0
    state.pop("research_round_in_progress", None)
    state["next_action"] = baseline_action
    state["blockers"] = []
    state.pop("halted_thesis_id", None)
    state.pop("halted_reason", None)
    state.pop("halted_thesis", None)
    state.pop("manual_review_theses", None)
    state.pop("builder_failed_theses", None)
    controller.write_state(state)
    return state


def resolve_next_action(controller: "AutoresearchController") -> dict[str, Any]:
    baseline_action = controller._check_baseline_rerun()
    if baseline_action:
        return controller._apply_forced_baseline_rerun(baseline_action)

    state = controller.read_state()
    results = controller.read_results()
    next_action = state.get("next_action")
    if (
        state.get("state") == "running"
        and isinstance(next_action, dict)
        and next_action.get("source") == "registered_prediction_retest"
    ):
        return state

    # Fresh jobs must always plan baseline first when the controller has been
    # reset to `running`, even if a previous halted thesis was preserved in
    # state for later recovery.
    if state.get("state") == "running" and not results:
        return controller.reconcile_state()

    halted_id = state.get("halted_thesis_id")
    if halted_id and state.get("halted_reason") == "requires_code_change":
        resumed = controller._try_resume_halted_thesis()
        if resumed is not None:
            return resumed
        raw_thesis = state.get("halted_thesis", {})
        return build_missing_primitives_for_state(
            controller,
            state,
            halted_id,
            raw_thesis if isinstance(raw_thesis, dict) else {},
            research_round=state.get("research_round"),
        )

    return controller.reconcile_state()
