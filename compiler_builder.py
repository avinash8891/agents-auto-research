from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from artifact_store import timestamp_ms, write_json_artifact
from strategies import STRATEGIES
from strategy_family import load_family


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
    for candidate_family in ("orb", *sorted(STRATEGIES)):
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
