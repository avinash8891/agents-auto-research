from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from autoresearch_planning import check_baseline_rerun as _planning_check_baseline_rerun
from persistence_utils import write_text_atomic as _write_text_atomic
from strategies import STRATEGIES
from trace_sdk import trace

if TYPE_CHECKING:
    from autoresearch_controller import AutoresearchController


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


def check_baseline_rerun(controller: "AutoresearchController") -> dict[str, Any] | None:
    return _planning_check_baseline_rerun(
        controller.root,
        controller.family,
        controller.baseline_tracker,
        controller.current_commit(),
        controller.read_results(),
    )


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


def resolve_next_action(
    controller: "AutoresearchController", state: dict[str, Any] | None = None
) -> dict[str, Any]:
    resumed = try_resume_halted_thesis(controller)
    if resumed is not None:
        return resumed

    baseline_action = check_baseline_rerun(controller)
    if baseline_action:
        return apply_forced_baseline_rerun(controller, baseline_action)

    return controller.reconcile_state()
