from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from compiler_pipeline import (
    build_missing_primitives,
    compile_config_thesis,
    compile_proposal_artifact,
    compile_research_thesis,
    thesis_needs_operationalization,
    validate_orb_runtime_config,
)
from orb_contract import compile_contract as legacy_orb_compile_contract
from research_types import ResearchThesis
from strategies import STRATEGIES
from strategies.ema.validate import validate_ema_runtime_config
from strategy_family import load_family


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


def test_compile_proposal_artifact_writes_family_queue_and_contract(tmp_path: Path) -> None:
    proposal = {
        "thesis_id": "ema_contract_ready",
        "strategy_family": "ema",
        "primitive_contract": [
            {"type": "ema_length", "value": 5},
            {"type": "timeframe_long", "minutes": 15},
            {"type": "timeframe_short", "minutes": 5},
            {"type": "risk_reward", "rr_ratio": 3.0},
        ],
    }

    result = compile_proposal_artifact(proposal, tmp_path)

    assert result["status"] == "ready_to_run"
    contract_path = tmp_path / "ema-contracts" / "ema_contract_ready.json"
    queue_path = tmp_path / "ema-run-queue" / "ema_contract_ready.json"
    assert json.loads(contract_path.read_text()) == result["normalized_contract"]
    queue = json.loads(queue_path.read_text())
    assert queue["status"] == "pending"
    assert queue["config"] == "ema-contracts/ema_contract_ready.json"


def test_orb_strategy_compile_contract_matches_legacy_compiler() -> None:
    contract = [
        {"type": "or_window", "minutes": 30},
        {"type": "risk_reward", "rr": 2.0},
        {"type": "time_stop", "enabled": True, "hour": 12, "minute": 0},
    ]

    strategy_result = STRATEGIES["orb"].compile_contract(contract)
    legacy_result = legacy_orb_compile_contract(contract)

    assert strategy_result.status == legacy_result.status
    assert strategy_result.runtime_config == legacy_result.runtime_config
    assert strategy_result.missing_primitives == legacy_result.missing_primitives
    assert strategy_result.normalized_contract == legacy_result.normalized_contract


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


@pytest.mark.parametrize("family_name,thesis_id", [("ema", "ema_missing"), ("orb", "orb_missing")])
def test_build_missing_primitives_dispatches_family_request_to_cli_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family_name: str, thesis_id: str
) -> None:
    family = load_family(family_name)
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": family_name,
                "hypothesis": "missing primitive should be delegated",
            }
        )
        + "\n"
    )
    (compilation_dir / f"{thesis_id}.json").write_text(
        json.dumps(
            {
                "normalized_contract": [{"type": "missing_probe"}],
                "missing_primitives": ["missing_probe"],
            }
        )
        + "\n"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text("""#!/usr/bin/env python3
import pathlib
import re
import sys

prompt = sys.argv[-1]
root = pathlib.Path(re.search(r"- Repo root: (.+)", prompt).group(1))
config = re.search(r"- Expected config path: (.+)", prompt).group(1)
target = root / config
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("builder_probe: true\\n")
print(f"generated {config}")
""")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"configs/variants/{thesis_id}.yaml"
    assert (tmp_path / result["generated_config"]).read_text() == "builder_probe: true\n"
    request = json.loads(
        (tmp_path / family.builder_requests_dirname / f"{thesis_id}.json").read_text()
    )
    assert request["family"] == family_name
    assert request["missing_primitives"] == ["missing_probe"]
