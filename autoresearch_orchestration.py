from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

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
RESEARCH_RETRY_BUILDER_ERROR_CODES = frozenset({"builder_config_validation_failed"})


def _activate_builder_config(
    controller: "AutoresearchController",
    state: dict[str, Any],
    thesis_id: str,
    generated_config: str,
    *,
    research_round: int | None = None,
) -> dict[str, Any]:
    state["state"] = "running"
    controller.clear_terminal_metadata(state)
    if research_round is not None:
        state["research_round"] = research_round
    state["current_thesis"] = {"config": generated_config, "status": "ready_to_run"}
    state["next_action"] = {
        "type": "run_experiment",
        "config": generated_config,
        "benchmark_command": controller.family.benchmark_command(generated_config),
        "requires_trade_analysis": True,
        "source": "builder",
        "builder_thesis_id": thesis_id,
    }
    state["blockers"] = []
    state.pop("halted_thesis_id", None)
    state.pop("halted_reason", None)
    state.pop("halted_thesis", None)
    controller.ctx.parent_experiment_id = ""
    thesis_path = controller.root / "experiments" / thesis_id / "thesis.json"
    thesis_payload: dict[str, Any] = {"thesis_id": thesis_id}
    if thesis_path.exists():
        try:
            loaded = json.loads(thesis_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("thesis.json unreadable for %s: %s", thesis_id, exc)
            loaded = {}
        if isinstance(loaded, dict):
            thesis_payload = loaded
    controller.ctx.current_contract = SimpleNamespace(
        experiment_id=thesis_id,
        strategy_family=controller.family.name,
        thesis_id=thesis_payload.get("thesis_id", thesis_id),
        hypothesis=thesis_payload.get("hypothesis", ""),
        mechanism=thesis_payload.get("mechanism", ""),
        config_changes=thesis_payload.get("config_changes", {}),
        expected_effects=thesis_payload.get("expected_effects", []),
        disqualifiers=thesis_payload.get("disqualifiers", []),
        required_diagnostics=thesis_payload.get("required_diagnostics", []),
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
            }
        ]
        state["next_action"] = {
            "type": "research",
            "reason": feedback,
            "reason_code": "research_retry_required",
            "requires_subagent": True,
            "artifact_dir": controller.family.research_dirname,
            "failed_thesis_id": thesis_id,
            "error_code": error_code,
        }
        heartbeat = state.setdefault("heartbeat", {})
        raw_builder_status = str(builder_result.get("status") or "error")
        _mark_builder_heartbeat_finished(state, thesis_id, "research_retry_required")
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
                "timestamp": iso8601_utc_now(),
            }
        )
        state["builder_failed_theses"] = builder_failed
        state["state"] = "blocked"
        if research_round is not None:
            state["research_round"] = research_round
        controller.clear_terminal_metadata(state)
        reason = f"Builder failed for {thesis_id}: {error_code}"
        state["blockers"] = [
            {
                "kind": "builder_failed",
                "detail": reason,
                "error_code": error_code,
            }
        ]
        state["next_action"] = {
            "type": "builder_failed",
            "reason": reason,
            "error_code": error_code,
            "requires_subagent": True,
            "artifact_dir": f"{controller.family.name}-builder-failed",
        }
        heartbeat = state.setdefault("heartbeat", {})
        raw_builder_status = str(builder_result.get("status") or "error")
        _mark_builder_heartbeat_finished(state, thesis_id, "builder_failed")
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
            "timestamp": iso8601_utc_now(),
        }
    )
    state["manual_review_theses"] = manual_review
    state["state"] = "blocked"
    if research_round is not None:
        state["research_round"] = research_round
    controller.clear_terminal_metadata(state)
    state["blockers"] = [
        {
            "kind": "manual_review",
            "detail": f"Builder failed for {thesis_id}; thesis marked manual_review for operator follow-up.",
        }
    ]
    state["next_action"] = {
        "type": "manual_review",
        "reason": f"Builder failed for {thesis_id}; operator review required.",
        "requires_subagent": False,
        "artifact_dir": f"{controller.family.name}-manual-review",
    }
    heartbeat = state.setdefault("heartbeat", {})
    raw_builder_status = str(builder_result.get("status") or "error")
    _mark_builder_heartbeat_finished(state, thesis_id, "manual_review")
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
    current = _find_reflexio_export_for_thesis(root_path, research_round, thesis_id)
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
        rejection_reason=str(reflection.get("rejection_reason") or ""),
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
    root_path: Path, research_round: int, thesis_id: str
) -> Path | None:
    matches = sorted(
        (root_path / "trace_exports").glob(
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
    state = _mark_builder_running(
        controller,
        state,
        thesis_id,
        research_round=research_round,
    )

    try:
        import compiler_pipeline

        builder_result = compiler_pipeline.build_missing_primitives(
            controller.root, thesis_id, artifact_root=controller.job_runtime_root
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
            controller.root,
            research_round=research_round,
            thesis_id=thesis_id,
            family=controller.family.name,
            builder_result=builder_result,
        )
    except Exception as exc:  # noqa: BLE001 - improvement export must not block orchestration
        _log.warning("refreshing reflexio export after builder failed: %s", exc)
    if builder_result.get("status") == "completed" and builder_result.get("validation_passed"):
        generated_config = builder_result.get("generated_config")
        if generated_config and (controller.root / generated_config).exists():
            _mark_builder_heartbeat_finished(state, thesis_id, "completed")
            state = _activate_builder_config(
                controller,
                state,
                thesis_id,
                generated_config,
                research_round=research_round,
            )
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
    exp_dir = controller.root / "experiments" / halted_id
    exp_dir.mkdir(parents=True, exist_ok=True)
    config_path = f"experiments/{halted_id}/runtime_config.json"
    _write_text_atomic(controller.root / config_path, json.dumps(runtime, indent=2) + "\n")
    resumed_thesis = dict(raw_thesis)
    resumed_thesis.setdefault("thesis_id", halted_id)
    resumed_thesis.setdefault("config_changes", config_changes)
    thesis_sidecar = {
        "experiment_id": halted_id,
        "strategy_family": controller.family.name,
        "thesis_id": resumed_thesis.get("thesis_id", halted_id),
        "hypothesis": resumed_thesis.get("hypothesis", ""),
        "mechanism": resumed_thesis.get("mechanism", ""),
        "config_changes": resumed_thesis.get("config_changes", {}),
        "expected_effects": resumed_thesis.get("expected_effects", []),
        "disqualifiers": resumed_thesis.get("disqualifiers", []),
        "required_diagnostics": resumed_thesis.get("required_diagnostics", []),
    }
    _write_text_atomic(
        exp_dir / "thesis.json",
        json.dumps(thesis_sidecar, indent=2) + "\n",
    )
    controller.ctx.current_contract = SimpleNamespace(
        experiment_id=halted_id,
        strategy_family=controller.family.name,
        thesis_id=thesis_sidecar["thesis_id"],
        hypothesis=thesis_sidecar["hypothesis"],
        mechanism=thesis_sidecar["mechanism"],
        config_changes=thesis_sidecar["config_changes"],
        expected_effects=thesis_sidecar["expected_effects"],
        disqualifiers=thesis_sidecar["disqualifiers"],
        required_diagnostics=thesis_sidecar["required_diagnostics"],
    )
    controller.ctx.parent_experiment_id = ""
    state["state"] = "running"
    controller.clear_terminal_metadata(state)
    state["current_thesis"] = {"config": config_path, "status": "ready_to_run"}
    state["next_action"] = {
        "type": "run_experiment",
        "config": config_path,
        "benchmark_command": controller.family.benchmark_command(config_path),
        "requires_trade_analysis": True,
        "source": "resumed_halted_thesis",
    }
    state["blockers"] = []
    state.pop("halted_thesis_id", None)
    state.pop("halted_reason", None)
    state.pop("halted_thesis", None)
    controller.write_state(state)
    trace("LOOP", f"resumed halted thesis={halted_id}")
    return state


def apply_forced_baseline_rerun(
    controller: "AutoresearchController", baseline_action: dict[str, Any]
) -> dict[str, Any]:
    state = controller.read_state()
    state["state"] = "running"
    controller.clear_terminal_metadata(state)
    state["next_action"] = baseline_action
    state["blockers"] = []
    controller.write_state(state)
    return state


def resolve_next_action(controller: "AutoresearchController") -> dict[str, Any]:
    baseline_action = controller._check_baseline_rerun()
    if baseline_action:
        return controller._apply_forced_baseline_rerun(baseline_action)

    state = controller.read_state()
    results = controller.read_results()

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
