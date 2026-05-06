from __future__ import annotations

import functools
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now, write_json_artifact
from autoresearch_logging import get_logger
from backtest.runtime_config import load_runtime_config
from persistence_utils import write_text_atomic
from strategies import STRATEGIES
from strategy_family import load_family
from trace_sdk import trace

log = get_logger(__name__)

BUILDER_CLI_TIMEOUT_SECONDS = 900
BUILDER_CLI_MODEL = "gpt-5.2"
BUILDER_CLI_REASONING_EFFORT: str | None = None


def _coerce_subprocess_output(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _timeout_output(exc: subprocess.TimeoutExpired) -> tuple[str, str]:
    stdout = getattr(exc, "stdout", None)
    if stdout is None:
        stdout = getattr(exc, "output", "")
    return _coerce_subprocess_output(stdout), _coerce_subprocess_output(getattr(exc, "stderr", ""))


def _resolve_missing_primitives(proposal: dict[str, Any], compilation: dict[str, Any]) -> list[str]:
    rp = proposal.get("requested_primitives")
    mp = compilation.get("missing_primitives")
    if rp is not None:
        return rp
    if mp is not None:
        return mp
    return sorted((proposal.get("config_changes") or {}).keys())


def _find_cli() -> str | None:
    if shutil.which("codex"):
        return "codex"
    return None


def _codex_supports_sandbox_flag(cli: str) -> bool:
    try:
        help_result = subprocess.run(
            [cli, "exec", "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    return "--sandbox" in help_text


def _read_json_artifact(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        log.error("Failed to read artifact %s: %s", path, exc)
        return None
    except json.JSONDecodeError as exc:
        log.error("Malformed JSON in artifact %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        log.error("Artifact %s is not a JSON object (got %s)", path, type(payload).__name__)
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


def _build_builder_prompt(
    *,
    thesis_id: str,
    root: Path,
    proposal_path: Path,
    compilation_path: Path,
    config_path: str,
    family_name: str,
    missing_primitives: list[str],
    prompt_extras: list[str],
) -> str:
    extra_block = "\n".join(prompt_extras)
    if extra_block:
        extra_block = f"\n{extra_block}"
    return f"""Goal:
Implement the missing primitive(s) for thesis `{thesis_id}` and write the resulting config artifact.

Context:
- Repo root: {root}
- Thesis artifact: {proposal_path}
- Contract artifact: {compilation_path}
- Expected config path: {config_path}
- Strategy family: {family_name}
- Missing primitives: {json.dumps(missing_primitives, indent=2)}{extra_block}

Instructions:
1. Read the thesis and contract artifacts from disk first.
2. If the change is already expressible with the existing family schema, write the config artifact at the expected path and stop.
3. Otherwise make the smallest code change needed to support the missing primitive(s), then add or update the narrowest tests that cover the new behavior.
4. Keep the edit scope tight. Do not refactor unrelated code.
5. Do not clean up, revert, or inspect unrelated dirty worktree changes.
6. As soon as the expected config exists and any narrow validation you choose has run, stop and return the final report. Do not continue with broad diff review.
7. Return a concise final report with:
   - whether implementation succeeded
   - files changed
   - tests run
   - generated config path
"""


def _validated_generated_config_result(
    *,
    config_abspath: Path,
    config_path: str,
    family_name: str,
    reason: str,
    exit_code: int | None,
    timed_out: bool,
    duration_seconds: float,
) -> dict[str, Any] | None:
    if not config_abspath.exists():
        return None
    try:
        load_runtime_config(str(config_abspath), family_name)
    except Exception as exc:
        log.error("Generated config failed validation for path=%s: %s", config_path, exc)
        validation_reason = f"generated config failed validation: {exc}"
        if timed_out:
            validation_reason = (
                f"builder timed out after writing an invalid config; {validation_reason}"
            )
        return {
            "status": "error",
            "reason": validation_reason,
            "generated_config": None,
            "validation_passed": False,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration_seconds, 3),
        }
    return {
        "status": "completed",
        "reason": reason,
        "generated_config": config_path,
        "validation_passed": True,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration_seconds, 3),
    }


def build_missing_primitives(root: Path, thesis_id: str) -> dict[str, Any]:
    """Dispatch CLI builder to implement missing primitives for a thesis."""
    started_at = time.monotonic()
    structured = _load_structured_thesis_artifacts(root, thesis_id)
    if structured is not None:
        proposal, compilation, proposal_path, compilation_path = structured
        family_name = proposal.get("strategy_family") or compilation.get("strategy_family")
        if not family_name:
            return {
                "status": "error",
                "reason": f"missing strategy_family in thesis artifacts for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
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
        missing_primitives = _resolve_missing_primitives(proposal, compilation)
        generated_name = f"experiments/{thesis_id}/runtime_config.json"
        config_path = generated_name
        builder_requests_dir = root / family.builder_requests_dirname
        attempt_dir = _builder_artifact_dir(root, family_name, thesis_id)
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
        family_name = proposal.get("strategy_family") or proposal.get("family")
        if not family_name:
            return {
                "status": "error",
                "reason": f"missing strategy_family/family field in proposal for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = _resolve_missing_primitives(proposal, compilation)
        generated_name = (
            f"{family.name}_{thesis_id}.yaml"
            if not thesis_id.startswith(f"{family.name}_")
            else f"{thesis_id}.yaml"
        )
        config_path = f"configs/variants/{generated_name}"
        builder_requests_dir = root / family.builder_requests_dirname
        attempt_dir = _builder_artifact_dir(root, family_name, thesis_id)
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
        (
            f"start thesis={thesis_id} model={BUILDER_CLI_MODEL}"
            + (f" effort={BUILDER_CLI_REASONING_EFFORT}" if BUILDER_CLI_REASONING_EFFORT else "")
        ),
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
    prompt = _build_builder_prompt(
        thesis_id=thesis_id,
        root=root,
        proposal_path=proposal_path,
        compilation_path=compilation_path,
        config_path=config_path,
        family_name=family_name,
        missing_primitives=missing_primitives,
        prompt_extras=[],
    )
    cli = _find_cli()
    if cli:
        builder_cmd = [cli, "exec", "--model", BUILDER_CLI_MODEL]
        if _codex_supports_sandbox_flag(cli):
            builder_cmd[2:2] = ["--sandbox", "workspace-write"]
    else:
        builder_cmd = []
    if BUILDER_CLI_REASONING_EFFORT:
        builder_cmd.extend(["-c", f'model_reasoning_effort="{BUILDER_CLI_REASONING_EFFORT}"'])
    _write_artifacts = functools.partial(
        _write_builder_attempt_artifacts,
        artifact_dir=attempt_dir,
        prompt=prompt,
        command=builder_cmd,
        cwd=root,
        timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
    )

    _write_artifacts(
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
        result = _validated_generated_config_result(
            config_abspath=config_abspath,
            config_path=config_path,
            family_name=family_name,
            reason="config already exists",
            exit_code=None,
            timed_out=False,
            duration_seconds=time.monotonic() - started_at,
        )
        assert result is not None
        _write_artifacts(result=result)
        trace(
            "BUILDER",
            f"finish thesis={thesis_id} status={result['status']} model={BUILDER_CLI_MODEL}",
            result,
            model_provider="codex",
            model_name=BUILDER_CLI_MODEL,
        )
        return result

    if not cli:
        result = {
            "status": "error",
            "reason": "No CLI available for builder dispatch",
            "generated_config": None,
            "validation_passed": False,
        }
        _write_artifacts(result=result)
        trace(
            "BUILDER",
            f"finish thesis={thesis_id} status=error model={BUILDER_CLI_MODEL}",
            result,
            model_provider="codex",
            model_name=BUILDER_CLI_MODEL,
        )
        return result

    try:
        proc = subprocess.run(
            builder_cmd,
            capture_output=True,
            text=True,
            cwd=str(root),
            input=prompt,
            timeout=BUILDER_CLI_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _timeout_output(exc)
        duration_seconds = time.monotonic() - started_at
        result = _validated_generated_config_result(
            config_abspath=config_abspath,
            config_path=config_path,
            family_name=family_name,
            reason=(
                f"builder timed out after writing a valid config: "
                f"{BUILDER_CLI_TIMEOUT_SECONDS}s: {exc}"
            ),
            exit_code=None,
            timed_out=True,
            duration_seconds=duration_seconds,
        )
        if result is None:
            result = {
                "status": "error",
                "reason": f"builder timed out after {BUILDER_CLI_TIMEOUT_SECONDS}s: {exc}",
                "generated_config": None,
                "validation_passed": False,
                "timed_out": True,
                "exit_code": None,
                "duration_seconds": round(duration_seconds, 3),
            }
        _write_artifacts(result=result, stdout=stdout, stderr=stderr)
        trace(
            "BUILDER",
            f"finish thesis={thesis_id} status={result['status']} model={BUILDER_CLI_MODEL}",
            result,
            model_provider="codex",
            model_name=BUILDER_CLI_MODEL,
        )
        return result
    proc_output = (proc.stdout or "") + (proc.stderr or "")

    generated = config_path if config_abspath.exists() else None
    if generated:
        out = _validated_generated_config_result(
            config_abspath=config_abspath,
            config_path=config_path,
            family_name=family_name,
            reason=proc_output,
            exit_code=proc.returncode,
            timed_out=False,
            duration_seconds=time.monotonic() - started_at,
        )
        assert out is not None
        if out["status"] == "error":
            _write_artifacts(result=out, stdout=proc.stdout or "", stderr=proc.stderr or "")
            trace(
                "BUILDER",
                f"finish thesis={thesis_id} status=error model={BUILDER_CLI_MODEL}",
                out,
                model_provider="codex",
                model_name=BUILDER_CLI_MODEL,
            )
            return out
    else:
        out = {
            "status": "error",
            "reason": proc_output,
            "generated_config": None,
            "validation_passed": False,
            "exit_code": proc.returncode,
            "timed_out": False,
            "duration_seconds": round(time.monotonic() - started_at, 3),
        }
    _write_artifacts(result=out, stdout=proc.stdout or "", stderr=proc.stderr or "")
    trace(
        "BUILDER",
        f"finish thesis={thesis_id} status={out['status']} model={BUILDER_CLI_MODEL}",
        out,
        model_provider="codex",
        model_name=BUILDER_CLI_MODEL,
    )
    return out
