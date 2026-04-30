from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from compiler_defaults import _get_ema_defaults, _get_orb_defaults
from compiler_validate import validate_ema_runtime_config, validate_orb_runtime_config
from config_hash import _config_hash
from strategy_family import load_family

if TYPE_CHECKING:
    from research_types import ExperimentContract, ResearchThesis


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
    from research_types import ExperimentContract

    family_name = thesis.strategy_family

    if thesis.requires_code_change:
        experiment_id = thesis.thesis_id
        experiment_dir = root / "experiments" / experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)
        (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
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
            status="needs_code",
        )
        (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
        return contract

    if family_name == "ema":
        defaults = _get_ema_defaults()
        allowed = set(defaults.keys())
        invalid_keys = sorted(set(thesis.config_changes.keys()) - allowed)

        if invalid_keys:
            experiment_id = thesis.thesis_id
            experiment_dir = root / "experiments" / experiment_id
            experiment_dir.mkdir(parents=True, exist_ok=True)
            (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
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
                status="needs_code",
            )
            (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
            return contract

        runtime_config = {**defaults, **thesis.config_changes}

        # Hard constraints: reject strategically nonsensical configs
        violations = validate_ema_runtime_config(runtime_config)
        if violations:
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )

        experiment_id = _config_hash(runtime_config)
        experiment_dir = root / "experiments" / experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)

        (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
        (experiment_dir / "runtime_config.json").write_text(
            json.dumps(runtime_config, indent=2) + "\n"
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
        (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
        return contract

    if family_name == "orb":
        defaults = _get_orb_defaults()
        allowed = set(defaults.keys())
        invalid_keys = sorted(set(thesis.config_changes.keys()) - allowed)

        if invalid_keys:
            experiment_id = thesis.thesis_id
            experiment_dir = root / "experiments" / experiment_id
            experiment_dir.mkdir(parents=True, exist_ok=True)
            (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
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
                status="needs_code",
            )
            (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
            return contract

        runtime_config = {**defaults, **thesis.config_changes}

        violations = validate_orb_runtime_config(runtime_config)
        if violations:
            raise ValueError(
                f"Config validation failed for thesis '{thesis.thesis_id}': "
                + "; ".join(violations)
            )

        experiment_id = _config_hash(runtime_config)
        experiment_dir = root / "experiments" / experiment_id
        experiment_dir.mkdir(parents=True, exist_ok=True)

        (experiment_dir / "thesis.json").write_text(thesis.model_dump_json(indent=2) + "\n")
        (experiment_dir / "runtime_config.json").write_text(
            json.dumps(runtime_config, indent=2) + "\n"
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
        (experiment_dir / "contract.json").write_text(contract.model_dump_json(indent=2) + "\n")
        return contract

    raise ValueError(f"compile_research_thesis does not support family '{family_name}' yet")
