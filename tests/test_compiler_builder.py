from __future__ import annotations

import json
from pathlib import Path

from compiler_builder import (
    BUILDER_CAPABILITY_REGISTRY,
    BuilderTask,
    _classify_requested_primitive_data,
    _load_builder_capability_registry,
    _record_builder_promotion_candidate,
    _register_declarative_entry_feature,
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


def test_promotion_manifest_appends_builder_capability_registry(tmp_path: Path) -> None:
    manifest = _record_builder_promotion_candidate(
        source_root=tmp_path,
        workspace_root=tmp_path,
        artifact_root=tmp_path,
        task=_builder_task(),
        thesis_id="ema-thesis",
    )

    entries = _load_builder_capability_registry(tmp_path)

    assert entries == [
        {
            "family_name": "ema",
            "kind": "entry_feature",
            "missing_primitives": ["rvol_spike"],
            "config_change_keys": [],
            "diagnostic_keys": [],
            "promoted_files": manifest["promoted_files"],
            "promotion_dir": manifest["promotion_dir"],
            "thesis_id": "ema-thesis",
            "build_status": "passed",
            "created_by": "agent",
            "created_at": manifest["created_at"],
        }
    ]


def test_requested_primitive_data_classifier_detects_missing_raw_inputs(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "raw_input_manifest.json"
    path.parent.mkdir()
    path.write_text('{"available_raw_inputs": ["ohlcv"]}')
    proposal = {
        "requested_primitive": {
            "name": "signed_volume_z",
            "kind": "entry_feature",
            "required_data": ["trade_signed_volume"],
        }
    }

    out = _classify_requested_primitive_data(tmp_path, proposal)

    assert out is not None
    assert out["error_code"] == "builder_needs_data"
    assert out["missing_raw_inputs"] == ["trade_signed_volume"]


def test_requested_primitive_data_classifier_ignores_ordinary_proposals(tmp_path: Path) -> None:
    assert _classify_requested_primitive_data(tmp_path, {"config_changes": {}}) is None


def test_requested_primitive_data_classifier_fails_loud_for_missing_manifest(
    tmp_path: Path,
) -> None:
    proposal = {
        "requested_primitive": {
            "name": "signed_volume_z",
            "kind": "entry_feature",
            "required_data": ["trade_signed_volume"],
        }
    }

    out = _classify_requested_primitive_data(tmp_path, proposal)

    assert out is not None
    assert out["error_code"] == "builder_raw_input_manifest_invalid"


def test_register_declarative_entry_feature_persists_agent_feature(tmp_path: Path) -> None:
    proposal = {
        "requested_primitive": {
            "name": "rvol_spike",
            "kind": "entry_feature",
            "formula": "rvol / rolling_mean(rvol, 20)",
            "required_data": ["ohlcv"],
        }
    }

    _register_declarative_entry_feature(
        tmp_path,
        proposal=proposal,
        family_name="ema",
        thesis_id="ema-thesis",
    )

    payload = (tmp_path / "runtime" / "agent_features.jsonl").read_text()
    assert '"column": "rvol_spike"' in payload
    assert '"status": "exploratory"' in payload
