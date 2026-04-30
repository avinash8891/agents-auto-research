from __future__ import annotations

import json
from pathlib import Path

import pytest

from compiler_pipeline import (
    build_missing_primitives,
    compile_config_thesis,
    compile_research_thesis,
    thesis_needs_operationalization,
    validate_orb_runtime_config,
)
from research_types import ResearchThesis
from strategies.ema.validate import validate_ema_runtime_config


class _DummyCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


def test_compiler_pipeline_public_facade_imports() -> None:
    expected = [
        "compile_research_thesis",
        "compile_proposal_artifact",
        "compile_config_thesis",
        "create_executable_artifact",
        "derive_thesis_artifacts",
        "write_research_artifact",
        "mark_request_completed",
        "_get_orb_defaults",
        "validate_orb_runtime_config",
        "thesis_needs_operationalization",
        "operationalize_thesis",
        "build_missing_primitives",
    ]

    import compiler_pipeline

    for name in expected:
        value = getattr(compiler_pipeline, name)
        assert value
        assert callable(value) or value is not None


def test_compile_config_thesis_uses_registered_strategy_defaults(tmp_path: Path) -> None:
    result = compile_config_thesis("ema", "ema-test", {"ema_length": 9}, tmp_path)
    assert result["status"] == "ready_to_run"
    assert result["runtime_config"]["ema_length"] == 9
    assert result["config_path"].startswith("ema-contracts/")


def test_validate_ema_runtime_config_rejects_negative_ema_length() -> None:
    violations = validate_ema_runtime_config({"ema_length": -5})
    assert "ema_length=-5: must be >= 2 (EMA of 1 is just price)" in violations


def test_validate_orb_runtime_config_rejects_or_minutes_out_of_range() -> None:
    violations = validate_orb_runtime_config({"or_minutes": 121})
    assert "or_minutes=121 out of range [5, 120]" in violations


def test_compile_research_thesis_writes_three_files_for_ready_to_run(tmp_path: Path) -> None:
    thesis = ResearchThesis(
        thesis_id="thesis-ready",
        strategy_family="ema",
        hypothesis="Shorter EMA reacts faster.",
        mechanism="Reduce lag in signal generation.",
        config_changes={"ema_length": 9},
    )

    contract = compile_research_thesis(thesis, tmp_path)

    experiment_dir = tmp_path / "experiments" / contract.experiment_id
    assert contract.status == "ready_to_run"
    assert (experiment_dir / "thesis.json").exists()
    assert (experiment_dir / "contract.json").exists()
    assert (experiment_dir / "runtime_config.json").exists()


def test_compile_research_thesis_status_needs_code_when_invalid_keys(tmp_path: Path) -> None:
    thesis = ResearchThesis(
        thesis_id="thesis-needs-code",
        strategy_family="ema",
        hypothesis="Use a missing primitive.",
        mechanism="Unknown config key should require code.",
        config_changes={"not_a_real_key": 1},
    )

    contract = compile_research_thesis(thesis, tmp_path)

    assert contract.status == "needs_code"
    assert contract.runtime_config == {}
    assert (tmp_path / "experiments" / thesis.thesis_id / "thesis.json").exists()
    assert (tmp_path / "experiments" / thesis.thesis_id / "contract.json").exists()
    assert not (tmp_path / "experiments" / thesis.thesis_id / "runtime_config.json").exists()


def test_thesis_needs_operationalization_detects_ambiguous_terms() -> None:
    thesis = {
        "hypothesis": "Stocks in play should work better.",
        "mechanism": "Focus on a narrow opening range.",
    }
    assert thesis_needs_operationalization(thesis) is True


def test_build_missing_primitives_returns_error_when_no_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    proposal_dir = root / "orb-proposals"
    compilation_dir = root / "orb-compilations"
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)

    thesis_id = "orb_missing_cli"
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps({"thesis_id": thesis_id, "strategy_family": "orb"}) + "\n"
    )
    (compilation_dir / f"{thesis_id}.json").write_text(
        json.dumps({"normalized_contract": [], "missing_primitives": ["x"]}) + "\n"
    )

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: None)

    def _fail_subprocess_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called when no CLI is available")

    monkeypatch.setattr("compiler_builder.subprocess.run", _fail_subprocess_run)

    result = build_missing_primitives(root, thesis_id)

    assert result == {
        "status": "error",
        "reason": "No CLI available for builder dispatch",
        "generated_config": None,
        "validation_passed": False,
    }
