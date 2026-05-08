from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from config_hash import _config_hash
from persistence_utils import write_text_atomic
from research_types import ExperimentContract
from strategies import STRATEGIES
from strategy_family import load_family

if TYPE_CHECKING:
    from research_types import ResearchThesis


def _baseline_config_path_for_family(family_name: str) -> str:
    return f"configs/{load_family(family_name).base_config_filename}"


def _resolved_base_config_path(thesis: "ResearchThesis") -> str:
    return thesis.base_config_path or _baseline_config_path_for_family(thesis.strategy_family)


def _load_base_runtime_config(root: Path, thesis: "ResearchThesis") -> dict:
    base_path = _resolved_base_config_path(thesis)
    path = root / base_path
    if thesis.base_config_path and not path.exists():
        raise ValueError(
            f"Base config path does not exist for thesis '{thesis.thesis_id}': {base_path}"
        )
    if not path.exists():
        return STRATEGIES[thesis.strategy_family].get_defaults()
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError(
            f"Base config must be a mapping for thesis '{thesis.thesis_id}': {base_path}"
        )
    runtime_config = payload.get("runtime_config", payload)
    if not isinstance(runtime_config, dict):
        raise ValueError(
            f"Base runtime config must be a mapping for thesis '{thesis.thesis_id}': {base_path}"
        )
    return dict(runtime_config)


def _needs_code_contract(
    thesis: "ResearchThesis",
    root: Path,
    *,
    artifact_root: Path | None = None,
    status: str = "needs_code",
) -> "ExperimentContract":
    family_name = thesis.strategy_family
    experiment_id = thesis.thesis_id
    experiment_dir = (artifact_root or root) / "experiments" / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(experiment_dir / "thesis.json", thesis.model_dump_json(indent=2) + "\n")
    contract = ExperimentContract(
        experiment_id=experiment_id,
        thesis_id=thesis.thesis_id,
        strategy_family=family_name,
        baseline_config_path=_resolved_base_config_path(thesis),
        base_experiment_id=thesis.base_experiment_id,
        base_config_hash=_config_hash(_load_base_runtime_config(root, thesis)),
        runtime_config={},
        hypothesis=thesis.hypothesis,
        mechanism=thesis.mechanism,
        expected_effects=thesis.expected_effects,
        disqualifiers=thesis.disqualifiers,
        required_diagnostics=thesis.required_diagnostics,
        status=status,
    )
    write_text_atomic(experiment_dir / "contract.json", contract.model_dump_json(indent=2) + "\n")
    return contract


def _compile_runtime_config_contract(
    thesis: "ResearchThesis",
    root: Path,
    runtime_config: dict,
    *,
    artifact_root: Path | None = None,
) -> "ExperimentContract":
    family_name = thesis.strategy_family
    experiment_id = _config_hash(runtime_config)
    experiment_dir = (artifact_root or root) / "experiments" / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)

    write_text_atomic(experiment_dir / "thesis.json", thesis.model_dump_json(indent=2) + "\n")
    write_text_atomic(
        experiment_dir / "runtime_config.json", json.dumps(runtime_config, indent=2) + "\n"
    )
    contract = ExperimentContract(
        experiment_id=experiment_id,
        thesis_id=thesis.thesis_id,
        strategy_family=family_name,
        baseline_config_path=_resolved_base_config_path(thesis),
        base_experiment_id=thesis.base_experiment_id,
        base_config_hash=_config_hash(_load_base_runtime_config(root, thesis)),
        runtime_config=runtime_config,
        hypothesis=thesis.hypothesis,
        mechanism=thesis.mechanism,
        expected_effects=thesis.expected_effects,
        disqualifiers=thesis.disqualifiers,
        required_diagnostics=thesis.required_diagnostics,
        status="ready_to_run",
    )
    write_text_atomic(experiment_dir / "contract.json", contract.model_dump_json(indent=2) + "\n")
    return contract


def _runtime_config_for_registered_strategy(
    family_name: str, config_changes: dict, base_config: dict
) -> dict | None:
    strategy = STRATEGIES.get(family_name)
    if strategy is None:
        return None
    defaults = strategy.get_defaults()
    allowed_keys = set(defaults) | set(base_config)
    invalid_keys = sorted(set(config_changes) - allowed_keys)
    if invalid_keys:
        return {}
    return {**base_config, **config_changes}


def _format_noop_config_change_error(thesis: "ResearchThesis", base_config: dict) -> str:
    unchanged_keys: list[str] = []
    for key, value in thesis.config_changes.items():
        if base_config.get(key) == value:
            unchanged_keys.append(f"{key}={value!r}")
    detail = (
        f" Unchanged keys: {', '.join(unchanged_keys)}."
        if unchanged_keys
        else " No config_changes were provided."
    )
    return (
        f"Thesis '{thesis.thesis_id}' rejected: config_changes did not change "
        f"runtime_config relative to base_config_path '{_resolved_base_config_path(thesis)}'."
        f"{detail}"
    )


def compile_research_thesis(
    thesis: "ResearchThesis",
    root: Path,
    *,
    artifact_root: Path | None = None,
) -> "ExperimentContract":
    """Convert a validated ResearchThesis into an ExperimentContract.

    Creates:
      {artifact_root or root}/experiments/{experiment_id}/thesis.json
      {artifact_root or root}/experiments/{experiment_id}/contract.json
      {artifact_root or root}/experiments/{experiment_id}/runtime_config.json

    The experiment_id is a content hash of the runtime config.
    """
    family_name = thesis.strategy_family

    if thesis.requires_code_change:
        return _needs_code_contract(thesis, root, artifact_root=artifact_root)

    base_config = _load_base_runtime_config(root, thesis)
    runtime_config = _runtime_config_for_registered_strategy(
        family_name, thesis.config_changes, base_config
    )
    if runtime_config is not None:
        if not runtime_config:
            return _needs_code_contract(thesis, root, artifact_root=artifact_root)
        if runtime_config == base_config:
            raise ValueError(_format_noop_config_change_error(thesis, base_config))
        violations = STRATEGIES[family_name].validate_runtime_config(runtime_config)
        if violations:
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )
        return _compile_runtime_config_contract(
            thesis, root, runtime_config, artifact_root=artifact_root
        )

    raise ValueError(f"compile_research_thesis does not support family '{family_name}' yet")
