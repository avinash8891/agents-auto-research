from __future__ import annotations

import json
from pathlib import Path

from compiler_builder import (
    BUILDER_CAPABILITY_REGISTRY,
    BUILDER_PROMOTION_QUEUE,
    BUILDER_PROMOTIONS_DIR,
    BUILDER_REQUEST_DIRNAME,
    BuilderTask,
    BuilderWorkspace,
)


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


def _builder_task(*, family_name: str = "ema", thesis_id: str = "ema-pullback") -> BuilderTask:
    return BuilderTask(
        thesis_id=thesis_id,
        family_name=family_name,
        proposal_path="runtime/jobs/job-7/research/round-3/builder_request/thesis.json",
        compilation_path="runtime/jobs/job-7/research/round-3/builder_request/contract.json",
        config_path="runtime/jobs/job-7/research/round-3/selected_config.json",
        base_config_path="configs/ema_base.yaml",
        missing_primitives=["ema_length"],
        required_diagnostics=[],
        required_diagnostic_specs=[],
        config_change_keys=["ema_length"],
        mechanism_contract_kind="entry_feature",
        implementation_scope=["strategies/ema/signals.py"],
    )


def _source_root_with_strategy(tmp_path: Path) -> Path:
    source_root = tmp_path / "code"
    (source_root / "strategies" / "ema").mkdir(parents=True)
    (source_root / "strategies" / "ema" / "signals.py").write_text("EMA_LENGTH = 21\n")
    (source_root / "__pycache__").mkdir()
    (source_root / "__pycache__" / "stale.pyc").write_bytes(b"compiled")
    (source_root / ".env").write_text("AUTORESEARCH_SECRET=should-not-copy\n")
    return source_root


def test_stage_inputs_copies_source_tree_proposal_and_compilation(tmp_path: Path) -> None:
    source_root = _source_root_with_strategy(tmp_path)
    proposal_path = source_root / "runtime" / "jobs" / "job-7" / "thesis.json"
    compilation_path = source_root / "runtime" / "jobs" / "job-7" / "contract.json"
    proposal_path.parent.mkdir(parents=True)
    proposal_path.write_text(json.dumps({"strategy_family": "ema"}) + "\n")
    compilation_path.write_text(json.dumps({"strategy_family": "ema"}) + "\n")

    artifact_root = tmp_path / "runtime" / "jobs" / "job-7" / "builder" / "ema-pullback"
    workspace = BuilderWorkspace(artifact_root=artifact_root, source_root=source_root)

    workspace_proposal, workspace_compilation = workspace.stage_inputs(
        proposal_path=proposal_path,
        compilation_path=compilation_path,
        config_path="runtime/jobs/job-7/research/round-3/selected_config.json",
    )

    workspace_root = workspace.workspace_dir
    # Source tree copied with builder ignores applied.
    assert (workspace_root / "strategies" / "ema" / "signals.py").read_text() == "EMA_LENGTH = 21\n"
    assert not (workspace_root / "__pycache__").exists()
    assert not (workspace_root / ".env").exists()
    # Proposal and compilation copied into their source-relative locations.
    assert workspace_proposal == workspace_root / "runtime" / "jobs" / "job-7" / "thesis.json"
    assert workspace_compilation == workspace_root / "runtime" / "jobs" / "job-7" / "contract.json"
    assert workspace_proposal.read_text() == proposal_path.read_text()
    # Generated-config parent directory created.
    assert (workspace_root / "runtime" / "jobs" / "job-7" / "research" / "round-3").is_dir()


def test_seed_from_capability_copies_promoted_files(tmp_path: Path) -> None:
    source_root = _source_root_with_strategy(tmp_path)
    task = _builder_task()
    promotion_dir = BUILDER_PROMOTIONS_DIR / task.family_name / task.thesis_id
    promoted_file = "strategies/ema/signals.py"
    seed_source = source_root / promotion_dir / "files" / promoted_file
    seed_source.parent.mkdir(parents=True)
    seed_source.write_text("EMA_LENGTH = 7\n")

    artifact_root = tmp_path / "runtime" / "jobs" / "job-7" / "builder" / task.thesis_id
    workspace = BuilderWorkspace(artifact_root=artifact_root, source_root=source_root)
    workspace.workspace_dir.mkdir(parents=True)

    seeded = workspace.seed_from_capability(
        seed_entry={
            "promotion_dir": promotion_dir.as_posix(),
            "promoted_files": [promoted_file],
        }
    )

    assert seeded == [promoted_file]
    assert (workspace.workspace_dir / promoted_file).read_text() == "EMA_LENGTH = 7\n"


def test_snapshot_sources_then_sync_root_changes_into_workspace(tmp_path: Path) -> None:
    source_root = _source_root_with_strategy(tmp_path)
    artifact_root = tmp_path / "runtime" / "jobs" / "job-7" / "builder" / "ema-pullback"
    workspace = BuilderWorkspace(artifact_root=artifact_root, source_root=source_root)
    workspace.workspace_dir.mkdir(parents=True)

    snapshot = workspace.snapshot_sources()
    assert "strategies/ema/signals.py" in snapshot

    # Mutate the source file after snapshotting; sync must carry it into the workspace.
    (source_root / "strategies" / "ema" / "signals.py").write_text("EMA_LENGTH = 13\n")
    workspace.sync_root_changes(source_snapshot=snapshot)

    synced = workspace.workspace_dir / "strategies" / "ema" / "signals.py"
    assert synced.read_text() == "EMA_LENGTH = 13\n"


def test_ensure_generated_config_copies_source_config(tmp_path: Path) -> None:
    source_root = _source_root_with_strategy(tmp_path)
    config_path = "runtime/jobs/job-7/research/round-3/selected_config.json"
    source_config = source_root / config_path
    source_config.parent.mkdir(parents=True)
    source_config.write_text(json.dumps({"ema_length": 7}) + "\n")

    artifact_root = tmp_path / "runtime" / "jobs" / "job-7" / "builder" / "ema-pullback"
    workspace = BuilderWorkspace(artifact_root=artifact_root, source_root=source_root)
    workspace.workspace_dir.mkdir(parents=True)

    workspace_config = workspace.ensure_generated_config(
        config_path=config_path,
        source_snapshot={},
    )

    assert workspace_config == workspace.workspace_dir / config_path
    assert workspace_config.read_text() == source_config.read_text()


def test_record_promotion_queues_manifest_and_registry(tmp_path: Path) -> None:
    source_root = _source_root_with_strategy(tmp_path)
    task = _builder_task()
    artifact_root = tmp_path / "runtime" / "jobs" / "job-7" / "builder" / task.thesis_id
    workspace = BuilderWorkspace(artifact_root=artifact_root, source_root=source_root)
    workspace_root = workspace.workspace_dir
    (workspace_root / "strategies" / "ema").mkdir(parents=True)
    # Workspace primitive differs from source — counts as a promotable change.
    (workspace_root / "strategies" / "ema" / "signals.py").write_text("EMA_LENGTH = 7\n")

    manifest = workspace.record_promotion(task=task, thesis_id=task.thesis_id)

    assert manifest["promoted_files"] == ["strategies/ema/signals.py"]
    assert manifest["promotion_status"] == "queued_review"
    promotion_root = source_root / BUILDER_PROMOTIONS_DIR / task.family_name / task.thesis_id
    assert (promotion_root / "files" / "strategies" / "ema" / "signals.py").read_text() == (
        "EMA_LENGTH = 7\n"
    )
    assert (promotion_root / "manifest.json").exists()
    assert (source_root / BUILDER_PROMOTION_QUEUE).exists()
    assert (source_root / BUILDER_CAPABILITY_REGISTRY).exists()


def test_snapshot_promotable_source_state_records_signals(tmp_path: Path) -> None:
    # Promotion artifacts land under the request dir so the staging layout
    # never leaks into the promotable source surface.
    source_root = _source_root_with_strategy(tmp_path)
    artifact_root = tmp_path / "runtime" / "jobs" / "job-7" / "builder" / "ema-pullback"
    workspace = BuilderWorkspace(artifact_root=artifact_root, source_root=source_root)

    assert workspace.request_dir.name == BUILDER_REQUEST_DIRNAME
    snapshot = workspace.snapshot_sources()
    size, mtime_ns = snapshot["strategies/ema/signals.py"]
    assert size == len("EMA_LENGTH = 21\n")
    assert mtime_ns > 0
