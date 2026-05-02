from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now, write_json_artifact
from backtest.runtime_config import load_runtime_config
from strategies import STRATEGIES
from strategy_family import load_family


def _find_cli() -> str | None:
    """Find the codex CLI."""
    if shutil.which("codex"):
        return "codex"
    return None


def _read_json_artifact(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _load_structured_thesis_artifacts(
    root: Path, thesis_id: str
) -> tuple[dict[str, Any], dict[str, Any], Path, Path] | None:
    experiment_dir = root / "experiments" / thesis_id
    thesis_path = experiment_dir / "thesis.json"
    contract_path = experiment_dir / "contract.json"
    thesis = _read_json_artifact(thesis_path)
    contract = _read_json_artifact(contract_path)
    if thesis is None or contract is None:
        return None
    return thesis, contract, thesis_path, contract_path


def build_missing_primitives(root: Path, thesis_id: str) -> dict[str, Any]:
    """Dispatch CLI builder to implement missing primitives for a thesis."""
    structured = _load_structured_thesis_artifacts(root, thesis_id)
    if structured is not None:
        proposal, compilation, proposal_path, compilation_path = structured
        family_name = proposal.get("strategy_family") or compilation.get("strategy_family") or "orb"
        try:
            family = load_family(family_name)
        except (KeyError, ValueError) as exc:
            return {
                "status": "error",
                "reason": f"unknown strategy family for structured thesis {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = (
            proposal.get("requested_primitives")
            or compilation.get("missing_primitives")
            or sorted((proposal.get("config_changes") or {}).keys())
        )
        generated_name = f"experiments/{thesis_id}/runtime_config.json"
        config_path = generated_name
        builder_requests_dir = root / family.builder_requests_dirname
        prompt_extras = [
            "- Thesis artifact: experiments/{thesis_id}/thesis.json",
            "- Contract artifact: experiments/{thesis_id}/contract.json",
            "- Thesis payload: {thesis_payload}",
            "- Contract payload: {contract_payload}",
        ]
    else:
        compilation_family_name = None
        for candidate_family in sorted(STRATEGIES):
            candidate_proposal_path = (
                root / load_family(candidate_family).proposals_dirname / f"{thesis_id}.json"
            )
            if candidate_proposal_path.exists():
                compilation_family_name = candidate_family
                break

        if compilation_family_name is None:
            return {
                "status": "error",
                "reason": f"missing proposal artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }

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

        try:
            proposal = json.loads(proposal_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "reason": f"malformed proposal artifact for {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        try:
            compilation = json.loads(compilation_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "reason": f"malformed compilation artifact for {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        family_name = proposal.get("strategy_family") or proposal["family"]
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = compilation.get("missing_primitives") or []
        generated_name = (
            f"{family.name}_{thesis_id}.yaml"
            if not thesis_id.startswith(f"{family.name}_")
            else f"{thesis_id}.yaml"
        )
        config_path = f"configs/variants/{generated_name}"
        builder_requests_dir = root / family.builder_requests_dirname
        prompt_extras = []
    write_json_artifact(
        builder_requests_dir / f"{thesis_id}.json",
        {
            "thesis_id": thesis_id,
            "family": family_name,
            "proposal_path": proposal_path.relative_to(root).as_posix(),
            "compilation_path": compilation_path.relative_to(root).as_posix(),
            "missing_primitives": missing_primitives,
            "normalized_contract": normalized_contract,
            "status": "requested",
            "timestamp": timestamp_now(),
        },
    )

    config_abspath = root / config_path
    if config_abspath.exists():
        try:
            load_runtime_config(str(config_abspath), family_name)
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"generated config failed validation: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
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
{chr(10).join(prompt_extras).format(
    thesis_id=thesis_id,
    thesis_payload=json.dumps(proposal, indent=2),
    contract_payload=json.dumps(compilation, indent=2),
) if prompt_extras else ""}

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
    if generated:
        try:
            load_runtime_config(str(config_abspath), family_name)
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"generated config failed validation: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
    return {
        "status": "completed" if generated else "error",
        "reason": result,
        "generated_config": generated,
        "validation_passed": bool(generated),
    }
