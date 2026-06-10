from __future__ import annotations

from pathlib import Path

from compiler_builder import BuilderTask, _builder_retry_prompt


def test_builder_retry_prompt_keeps_workspace_artifact_paths() -> None:
    workspace_root = Path("/tmp/autoresearch-builder-workspace")
    host_root = Path("/srv/autoresearch")
    task = BuilderTask(
        thesis_id="thesis-1",
        family_name="ema",
        proposal_path="builder_request/thesis-1.json",
        compilation_path="builder_request/thesis-1_compilation.json",
        config_path="runtime/jobs/job-1/research/round-1/generated_config.yaml",
        base_config_path="configs/ema_base.yaml",
        missing_primitives=["new_gate"],
        required_diagnostics=[],
        required_diagnostic_specs=[],
        config_change_keys=["new_gate"],
        mechanism_contract_kind="runtime_config",
        implementation_scope=["strategies/ema"],
    )

    prompt = _builder_retry_prompt(
        task=task,
        thesis_id="thesis-1",
        root=workspace_root,
        proposal_path=workspace_root / "builder_request" / "thesis-1.json",
        compilation_path=workspace_root / "builder_request" / "thesis-1_compilation.json",
        config_path="runtime/jobs/job-1/research/round-1/generated_config.yaml",
        family_name="ema",
        base_config_path="configs/ema_base.yaml",
        missing_primitives=["new_gate"],
        previous_result={"implementation_verification_failures": ["missing test"]},
    )

    assert f"Repo root: {workspace_root}" in prompt
    assert str(workspace_root / "builder_request" / "thesis-1.json") in prompt
    assert str(host_root) not in prompt
