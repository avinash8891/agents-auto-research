from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from artifact_store import timestamp_ms, write_json_artifact
from compiler_operationalize import operationalize_thesis
from config_hash import _config_hash
from family_research import validate_family_config_changes
from strategies import STRATEGIES
from strategy_family import load_family


def compile_config_thesis(
    family_name: str,
    thesis_id: str,
    config_changes: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Compile a registered strategy thesis from config changes.

    Two steps:
      1. Validate config_changes keys against the strategy defaults.
      2. runtime_config = {**defaults, **config_changes}, write JSON.

    Filenames use config_hash (deterministic content hash), not thesis_id.
    thesis_id is stored as metadata inside the file for human readability.
    Identical configs get the same hash = free dedup.

    Returns {"status": ..., "config_path": ..., "runtime_config": ..., "config_hash": ...}
    """
    family = load_family(family_name)
    strategy = STRATEGIES[family_name]
    defaults = strategy.get_defaults()
    allowed = set(defaults.keys())
    invalid_keys = sorted(set(config_changes.keys()) - allowed)
    if invalid_keys:
        return {
            "status": "requires_code_change",
            "invalid_keys": invalid_keys,
            "config_path": None,
            "runtime_config": {},
        }

    runtime_config = {**defaults, **config_changes}

    violations = strategy.validate_runtime_config(runtime_config)
    if violations:
        return {
            "status": "rejected_at_compile",
            "violations": violations,
            "config_path": None,
            "runtime_config": runtime_config,
        }

    config_hash = _config_hash(runtime_config)

    contracts_dir = root / family.contracts_dirname
    contracts_dir.mkdir(parents=True, exist_ok=True)
    config_path = contracts_dir / f"{config_hash}.json"

    # Skip write if identical config already exists (dedup)
    if not config_path.exists():
        config_path.write_text(json.dumps(runtime_config, indent=2) + "\n")

    # Write queue entry (also keyed by hash)
    run_queue_dir = root / family.run_queue_dirname
    run_queue_dir.mkdir(parents=True, exist_ok=True)
    write_json_artifact(
        run_queue_dir / f"{config_hash}.json",
        {
            "queue_id": config_hash,
            "thesis_id": thesis_id,
            "status": "pending",
            "config": config_path.relative_to(root).as_posix(),
            "timestamp": timestamp_ms(),
        },
    )
    return {
        "status": "ready_to_run",
        "config_path": config_path.relative_to(root).as_posix(),
        "config_hash": config_hash,
        "runtime_config": runtime_config,
    }


def compile_proposal_artifact(
    proposal: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Compile a thesis proposal into a family-specific runtime artifact."""
    thesis_id = proposal["thesis_id"]
    family_name = proposal["strategy_family"]
    family = load_family(family_name)
    contract = proposal.get("primitive_contract", [])
    result = STRATEGIES[family_name].compile_contract(contract)

    compilations_dir = root / family.compilations_dirname
    run_queue_dir = root / family.run_queue_dirname
    contracts_dir = root / family.contracts_dirname

    compilation_payload = {
        "thesis_id": thesis_id,
        "family": family_name,
        "status": result.status,
        "runtime_config": result.runtime_config,
        "missing_primitives": result.missing_primitives,
        "normalized_contract": result.normalized_contract,
        "timestamp": timestamp_ms(),
    }
    write_json_artifact(compilations_dir / f"{thesis_id}.json", compilation_payload)

    if result.status != "ready_to_run":
        return compilation_payload

    contract_path = contracts_dir / f"{thesis_id}.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(result.normalized_contract, indent=2) + "\n")

    queue_payload = {
        "queue_id": thesis_id,
        "thesis_id": thesis_id,
        "status": "pending",
        "config": contract_path.relative_to(root).as_posix(),
        "timestamp": timestamp_ms(),
    }
    write_json_artifact(run_queue_dir / f"{thesis_id}.json", queue_payload)
    return compilation_payload


def create_executable_artifact(
    thesis_dir: Path,
    base_config_path: Path,
    thesis: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Create executable config from a thesis.

    Registered strategies: validate config_changes, merge with baseline, write JSON.
    Otherwise operationalize + primitive_contract compilation.

    Returns {"generated_config": str|None, "generated_config_needs_build": bool, ...}.
    """
    thesis["strategy_family"] = thesis.get("strategy_family") or _infer_family_from_paths(
        thesis_dir, base_config_path
    )
    thesis_id = thesis.get("thesis_id", "")
    family_name = thesis["strategy_family"]

    family = load_family(family_name)

    if thesis.get("requires_code_change"):
        proposals_dir = root / family.proposals_dirname
        write_json_artifact(
            proposals_dir / f"{thesis_id}.json",
            {
                "thesis_id": thesis_id,
                "hypothesis": thesis.get("hypothesis", ""),
                "strategy_family": family_name,
                "mechanism": thesis.get("mechanism", ""),
                "config_changes": thesis.get("config_changes", {}),
                "requires_code_change": True,
                "evidence": thesis.get("evidence", []),
                "timestamp": timestamp_ms(),
            },
        )
        return {
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": thesis_id,
        }

    if family_name in STRATEGIES and thesis.get("config_changes"):
        config_changes = thesis.get("config_changes", {})
        result = compile_config_thesis(family_name, thesis_id, config_changes, root)
        if result["status"] != "ready_to_run":
            return {
                "generated_config": None,
                "generated_config_needs_build": True,
                "generated_thesis_id": thesis_id,
            }
        config_hash = result["config_hash"]
        proposals_dir = root / family.proposals_dirname
        write_json_artifact(
            proposals_dir / f"{config_hash}.json",
            {
                "thesis_id": thesis_id,
                "config_hash": config_hash,
                "hypothesis": thesis.get("hypothesis", ""),
                "strategy_family": family_name,
                "mechanism": thesis.get("mechanism", ""),
                "config_changes": thesis.get("config_changes", {}),
                "evidence": thesis.get("evidence", []),
                "timestamp": timestamp_ms(),
            },
        )
        return {
            "generated_config": result["config_path"],
            "generated_config_needs_build": False,
            "generated_thesis_id": thesis_id,
        }

    thesis = validate_family_config_changes(family_name, thesis)
    thesis = operationalize_thesis(dict(thesis))
    proposal = {
        "thesis_id": thesis_id,
        "strategy_family": family_name,
        "primitive_contract": thesis.get("primitive_contract", []),
        "timestamp": timestamp_ms(),
    }
    compilation = compile_proposal_artifact(proposal, root)
    if compilation["status"] != "ready_to_run":
        return {
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": thesis_id,
        }
    return {
        "generated_config": f"{family.contracts_dirname}/{thesis_id}.json",
        "generated_config_needs_build": False,
        "generated_thesis_id": thesis_id,
    }


def derive_thesis_artifacts(
    thesis_dir: Path,
    base_config_path: Path,
    parsed: dict[str, Any],
    root: Path,
) -> list[str]:
    """Create thesis artifacts + configs from research findings.

    Iterates over suggested_theses in parsed, validates, operationalizes,
    and compiles each. Returns list of generated config paths.
    """
    suggested = parsed.get("suggested_theses", [])
    if not suggested:
        return []

    generated: list[str] = []
    for thesis in suggested:
        thesis.setdefault("strategy_family", _infer_family_from_paths(thesis_dir, base_config_path))
        thesis = validate_family_config_changes(thesis["strategy_family"], thesis)
        result = create_executable_artifact(thesis_dir, base_config_path, thesis, root)
        if result.get("generated_config"):
            generated.append(result["generated_config"])

    return generated


def _infer_family_from_paths(thesis_dir: Path, base_config_path: Path) -> str:
    haystack = " ".join(
        part.lower() for path in (thesis_dir, base_config_path) for part in path.parts
    )
    for family_name in sorted(STRATEGIES, key=len, reverse=True):
        if family_name.lower() in haystack:
            return family_name
    raise ValueError(
        "Unable to infer strategy family from thesis/base-config paths; "
        "research artifacts must include strategy_family"
    )


def write_research_artifact(
    research_dir: Path,
    request: dict[str, Any],
    parsed: dict[str, Any],
    raw_output: str,
    *,
    research_mode: str = "grounded",
    external_research_attempted: bool = True,
    external_research_attempts: int = 1,
    fallback_reason: str | None = None,
) -> Path:
    """Write completed research artifact to disk."""
    research_dir.mkdir(parents=True, exist_ok=True)
    request_id = request.get("request_id", f"research-{int(time.time())}")

    artifact = {
        "request_id": request_id,
        "status": "completed",
        "timestamp": int(time.time() * 1000),
        "research_mode": research_mode,
        "external_research_attempted": external_research_attempted,
        "external_research_attempts": external_research_attempts,
        "findings": parsed.get("findings", []),
        "suggested_theses": parsed.get("suggested_theses", []),
        "sources": parsed.get("sources", []),
    }
    if fallback_reason:
        artifact["fallback_reason"] = fallback_reason

    artifact_path = research_dir / f"{request_id}-findings.json"
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n")

    raw_path = research_dir / f"{request_id}-raw.txt"
    raw_path.write_text(raw_output)

    return artifact_path


def mark_request_completed(request_path: Path) -> None:
    """Mark a research request as completed."""
    payload = json.loads(request_path.read_text())
    payload["status"] = "completed"
    payload["completed_at"] = int(time.time() * 1000)
    request_path.write_text(json.dumps(payload, indent=2) + "\n")
