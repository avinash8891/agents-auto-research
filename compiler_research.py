from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from config_hash import _config_hash
from persistence_utils import write_text_atomic
from research_types import ExperimentContract
from strategies import STRATEGIES
from strategy_family import load_family

if TYPE_CHECKING:
    from research_types import ResearchThesis

def _needs_code_contract(
    thesis: "ResearchThesis",
    root: Path,
    *,
    status: str = "needs_code",
) -> "ExperimentContract":
    family_name = thesis.strategy_family
    experiment_id = thesis.thesis_id
    experiment_dir = root / "experiments" / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(experiment_dir / "thesis.json", thesis.model_dump_json(indent=2) + "\n")
    contract = ExperimentContract(
        experiment_id=experiment_id,
        thesis_id=thesis.thesis_id,
        strategy_family=family_name,
        baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
        runtime_config={},
        hypothesis=thesis.hypothesis,
        mechanism=thesis.mechanism,
        expected_effects=thesis.expected_effects,
        disqualifiers=thesis.disqualifiers,
        required_diagnostics=thesis.required_diagnostics,
        status=status,
    )
    write_text_atomic(
        experiment_dir / "contract.json", contract.model_dump_json(indent=2) + "\n"
    )
    return contract


def _compile_runtime_config_contract(
    thesis: "ResearchThesis",
    root: Path,
    runtime_config: dict,
) -> "ExperimentContract":
    family_name = thesis.strategy_family
    experiment_id = _config_hash(runtime_config)
    experiment_dir = root / "experiments" / experiment_id
    experiment_dir.mkdir(parents=True, exist_ok=True)

    write_text_atomic(experiment_dir / "thesis.json", thesis.model_dump_json(indent=2) + "\n")
    write_text_atomic(
        experiment_dir / "runtime_config.json", json.dumps(runtime_config, indent=2) + "\n"
    )
    contract = ExperimentContract(
        experiment_id=experiment_id,
        thesis_id=thesis.thesis_id,
        strategy_family=family_name,
        baseline_config_path=f"configs/{load_family(family_name).base_config_filename}",
        runtime_config=runtime_config,
        hypothesis=thesis.hypothesis,
        mechanism=thesis.mechanism,
        expected_effects=thesis.expected_effects,
        disqualifiers=thesis.disqualifiers,
        required_diagnostics=thesis.required_diagnostics,
        status="ready_to_run",
    )
    write_text_atomic(
        experiment_dir / "contract.json", contract.model_dump_json(indent=2) + "\n"
    )
    return contract


def _runtime_config_for_registered_strategy(family_name: str, config_changes: dict) -> dict | None:
    strategy = STRATEGIES.get(family_name)
    if strategy is None:
        return None
    defaults = strategy.get_defaults()
    invalid_keys = sorted(set(config_changes) - set(defaults))
    if invalid_keys:
        return {}
    return {**defaults, **config_changes}


def compile_research_thesis(
    thesis: "ResearchThesis",
    root: Path,
) -> "ExperimentContract":
    """Convert a validated ResearchThesis into an ExperimentContract.

    Creates:
      experiments/{experiment_id}/thesis.json
      experiments/{experiment_id}/contract.json
      experiments/{experiment_id}/runtime_config.json

    The experiment_id is a content hash of the runtime config.
    """
    family_name = thesis.strategy_family

    if thesis.requires_code_change:
        return _needs_code_contract(thesis, root)

    runtime_config = _runtime_config_for_registered_strategy(family_name, thesis.config_changes)
    if runtime_config is not None:
        if not runtime_config:
            return _needs_code_contract(thesis, root)
        violations = STRATEGIES[family_name].validate_runtime_config(runtime_config)
        if violations:
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )
        return _compile_runtime_config_contract(thesis, root, runtime_config)

    raise ValueError(f"compile_research_thesis does not support family '{family_name}' yet")
