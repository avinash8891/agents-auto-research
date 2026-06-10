from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now
from compiler_operationalize import operationalize_thesis
from compiler_research import compile_research_thesis
from family_research_spec import validate_family_config_changes
from persistence_utils import write_text_atomic
from research_types import ResearchThesis
from strategies import STRATEGIES
from thesis_validator import normalize_thesis_payload


def _default_runtime_root(root: Path, artifact_root: Path | None) -> Path:
    if artifact_root is None:
        raise ValueError("job-scoped artifact_root is required for thesis artifact compilation")
    return artifact_root


def compile_config_thesis(
    family_name: str,
    thesis_id: str,
    config_changes: dict[str, Any],
    root: Path,
    *,
    artifact_root: Path | None = None,
    job: int | None = None,
    created_for_commit: str = "",
) -> dict[str, Any]:
    raise RuntimeError(
        "legacy contract/run-queue compilation is not supported; compile theses inside a research round"
    )


def compile_proposal_artifact(
    proposal: dict[str, Any],
    root: Path,
    *,
    artifact_root: Path | None = None,
    job: int | None = None,
    created_for_commit: str = "",
) -> dict[str, Any]:
    raise RuntimeError(
        "legacy proposal compilation is not supported; research rounds select at most one backtest"
    )


def create_executable_artifact(
    thesis_dir: Path,
    base_config_path: Path,
    thesis: dict[str, Any],
    root: Path,
    *,
    artifact_root: Path | None = None,
    job: int | None = None,
    created_for_commit: str = "",
) -> dict[str, Any]:
    """Create executable config from a thesis.

    Registered strategies: validate config_changes, merge with baseline, write JSON.
    Otherwise operationalize + primitive_contract compilation.

    Returns {"generated_config": str|None, "generated_config_needs_build": bool, ...}.
    """
    thesis["strategy_family"] = thesis.get("strategy_family") or _infer_family_from_paths(
        thesis_dir, base_config_path
    )
    family_name = thesis["strategy_family"]
    thesis = validate_family_config_changes(family_name, thesis)
    thesis = operationalize_thesis(dict(thesis))
    runtime_root = _default_runtime_root(root, artifact_root)
    research_thesis = ResearchThesis.model_validate(normalize_thesis_payload(dict(thesis)))
    contract = compile_research_thesis(research_thesis, root, artifact_root=runtime_root)
    if contract.status != "ready_to_run":
        return {
            "generated_config": None,
            "generated_config_needs_build": True,
            "generated_thesis_id": research_thesis.thesis_id,
        }
    return {
        "generated_config": runtime_root.joinpath("selected_config.json")
        .relative_to(root)
        .as_posix(),
        "generated_config_needs_build": False,
        "generated_thesis_id": research_thesis.thesis_id,
    }


def derive_thesis_artifacts(
    thesis_dir: Path,
    base_config_path: Path,
    parsed: dict[str, Any],
    root: Path,
    *,
    artifact_root: Path | None = None,
    job: int | None = None,
    created_for_commit: str = "",
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
        result = create_executable_artifact(
            thesis_dir,
            base_config_path,
            thesis,
            root,
            artifact_root=artifact_root,
            job=job,
            created_for_commit=created_for_commit,
        )
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
    job: int | None = None,
) -> Path:
    """Write completed research artifact to disk."""
    research_dir.mkdir(parents=True, exist_ok=True)
    request_id = request.get("request_id", f"research-{int(time.time())}")

    artifact = {
        "request_id": request_id,
        "status": "completed",
        "timestamp": timestamp_now(),
        "research_mode": research_mode,
        "job_id": job if job is not None else request.get("job_id"),
        "external_research_attempted": external_research_attempted,
        "external_research_attempts": external_research_attempts,
        "findings": parsed.get("findings", []),
        "suggested_theses": parsed.get("suggested_theses", []),
        "sources": parsed.get("sources", []),
    }
    if fallback_reason:
        artifact["fallback_reason"] = fallback_reason

    artifact_path = research_dir / f"{request_id}-findings.json"
    write_text_atomic(artifact_path, json.dumps(artifact, indent=2) + "\n")

    raw_path = research_dir / f"{request_id}-raw.txt"
    write_text_atomic(raw_path, raw_output)

    return artifact_path


def mark_request_completed(request_path: Path) -> None:
    """Mark a research request as completed."""
    payload = json.loads(request_path.read_text())
    payload["status"] = "completed"
    payload["completed_at"] = timestamp_now()
    write_text_atomic(request_path, json.dumps(payload, indent=2) + "\n")
