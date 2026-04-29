"""Thesis compilation pipeline.

Converts raw research theses into executable config artifacts:
  1. Operationalization — resolves ambiguous terms into exact contracts
  2. Compilation — contract → runtime config via family-specific compiler
  3. Artifact creation — writes proposal, compilation, queue files
  4. Builder dispatch — implements missing primitives via CLI

Migrated from research_subagent.py — all thesis-to-config logic lives here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from compiler_builder import build_missing_primitives
from compiler_defaults import _get_ema_defaults, _get_orb_defaults
from compiler_legacy import compile_ema_thesis as compile_ema_thesis
from compiler_legacy import compile_proposal_artifact as compile_proposal_artifact
from compiler_legacy import create_executable_artifact as create_executable_artifact
from compiler_legacy import derive_thesis_artifacts as derive_thesis_artifacts
from compiler_legacy import mark_request_completed as mark_request_completed
from compiler_legacy import write_research_artifact as write_research_artifact
from compiler_operationalize import operationalize_thesis as operationalize_thesis
from compiler_operationalize import (
    thesis_needs_operationalization as thesis_needs_operationalization,
)
from compiler_validate import (
    _config_content_hash,
    validate_ema_runtime_config,
    validate_orb_runtime_config,
)
from strategy_family import load_family

if TYPE_CHECKING:
    from research_types import ExperimentContract, ResearchThesis

build_missing_primitives = build_missing_primitives


# ---------------------------------------------------------------------------
# Research thesis → ExperimentContract (new structured path)
# ---------------------------------------------------------------------------


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

        experiment_id = _config_content_hash(runtime_config)
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

        experiment_id = _config_content_hash(runtime_config)
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


# ---------------------------------------------------------------------------
# Legacy artifact creation (kept for ORB compatibility)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Builder dispatch: implements missing primitives via CLI
# ---------------------------------------------------------------------------
