from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

_log = logging.getLogger(__name__)

from persistence_utils import utc_now_iso8601 as iso8601_utc_now
from persistence_utils import write_text_atomic as _write_text_atomic
from strategies import STRATEGIES
from trace_sdk import trace

if TYPE_CHECKING:
    from autoresearch_controller import AutoresearchController


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


def _mark_builder_manual_review(
    controller: "AutoresearchController",
    state: dict[str, Any],
    thesis_id: str,
    thesis: dict[str, Any],
    builder_result: dict[str, Any],
    *,
    research_round: int | None = None,
) -> dict[str, Any]:
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
    heartbeat["blocked_thesis"] = thesis_id
    heartbeat["blocked_builder_status"] = str(builder_result.get("status") or "error")
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
    heartbeat["builder_status"] = "running"
    heartbeat["builder_thesis"] = thesis_id
    heartbeat["builder_started_at"] = iso8601_utc_now()
    controller.write_state(state)
    return state


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
    import compiler_pipeline

    try:
        builder_result = compiler_pipeline.build_missing_primitives(controller.root, thesis_id)
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
    if builder_result.get("status") == "completed" and builder_result.get("validation_passed"):
        generated_config = builder_result.get("generated_config")
        if generated_config and (controller.root / generated_config).exists():
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
