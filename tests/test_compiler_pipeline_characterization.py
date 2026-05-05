from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import compiler_builder
import compiler_operationalize as co
import persistence_utils
from compiler_operationalize import finalize_thesis_config_changes
from compiler_pipeline import (
    build_missing_primitives,
    compile_config_thesis,
    compile_proposal_artifact,
    compile_research_thesis,
    create_executable_artifact,
    operationalize_thesis,
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


def test_compile_config_thesis_does_not_publish_scope_invalid_runtime_config(
    tmp_path: Path,
) -> None:
    original_validate_scope = STRATEGIES["orb"].validate_runtime_config_scope
    STRATEGIES["orb"].validate_runtime_config_scope = lambda config, source_path=None: (_ for _ in ()).throw(  # type: ignore[assignment]
        ValueError("scope invalid")
    )
    try:
        result = compile_config_thesis(
            "orb",
            "orb-test",
            {"or_minutes": 20},
            tmp_path,
        )
    finally:
        STRATEGIES["orb"].validate_runtime_config_scope = original_validate_scope  # type: ignore[assignment]

    assert result["status"] != "ready_to_run"
    assert result["config_path"] is None


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


def test_compile_research_thesis_leaves_no_tmp_artifacts(tmp_path: Path) -> None:
    thesis = ResearchThesis(
        thesis_id="thesis-ready",
        strategy_family="ema",
        hypothesis="Shorter EMA reacts faster.",
        mechanism="Reduce lag in signal generation.",
        config_changes={"ema_length": 9},
    )

    compile_research_thesis(thesis, tmp_path)

    assert not list(tmp_path.rglob("*.tmp"))


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


def test_compile_proposal_artifact_writes_atomic_json_artifacts(tmp_path: Path) -> None:
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

    compile_proposal_artifact(proposal, tmp_path)

    assert not list(tmp_path.rglob("*.tmp"))


def test_compile_proposal_artifact_rejects_unloadable_ready_contract(
    tmp_path: Path, monkeypatch
) -> None:
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

    original_compile = STRATEGIES["ema"].compile_contract

    def _bad_compile(contract):
        result = original_compile(contract)
        return type(result)(
            status="ready_to_run",
            runtime_config={"ema_length": -1},
            missing_primitives=result.missing_primitives,
            normalized_contract=result.normalized_contract,
        )

    monkeypatch.setattr(STRATEGIES["ema"], "compile_contract", _bad_compile)

    result = compile_proposal_artifact(proposal, tmp_path)

    assert result["status"] == "rejected_at_compile"
    assert not (tmp_path / "ema-contracts" / "ema_contract_ready.json").exists()
    assert not (tmp_path / "ema-run-queue" / "ema_contract_ready.json").exists()


def test_compile_proposal_artifact_does_not_publish_compilation_before_queue(
    tmp_path: Path, monkeypatch
) -> None:
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

    original_replace = persistence_utils.os.replace

    def _crash_on_queue_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]):
        if Path(dst).parent.name == "ema-run-queue":
            raise RuntimeError("queue write failed")
        return original_replace(src, dst)

    monkeypatch.setattr(persistence_utils.os, "replace", _crash_on_queue_replace)

    with pytest.raises(RuntimeError, match="queue write failed"):
        compile_proposal_artifact(proposal, tmp_path)

    assert not (tmp_path / "ema-compilations" / "ema_contract_ready.json").exists()
    assert not (tmp_path / "ema-contracts" / "ema_contract_ready.json").exists()
    assert not (tmp_path / "ema-run-queue" / "ema_contract_ready.json").exists()


def test_compile_proposal_artifact_leaves_no_tmp_artifacts_after_publish(tmp_path: Path) -> None:
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

    compile_proposal_artifact(proposal, tmp_path)

    assert not list(tmp_path.rglob("*.tmp"))


def test_compile_proposal_artifact_persists_iso8601_timestamps(tmp_path: Path) -> None:
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

    queue_payload = json.loads((tmp_path / "ema-run-queue" / "ema_contract_ready.json").read_text())
    compilation_payload = result
    assert isinstance(queue_payload["timestamp"], str)
    assert queue_payload["timestamp"].endswith("+00:00") or queue_payload["timestamp"].endswith("Z")
    assert isinstance(compilation_payload["timestamp"], str)
    assert compilation_payload["timestamp"].endswith("+00:00") or compilation_payload[
        "timestamp"
    ].endswith("Z")


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


def test_operationalize_thesis_preserves_ambiguous_intent_even_with_config_changes() -> None:
    thesis = {
        "strategy_family": "orb",
        "hypothesis": "Use stocks in play universe.",
        "mechanism": "Focus on a narrow opening range.",
        "config_changes": {"or_minutes": 5},
    }

    operationalized = operationalize_thesis(dict(thesis))

    assert thesis_needs_operationalization(thesis) is True
    assert operationalized["primitive_contract"] != STRATEGIES[
        "orb"
    ].map_config_changes_to_contract({"or_minutes": 5})


def test_operationalization_agent_import_path_remains_compatible(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def fake_run_single_agent(name, prompt, agent_def, retries=2, timeout=300):
        called["name"] = name
        called["prompt"] = prompt
        called["agent_def"] = agent_def
        return {
            "resolved_changes": {"entry_cutoff_time": "09:35"},
            "reasoning": "resolved",
            "requires_code_change": False,
        }

    monkeypatch.setattr("agent_orchestrator._run_single_agent", fake_run_single_agent)

    thesis = {
        "thesis_id": "open_window",
        "strategy_family": "ema",
        "hypothesis": "narrow the opening range",
        "mechanism": "restrict entry timing to a narrower open window",
    }

    result = co._run_operationalization_agent(thesis)

    assert called["name"] == "operationalization-agent"
    assert result["resolved_changes"] == {"entry_cutoff_time": "09:35"}
    assert result["requires_code_change"] is False


def test_finalize_thesis_config_changes_carries_resolved_changes_into_proposal_contract(
    tmp_path: Path,
) -> None:
    thesis = {
        "thesis_id": "resolved-changes",
        "strategy_family": "orb",
        "hypothesis": "Stocks in play should outperform.",
        "mechanism": "Use a narrower opening range.",
    }
    clarification = {
        "resolved_changes": {"or_minutes": 10},
        "reasoning": "Resolved into explicit runtime config.",
        "requires_code_change": False,
    }

    finalized = finalize_thesis_config_changes(thesis, clarification)
    thesis_dir = tmp_path / "theses"
    thesis_dir.mkdir()
    base_config = tmp_path / "configs" / "orb_base.yaml"
    base_config.parent.mkdir(parents=True, exist_ok=True)
    base_config.write_text("validation_start: 2020-01-01\nvalidation_end: 2020-12-31\n")
    result = create_executable_artifact(thesis_dir, base_config, finalized, tmp_path)

    assert result["generated_config"].startswith("orb-contracts/")
    payload = json.loads((tmp_path / result["generated_config"]).read_text())
    assert payload["or_minutes"] == 10


def test_finalize_thesis_config_changes_rejects_incomplete_resolved_changes_for_supported_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thesis = {
        "thesis_id": "resolved-changes",
        "strategy_family": "orb",
        "hypothesis": "Stocks in play should outperform.",
        "mechanism": "Use a narrower opening range.",
    }
    clarification = {
        "resolved_contract": [{"type": "opening_range", "or_minutes": 10}],
        "resolved_changes": {"or_minutes": 10},
        "reasoning": "Resolved into explicit runtime config.",
        "requires_code_change": False,
    }

    monkeypatch.setattr(
        STRATEGIES["orb"],
        "render_contract_to_runtime_config",
        lambda contract: (_ for _ in ()).throw(ValueError("cannot render")),
    )

    with pytest.raises(ValueError, match="resolved contract could not be rendered"):
        finalize_thesis_config_changes(thesis, clarification)


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


def test_build_missing_primitives_uses_short_timeout_for_codex_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    family = load_family("ema")
    thesis_id = "ema_timeout_probe"
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps({"thesis_id": thesis_id, "strategy_family": "ema"}) + "\n"
    )
    (compilation_dir / f"{thesis_id}.json").write_text(
        json.dumps({"normalized_contract": [], "missing_primitives": ["probe"]}) + "\n"
    )

    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, cwd, timeout, input):
        captured["cmd"] = cmd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["input"] = input
        target = tmp_path / "configs" / "variants" / f"{thesis_id}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "data_universe: nasdaq8\nvalidation_start: 2020-01-01\nvalidation_end: 2020-12-31\n"
        )
        return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_bypass_flag", lambda *args, **kwargs: True
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert captured["cmd"] == [
        "codex",
        "--dangerously-bypass-approvals-and-sandbox",
        "exec",
        "--model",
        "gpt-5.4",
    ]
    assert captured["input"].startswith("Goal:\nImplement the missing primitive(s)")
    assert "Thesis payload" not in captured["input"]
    assert "Contract payload" not in captured["input"]
    assert captured["timeout"] == compiler_builder.BUILDER_CLI_TIMEOUT_SECONDS
    assert result["status"] == "completed"
    attempt_dir = tmp_path / family.builder_requests_dirname / thesis_id
    assert (attempt_dir / "prompt.txt").exists()
    assert (attempt_dir / "command.json").exists()
    assert (attempt_dir / "stdout.log").exists()
    assert (attempt_dir / "stderr.log").exists()
    assert (attempt_dir / "result.json").exists()
    assert json.loads((attempt_dir / "result.json").read_text())["exit_code"] == 0


def test_build_missing_primitives_reports_timeout_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    family = load_family("ema")
    thesis_id = "ema_timeout_expired"
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps({"thesis_id": thesis_id, "strategy_family": "ema"}) + "\n"
    )
    (compilation_dir / f"{thesis_id}.json").write_text(
        json.dumps({"normalized_contract": [], "missing_primitives": ["probe"]}) + "\n"
    )

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("cmd") or args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_bypass_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert "timed out after" in result["reason"]
    attempt_dir = tmp_path / family.builder_requests_dirname / thesis_id
    assert (attempt_dir / "result.json").exists()
    assert json.loads((attempt_dir / "result.json").read_text())["timed_out"] is True


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
    codex = bin_dir / "codex"
    codex.write_text("""#!/usr/bin/env python3
import pathlib
import re
import sys

prompt = sys.stdin.read() or sys.argv[-1]
root = pathlib.Path(re.search(r"- Repo root: (.+)", prompt).group(1))
config = re.search(r"- Expected config path: (.+)", prompt).group(1)
target = root / config
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("data_universe: nasdaq8\\nbuilder_probe: true\\nallow_unbounded_research_backtest: true\\nvalidation_start: 2020-01-01\\nvalidation_end: 2020-12-31\\n")
print(f"generated {config}")
""")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"configs/variants/{thesis_id}.yaml"
    written = (tmp_path / result["generated_config"]).read_text()
    assert "builder_probe: true" in written
    request = json.loads(
        (tmp_path / family.builder_requests_dirname / f"{thesis_id}.json").read_text()
    )
    assert request["family"] == family_name
    assert request["missing_primitives"] == ["missing_probe"]


def test_build_missing_primitives_rejects_invalid_generated_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    family = load_family("ema")
    thesis_id = "ema_invalid_generated"
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps({"thesis_id": thesis_id, "strategy_family": "ema", "hypothesis": "bad build"})
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
    codex = bin_dir / "codex"
    codex.write_text("""#!/usr/bin/env python3
import pathlib
import re
import sys

prompt = sys.stdin.read() or sys.argv[-1]
root = pathlib.Path(re.search(r"- Repo root: (.+)", prompt).group(1))
config = re.search(r"- Expected config path: (.+)", prompt).group(1)
target = root / config
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("ema_length: -1\\n")
print(f"generated {config}")
""")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert result["validation_passed"] is False


def test_build_missing_primitives_reports_malformed_upstream_artifacts(tmp_path: Path) -> None:
    family = load_family("ema")
    thesis_id = "ema_broken_artifacts"
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text("{not valid json")
    (compilation_dir / f"{thesis_id}.json").write_text(json.dumps({"normalized_contract": []}))

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert "malformed" in result["reason"].lower()


def test_build_missing_primitives_uses_structured_halted_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thesis_id = "halted_thesis"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "tighten ema length",
                "mechanism": "reduce lag",
                "config_changes": {"ema_length": 7},
                "requested_primitives": ["ema_length"],
            }
        )
        + "\n"
    )
    (experiment_dir / "contract.json").write_text(
        json.dumps(
            {
                "experiment_id": thesis_id,
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "baseline_config_path": "configs/ema_base.yaml",
                "runtime_config": {},
                "hypothesis": "tighten ema length",
                "mechanism": "reduce lag",
                "status": "needs_code",
            }
        )
        + "\n"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text("""#!/usr/bin/env python3
import pathlib
import re
import sys

prompt = sys.stdin.read() or sys.argv[-1]
root = pathlib.Path(re.search(r"- Repo root: (.+)", prompt).group(1))
config = re.search(r"- Expected config path: (.+)", prompt).group(1)
target = root / config
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text('''{"data_universe": "nasdaq8", "allow_unbounded_research_backtest": true, "validation_start": "2020-01-01", "validation_end": "2020-12-31", "timeframe_long": 15, "timeframe_short": 5, "ema_length": 7, "rr_ratio": 2.5}''')
print(f"generated {config}")
""")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"experiments/{thesis_id}/runtime_config.json"
    assert (experiment_dir / "runtime_config.json").exists()
    request = json.loads((tmp_path / "ema-builder-requests" / f"{thesis_id}.json").read_text())
    assert request["family"] == "ema"
    assert request["missing_primitives"] == ["ema_length"]


def test_build_missing_primitives_rejects_unknown_structured_family(
    tmp_path: Path,
) -> None:
    thesis_id = "halted_unknown_family"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "not-a-real-family",
                "hypothesis": "tighten ema length",
                "mechanism": "reduce lag",
                "config_changes": {"ema_length": 7},
            }
        )
        + "\n"
    )
    (experiment_dir / "contract.json").write_text(
        json.dumps(
            {
                "experiment_id": thesis_id,
                "thesis_id": thesis_id,
                "strategy_family": "not-a-real-family",
                "baseline_config_path": "configs/ema_base.yaml",
                "runtime_config": {},
                "hypothesis": "tighten ema length",
                "mechanism": "reduce lag",
                "status": "needs_code",
            }
        )
        + "\n"
    )

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert "unknown strategy family" in result["reason"]


def test_build_missing_primitives_reports_error_when_proposal_has_no_family_field(
    tmp_path: Path,
) -> None:
    # Covers the legacy path guard added for compiler_builder.py:196 —
    # proposal with neither strategy_family nor family must return a clean error dict,
    # not raise KeyError.
    family = load_family("ema")
    thesis_id = "ema_no_family_field"
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                # intentionally omit both strategy_family and family
                "hypothesis": "missing family field probe",
            }
        )
        + "\n"
    )
    (compilation_dir / f"{thesis_id}.json").write_text(
        json.dumps({"normalized_contract": [], "missing_primitives": ["probe"]}) + "\n"
    )

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert "missing strategy_family/family" in result["reason"]
    assert result["generated_config"] is None
    assert result["validation_passed"] is False
