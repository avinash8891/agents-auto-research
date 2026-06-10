"""Directory-based artifact discovery for round-scoped autoresearch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_io import read_json_artifacts as read_artifact_json_files
from autoresearch_logging import get_logger

_log = get_logger(__name__)


def _serialize_artifact_path(path: Path, root: Path) -> str:
    """Prefer a repo-relative artifact path, but preserve absolute paths.

    Artifact discovery can surface files stored outside the controller root,
    so callers must not assume `path` is always a descendant of `root`.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def read_artifacts_relative_to_root(directory: Path, root: Path) -> list[dict[str, Any]]:
    """Read all *.json artifacts under `directory` and rewrite each
    payload's `artifact_path` to be relative to `root` (POSIX form)."""
    artifacts = read_artifact_json_files(directory)
    for payload in artifacts:
        artifact_path = payload.get("artifact_path")
        if artifact_path:
            payload["artifact_path"] = _serialize_artifact_path(Path(artifact_path), root)
    return artifacts


def read_research_artifacts(
    research_dir: Path,
    root: Path,
    job: int | None = None,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    if research_dir.exists():
        loaded: list[tuple[Path, dict[str, Any]]] = []
        for path in research_dir.glob("round-*/round.json"):
            try:
                payload = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            payload["artifact_path"] = _serialize_artifact_path(path, root)
            loaded.append((path, payload))
        loaded.sort(key=lambda item: _research_round_sort_key(item[0], item[1]))
        artifacts.extend(payload for _, payload in loaded)
    if job is None:
        return artifacts
    matched: list[dict[str, Any]] = []
    for artifact in artifacts:
        raw_job = artifact.get("job")
        if raw_job is None:
            raw_job = artifact.get("job_id")
        if raw_job is None:
            continue
        try:
            artifact_job = int(raw_job)
        except (TypeError, ValueError) as exc:
            _log.warning(
                "Skipping artifact with malformed job field (path=%s, job=%r): %s",
                artifact.get("artifact_path", "<unknown>"),
                raw_job,
                exc,
            )
            continue
        if artifact_job == job:
            matched.append(artifact)
    return matched


def _research_round_sort_key(path: Path, payload: dict[str, Any]) -> tuple[int, str]:
    raw_round = payload.get("round_number")
    if raw_round is None:
        name = path.parent.name.removeprefix("round-")
        raw_round = name.split("-", 1)[0]
    try:
        return int(raw_round), path.as_posix()
    except (TypeError, ValueError):
        return -1, path.as_posix()


def read_thesis_artifacts(proposals_dir: Path, root: Path) -> list[dict[str, Any]]:
    raise RuntimeError(
        "loose thesis artifact discovery is no longer supported; read artifacts from a specific "
        "research round or builder_request directory"
    )


def read_run_queue(run_queue_dir: Path, root: Path) -> list[dict[str, Any]]:
    raise RuntimeError(
        "variant backtest queues are no longer supported; read round-scoped backtest artifacts "
        "instead of run-queue entries"
    )


def queue_from_thesis_artifacts(
    run_queue_dir: Path,
    root: Path,
    results: list[object],
    job: int | None = None,
) -> list[str]:
    raise RuntimeError(
        "queue-driven thesis execution is no longer supported; each research round may select at "
        "most one backtest"
    )
