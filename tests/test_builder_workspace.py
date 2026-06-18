from __future__ import annotations

from pathlib import Path

from compiler_builder import BuilderWorkspace


def test_request_dir_is_under_the_artifact_root() -> None:
    workspace = BuilderWorkspace(artifact_root=Path("/runtime/jobs/job-7/builder/ema-pullback"))
    assert workspace.request_dir == Path("/runtime/jobs/job-7/builder/ema-pullback/builder_request")


def test_workspace_dir_is_under_the_request_dir() -> None:
    workspace = BuilderWorkspace(artifact_root=Path("/runtime/jobs/job-7/builder/ema-pullback"))
    assert workspace.workspace_dir == workspace.request_dir / "workspace"


def test_attempt_dir_is_numbered_under_the_request_dir() -> None:
    workspace = BuilderWorkspace(artifact_root=Path("/runtime/jobs/job-7/builder/ema-pullback"))
    assert workspace.attempt_dir(1) == workspace.request_dir / "attempt-1"
    assert workspace.attempt_dir(3) == workspace.request_dir / "attempt-3"
