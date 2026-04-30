"""Directory-based artifact discovery for autoresearch.

Wraps artifact_io.read_json_artifacts so that the returned `artifact_path`
fields are made relative to the controller root, then exposes the family-
specific lookups (research artifacts, thesis proposals, run-queue artifacts).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_io import read_json_artifacts as read_artifact_json_files
from autoresearch_state import ExperimentRecord


def read_artifacts_relative_to_root(directory: Path, root: Path) -> list[dict[str, Any]]:
    """Read all *.json artifacts under `directory` and rewrite each
    payload's `artifact_path` to be relative to `root` (POSIX form)."""
    artifacts = read_artifact_json_files(directory)
    for payload in artifacts:
        artifact_path = payload.get("artifact_path")
        if artifact_path:
            payload["artifact_path"] = Path(artifact_path).relative_to(root).as_posix()
    return artifacts


def read_research_artifacts(research_dir: Path, root: Path) -> list[dict[str, Any]]:
    return read_artifacts_relative_to_root(research_dir, root)


def read_thesis_artifacts(proposals_dir: Path, root: Path) -> list[dict[str, Any]]:
    return read_artifacts_relative_to_root(proposals_dir, root)


def read_run_queue(run_queue_dir: Path, root: Path) -> list[dict[str, Any]]:
    return read_artifacts_relative_to_root(run_queue_dir, root)


def queue_from_thesis_artifacts(
    run_queue_dir: Path,
    root: Path,
    results: list[ExperimentRecord],
) -> list[str]:
    """Return pending run-queue config paths that exist on disk and have
    not already been attempted."""
    attempted = {result.config for result in results if result.config}
    queued: list[str] = []
    for artifact in read_run_queue(run_queue_dir, root):
        if artifact.get("status") != "pending":
            continue
        config = artifact.get("config")
        if not config or config in attempted or config in queued:
            continue
        if not (root / config).exists():
            continue
        queued.append(config)
    return queued
