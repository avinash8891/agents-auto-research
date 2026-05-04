from __future__ import annotations

import json
import shutil
import string
import subprocess
import time
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now, write_json_artifact
from backtest.runtime_config import load_runtime_config
from persistence_utils import write_text_atomic
from strategies import STRATEGIES
from strategy_family import load_family
from trace_sdk import trace

BUILDER_CLI_TIMEOUT_SECONDS = 300
BUILDER_CLI_MODEL = "gpt-5.4-mini"
BUILDER_CLI_REASONING_EFFORT = "medium"


def _find_cli() -> str | None:
    """Find the codex CLI."""
    if shutil.which("codex"):
        return "codex"
    return None


def _read_json_artifact(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _load_structured_thesis_artifacts(
    root: Path, thesis_id: str
) -> tuple[dict[str, Any], dict[str, Any], Path, Path] | None:
    experiment_dir = root / "experiments" / thesis_id
    thesis_path = experiment_dir / "thesis.json"
    contract_path = experiment_dir / "contract.json"
    thesis = _read_json_artifact(thesis_path)
    contract = _read_json_artifact(contract_path)
    if thesis is None or contract is None:
        return None
    return thesis, contract, thesis_path, contract_path


def _builder_artifact_dir(root: Path, family_name: str, thesis_id: str) -> Path:
    family = load_family(family_name)
    return root / family.builder_requests_dirname / thesis_id


def _write_builder_attempt_artifacts(
    *,
    artifact_dir: Path,
    prompt: str,
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    result: dict[str, Any],
    stdout: str = "",
    stderr: str = "",
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(artifact_dir / "prompt.txt", prompt)
    write_json_artifact(
        artifact_dir / "command.json",
        {
            "command": command,
            "cwd": str(cwd),
            "timeout_seconds": timeout_seconds,
            "model_provider": "codex",
            "model": BUILDER_CLI_MODEL,
            "model_reasoning_effort": BUILDER_CLI_REASONING_EFFORT,
        },
    )
    write_text_atomic(artifact_dir / "stdout.log", stdout)
    write_text_atomic(artifact_dir / "stderr.log", stderr)
    write_json_artifact(artifact_dir / "result.json", result)


def build_missing_primitives(root: Path, thesis_id: str) -> dict[str, Any]:
    """Dispatch CLI builder to implement missing primitives for a thesis."""
    started_at = time.monotonic()
    structured = _load_structured_thesis_artifacts(root, thesis_id)
    if structured is not None:
        proposal, compilation, proposal_path, compilation_path = structured
        family_name = proposal.get("strategy_family") or compilation.get("strategy_family") or "orb"
        try:
            family = load_family(family_name)
        except (KeyError, ValueError) as exc:
            return {
                "status": "error",
                "reason": f"unknown strategy family for structured thesis {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = (
            proposal.get("requested_primitives")
            or compilation.get("missing_primitives")
            or sorted((proposal.get("config_changes") or {}).keys())
        )
        generated_name = f"experiments/{thesis_id}/runtime_config.json"
        config_path = generated_name
        builder_requests_dir = root / family.builder_requests_dirname
        attempt_dir = _builder_artifact_dir(root, family_name, thesis_id)
        prompt_extras = [
            "- Thesis artifact: experiments/$thesis_id/thesis.json",
            "- Contract artifact: experiments/$thesis_id/contract.json",
            "- Thesis payload: $thesis_payload",
            "- Contract payload: $contract_payload",
        ]
    else:
        compilation_family_name = None
        for candidate_family in sorted(STRATEGIES):
            candidate_proposal_path = (
                root / load_family(candidate_family).proposals_dirname / f"{thesis_id}.json"
            )
            if candidate_proposal_path.exists():
                compilation_family_name = candidate_family
                break

        if compilation_family_name is None:
            return {
                "status": "error",
                "reason": f"missing proposal artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }

        family = load_family(compilation_family_name)
        proposal_path = root / family.proposals_dirname / f"{thesis_id}.json"
        compilation_path = root / family.compilations_dirname / f"{thesis_id}.json"
        if not proposal_path.exists():
            return {
                "status": "error",
                "reason": f"missing proposal artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
        if not compilation_path.exists():
            return {
                "status": "error",
                "reason": f"missing compilation artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }

        try:
            proposal = json.loads(proposal_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "reason": f"malformed proposal artifact for {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        try:
            compilation = json.loads(compilation_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "reason": f"malformed compilation artifact for {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        family_name = proposal.get("strategy_family") or proposal["family"]
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = compilation.get("missing_primitives") or []
        generated_name = (
            f"{family.name}_{thesis_id}.yaml"
            if not thesis_id.startswith(f"{family.name}_")
            else f"{thesis_id}.yaml"
        )
        config_path = f"configs/variants/{generated_name}"
        builder_requests_dir = root / family.builder_requests_dirname
        attempt_dir = _builder_artifact_dir(root, family_name, thesis_id)
        prompt_extras = []
    request_payload = {
        "thesis_id": thesis_id,
        "family": family_name,
        "proposal_path": proposal_path.relative_to(root).as_posix(),
        "compilation_path": compilation_path.relative_to(root).as_posix(),
        "missing_primitives": missing_primitives,
        "normalized_contract": normalized_contract,
        "status": "requested",
        "timestamp": timestamp_now(),
        "model_provider": "codex",
        "model": BUILDER_CLI_MODEL,
        "model_reasoning_effort": BUILDER_CLI_REASONING_EFFORT,
    }
    write_json_artifact(builder_requests_dir / f"{thesis_id}.json", request_payload)
    trace(
        "BUILDER",
        f"start thesis={thesis_id} model={BUILDER_CLI_MODEL} effort={BUILDER_CLI_REASONING_EFFORT}",
        {
            "thesis_id": thesis_id,
            "family": family_name,
            "model_provider": "codex",
            "model": BUILDER_CLI_MODEL,
            "model_reasoning_effort": BUILDER_CLI_REASONING_EFFORT,
        },
        model_provider="codex",
        model_name=BUILDER_CLI_MODEL,
    )

    config_abspath = root / config_path
    prompt = f"""Goal:
Automatically implement the missing primitive(s) for thesis `{thesis_id}` and create the resulting config artifact.

Context:
- Repo root: {root}
- Proposal artifact: {proposal_path}
- Compilation artifact: {compilation_path}
- Expected config path: {config_path}
- Missing primitives: {json.dumps(missing_primitives, indent=2)}
- Normalized contract: {json.dumps(normalized_contract, indent=2)}
- Hypothesis: {proposal.get("hypothesis", "")}
{string.Template(chr(10).join(prompt_extras)).safe_substitute(
    thesis_id=thesis_id,
    thesis_payload=json.dumps(proposal, indent=2),
    contract_payload=json.dumps(compilation, indent=2),
) if prompt_extras else ""}

Requirements:
- Write the necessary code changes in this repo.
- Add or update tests for the new primitive behavior.
- Create the config file at `{config_path}` if it becomes expressible.
- Return a concise final report with:
  1. whether implementation succeeded
  2. files changed
  3. tests run
  4. generated config path

Constraints:
- Preserve existing behavior except for the new primitive support.
- If implementation cannot be completed safely, explain why.
"""
    builder_cmd = [
        "codex",
        "exec",
        "--model",
        BUILDER_CLI_MODEL,
        "-c",
        f'model_reasoning_effort="{BUILDER_CLI_REASONING_EFFORT}"',
        prompt,
    ]

    _write_builder_attempt_artifacts(
        artifact_dir=attempt_dir,
        prompt=prompt,
        command=builder_cmd,
        cwd=root,
        timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
        result={
            "status": "requested",
            "reason": "builder queued",
            "generated_config": None,
            "validation_passed": False,
            "model_provider": "codex",
            "model": BUILDER_CLI_MODEL,
            "model_reasoning_effort": BUILDER_CLI_REASONING_EFFORT,
        },
    )

    if config_abspath.exists():
        try:
            load_runtime_config(str(config_abspath), family_name)
        except Exception as exc:
            result = {
                "status": "error",
                "reason": f"generated config failed validation: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
            _write_builder_attempt_artifacts(
                artifact_dir=attempt_dir,
                prompt=prompt,
                command=builder_cmd,
                cwd=root,
                timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
                result=result,
            )
            trace(
                "BUILDER",
                f"finish thesis={thesis_id} status=error model={BUILDER_CLI_MODEL}",
                result,
                model_provider="codex",
                model_name=BUILDER_CLI_MODEL,
            )
            return result
        result = {
            "status": "completed",
            "reason": "config already exists",
            "generated_config": config_path,
            "validation_passed": True,
        }
        _write_builder_attempt_artifacts(
            artifact_dir=attempt_dir,
            prompt=prompt,
            command=builder_cmd,
            cwd=root,
            timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
            result=result,
        )
        trace(
            "BUILDER",
            f"finish thesis={thesis_id} status=completed model={BUILDER_CLI_MODEL}",
            result,
            model_provider="codex",
            model_name=BUILDER_CLI_MODEL,
        )
        return result

    cli = _find_cli()
    if not cli:
        result = {
            "status": "error",
            "reason": "No CLI available for builder dispatch",
            "generated_config": None,
            "validation_passed": False,
        }
        _write_builder_attempt_artifacts(
            artifact_dir=attempt_dir,
            prompt=prompt,
            command=builder_cmd,
            cwd=root,
            timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
            result=result,
        )
        trace(
            "BUILDER",
            f"finish thesis={thesis_id} status=error model={BUILDER_CLI_MODEL}",
            result,
            model_provider="codex",
            model_name=BUILDER_CLI_MODEL,
        )
        return result

    cmd = builder_cmd

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=BUILDER_CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _coerce_subprocess_output(getattr(exc, "stdout", ""))
        stderr = _coerce_subprocess_output(getattr(exc, "stderr", ""))
        result = {
            "status": "error",
            "reason": f"builder timed out after {BUILDER_CLI_TIMEOUT_SECONDS}s: {exc}",
            "generated_config": None,
            "validation_passed": False,
            "timed_out": True,
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        }
        _write_builder_attempt_artifacts(
            artifact_dir=attempt_dir,
            prompt=prompt,
            command=cmd,
            cwd=root,
            timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
            result=result,
            stdout=stdout,
            stderr=stderr,
        )
        trace(
            "BUILDER",
            f"finish thesis={thesis_id} status=error model={BUILDER_CLI_MODEL}",
            result,
            model_provider="codex",
            model_name=BUILDER_CLI_MODEL,
        )
        return result
    result = (proc.stdout or "") + (proc.stderr or "")

    generated = config_path if config_abspath.exists() else None
    if generated:
        try:
            load_runtime_config(str(config_abspath), family_name)
        except Exception as exc:
            out = {
                "status": "error",
                "reason": f"generated config failed validation: {exc}",
                "generated_config": None,
                "validation_passed": False,
                "exit_code": proc.returncode,
                "timed_out": False,
                "duration_seconds": round(time.monotonic() - started_at, 3),
            }
            _write_builder_attempt_artifacts(
                artifact_dir=attempt_dir,
                prompt=prompt,
                command=cmd,
                cwd=root,
                timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
                result=out,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
            )
            trace(
                "BUILDER",
                f"finish thesis={thesis_id} status=error model={BUILDER_CLI_MODEL}",
                out,
                model_provider="codex",
                model_name=BUILDER_CLI_MODEL,
            )
            return out
    out = {
        "status": "completed" if generated else "error",
        "reason": result,
        "generated_config": generated,
        "validation_passed": bool(generated),
        "exit_code": proc.returncode,
        "timed_out": False,
        "duration_seconds": round(time.monotonic() - started_at, 3),
    }
    _write_builder_attempt_artifacts(
        artifact_dir=attempt_dir,
        prompt=prompt,
        command=cmd,
        cwd=root,
        timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
        result=out,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
    trace(
        "BUILDER",
        f"finish thesis={thesis_id} status={out['status']} model={BUILDER_CLI_MODEL}",
        out,
        model_provider="codex",
        model_name=BUILDER_CLI_MODEL,
    )
    return out


def _coerce_subprocess_output(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
