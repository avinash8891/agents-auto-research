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
    create_executable_artifact,
)
from research_types import ResearchThesis
from strategies import STRATEGIES


def _ema_runtime_config(**overrides):
    config = dict(STRATEGIES["ema"].get_defaults())
    config.update(overrides)
    return config


def _write_ema_baseline(root: Path) -> None:
    (root / "configs").mkdir(parents=True, exist_ok=True)
    (root / "configs" / "ema_base.yaml").write_text(json.dumps(_ema_runtime_config()) + "\n")


def _write_builder_request(round_root: Path, thesis_id: str, *, family: str = "ema") -> None:
    request_dir = round_root / "builder_request"
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "thesis.json").write_text(
        json.dumps(
            {
                "thesis_id": thesis_id,
                "strategy_family": family,
                "hypothesis": "tighten ema length",
                "mechanism": "reduce lag",
                "config_changes": {"ema_length": 7} if family == "ema" else {},
                "requested_primitives": ["ema_length"],
            }
        )
        + "\n"
    )
    (request_dir / "contract.json").write_text(
        json.dumps(
            {
                "contract_id": thesis_id,
                "thesis_id": thesis_id,
                "strategy_family": family,
                "baseline_config_path": (
                    f"configs/{family}_base.yaml" if family != "ema" else "configs/ema_base.yaml"
                ),
                "runtime_config": {},
                "hypothesis": "tighten ema length",
                "mechanism": "reduce lag",
                "status": "needs_code",
                "missing_primitives": ["ema_length"],
                "normalized_contract": [],
            }
        )
        + "\n"
    )


def test_compile_config_thesis_fails_loudly_for_legacy_entrypoint(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not supported"):
        compile_config_thesis(
            "ema", "ema-test", {"ema_length": 9}, tmp_path, artifact_root=tmp_path
        )


def test_compile_proposal_artifact_fails_loudly_for_legacy_entrypoint(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not supported"):
        compile_proposal_artifact(
            {"thesis_id": "x", "strategy_family": "ema"}, tmp_path, artifact_root=tmp_path
        )


def test_compile_research_thesis_writes_round_selected_artifacts(tmp_path: Path) -> None:
    _write_ema_baseline(tmp_path)
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-1"
    thesis = ResearchThesis(
        thesis_id="thesis-ready",
        strategy_family="ema",
        hypothesis="Shorter EMA reacts faster.",
        mechanism="Reduce lag in signal generation.",
        config_changes={"ema_length": 9},
    )

    contract = compile_research_thesis(thesis, tmp_path, artifact_root=round_root)

    assert contract.status == "ready_to_run"
    assert (round_root / "selected_thesis.json").exists()
    assert (round_root / "selected_contract.json").exists()
    assert (round_root / "selected_config.json").exists()


def test_compile_research_thesis_writes_builder_request_for_needs_code(tmp_path: Path) -> None:
    _write_ema_baseline(tmp_path)
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-2"
    thesis = ResearchThesis(
        thesis_id="needs-code",
        strategy_family="ema",
        hypothesis="Use unsupported primitive.",
        mechanism="Request a missing primitive.",
        config_changes={"not_a_real_key": 1},
    )

    contract = compile_research_thesis(thesis, tmp_path, artifact_root=round_root)

    assert contract.status == "needs_code"
    assert (round_root / "builder_request" / "thesis.json").exists()
    assert (round_root / "builder_request" / "contract.json").exists()
    assert not (round_root / "selected_config.json").exists()


def test_create_executable_artifact_returns_round_selected_config_path(tmp_path: Path) -> None:
    _write_ema_baseline(tmp_path)
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-3"
    thesis_dir = tmp_path / "theses"
    thesis_dir.mkdir()
    base_config = tmp_path / "configs" / "ema_base.yaml"

    result = create_executable_artifact(
        thesis_dir,
        base_config,
        {
            "thesis_id": "resolved",
            "strategy_family": "ema",
            "hypothesis": "Shorter EMA reacts faster.",
            "mechanism": "Reduce lag in signal generation.",
            "config_changes": {"ema_length": 7},
        },
        tmp_path,
        artifact_root=round_root,
    )

    assert result["generated_config"] == "runtime/jobs/job-1/research/round-3/selected_config.json"
    assert result["generated_config_needs_build"] is False


def test_build_missing_primitives_requires_round_builder_request_artifacts(tmp_path: Path) -> None:
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-4"

    result = build_missing_primitives(tmp_path, "missing", artifact_root=round_root)

    assert result["status"] == "error"
    assert result["error_code"] == "builder_missing_round_artifacts"


def test_build_missing_primitives_rejects_legacy_experiment_artifacts(tmp_path: Path) -> None:
    research_round_id = "job-1-round-5"
    legacy_dir = (
        tmp_path
        / "runtime"
        / "jobs"
        / "job-1"
        / "research"
        / "round-5"
        / "experiments"
        / research_round_id
    )
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "thesis.json").write_text("{}\n")
    (legacy_dir / "contract.json").write_text("{}\n")

    with pytest.raises(RuntimeError, match="legacy builder experiment directory"):
        build_missing_primitives(
            tmp_path,
            "any-thesis-id",
            artifact_root=tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-5",
            research_round_id=research_round_id,
        )


def test_build_missing_primitives_rejects_unknown_structured_family(tmp_path: Path) -> None:
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-6"
    _write_builder_request(round_root, "unknown", family="not-a-real-family")

    result = build_missing_primitives(tmp_path, "unknown", artifact_root=round_root)

    assert result["status"] == "error"
    assert result["error_code"] == "builder_unknown_strategy_family"


def test_build_missing_primitives_writes_round_selected_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_ema_baseline(tmp_path)
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-7"
    thesis_id = "builder-success"
    _write_builder_request(round_root, thesis_id)

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
target.write_text('{"ema_length": 7}\\n')
print("generated")
""")
    codex.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        "compiler_builder._validated_generated_config_result",
        lambda **kwargs: {
            "status": "completed",
            "generated_config": kwargs["config_path"],
            "validation_passed": True,
            "implementation_verification_passed": True,
            "timed_out": False,
            "exit_code": 0,
            "duration_seconds": 0.1,
            "reason": "",
        },
    )

    result = build_missing_primitives(tmp_path, thesis_id, artifact_root=round_root)

    assert result["status"] == "completed"
    assert result["generated_config"] == "runtime/jobs/job-1/research/round-7/selected_config.json"
    assert (tmp_path / result["generated_config"]).exists()


def test_build_missing_primitives_marks_missing_builder_report_but_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_ema_baseline(tmp_path)
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-8"
    thesis_id = "builder-no-report"
    _write_builder_request(round_root, thesis_id)

    def fake_run(*args, **kwargs):
        target = round_root / "selected_config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_ema_runtime_config(ema_length=7)) + "\n")
        return type("Proc", (), {"stdout": "generated config", "stderr": "", "returncode": 0})()

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr("compiler_builder.subprocess.run", fake_run)
    monkeypatch.setattr(
        "compiler_builder._validated_generated_config_result",
        lambda **kwargs: {
            "status": "completed",
            "generated_config": kwargs["config_path"],
            "validation_passed": True,
            "implementation_verification_passed": True,
            "timed_out": False,
            "exit_code": 0,
            "duration_seconds": 0.1,
            "reason": "",
        },
    )

    result = build_missing_primitives(tmp_path, thesis_id, artifact_root=round_root)

    assert result["status"] == "completed"
    assert result["builder_self_check"]["notes"] == ["builder_final_report_missing_or_malformed"]


def test_build_missing_primitives_reports_invalid_generated_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_ema_baseline(tmp_path)
    round_root = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-9"
    thesis_id = "builder-invalid"
    _write_builder_request(round_root, thesis_id)

    monkeypatch.setattr("compiler_builder.shutil.which", lambda _: "codex")
    monkeypatch.setattr(
        "compiler_builder._codex_supports_sandbox_flag", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        "compiler_builder.subprocess.run",
        lambda *args, **kwargs: (
            (round_root / "selected_config.json").write_text(
                json.dumps(_ema_runtime_config(ema_length=7)) + "\n"
            ),
            type("Proc", (), {"stdout": "{}", "stderr": "", "returncode": 0})(),
        )[1],
    )
    monkeypatch.setattr(
        "compiler_builder._validated_generated_config_result",
        lambda **kwargs: {
            "status": "error",
            "generated_config": None,
            "validation_passed": False,
            "implementation_verification_passed": False,
            "timed_out": False,
            "exit_code": 1,
            "duration_seconds": 0.1,
            "reason": "config invalid",
            "error_code": "builder_config_validation_failed",
        },
    )

    result = build_missing_primitives(tmp_path, thesis_id, artifact_root=round_root)

    assert result["status"] == "error"
    assert result["error_code"] == "builder_config_validation_failed"
