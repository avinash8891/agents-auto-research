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
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from artifact_store import timestamp_ms, write_json_artifact
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


def _find_cli() -> str | None:
    """Find claude or codex CLI."""
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"
    return None


def build_missing_primitives(root: Path, thesis_id: str) -> dict[str, Any]:
    """Dispatch CLI builder to implement missing primitives for a thesis."""
    compilation_family_name = "orb"
    for candidate_family in ("orb", "ema"):
        candidate_proposal_path = (
            root / load_family(candidate_family).proposals_dirname / f"{thesis_id}.json"
        )
        if candidate_proposal_path.exists():
            compilation_family_name = candidate_family
            break

    family = load_family(compilation_family_name)
    proposal_path = root / family.proposals_dirname / f"{thesis_id}.json"
    compilation_path = root / family.compilations_dirname / f"{thesis_id}.json"
    if not proposal_path.exists():
        return {
            "status": "error",
            "reason": f"missing proposal artifact for {thesis_id}",
            "generated_config": None,
            "validation_passed": False,
        }
    if not compilation_path.exists():
        return {
            "status": "error",
            "reason": f"missing compilation artifact for {thesis_id}",
            "generated_config": None,
            "validation_passed": False,
        }

    proposal = json.loads(proposal_path.read_text())
    compilation = json.loads(compilation_path.read_text())
    family_name = proposal.get("strategy_family") or proposal.get("family", "orb")
    family = load_family(family_name)
    normalized_contract = compilation.get("normalized_contract") or []
    missing_primitives = compilation.get("missing_primitives") or []
    generated_name = (
        f"{family.name}_{thesis_id}.yaml"
        if not thesis_id.startswith(f"{family.name}_")
        else f"{thesis_id}.yaml"
    )
    config_path = f"configs/variants/{generated_name}"
    write_json_artifact(
        root / family.builder_requests_dirname / f"{thesis_id}.json",
        {
            "thesis_id": thesis_id,
            "family": family_name,
            "proposal_path": proposal_path.relative_to(root).as_posix(),
            "compilation_path": compilation_path.relative_to(root).as_posix(),
            "missing_primitives": missing_primitives,
            "normalized_contract": normalized_contract,
            "status": "requested",
            "timestamp": timestamp_ms(),
        },
    )

    config_abspath = root / config_path
    if config_abspath.exists():
        return {
            "status": "completed",
            "reason": "config already exists",
            "generated_config": config_path,
            "validation_passed": True,
        }

    prompt = f"""Goal:
Automatically implement the missing primitive(s) for thesis `{thesis_id}` and create the resulting config artifact.

Context:
- Repo root: {root}
- Proposal artifact: {proposal_path}
- Compilation artifact: {compilation_path}
- Expected config path: {config_path}
- Missing primitives: {json.dumps(missing_primitives, indent=2)}
- Normalized contract: {json.dumps(normalized_contract, indent=2)}
- Hypothesis: {proposal.get("hypothesis", "")}

Requirements:
- Write the necessary code changes in this repo.
- Add or update tests for the new primitive behavior.
- Create the config file at `{config_path}` if it becomes expressible.
- Return a concise final report with:
  1. whether implementation succeeded
  2. files changed
  3. tests run
  4. generated config path

Constraints:
- Preserve existing behavior except for the new primitive support.
- If implementation cannot be completed safely, explain why.
"""

    cli = _find_cli()
    if not cli:
        return {
            "status": "error",
            "reason": "No CLI available for builder dispatch",
            "generated_config": None,
            "validation_passed": False,
        }

    if cli == "claude":
        cmd = [
            "claude",
            "-p",
            "--model",
            "claude-opus-4-6",
            "--allowedTools",
            "Read,LS,Grep,Glob,ApplyPatch,Execute",
            "--no-session-persistence",
            prompt,
        ]
    else:
        cmd = ["codex", "exec", prompt]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=900,
    )
    result = (proc.stdout or "") + (proc.stderr or "")

    generated = config_path if config_abspath.exists() else None
    return {
        "status": "completed" if generated else "error",
        "reason": result,
        "generated_config": generated,
        "validation_passed": bool(generated),
    }
