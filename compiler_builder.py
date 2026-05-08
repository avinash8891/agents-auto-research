from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now, write_json_artifact
from autoresearch_logging import get_logger
from compiler_implementation_verify import verify_builder_implementation_contract
from improvement_reflexion import build_latest_reflexion_feedback
from persistence_utils import write_text_atomic
from strategies import STRATEGIES
from strategy_family import load_family
from trace_sdk import record_event, trace

log = get_logger(__name__)

BUILDER_CLI_TIMEOUT_SECONDS = 900
BUILDER_IMPLEMENTATION_RETRY_LIMIT = 1
BUILDER_CLI_MODEL = "gpt-5.2"
BUILDER_CLI_REASONING_EFFORT: str | None = None
LEGACY_NESTED_CONFIG_KEY_REQUEST = "new_config_keys_needed"
THESIS_METADATA_CONFIG_KEYS = frozenset({"requires_code_change", LEGACY_NESTED_CONFIG_KEY_REQUEST})


def _resolved_builder_base_config_path(compilation: dict[str, Any], family_name: str) -> str:
    base_config_path = compilation.get("baseline_config_path")
    if isinstance(base_config_path, str) and base_config_path.strip():
        return base_config_path
    return load_family(family_name).baseline_config_path


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
    config_changes = proposal.get("config_changes") or {}
    if not isinstance(config_changes, dict):
        return []
    return sorted(key for key in config_changes if key not in THESIS_METADATA_CONFIG_KEYS)


def _normalize_proposal_config_changes(proposal: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Keep thesis metadata out of runtime config_changes before builder reads it."""
    config_changes = proposal.get("config_changes")
    if not isinstance(config_changes, dict):
        return proposal, False
    leaked = THESIS_METADATA_CONFIG_KEYS & set(config_changes)
    if not leaked:
        return proposal, False
    normalized = dict(proposal)
    normalized_changes = dict(config_changes)
    for key in leaked:
        value = normalized_changes.pop(key)
        if key == "requires_code_change" and value:
            normalized[key] = True
        elif key == LEGACY_NESTED_CONFIG_KEY_REQUEST and isinstance(value, dict):
            normalized_changes.update(value)
            normalized["requires_code_change"] = True
    normalized["config_changes"] = normalized_changes
    return normalized, True


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
) -> list[str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = artifact_dir / "prompt.txt"
    command_path = artifact_dir / "command.json"
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    result_path = artifact_dir / "result.json"
    write_text_atomic(prompt_path, prompt)
    write_json_artifact(
        command_path,
        {
            "command": command,
            "cwd": str(cwd),
            "timeout_seconds": timeout_seconds,
            "model_provider": "codex",
            "model": BUILDER_CLI_MODEL,
            "model_reasoning_effort": BUILDER_CLI_REASONING_EFFORT,
        },
    )
    write_text_atomic(stdout_path, stdout)
    write_text_atomic(stderr_path, stderr)
    write_json_artifact(result_path, result)
    return [
        str(path) for path in (prompt_path, command_path, stdout_path, stderr_path, result_path)
    ]


def _trace_builder_finish(
    *,
    thesis_id: str,
    result: dict[str, Any],
    artifact_paths: list[str],
) -> None:
    trace(
        "BUILDER",
        f"finish thesis={thesis_id} status={result['status']} model={BUILDER_CLI_MODEL}",
        result,
        model_provider="codex",
        model_name=BUILDER_CLI_MODEL,
    )
    record_event(
        source_module="compiler_builder",
        category="builder",
        action="finish",
        summary=f"builder finish thesis={thesis_id} status={result['status']}",
        payload={"thesis_id": thesis_id, "result": result},
        artifact_paths=artifact_paths,
        model_provider="codex",
        model_name=BUILDER_CLI_MODEL,
    )


def _validate_generated_config_in_fresh_python(
    *, root: Path, config_abspath: Path, family_name: str
) -> None:
    """Validate generated configs after builder edits using newly loaded code."""
    pythonpath_entries = [str(root), str(Path(__file__).resolve().parent)]
    existing_pythonpath = os.environ.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "from backtest.runtime_config import load_runtime_config\n"
                "load_runtime_config(sys.argv[1], sys.argv[2])\n"
            ),
            str(config_abspath),
            family_name,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(pythonpath_entries)},
    )
    if proc.returncode != 0:
        output = "\n".join(part for part in (proc.stdout, proc.stderr) if part).strip()
        if not output:
            output = f"validator exited with code {proc.returncode}"
        raise RuntimeError(output)


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
    base_config_path: str = "",
) -> str:
    extra_block = "\n".join(prompt_extras)
    if extra_block:
        extra_block = f"\n{extra_block}"
    return f"""Instructions:
1. Read the thesis and contract artifacts from disk first.
2. Start from the base config artifact. Preserve every base config key unless the thesis config_changes explicitly changes it.
3. If the change is already expressible with the existing family schema, write the config artifact at the expected path and stop.
4. Otherwise make the smallest code change needed to support the missing primitive(s), then add or update the narrowest tests that cover the new behavior.
5. Keep the edit scope tight. Do not refactor unrelated code.
6. Do not clean up, revert, or inspect unrelated dirty worktree changes.
7. As soon as the expected config exists and any narrow validation you choose has run, stop and return the final report. Do not continue with broad diff review.
8. Do not treat runtime config validation as proof that a primitive is implemented.
9. If a config key is not consumed by strategy runtime code, implement that code path or report failure instead of writing a no-op key.
10. For missing primitives, add or update tests that prove the key changes strategy behavior or emitted diagnostics.
11. Return a concise final report with:
   - whether implementation succeeded
   - files changed
   - tests run
   - generated config path

Context:
- Repo root: {root}
- Thesis artifact: {proposal_path}
- Contract artifact: {compilation_path}
- Expected config path: {config_path}
- Base config path: {base_config_path}
- Strategy family: {family_name}
- Missing primitives: {json.dumps(missing_primitives, indent=2)}{extra_block}

Goal:
Implement the missing primitive(s) for thesis `{thesis_id}` and write the resulting config artifact.
"""


def _implementation_retry_prompt_extras(previous_result: dict[str, Any]) -> list[str]:
    failures = previous_result.get("implementation_verification_failures") or []
    if failures:
        return [
            "Previous builder attempt failed deterministic implementation verification.",
            "Fix these exact verifier failures before reporting success:",
            json.dumps(failures, indent=2),
            "Do not rewrite unrelated code. Patch only the missing implementation/diagnostic gaps.",
        ]
    if previous_result.get("status") == "error" and not previous_result.get("validation_passed"):
        return [
            "Previous generated config failed runtime validation before backtest.",
            "Fix the generated runtime config and overwrite it before reporting success.",
            str(previous_result.get("reason") or "unknown validation failure"),
            (
                "Do not copy thesis metadata keys such as requires_code_change or "
                "new_config_keys_needed into runtime_config.json; they belong only "
                "in thesis.json."
            ),
        ]
    return []


def _should_retry_builder_result(result: dict[str, Any]) -> bool:
    return bool(result.get("implementation_verification_failures"))


def _builder_retry_prompt(
    *,
    thesis_id: str,
    root: Path,
    proposal_path: Path,
    compilation_path: Path,
    config_path: str,
    family_name: str,
    base_config_path: str,
    missing_primitives: list[str],
    previous_result: dict[str, Any] | None,
) -> str:
    prompt_extras = _implementation_retry_prompt_extras(previous_result) if previous_result else []
    return _build_builder_prompt(
        thesis_id=thesis_id,
        root=root,
        proposal_path=proposal_path,
        compilation_path=compilation_path,
        config_path=config_path,
        family_name=family_name,
        base_config_path=base_config_path,
        missing_primitives=missing_primitives,
        prompt_extras=prompt_extras,
    )


def _validated_generated_config_result(
    *,
    root: Path,
    thesis: dict[str, Any],
    contract: dict[str, Any],
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
        _validate_generated_config_in_fresh_python(
            root=root, config_abspath=config_abspath, family_name=family_name
        )
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
    verification = verify_builder_implementation_contract(
        root=root,
        thesis=thesis,
        contract=contract,
        generated_config_path=config_path,
        family_name=family_name,
    )
    if not verification.passed:
        verification_reason = "implementation_contract_failed: " + "; ".join(verification.failures)
        log.error("Generated config failed implementation verification: %s", verification_reason)
        return {
            "status": "error",
            "reason": verification_reason,
            "generated_config": None,
            "validation_passed": True,
            "implementation_verification_passed": False,
            "implementation_verification_failures": verification.failures,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration_seconds, 3),
        }
    return {
        "status": "completed",
        "reason": reason,
        "generated_config": config_path,
        "validation_passed": True,
        "implementation_verification_passed": True,
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
        proposal, proposal_normalized = _normalize_proposal_config_changes(proposal)
        if proposal_normalized:
            write_json_artifact(proposal_path, proposal)
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
        base_config_path = _resolved_builder_base_config_path(compilation, family_name)
        if not compilation.get("baseline_config_path"):
            compilation = {**compilation, "baseline_config_path": base_config_path}
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
        proposal, proposal_normalized = _normalize_proposal_config_changes(proposal)
        if proposal_normalized:
            write_json_artifact(proposal_path, proposal)
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
        base_config_path = _resolved_builder_base_config_path(compilation, family_name)
        if not compilation.get("baseline_config_path"):
            compilation = {**compilation, "baseline_config_path": base_config_path}
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
        "base_config_path": base_config_path,
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
    prompt_extras = []
    builder_reflexion = build_latest_reflexion_feedback(root, agent="builder")
    if builder_reflexion:
        prompt_extras.extend(
            [
                "AGENT REFLEXION FEEDBACK FROM PRIOR BUILDER ATTEMPTS:",
                builder_reflexion,
                "Apply this only to avoid repeated builder failure modes; the thesis artifacts remain the source of truth.",
            ]
        )
    prompt = _build_builder_prompt(
        thesis_id=thesis_id,
        root=root,
        proposal_path=proposal_path,
        compilation_path=compilation_path,
        config_path=config_path,
        family_name=family_name,
        base_config_path=base_config_path,
        missing_primitives=missing_primitives,
        prompt_extras=prompt_extras,
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

    out: dict[str, Any] | None = None
    if config_abspath.exists():
        existing_result = _validated_generated_config_result(
            root=root,
            thesis=proposal,
            contract=compilation,
            config_abspath=config_abspath,
            config_path=config_path,
            family_name=family_name,
            reason="config already exists",
            exit_code=None,
            timed_out=False,
            duration_seconds=time.monotonic() - started_at,
        )
        assert existing_result is not None
        if existing_result["status"] != "error":
            artifact_paths = _write_artifacts(result=existing_result)
            _trace_builder_finish(
                thesis_id=thesis_id, result=existing_result, artifact_paths=artifact_paths
            )
            return existing_result
        out = existing_result

    if not cli:
        result = {
            "status": "error",
            "reason": (
                out["reason"] if out is not None else "No CLI available for builder dispatch"
            ),
            "generated_config": None,
            "validation_passed": False,
        }
        artifact_paths = _write_artifacts(result=result)
        _trace_builder_finish(thesis_id=thesis_id, result=result, artifact_paths=artifact_paths)
        return result

    stdout_log = ""
    stderr_log = ""
    last_prompt = prompt
    for attempt_index in range(BUILDER_IMPLEMENTATION_RETRY_LIMIT + 1):
        attempt_prompt = prompt
        if out:
            attempt_prompt = _builder_retry_prompt(
                thesis_id=thesis_id,
                root=root,
                proposal_path=proposal_path,
                compilation_path=compilation_path,
                config_path=config_path,
                family_name=family_name,
                base_config_path=base_config_path,
                missing_primitives=missing_primitives,
                previous_result=out,
            )
        last_prompt = attempt_prompt
        try:
            proc = subprocess.run(
                builder_cmd,
                capture_output=True,
                text=True,
                cwd=str(root),
                input=attempt_prompt,
                timeout=BUILDER_CLI_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_log, stderr_log = _timeout_output(exc)
            duration_seconds = time.monotonic() - started_at
            out = _validated_generated_config_result(
                root=root,
                thesis=proposal,
                contract=compilation,
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
            if out is None:
                out = {
                    "status": "error",
                    "reason": f"builder timed out after {BUILDER_CLI_TIMEOUT_SECONDS}s: {exc}",
                    "generated_config": None,
                    "validation_passed": False,
                    "timed_out": True,
                    "exit_code": None,
                    "duration_seconds": round(duration_seconds, 3),
                }
        else:
            stdout_log = proc.stdout or ""
            stderr_log = proc.stderr or ""
            proc_output = stdout_log + stderr_log
            generated = config_path if config_abspath.exists() else None
            if generated:
                out = _validated_generated_config_result(
                    root=root,
                    thesis=proposal,
                    contract=compilation,
                    config_abspath=config_abspath,
                    config_path=config_path,
                    family_name=family_name,
                    reason=proc_output,
                    exit_code=proc.returncode,
                    timed_out=False,
                    duration_seconds=time.monotonic() - started_at,
                )
                assert out is not None
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
        out["builder_attempts"] = attempt_index + 1
        if out["status"] != "error" or not _should_retry_builder_result(out):
            break
    assert out is not None
    artifact_paths = _write_builder_attempt_artifacts(
        artifact_dir=attempt_dir,
        prompt=last_prompt,
        command=builder_cmd,
        cwd=root,
        timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
        result=out,
        stdout=stdout_log,
        stderr=stderr_log,
    )
    _trace_builder_finish(thesis_id=thesis_id, result=out, artifact_paths=artifact_paths)
    return out
