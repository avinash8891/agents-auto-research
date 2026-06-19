from __future__ import annotations

import json
from pathlib import Path

from compiler_builder import (
    BUILDER_CAPABILITY_REGISTRY,
    BuilderTask,
    _load_builder_capability_registry,
    _record_builder_promotion_candidate,
)


def _builder_task() -> BuilderTask:
    return BuilderTask(
        thesis_id="ema-thesis",
        family_name="ema",
        proposal_path="runtime/proposals/ema-thesis.json",
        compilation_path="runtime/compilations/ema-thesis.json",
        config_path="configs/variants/ema_thesis.yaml",
        base_config_path="configs/ema_base.yaml",
        missing_primitives=["rvol_spike"],
        required_diagnostics=[],
        required_diagnostic_specs=[],
        config_change_keys=[],
        mechanism_contract_kind="entry_feature",
        implementation_scope=[],
    )


def test_builder_capability_registry_latest_entry_wins(tmp_path: Path) -> None:
    path = tmp_path / BUILDER_CAPABILITY_REGISTRY
    path.parent.mkdir(parents=True)
    first = {
        "family_name": "ema",
        "kind": "entry_feature",
        "missing_primitives": ["rvol_spike"],
        "config_change_keys": [],
        "diagnostic_keys": [],
        "promotion_dir": "old",
    }
    second = {**first, "promotion_dir": "new"}
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")

    entries = _load_builder_capability_registry(tmp_path)

    assert len(entries) == 1
    assert entries[0]["promotion_dir"] == "new"


def test_promotion_manifest_marks_agent_created(tmp_path: Path) -> None:
    manifest = _record_builder_promotion_candidate(
        source_root=tmp_path,
        workspace_root=tmp_path,
        artifact_root=tmp_path,
        task=_builder_task(),
        thesis_id="ema-thesis",
    )

    assert manifest["created_by"] == "agent"
    assert manifest["created_at"]
