from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import compiler_builder
import compiler_operationalize as co
import persistence_utils
from compiler_implementation_verify import verify_builder_implementation_contract
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


def _ema_runtime_config(**overrides):
    config = dict(STRATEGIES["ema"].get_defaults())
    config.update(overrides)
    return config


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


def test_compile_config_thesis_classifies_unknown_ema_key_as_code_change(tmp_path: Path) -> None:
    result = compile_config_thesis(
        "ema",
        "ema-vwap",
        {"vwap_side_gate_enabled": True},
        tmp_path,
    )

    assert result["status"] == "requires_code_change"
    assert result["invalid_keys"] == ["vwap_side_gate_enabled"]
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


def test_compile_research_thesis_applies_changes_to_declared_base_config(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "experiments" / "best_trailing"
    base_dir.mkdir(parents=True)
    base_path = base_dir / "runtime_config.json"
    base_path.write_text(
        json.dumps(
            {
                **STRATEGIES["ema"].get_defaults(),
                "trail_after_r": 1.0,
            }
        )
        + "\n"
    )
    thesis = ResearchThesis(
        thesis_id="delay-on-best",
        strategy_family="ema",
        hypothesis="Delay open entries on the best trailing configuration.",
        mechanism="Preserve trailing exits while adding the open delay gate.",
        base_experiment_id="best_trailing",
        base_config_path="experiments/best_trailing/runtime_config.json",
        config_changes={
            "max_trades_per_day": 2,
        },
    )

    contract = compile_research_thesis(thesis, tmp_path)

    experiment_dir = tmp_path / "experiments" / contract.experiment_id
    runtime_config = json.loads((experiment_dir / "runtime_config.json").read_text())
    contract_payload = json.loads((experiment_dir / "contract.json").read_text())
    assert (
        contract_payload["baseline_config_path"] == "experiments/best_trailing/runtime_config.json"
    )
    assert contract_payload["base_experiment_id"] == "best_trailing"
    assert runtime_config["trail_after_r"] == 1.0
    assert runtime_config["max_trades_per_day"] == 2


def test_compile_research_thesis_rejects_noop_config_change_against_base(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / "experiments" / "best_opening_drive"
    base_dir.mkdir(parents=True)
    base_path = base_dir / "runtime_config.json"
    base_path.write_text(
        json.dumps(
            {
                **STRATEGIES["ema"].get_defaults(),
                "max_stop_distance_pct": None,
            }
        )
        + "\n"
    )
    thesis = ResearchThesis(
        thesis_id="widen-stop-noop",
        strategy_family="ema",
        hypothesis="Remove the max stop cap on the current best configuration.",
        mechanism="The stop cap may cause premature exits.",
        base_experiment_id="best_opening_drive",
        base_config_path="experiments/best_opening_drive/runtime_config.json",
        config_changes={"max_stop_distance_pct": None},
    )

    with pytest.raises(ValueError, match="config_changes did not change runtime_config"):
        compile_research_thesis(thesis, tmp_path)

    assert not list((tmp_path / "experiments").glob("*/runtime_config.json.tmp"))
    assert not (tmp_path / "experiments" / "widen-stop-noop" / "runtime_config.json").exists()


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


def test_build_missing_primitives_includes_builder_reflexion_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTORESEARCH_IMPROVEMENT_REFLEXION", "1")
    family = load_family("ema")
    thesis_id = "ema_reflexion_builder"
    proposal_dir = tmp_path / family.proposals_dirname
    compilation_dir = tmp_path / family.compilations_dirname
    proposal_dir.mkdir(parents=True)
    compilation_dir.mkdir(parents=True)
    (proposal_dir / f"{thesis_id}.json").write_text(
        json.dumps({"thesis_id": thesis_id, "strategy_family": "ema"}) + "\n"
    )
    (compilation_dir / f"{thesis_id}.json").write_text(
        json.dumps({"normalized_contract": [], "missing_primitives": ["open_delay"]}) + "\n"
    )
    reflexio_dir = tmp_path / "trace_exports" / "round-003-prior" / "reflexio"
    reflexio_dir.mkdir(parents=True)
    (reflexio_dir / "reflexio-event.json").write_text(
        json.dumps(
            {
                "system": "reflexio",
                "episode": {
                    "round": 3,
                    "family": "ema",
                    "thesis_id": "prior",
                    "outcome": "builder_failed",
                },
                "reflection": {
                    "reasoning": "builder copied planning metadata into runtime config",
                    "rejection_reason": "unsupported runtime key",
                    "quality": {},
                },
                "trajectory": [
                    {
                        "agent": "builder",
                        "action": "tool_result",
                        "summary": "new_config_keys_needed leaked into runtime_config.json",
                    },
                    {
                        "agent": "analyst",
                        "action": "tool_result",
                        "summary": "analyst path probe should not enter builder prompt",
                    },
                ],
                "resources": {"usage": {}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: None)

    build_missing_primitives(tmp_path, thesis_id)

    prompt = (tmp_path / family.builder_requests_dirname / thesis_id / "prompt.txt").read_text(
        encoding="utf-8"
    )
    assert "PRIOR AGENT REFLEXION (round 3, agent builder)" in prompt
    assert "new_config_keys_needed leaked into runtime_config.json" in prompt
    assert "analyst path probe" not in prompt


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

    def fake_run(cmd, capture_output, text, cwd=None, timeout=None, input=None, **kwargs):
        if cmd == ["codex", "exec", "--help"]:
            return type(
                "HelpProc",
                (),
                {
                    "stdout": "usage: codex exec [OPTIONS]\n  --sandbox <SANDBOX_MODE>\n",
                    "stderr": "",
                    "returncode": 0,
                },
            )()
        if "-c" in cmd:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        captured["cmd"] = cmd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["input"] = input
        target = tmp_path / "configs" / "variants" / f"{thesis_id}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_ema_runtime_config()) + "\n")
        return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert captured["cmd"] == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--model",
        "gpt-5.2",
    ]
    assert captured["input"].startswith("Instructions:\n")
    assert captured["input"].find("Instructions:\n") < captured["input"].find("Context:\n")
    assert captured["input"].find("Context:\n") < captured["input"].find("Goal:\n")
    assert "Base config path: configs/ema_base.yaml" in captured["input"]
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
    request = json.loads(
        (tmp_path / family.builder_requests_dirname / f"{thesis_id}.json").read_text()
    )
    assert request["base_config_path"] == "configs/ema_base.yaml"


def test_build_missing_primitives_trace_links_builder_attempt_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    family = load_family("ema")
    thesis_id = "ema_trace_artifacts"
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

    def fake_run(cmd, capture_output, text, cwd=None, timeout=None, input=None, **kwargs):
        target = tmp_path / "configs" / "variants" / f"{thesis_id}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_ema_runtime_config()) + "\n")
        return type(
            "Proc", (), {"stdout": "builder stdout", "stderr": "builder stderr", "returncode": 0}
        )()

    events: list[dict[str, object]] = []
    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)
    monkeypatch.setattr("compiler_builder.trace", lambda *args, **kwargs: None)
    monkeypatch.setattr("compiler_builder.record_event", lambda **kwargs: events.append(kwargs))

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    finish_events = [
        event for event in events if event["category"] == "builder" and event["action"] == "finish"
    ]
    assert len(finish_events) == 1
    artifact_names = {Path(path).name for path in finish_events[0]["artifact_paths"]}
    assert {
        "prompt.txt",
        "command.json",
        "stdout.log",
        "stderr.log",
        "result.json",
    } <= artifact_names


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
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert "timed out after" in result["reason"]
    attempt_dir = tmp_path / family.builder_requests_dirname / thesis_id
    assert (attempt_dir / "result.json").exists()
    assert json.loads((attempt_dir / "result.json").read_text())["timed_out"] is True


def test_build_missing_primitives_accepts_valid_config_written_before_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    thesis_id = "momentum_gated_trailing_activation"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "gate trailing by opening momentum",
                "mechanism": "only allow trailing in persistent opening regimes",
                "config_changes": {"requires_engine_change": True},
                "requested_primitives": [],
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
                "hypothesis": "gate trailing by opening momentum",
                "mechanism": "only allow trailing in persistent opening regimes",
                "status": "needs_code",
            }
        )
        + "\n"
    )

    def fake_run(cmd, *args, **kwargs):
        if "-c" in cmd:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        target = experiment_dir / "runtime_config.json"
        target.write_text(json.dumps(_ema_runtime_config()) + "\n")
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=kwargs["timeout"],
            output="generated config but kept inspecting diff",
            stderr="still reviewing unrelated dirty worktree",
        )

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"experiments/{thesis_id}/runtime_config.json"
    assert result["validation_passed"] is True
    assert result["timed_out"] is True
    assert "timed out after writing a valid config" in result["reason"]
    persisted = json.loads(
        (tmp_path / "ema-builder-requests" / thesis_id / "result.json").read_text()
    )
    assert persisted["status"] == "completed"
    assert persisted["timed_out"] is True
    assert (
        tmp_path / "ema-builder-requests" / thesis_id / "stdout.log"
    ).read_text() == "generated config but kept inspecting diff"
    assert (
        tmp_path / "ema-builder-requests" / thesis_id / "stderr.log"
    ).read_text() == "still reviewing unrelated dirty worktree"


def test_builder_implementation_verifier_rejects_unconsumed_vwap_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data-root"
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "validate.py").write_text(
        "SUPPORTED = ['vwap_side_gate_enabled', 'vwap_side_consecutive_closes_required']\n"
    )
    experiment_dir = tmp_path / "experiments" / "vwap_thesis"
    experiment_dir.mkdir(parents=True)
    config_path = experiment_dir / "runtime_config.json"
    config_path.write_text(
        json.dumps(
            {
                "data_universe": "nasdaq8",
                "vwap_side_gate_enabled": True,
                "vwap_side_consecutive_closes_required": 2,
            }
        )
        + "\n"
    )
    thesis = {
        "config_changes": {
            "vwap_side_gate_enabled": True,
            "vwap_side_consecutive_closes_required": 2,
        },
        "required_diagnostics": ["trade_count_blocked_by_vwap_gate"],
        "mechanism": "Require price acceptance below VWAP.",
    }

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/vwap_thesis/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert "config_key_not_consumed_by_runtime:vwap_side_gate_enabled" in result.failures
    assert (
        "config_key_not_consumed_by_runtime:vwap_side_consecutive_closes_required"
        in result.failures
    )
    assert "required_diagnostic_not_emitted:trade_count_blocked_by_vwap_gate" in result.failures
    assert any(failure.startswith("vwap_data_dependency_missing:") for failure in result.failures)


def test_builder_implementation_verifier_rejects_metadata_config_change_consumption(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("config.get('requires_code_change')\n")
    experiment_dir = tmp_path / "experiments" / "metadata_leak"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps({"requires_code_change": True}) + "\n"
    )
    thesis = {"config_changes": {"requires_code_change": True}}

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/metadata_leak/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert "metadata_config_change_not_allowed:requires_code_change" in result.failures


def test_builder_implementation_verifier_rejects_new_config_keys_needed_runtime_metadata(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("config.get('new_config_keys_needed')\n")
    experiment_dir = tmp_path / "experiments" / "metadata_leak"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps(
            {
                "new_config_keys_needed": {
                    "open_execution_delay_minutes": 3,
                }
            }
        )
        + "\n"
    )
    thesis = {
        "config_changes": {
            "new_config_keys_needed": {
                "open_execution_delay_minutes": 3,
            }
        }
    }

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/metadata_leak/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert "metadata_config_change_not_allowed:new_config_keys_needed" in result.failures


def test_builder_implementation_verifier_rejects_undeclared_drift_from_base_config(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "strategy.py").write_text("config.get('max_trades_per_day')\n")
    base_dir = tmp_path / "experiments" / "best_trailing"
    base_dir.mkdir(parents=True)
    (base_dir / "runtime_config.json").write_text(
        json.dumps(
            {
                **STRATEGIES["ema"].get_defaults(),
                "trail_after_r": 1.0,
                "max_trades_per_day": 3,
            }
        )
        + "\n"
    )
    experiment_dir = tmp_path / "experiments" / "delay_on_best"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps(
            {
                **STRATEGIES["ema"].get_defaults(),
                "trail_after_r": None,
                "max_trades_per_day": 2,
            }
        )
        + "\n"
    )
    thesis = {
        "config_changes": {
            "max_trades_per_day": 2,
        }
    }
    contract = {"baseline_config_path": "experiments/best_trailing/runtime_config.json"}

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        contract=contract,
        generated_config_path="experiments/delay_on_best/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert "unexpected_config_drift:trail_after_r base=1.0 generated=null" in result.failures


def test_builder_implementation_verifier_uses_registered_baseline_filename_for_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    family_name = "custom_family"
    experiment_dir = tmp_path / "experiments" / "custom"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(json.dumps({"alpha": 1, "beta": 2}) + "\n")

    monkeypatch.setattr(
        "compiler_implementation_verify.load_family",
        lambda name: SimpleNamespace(baseline_config_path="configs/custom_seed.yaml"),
    )
    monkeypatch.setitem(
        STRATEGIES,
        family_name,
        SimpleNamespace(get_defaults=lambda: {"alpha": 1, "beta": 2}),
    )

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis={"config_changes": {}},
        contract={"baseline_config_path": "configs/custom_seed.yaml"},
        generated_config_path="experiments/custom/runtime_config.json",
        family_name=family_name,
    )

    assert result.failures == []
    assert result.passed is True


def test_builder_normalizes_legacy_new_config_keys_needed_before_missing_primitives() -> None:
    proposal = {
        "requires_code_change": True,
        "config_changes": {
            "new_config_keys_needed": {
                "open_execution_delay_enabled": True,
                "open_execution_delay_minutes": 3,
            }
        },
    }

    normalized, changed = compiler_builder._normalize_proposal_config_changes(proposal)

    assert changed is True
    assert normalized["requires_code_change"] is True
    assert normalized["config_changes"] == {
        "open_execution_delay_enabled": True,
        "open_execution_delay_minutes": 3,
    }
    assert compiler_builder._resolve_missing_primitives(normalized, {}) == [
        "open_execution_delay_enabled",
        "open_execution_delay_minutes",
    ]


def test_builder_implementation_verifier_requires_thesis_diagnostic_names(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "exits.py").write_text(
        "config.get('per_symbol_entry_cooldown_minutes')\n"
        "event_logger.log_order_rejected(reason='entry_cooldown')\n"
    )
    experiment_dir = tmp_path / "experiments" / "cooldown_thesis"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps({"per_symbol_entry_cooldown_minutes": 15}) + "\n"
    )
    thesis = {
        "config_changes": {"per_symbol_entry_cooldown_minutes": 15},
        "required_diagnostics": ["executed_trade_count_blocked_by_cooldown"],
    }

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/cooldown_thesis/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert (
        "required_diagnostic_not_emitted:executed_trade_count_blocked_by_cooldown"
        in result.failures
    )


def test_builder_implementation_verifier_requires_custom_pf_diagnostic_names(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "exits.py").write_text(
        "config.get('per_symbol_entry_cooldown_minutes')\n"
        "event_logger.log_order_rejected(reason='entry_cooldown')\n"
    )
    experiment_dir = tmp_path / "experiments" / "cooldown_pf_bucket_thesis"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps({"per_symbol_entry_cooldown_minutes": 15}) + "\n"
    )
    thesis = {
        "config_changes": {"per_symbol_entry_cooldown_minutes": 15},
        "required_diagnostics": ["pf_by_time_since_last_same_symbol_entry_bucket"],
    }

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/cooldown_pf_bucket_thesis/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert (
        "required_diagnostic_not_emitted:pf_by_time_since_last_same_symbol_entry_bucket"
        in result.failures
    )


def test_builder_implementation_verifier_accepts_custom_pf_diagnostic_from_metrics(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "exits.py").write_text("config.get('per_symbol_entry_cooldown_minutes')\n")
    (tmp_path / "metrics.py").write_text(
        "diag['pf_by_time_since_last_same_symbol_entry_bucket'] = {}\n"
    )
    experiment_dir = tmp_path / "experiments" / "cooldown_pf_bucket_thesis"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps({"per_symbol_entry_cooldown_minutes": 15}) + "\n"
    )
    thesis = {
        "config_changes": {"per_symbol_entry_cooldown_minutes": 15},
        "required_diagnostics": ["pf_by_time_since_last_same_symbol_entry_bucket"],
    }

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/cooldown_pf_bucket_thesis/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is True
    assert result.failures == []


def test_builder_implementation_verifier_reports_non_utf8_diagnostic_sources(
    tmp_path: Path,
) -> None:
    strategy_dir = tmp_path / "strategies" / "ema"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "exits.py").write_text("config.get('per_symbol_entry_cooldown_minutes')\n")
    (tmp_path / "metrics.py").write_bytes(b"\xff\xfe\x00")
    experiment_dir = tmp_path / "experiments" / "cooldown_pf_bucket_thesis"
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps({"per_symbol_entry_cooldown_minutes": 15}) + "\n"
    )
    thesis = {
        "config_changes": {"per_symbol_entry_cooldown_minutes": 15},
        "required_diagnostics": ["pf_by_time_since_last_same_symbol_entry_bucket"],
    }

    result = verify_builder_implementation_contract(
        root=tmp_path,
        thesis=thesis,
        generated_config_path="experiments/cooldown_pf_bucket_thesis/runtime_config.json",
        family_name="ema",
    )

    assert result.passed is False
    assert "source_read_failed:metrics.py:UnicodeDecodeError" in result.failures
    assert (
        "required_diagnostic_not_emitted:pf_by_time_since_last_same_symbol_entry_bucket"
        in result.failures
    )


def test_build_missing_primitives_validates_generated_config_in_fresh_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thesis_id = "builder_adds_runtime_key"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "add a new implemented runtime key",
                "mechanism": "builder changes code and validation before writing config",
                "config_changes": {"new_builder_key": 1},
                "requested_primitives": ["new_builder_key"],
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
                "status": "needs_code",
            }
        )
        + "\n"
    )

    validation_calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        if cmd == ["codex", "exec", "--help"]:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        if cmd[:2] == ["codex", "exec"]:
            strategy_dir = tmp_path / "strategies" / "ema"
            strategy_dir.mkdir(parents=True, exist_ok=True)
            (strategy_dir / "strategy.py").write_text("config.get('new_builder_key')\n")
            (experiment_dir / "runtime_config.json").write_text(
                json.dumps(_ema_runtime_config(new_builder_key=1)) + "\n"
            )
            return type("Proc", (), {"stdout": "generated", "stderr": "", "returncode": 0})()
        if "-c" in cmd:
            validation_calls.append(cmd)
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        raise AssertionError(f"unexpected subprocess command: {cmd!r}")

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"experiments/{thesis_id}/runtime_config.json"
    assert result["validation_passed"] is True
    assert validation_calls


def test_build_missing_primitives_retries_once_with_verifier_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thesis_id = "builder_retry_missing_diagnostic"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "emit diagnostic for a newly implemented key",
                "mechanism": "use a runtime gate and report how often it blocks",
                "config_changes": {"new_builder_key": 1},
                "requested_primitives": ["new_builder_key"],
                "required_diagnostics": ["new_builder_metric"],
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
                "status": "needs_code",
            }
        )
        + "\n"
    )

    codex_prompts: list[str] = []

    def fake_run(cmd, *args, **kwargs):
        if cmd == ["codex", "exec", "--help"]:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        if "-c" in cmd:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        if cmd[:2] == ["codex", "exec"]:
            codex_prompts.append(kwargs["input"])
            strategy_dir = tmp_path / "strategies" / "ema"
            strategy_dir.mkdir(parents=True, exist_ok=True)
            strategy_text = "config.get('new_builder_key')\n"
            if len(codex_prompts) == 2:
                assert "required_diagnostic_not_emitted:new_builder_metric" in kwargs["input"]
                strategy_text += "logger.info('new_builder_metric')\n"
            (strategy_dir / "strategy.py").write_text(strategy_text)
            (experiment_dir / "runtime_config.json").write_text(
                json.dumps(_ema_runtime_config(new_builder_key=1)) + "\n"
            )
            return type(
                "Proc",
                (),
                {"stdout": f"attempt {len(codex_prompts)}", "stderr": "", "returncode": 0},
            )()
        raise AssertionError(f"unexpected subprocess command: {cmd!r}")

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["builder_attempts"] == 2
    assert result["implementation_verification_passed"] is True
    assert len(codex_prompts) == 2
    attempt_dir = tmp_path / "ema-builder-requests" / thesis_id
    assert (
        "required_diagnostic_not_emitted:new_builder_metric"
        in (attempt_dir / "prompt.txt").read_text()
    )
    assert json.loads((attempt_dir / "result.json").read_text())["builder_attempts"] == 2
    assert (attempt_dir / "stdout.log").read_text() == "attempt 2"


def test_build_missing_primitives_does_not_retry_fresh_validation_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thesis_id = "builder_no_retry_invalid_config"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "bad builder config should fail fast",
                "mechanism": "invalid runtime values are not verifier feedback",
                "config_changes": {"new_builder_key": 1},
                "requested_primitives": ["new_builder_key"],
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
                "status": "needs_code",
            }
        )
        + "\n"
    )
    codex_call_count = 0

    def fake_run(cmd, *args, **kwargs):
        nonlocal codex_call_count
        if cmd == ["codex", "exec", "--help"]:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        if "-c" in cmd:
            return type(
                "Proc",
                (),
                {"stdout": "", "stderr": "invalid generated config", "returncode": 1},
            )()
        if cmd[:2] == ["codex", "exec"]:
            codex_call_count += 1
            (experiment_dir / "runtime_config.json").write_text(json.dumps({"family": "ema"}))
            return type("Proc", (), {"stdout": "attempt 1", "stderr": "", "returncode": 0})()
        raise AssertionError(f"unexpected subprocess command: {cmd!r}")

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert result["builder_attempts"] == 1
    assert result["validation_passed"] is False
    assert codex_call_count == 1


def test_build_missing_primitives_rebuilds_existing_invalid_generated_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thesis_id = "builder_rebuilds_stale_invalid_config"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "stale invalid config should not block builder resume",
                "mechanism": "builder must overwrite bad metadata leakage",
                "config_changes": {"new_builder_key": 1},
                "requested_primitives": ["new_builder_key"],
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
                "status": "needs_code",
            }
        )
        + "\n"
    )
    config_path = experiment_dir / "runtime_config.json"
    config_path.write_text(json.dumps({"family": "ema", "requires_code_change": True}) + "\n")
    codex_call_count = 0

    def fake_run(cmd, *args, **kwargs):
        nonlocal codex_call_count
        if cmd == ["codex", "exec", "--help"]:
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        if "-c" in cmd:
            if "requires_code_change" in config_path.read_text():
                return type(
                    "Proc",
                    (),
                    {
                        "stdout": "",
                        "stderr": "Unsupported EMA runtime config keys: requires_code_change",
                        "returncode": 1,
                    },
                )()
            return type("Proc", (), {"stdout": "", "stderr": "", "returncode": 0})()
        if cmd[:2] == ["codex", "exec"]:
            codex_call_count += 1
            strategy_dir = tmp_path / "strategies" / "ema"
            strategy_dir.mkdir(parents=True, exist_ok=True)
            (strategy_dir / "strategy.py").write_text("config.get('new_builder_key')\n")
            config_path.write_text(json.dumps(_ema_runtime_config(new_builder_key=1)) + "\n")
            return type("Proc", (), {"stdout": "rebuilt", "stderr": "", "returncode": 0})()
        raise AssertionError(f"unexpected subprocess command: {cmd!r}")

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"experiments/{thesis_id}/runtime_config.json"
    assert result["validation_passed"] is True
    assert codex_call_count == 1
    assert "requires_code_change" not in config_path.read_text()


def test_builder_prompt_requires_code_consumption_proof_for_missing_primitives(
    tmp_path: Path,
) -> None:
    prompt = compiler_builder._build_builder_prompt(
        thesis_id="vwap_gate",
        root=tmp_path,
        proposal_path=tmp_path / "experiments" / "vwap_gate" / "thesis.json",
        compilation_path=tmp_path / "experiments" / "vwap_gate" / "contract.json",
        config_path="experiments/vwap_gate/runtime_config.json",
        family_name="ema",
        missing_primitives=["vwap_side_gate_enabled"],
        prompt_extras=[],
    )

    assert (
        "Do not treat runtime config validation as proof that a primitive is implemented" in prompt
    )
    assert "If a config key is not consumed by strategy runtime code" in prompt
    assert "add or update tests that prove the key changes strategy behavior" in prompt


def test_build_missing_primitives_reports_timeout_when_generated_config_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    thesis_id = "invalid_config_timeout"
    experiment_dir = tmp_path / "experiments" / thesis_id
    experiment_dir.mkdir(parents=True, exist_ok=True)
    (experiment_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": "ema",
                "hypothesis": "generate an invalid config",
                "mechanism": "missing required runtime fields",
                "config_changes": {"requires_engine_change": True},
                "requested_primitives": [],
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
                "hypothesis": "generate an invalid config",
                "mechanism": "missing required runtime fields",
                "status": "needs_code",
            }
        )
        + "\n"
    )

    def fake_run(cmd, *args, **kwargs):
        (experiment_dir / "runtime_config.json").write_text(json.dumps({"family": "ema"}) + "\n")
        raise subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=kwargs["timeout"],
            output="generated invalid config then stalled",
        )

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "error"
    assert result["validation_passed"] is False
    assert result["timed_out"] is True
    assert "timed out after writing an invalid config" in result["reason"]
    assert "generated config failed validation" in result["reason"]


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
    ema_config = json.dumps(STRATEGIES["ema"].get_defaults())
    orb_config = json.dumps(STRATEGIES["orb"].get_defaults())
    codex.write_text(f"""#!/usr/bin/env python3
import pathlib
import re
import sys

prompt = sys.stdin.read() or sys.argv[-1]
root = pathlib.Path(re.search(r"- Repo root: (.+)", prompt).group(1))
config = re.search(r"- Expected config path: (.+)", prompt).group(1)
target = root / config
target.parent.mkdir(parents=True, exist_ok=True)
if "ema" in config:
    target.write_text({ema_config!r} + "\\n")
else:
    target.write_text({orb_config!r} + "\\n")
print(f"generated {{config}}")
""")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    result = build_missing_primitives(tmp_path, thesis_id)

    assert result["status"] == "completed"
    assert result["generated_config"] == f"configs/variants/{thesis_id}.yaml"
    written = (tmp_path / result["generated_config"]).read_text()
    if family_name == "ema":
        assert '"ema_length": 5' in written
    else:
        assert '"or_minutes": 30' in written
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
print(f"generated {{config}}")
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
    generated_config = json.dumps(_ema_runtime_config(ema_length=7))
    codex.write_text(f"""#!/usr/bin/env python3
import pathlib
import re
import sys

prompt = sys.stdin.read() or sys.argv[-1]
root = pathlib.Path(re.search(r"- Repo root: (.+)", prompt).group(1))
config = re.search(r"- Expected config path: (.+)", prompt).group(1)
target = root / config
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text('''{generated_config}''')
print(f"generated {{config}}")
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
