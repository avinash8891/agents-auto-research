from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from artifact_io import write_json_artifact
from autoresearch_artifacts import (
    queue_from_thesis_artifacts,
    read_artifacts_relative_to_root,
    read_research_artifacts,
    read_run_queue,
    read_thesis_artifacts,
    round_number_from_path,
)


def _write_artifact(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


def test_read_artifacts_relativizes_artifact_path_to_root(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-1"
    _write_artifact(artifacts_dir, "round.json", {"status": "completed"})

    out = read_artifacts_relative_to_root(artifacts_dir, tmp_path)

    assert out[0]["artifact_path"] == "runtime/jobs/job-1/research/round-1/round.json"


def test_read_research_artifacts_preserves_absolute_paths_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-artifact-outside"
    round_dir = outside / "round-1"
    round_path = _write_artifact(round_dir, "round.json", {"job_id": 1, "round_number": 1})

    out = read_research_artifacts(outside, tmp_path, job=1)

    assert out[0]["artifact_path"] == round_path.as_posix()


def test_read_artifacts_returns_sorted_by_filename(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "queue"
    _write_artifact(artifacts_dir, "z.json", {"id": "z"})
    _write_artifact(artifacts_dir, "a.json", {"id": "a"})

    assert [a["id"] for a in read_artifacts_relative_to_root(artifacts_dir, tmp_path)] == [
        "a",
        "z",
    ]


def test_read_research_artifacts_reads_round_json_only(tmp_path: Path) -> None:
    research_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research"
    _write_artifact(
        research_dir / "round-0-baseline",
        "round.json",
        {"job_id": 1, "round_number": 0},
    )
    _write_artifact(research_dir / "round-1", "round.json", {"job_id": 1, "round_number": 1})
    _write_artifact(research_dir / "round-1" / "backtest", "result.json", {"ignored": True})

    out = read_research_artifacts(research_dir, tmp_path, job=1)

    assert [artifact["round_number"] for artifact in out] == [0, 1]


def test_round_number_from_path_handles_baseline_and_malformed_round_dirs(tmp_path: Path) -> None:
    assert round_number_from_path(tmp_path / "research" / "round-0-baseline" / "x.json") == 0
    assert round_number_from_path(tmp_path / "research" / "round-12" / "x.json") == 12
    assert round_number_from_path(tmp_path / "research" / "round-latest" / "x.json") is None


def test_round_number_from_path_uses_nearest_round_directory(tmp_path: Path) -> None:
    path = tmp_path / "round-99-archive" / "research" / "round-2" / "round.json"

    assert round_number_from_path(path) == 2


def test_read_research_artifacts_sorts_round_directories_numerically(tmp_path: Path) -> None:
    research_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research"
    _write_artifact(research_dir / "round-10", "round.json", {"job_id": 1, "round_number": 10})
    _write_artifact(research_dir / "round-2", "round.json", {"job_id": 1, "round_number": 2})

    out = read_research_artifacts(research_dir, tmp_path, job=1)

    assert [artifact["round_number"] for artifact in out] == [2, 10]


def test_read_research_artifacts_filters_by_job(tmp_path: Path) -> None:
    research_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research"
    _write_artifact(research_dir / "round-1", "round.json", {"job_id": 1, "round_number": 1})
    _write_artifact(research_dir / "round-2", "round.json", {"job_id": 2, "round_number": 2})

    out = read_research_artifacts(research_dir, tmp_path, job=2)

    assert len(out) == 1
    assert out[0]["artifact_path"].endswith("round-2/round.json")


def test_read_research_artifacts_without_job_returns_unfiltered_rounds(tmp_path: Path) -> None:
    research_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research"
    _write_artifact(research_dir / "round-1", "round.json", {"job_id": 1, "round_number": 1})
    _write_artifact(research_dir / "round-2", "round.json", {"job_id": 2, "round_number": 2})

    out = read_research_artifacts(research_dir, tmp_path)

    assert [artifact["job_id"] for artifact in out] == [1, 2]


def test_read_research_artifacts_raises_on_malformed_round_json(tmp_path: Path) -> None:
    research_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research"
    _write_artifact(research_dir / "round-1", "round.json", {"job_id": 1, "round_number": 1})
    bad_round = research_dir / "round-2"
    bad_round.mkdir(parents=True)
    (bad_round / "round.json").write_text("{not-json")

    with pytest.raises(json.JSONDecodeError):
        read_research_artifacts(research_dir, tmp_path)


def test_read_research_artifacts_rejects_missing_and_malformed_job_fields(
    tmp_path: Path,
) -> None:
    research_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research"
    _write_artifact(research_dir / "round-1", "round.json", {"job_id": 1, "round_number": 1})
    _write_artifact(research_dir / "round-2", "round.json", {"status": "completed"})

    with pytest.raises(ValidationError, match="job_id"):
        read_research_artifacts(research_dir, tmp_path, job=1)


def test_read_thesis_artifacts_fails_loudly_for_legacy_loose_roots(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no longer supported"):
        read_thesis_artifacts(tmp_path / "proposals", tmp_path)


def test_read_run_queue_fails_loudly_for_legacy_queue_dirs(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no longer supported"):
        read_run_queue(tmp_path / "run-queue", tmp_path)


def test_queue_from_thesis_artifacts_fails_loudly_for_variant_queueing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no longer supported"):
        queue_from_thesis_artifacts(tmp_path / "run-queue", tmp_path, [])


def test_write_json_artifact_persists_iso8601_timestamp_strings(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"

    write_json_artifact(path, {"status": "ok", "timestamp": "2026-04-30T12:00:00+00:00"})

    payload = json.loads(path.read_text())
    assert payload["timestamp"] == "2026-04-30T12:00:00+00:00"
