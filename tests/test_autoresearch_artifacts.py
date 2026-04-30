"""Unit tests for autoresearch_artifacts.

Project rule G: real production names. Status strings (`pending`,
`completed`) match what the live conductor / loop emits.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch_artifacts import (
    queue_from_thesis_artifacts,
    read_artifacts_relative_to_root,
    read_research_artifacts,
    read_run_queue,
    read_thesis_artifacts,
)
from autoresearch_state import ExperimentRecord
from artifact_io import write_json_artifact


def _write_artifact(directory: Path, name: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


# ── read_artifacts_relative_to_root ──────────────────────────────


def test_read_artifacts_relativizes_artifact_path_to_root(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "ema-research"
    _write_artifact(
        artifacts_dir,
        "research-2026-04-29.json",
        {"status": "completed", "findings": ["thesis on regime filters"]},
    )

    out = read_artifacts_relative_to_root(artifacts_dir, tmp_path)
    assert len(out) == 1
    # artifact_path is rewritten relative to root, in POSIX form.
    assert out[0]["artifact_path"] == "ema-research/research-2026-04-29.json"
    assert out[0]["status"] == "completed"


def test_read_artifacts_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert read_artifacts_relative_to_root(tmp_path / "does-not-exist", tmp_path) == []


def test_read_artifacts_skips_malformed_json(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "ema-run-queue"
    artifacts_dir.mkdir()
    (artifacts_dir / "good.json").write_text(
        json.dumps({"thesis_id": "g", "status": "pending", "config": "x.json"})
    )
    (artifacts_dir / "broken.json").write_text("not valid json {")

    out = read_artifacts_relative_to_root(artifacts_dir, tmp_path)
    assert len(out) == 1
    assert out[0]["thesis_id"] == "g"


def test_read_artifacts_skips_unreadable_json_file(tmp_path: Path, monkeypatch) -> None:
    artifacts_dir = tmp_path / "ema-run-queue"
    artifacts_dir.mkdir()
    good = artifacts_dir / "good.json"
    bad = artifacts_dir / "bad.json"
    good.write_text(json.dumps({"thesis_id": "g", "status": "pending", "config": "x.json"}))
    bad.write_text("{}")

    original_read_text = Path.read_text

    def _read_text(self: Path, *args, **kwargs):
        if self == bad:
            raise OSError("simulated read failure")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)

    out = read_artifacts_relative_to_root(artifacts_dir, tmp_path)
    assert len(out) == 1
    assert out[0]["thesis_id"] == "g"


def test_write_json_artifact_persists_iso8601_timestamp_strings(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"

    write_json_artifact(path, {"status": "ok", "timestamp": "2026-04-30T12:00:00+00:00"})

    payload = json.loads(path.read_text())
    assert payload["timestamp"] == "2026-04-30T12:00:00+00:00"
    assert isinstance(payload["timestamp"], str)


def test_read_artifacts_returns_sorted_by_filename(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "queue"
    _write_artifact(artifacts_dir, "z.json", {"id": "z"})
    _write_artifact(artifacts_dir, "a.json", {"id": "a"})
    _write_artifact(artifacts_dir, "m.json", {"id": "m"})

    ids = [a["id"] for a in read_artifacts_relative_to_root(artifacts_dir, tmp_path)]
    assert ids == ["a", "m", "z"]


# ── read_research_artifacts / read_thesis_artifacts / read_run_queue ──


def test_read_research_artifacts_uses_research_dir(tmp_path: Path) -> None:
    research_dir = tmp_path / "ema-research"
    _write_artifact(research_dir, "round-1.json", {"status": "completed"})
    out = read_research_artifacts(research_dir, tmp_path)
    assert len(out) == 1
    assert out[0]["artifact_path"] == "ema-research/round-1.json"


def test_read_thesis_artifacts_uses_proposals_dir(tmp_path: Path) -> None:
    proposals_dir = tmp_path / "ema-proposals"
    _write_artifact(
        proposals_dir,
        "trailing_stop.json",
        {"thesis_id": "trailing_stop", "family": "exit"},
    )
    out = read_thesis_artifacts(proposals_dir, tmp_path)
    assert len(out) == 1
    assert out[0]["family"] == "exit"


def test_read_run_queue_returns_relative_paths(tmp_path: Path) -> None:
    run_queue_dir = tmp_path / "ema-run-queue"
    _write_artifact(
        run_queue_dir,
        "thesis-001.json",
        {
            "thesis_id": "thesis-001",
            "config": "experiments/abc/runtime_config.json",
            "status": "pending",
        },
    )
    out = read_run_queue(run_queue_dir, tmp_path)
    assert out[0]["artifact_path"] == "ema-run-queue/thesis-001.json"


# ── queue_from_thesis_artifacts ──────────────────────────────────


def test_queue_excludes_already_attempted_configs(tmp_path: Path) -> None:
    run_queue_dir = tmp_path / "queue"
    config_already_attempted = "experiments/done/runtime_config.json"
    config_to_run = "experiments/new/runtime_config.json"
    # Both runtime configs must exist on disk for the queue check.
    for c in (config_already_attempted, config_to_run):
        full = tmp_path / c
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("{}")

    _write_artifact(
        run_queue_dir,
        "done.json",
        {"thesis_id": "done", "config": config_already_attempted, "status": "pending"},
    )
    _write_artifact(
        run_queue_dir,
        "new.json",
        {"thesis_id": "new", "config": config_to_run, "status": "pending"},
    )

    results = [
        ExperimentRecord(config_already_attempted, 1.2, "keep", "", 1, {}),
    ]
    queued = queue_from_thesis_artifacts(run_queue_dir, tmp_path, results)
    assert queued == [config_to_run]


def test_queue_skips_non_pending_status(tmp_path: Path) -> None:
    run_queue_dir = tmp_path / "queue"
    config_path = "experiments/x/runtime_config.json"
    (tmp_path / config_path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / config_path).write_text("{}")

    _write_artifact(
        run_queue_dir,
        "x.json",
        {"thesis_id": "x", "config": config_path, "status": "completed"},
    )
    assert queue_from_thesis_artifacts(run_queue_dir, tmp_path, []) == []


def test_queue_skips_when_runtime_config_does_not_exist_on_disk(tmp_path: Path) -> None:
    run_queue_dir = tmp_path / "queue"
    _write_artifact(
        run_queue_dir,
        "ghost.json",
        {
            "thesis_id": "ghost",
            "config": "experiments/ghost/runtime_config.json",
            "status": "pending",
        },
    )
    assert queue_from_thesis_artifacts(run_queue_dir, tmp_path, []) == []


def test_queue_dedupes_within_a_single_pass(tmp_path: Path) -> None:
    run_queue_dir = tmp_path / "queue"
    config = "experiments/dup/runtime_config.json"
    (tmp_path / config).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / config).write_text("{}")
    # Two queue artifacts pointing at the same runtime config.
    _write_artifact(run_queue_dir, "a.json", {"config": config, "status": "pending"})
    _write_artifact(run_queue_dir, "b.json", {"config": config, "status": "pending"})

    queued = queue_from_thesis_artifacts(run_queue_dir, tmp_path, [])
    assert queued == [config]


def test_queue_skips_malformed_runtime_config_even_if_path_exists(tmp_path: Path) -> None:
    run_queue_dir = tmp_path / "ema-run-queue"
    config = "experiments/runtime_config.json"
    (tmp_path / config).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / config).write_text("{not valid json")
    _write_artifact(run_queue_dir, "bad.json", {"config": config, "status": "pending"})

    queued = queue_from_thesis_artifacts(run_queue_dir, tmp_path, [])
    assert queued == []
