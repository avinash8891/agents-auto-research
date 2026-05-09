from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now, write_json_artifact
from autoresearch_logging import get_logger
from autoresearch_paths import serialize_config_path
from compiler_implementation_verify import verify_builder_implementation_contract
from improvement_reflexion import build_latest_reflexion_feedback
from persistence_utils import write_text_atomic
from strategies import STRATEGIES
from strategy_family import load_family
from trace_sdk import record_event, record_usage_event, trace

log = get_logger(__name__)

BUILDER_CLI_TIMEOUT_SECONDS = 900
BUILDER_IMPLEMENTATION_RETRY_LIMIT = 1
BUILDER_CLI_MODEL = "gpt-5.2"
BUILDER_CLI_REASONING_EFFORT: str | None = None
LEGACY_NESTED_CONFIG_KEY_REQUEST = "new_config_keys_needed"
THESIS_METADATA_CONFIG_KEYS = frozenset({"requires_code_change", LEGACY_NESTED_CONFIG_KEY_REQUEST})
BUILDER_WORKSPACE_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "logs",
        "runtime",
        "data",
        ".entire",
    }
)
BUILDER_WORKSPACE_EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".db", ".pem", ".key", ".p12", ".pfx")
BUILDER_WORKSPACE_EXCLUDED_SECRET_NAMES = frozenset(
    {
        ".env",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
BUILDER_CAPABILITY_REGISTRY = Path("runtime") / "builder_capability_registry.jsonl"
BUILDER_PROMOTIONS_DIR = Path("runtime") / "builder-promotions"
BUILDER_PROMOTION_QUEUE = Path("runtime") / "builder-promotion-queue.jsonl"


def _is_builder_artifact_dir_name(name: str) -> bool:
    if name in {
        "proposals",
        "compilations",
        "contracts",
        "run-queue",
        "research",
        "builder-requests",
        "experiments",
    }:
        return True
    return name.endswith(
        (
            "-proposals",
            "-compilations",
            "-contracts",
            "-run-queue",
            "-research",
            "-builder-requests",
            "-experiments",
        )
    )


@dataclass(frozen=True)
class BuilderTask:
    thesis_id: str
    family_name: str
    proposal_path: str
    compilation_path: str
    config_path: str
    base_config_path: str
    missing_primitives: list[str]
    required_diagnostics: list[str]
    required_diagnostic_specs: list[dict[str, Any]]
    config_change_keys: list[str]
    mechanism_contract_kind: str
    implementation_scope: list[str]


@dataclass(frozen=True)
class BuilderSelfCheck:
    mechanism_implemented: bool
    diagnostics_emitted: bool
    config_surface_valid: bool
    tests_covering_behavior: bool
    notes: list[str]


@dataclass(frozen=True)
class BuilderResultEnvelope:
    status: str
    generated_config: str | None
    validation_passed: bool
    builder_attempts: int
    phase: str
    task: dict[str, Any]
    self_check: dict[str, Any]


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


def _coerce_builder_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _normalize_builder_notes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _builder_self_check_with_note(note: str) -> BuilderSelfCheck:
    return BuilderSelfCheck(
        mechanism_implemented=False,
        diagnostics_emitted=False,
        config_surface_valid=False,
        tests_covering_behavior=False,
        notes=[note],
    )


def _verified_builder_self_check(
    verification: Any,
    builder_self_check: BuilderSelfCheck | None,
) -> BuilderSelfCheck:
    base = builder_self_check or _builder_self_check_with_note(
        "builder_final_report_missing_or_malformed"
    )
    return BuilderSelfCheck(
        mechanism_implemented=base.mechanism_implemented,
        diagnostics_emitted=base.diagnostics_emitted,
        config_surface_valid=base.config_surface_valid,
        tests_covering_behavior=bool(getattr(verification, "tests_covering_behavior", False)),
        notes=list(base.notes),
    )


def _extract_codex_cli_usage(stdout: str) -> tuple[dict[str, int], str] | None:
    usage_result: tuple[dict[str, int], str] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            raw_usage = event["usage"]
            input_tokens = int(raw_usage.get("input_tokens") or 0)
            output_tokens = int(raw_usage.get("output_tokens") or 0)
            if "total_tokens" in raw_usage and raw_usage["total_tokens"] is not None:
                total_tokens = int(raw_usage["total_tokens"])
            else:
                total_tokens = input_tokens + output_tokens
            usage_result = (
                {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": int(raw_usage.get("cached_input_tokens") or 0),
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": int(raw_usage.get("reasoning_output_tokens") or 0),
                    "total_tokens": total_tokens,
                },
                "codex_json_turn_completed",
            )
            continue
        if event.get("type") != "event_msg":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "token_count":
            continue
        info = payload.get("info")
        if not isinstance(info, dict):
            continue
        raw_usage = info.get("last_token_usage") or info.get("total_token_usage")
        if not isinstance(raw_usage, dict):
            continue
        usage_result = (
            {
                "input_tokens": int(raw_usage.get("input_tokens") or 0),
                "cached_input_tokens": int(raw_usage.get("cached_input_tokens") or 0),
                "output_tokens": int(raw_usage.get("output_tokens") or 0),
                "reasoning_output_tokens": int(raw_usage.get("reasoning_output_tokens") or 0),
                "total_tokens": int(raw_usage.get("total_tokens") or 0),
            },
            "codex_json_last_token_usage",
        )
    return usage_result


def _extract_builder_self_check(output: str) -> BuilderSelfCheck | None:
    if not output.strip():
        return None
    decoder = json.JSONDecoder()
    last_match: BuilderSelfCheck | None = None
    saw_candidate = False
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("builder_self_check")
        if not isinstance(candidate, dict):
            continue
        saw_candidate = True
        last_match = BuilderSelfCheck(
            mechanism_implemented=_coerce_builder_bool(candidate.get("mechanism_implemented")),
            diagnostics_emitted=_coerce_builder_bool(candidate.get("diagnostics_emitted")),
            config_surface_valid=_coerce_builder_bool(candidate.get("config_surface_valid")),
            tests_covering_behavior=_coerce_builder_bool(candidate.get("tests_covering_behavior")),
            notes=_normalize_builder_notes(candidate.get("notes")),
        )
    if last_match is not None:
        return last_match
    if saw_candidate:
        return _builder_self_check_with_note("builder_self_check_unusable")
    return None


def _emit_builder_usage(
    *,
    thesis_id: str,
    attempt_number: int,
    usage_result: tuple[dict[str, int], str] | None,
) -> dict[str, Any] | None:
    if usage_result is None:
        return None
    usage, usage_source = usage_result
    record_usage_event(
        "builder",
        model_provider="codex",
        model_name=BUILDER_CLI_MODEL,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
        reasoning_output_tokens=int(usage.get("reasoning_output_tokens") or 0),
        usage_source=usage_source,
        dedupe_key=f"builder:{thesis_id}:attempt:{attempt_number}",
        thesis_id=thesis_id,
    )
    return {**usage, "usage_source": usage_source}


def _resolve_missing_primitives(proposal: dict[str, Any], compilation: dict[str, Any]) -> list[str]:
    rp = proposal.get("requested_primitives")
    mp = compilation.get("missing_primitives")
    if isinstance(rp, list) and rp:
        return rp
    if isinstance(mp, list) and mp:
        return mp
    if isinstance(rp, list):
        return rp
    if isinstance(mp, list):
        return mp
    return []


def _validate_missing_primitives_contract(
    *, thesis_id: str, compilation: dict[str, Any], missing_primitives: list[str]
) -> dict[str, Any] | None:
    requires_code_change = bool(
        compilation.get("status") == "needs_code" or compilation.get("requires_code_change")
    )
    if requires_code_change and not missing_primitives:
        return {
            "status": "error",
            "error_code": "builder_missing_primitive_contract",
            "reason": (
                f"needs_code thesis '{thesis_id}' is missing requested/missing primitives; "
                "operationalization must resolve the code-change contract before builder"
            ),
            "generated_config": None,
            "validation_passed": False,
        }
    return None


def _builder_mechanism_contract_kind(
    proposal: dict[str, Any], compilation: dict[str, Any], missing_primitives: list[str]
) -> str:
    config_changes = proposal.get("config_changes") or {}
    runtime_keys = (
        [
            key
            for key in config_changes
            if key not in THESIS_METADATA_CONFIG_KEYS and key != "requires_engine_change"
        ]
        if isinstance(config_changes, dict)
        else []
    )
    has_runtime_keys = bool(runtime_keys)
    has_primitives = bool(missing_primitives or compilation.get("missing_primitives"))
    required_diagnostics = proposal.get("required_diagnostics") or []
    has_custom_diagnostics = bool(required_diagnostics)
    if has_primitives and has_runtime_keys and has_custom_diagnostics:
        return "mixed_code_config_diagnostics"
    if has_primitives and has_custom_diagnostics:
        return "code_and_diagnostics"
    if has_primitives and has_runtime_keys:
        return "code_and_config"
    if has_primitives:
        return "code_only"
    if has_runtime_keys and has_custom_diagnostics:
        return "config_and_diagnostics"
    if has_runtime_keys:
        return "config_only"
    if has_custom_diagnostics:
        return "diagnostics_only"
    return "unknown"


def _builder_implementation_scope(
    proposal: dict[str, Any], required_diagnostic_specs: list[dict[str, Any]]
) -> list[str]:
    surfaces = {"strategy_runtime"}
    config_changes = proposal.get("config_changes") or {}
    if isinstance(config_changes, dict) and any(
        key not in THESIS_METADATA_CONFIG_KEYS and key != "requires_engine_change"
        for key in config_changes
    ):
        surfaces.add("runtime_config")
    for spec in required_diagnostic_specs:
        surface = str(spec.get("surface") or "any")
        if surface == "metrics":
            surfaces.add("metrics")
        elif surface == "strategy_diagnostics":
            surfaces.add("strategy_diagnostics")
        elif surface == "experiment_evaluation":
            surfaces.add("experiment_evaluation")
        else:
            surfaces.update({"metrics", "strategy_diagnostics", "experiment_evaluation"})
    return sorted(surfaces)


def _build_builder_task(
    *,
    thesis_id: str,
    family_name: str,
    proposal_path: Path,
    compilation_path: Path,
    config_path: str,
    base_config_path: str,
    proposal: dict[str, Any],
    compilation: dict[str, Any],
    missing_primitives: list[str],
) -> BuilderTask:
    required_diagnostics = proposal.get("required_diagnostics") or []
    if not isinstance(required_diagnostics, list):
        required_diagnostics = []
    required_diagnostic_specs = (
        compilation.get("required_diagnostic_specs")
        or proposal.get("required_diagnostic_specs")
        or []
    )
    if not isinstance(required_diagnostic_specs, list):
        required_diagnostic_specs = []
    config_changes = proposal.get("config_changes") or {}
    config_change_keys = (
        sorted(
            key
            for key in config_changes
            if key not in THESIS_METADATA_CONFIG_KEYS and key != "requires_engine_change"
        )
        if isinstance(config_changes, dict)
        else []
    )
    return BuilderTask(
        thesis_id=thesis_id,
        family_name=family_name,
        proposal_path=proposal_path.as_posix(),
        compilation_path=compilation_path.as_posix(),
        config_path=config_path,
        base_config_path=base_config_path,
        missing_primitives=list(missing_primitives),
        required_diagnostics=list(required_diagnostics),
        required_diagnostic_specs=[
            dict(item) for item in required_diagnostic_specs if isinstance(item, dict)
        ],
        config_change_keys=config_change_keys,
        mechanism_contract_kind=_builder_mechanism_contract_kind(
            proposal, compilation, missing_primitives
        ),
        implementation_scope=_builder_implementation_scope(
            proposal,
            [dict(item) for item in required_diagnostic_specs if isinstance(item, dict)],
        ),
    )


def _empty_builder_self_check() -> BuilderSelfCheck:
    return BuilderSelfCheck(
        mechanism_implemented=False,
        diagnostics_emitted=False,
        config_surface_valid=False,
        tests_covering_behavior=False,
        notes=[],
    )


def _result_with_builder_envelope(
    result: dict[str, Any],
    *,
    task: BuilderTask,
    phase: str,
    self_check: BuilderSelfCheck | None = None,
) -> dict[str, Any]:
    envelope = BuilderResultEnvelope(
        status=str(result.get("status") or "error"),
        generated_config=result.get("generated_config"),
        validation_passed=bool(result.get("validation_passed")),
        builder_attempts=int(result.get("builder_attempts") or 0),
        phase=phase,
        task=asdict(task),
        self_check=asdict(self_check or _empty_builder_self_check()),
    )
    enriched = dict(result)
    enriched["builder_task"] = envelope.task
    enriched["builder_phase"] = envelope.phase
    enriched["builder_self_check"] = envelope.self_check
    return enriched


def _ensure_builder_envelope(
    result: dict[str, Any],
    *,
    task: BuilderTask,
    phase: str,
    self_check: BuilderSelfCheck | None = None,
) -> dict[str, Any]:
    if "builder_task" in result and "builder_phase" in result and "builder_self_check" in result:
        return result
    if self_check is None and phase == "completed":
        self_check = _builder_self_check_with_note("builder_final_report_missing_or_malformed")
    return _result_with_builder_envelope(
        result,
        task=task,
        phase=phase,
        self_check=self_check,
    )


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


def _codex_exec_help_text(cli: str) -> str:
    try:
        help_result = subprocess.run(
            [cli, "exec", "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{help_result.stdout}\n{help_result.stderr}"


def _codex_supports_exec_flag(cli: str, flag: str) -> bool:
    return flag in _codex_exec_help_text(cli)


def _codex_supports_sandbox_flag(cli: str) -> bool:
    return _codex_supports_exec_flag(cli, "--sandbox")


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
    root: Path, thesis_id: str, *, artifact_root: Path | None = None
) -> tuple[dict[str, Any], dict[str, Any], Path, Path] | None:
    roots = [artifact_root] if artifact_root is not None else []
    roots.append(root)
    for current_root in roots:
        experiment_dir = current_root / "experiments" / thesis_id
        thesis_path = experiment_dir / "thesis.json"
        contract_path = experiment_dir / "contract.json"
        thesis = _read_json_artifact(thesis_path)
        contract = _read_json_artifact(contract_path)
        if thesis is not None and contract is not None:
            return thesis, contract, thesis_path, contract_path
    return None


def _builder_artifact_dir(
    root: Path, family_name: str, thesis_id: str, *, artifact_root: Path | None = None
) -> Path:
    if artifact_root is None:
        raise ValueError("job-scoped artifact_root is required for builder artifacts")
    return artifact_root / "builder-requests" / thesis_id


def _builder_attempt_artifact_dir(artifact_dir: Path, attempt_number: int) -> Path:
    return artifact_dir / f"attempt-{attempt_number}"


def _builder_workspace_dir(artifact_dir: Path) -> Path:
    return artifact_dir / "workspace"


def _builder_source_ignore(_dir: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        normalized_name = name.lower()
        if name in BUILDER_WORKSPACE_EXCLUDED_NAMES:
            ignored.add(name)
            continue
        if _is_builder_artifact_dir_name(name):
            ignored.add(name)
            continue
        if (
            normalized_name in BUILDER_WORKSPACE_EXCLUDED_SECRET_NAMES
            or normalized_name.startswith(".env.")
            or normalized_name.endswith(BUILDER_WORKSPACE_EXCLUDED_SUFFIXES)
        ):
            ignored.add(name)
    return ignored


def _copy_builder_source_tree(root: Path, workspace_root: Path) -> None:
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    shutil.copytree(root, workspace_root, symlinks=True, ignore=_builder_source_ignore)


def _copy_file_into_workspace(*, source: Path, source_root: Path, workspace_root: Path) -> Path:
    relative = source.relative_to(source_root)
    destination = workspace_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _safe_builder_relative_path(relative_path: str) -> Path | None:
    rel = Path(relative_path)
    if not rel.parts or rel.is_absolute():
        return None
    if any(part in {"", ".", ".."} for part in rel.parts):
        return None
    return rel


def _promotable_builder_path(relative_path: str) -> bool:
    rel = _safe_builder_relative_path(relative_path)
    if rel is None:
        return False
    if rel.parts[0] in {"runtime", "logs", "data", "build", "dist"}:
        return False
    if any(_is_builder_artifact_dir_name(part) for part in rel.parts):
        return False
    if "__pycache__" in rel.parts:
        return False
    return rel.suffix in {".py", ".json", ".yaml", ".yml"}


def _workspace_changed_promotable_files(*, source_root: Path, workspace_root: Path) -> list[str]:
    changed: list[str] = []
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace_root).as_posix()
        if not _promotable_builder_path(rel):
            continue
        source_path = source_root / rel
        if not source_path.exists():
            changed.append(rel)
            continue
        try:
            if path.read_bytes() != source_path.read_bytes():
                changed.append(rel)
        except OSError:
            changed.append(rel)
    return changed


def _snapshot_promotable_source_state(source_root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root).as_posix()
        if not _promotable_builder_path(rel):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[rel] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _workspace_unified_diff(
    *,
    source_root: Path,
    workspace_root: Path,
    changed_files: list[str],
) -> str:
    chunks: list[str] = []
    for rel in changed_files:
        source_path = source_root / rel
        workspace_path = workspace_root / rel
        before = (
            source_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if source_path.exists()
            else []
        )
        after = workspace_path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
        chunks.extend(
            unified_diff(
                before,
                after,
                fromfile=rel,
                tofile=rel,
            )
        )
    return "".join(chunks)


def _load_builder_capability_registry(root: Path) -> list[dict[str, Any]]:
    path = root / BUILDER_CAPABILITY_REGISTRY
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _builder_seed_score(entry: dict[str, Any], task: BuilderTask) -> int:
    if entry.get("family_name") != task.family_name:
        return -1
    score = 0
    score += len(set(entry.get("missing_primitives") or []) & set(task.missing_primitives)) * 5
    score += len(set(entry.get("config_change_keys") or []) & set(task.config_change_keys)) * 3
    score += (
        len(
            set(entry.get("diagnostic_keys") or [])
            & {spec.get("key", "") for spec in task.required_diagnostic_specs}
        )
        * 2
    )
    return score


def _best_seeded_capability(root: Path, task: BuilderTask) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0
    for entry in _load_builder_capability_registry(root):
        score = _builder_seed_score(entry, task)
        if score > best_score:
            best = entry
            best_score = score
    return best


def _seed_workspace_from_capability(
    *,
    source_root: Path,
    workspace_root: Path,
    seed_entry: dict[str, Any] | None,
) -> list[str]:
    if not seed_entry:
        return []
    promotion_dir = _safe_builder_relative_path(str(seed_entry.get("promotion_dir") or ""))
    if promotion_dir is None:
        return []
    promotion_root = source_root / promotion_dir
    changed_files = [
        str(item)
        for item in seed_entry.get("promoted_files") or []
        if _promotable_builder_path(str(item))
    ]
    if not promotion_root.exists():
        return []
    seeded: list[str] = []
    for rel in changed_files:
        safe_rel = _safe_builder_relative_path(rel)
        if safe_rel is None:
            continue
        source = promotion_root / "files" / safe_rel
        if not source.exists():
            continue
        destination = workspace_root / safe_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        seeded.append(safe_rel.as_posix())
    return seeded


def _record_builder_promotion_candidate(
    *,
    source_root: Path,
    workspace_root: Path,
    artifact_root: Path | None,
    task: BuilderTask,
    thesis_id: str,
) -> dict[str, Any]:
    artifact_root_path = artifact_root or source_root
    safe_thesis_id = _safe_builder_relative_path(thesis_id)
    if safe_thesis_id is None or len(safe_thesis_id.parts) != 1:
        raise ValueError(f"unsafe thesis_id for builder promotion: {thesis_id!r}")
    changed_files = _workspace_changed_promotable_files(
        source_root=source_root,
        workspace_root=workspace_root,
    )
    promotion_root = source_root / BUILDER_PROMOTIONS_DIR / task.family_name / safe_thesis_id
    if promotion_root.exists():
        shutil.rmtree(promotion_root)
    (promotion_root / "files").mkdir(parents=True, exist_ok=True)
    for rel in changed_files:
        source = workspace_root / rel
        destination = promotion_root / "files" / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    diff_text = _workspace_unified_diff(
        source_root=source_root,
        workspace_root=workspace_root,
        changed_files=changed_files,
    )
    write_text_atomic(promotion_root / "workspace.patch", diff_text)
    manifest = {
        "thesis_id": thesis_id,
        "family_name": task.family_name,
        "missing_primitives": task.missing_primitives,
        "config_change_keys": task.config_change_keys,
        "diagnostic_keys": [spec.get("key", "") for spec in task.required_diagnostic_specs],
        "promoted_files": changed_files,
        "execution_root": workspace_root.as_posix(),
        "promotion_dir": promotion_root.relative_to(source_root).as_posix(),
        "artifact_root": artifact_root_path.as_posix(),
        "promotion_status": "queued_review",
        "timestamp": timestamp_now(),
    }
    write_json_artifact(promotion_root / "manifest.json", manifest)
    _append_jsonl(source_root / BUILDER_PROMOTION_QUEUE, manifest)
    return manifest


def _ensure_workspace_generated_config(
    *,
    source_root: Path,
    workspace_root: Path,
    config_path: str,
    source_snapshot: dict[str, tuple[int, int]],
) -> Path:
    workspace_config = workspace_root / config_path
    source_config = source_root / config_path
    if source_config.exists():
        copy_required = not workspace_config.exists()
        try:
            stat = source_config.stat()
        except OSError:
            stat = None
        if stat is not None:
            source_signature = (stat.st_size, stat.st_mtime_ns)
            if source_snapshot.get(config_path) != source_signature:
                copy_required = True
        if copy_required:
            workspace_config.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_config, workspace_config)
    if workspace_config.exists():
        return workspace_config
    if source_config.exists():
        workspace_config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_config, workspace_config)
    return workspace_config


def _sync_root_promotable_changes_into_workspace(
    *,
    source_root: Path,
    workspace_root: Path,
    source_snapshot: dict[str, tuple[int, int]],
) -> None:
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(source_root).as_posix()
        if not _promotable_builder_path(rel):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        signature = (stat.st_size, stat.st_mtime_ns)
        if source_snapshot.get(rel) == signature:
            continue
        workspace_path = workspace_root / rel
        if workspace_path.exists():
            try:
                if workspace_path.read_bytes() == path.read_bytes():
                    continue
            except OSError:
                pass
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, workspace_path)


def _persist_builder_attempt_bundle(
    *,
    artifact_dir: Path,
    source_root: Path,
    workspace_root: Path,
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
    verification_path = artifact_dir / "verification_report.json"
    failure_trace_path = artifact_dir / "failure_trace.txt"
    patch_path = artifact_dir / "workspace.patch"
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
    verification_payload = {
        "status": result.get("status"),
        "error_code": result.get("error_code"),
        "validation_passed": result.get("validation_passed"),
        "implementation_verification_passed": result.get("implementation_verification_passed"),
        "implementation_verification_failures": result.get("implementation_verification_failures")
        or [],
        "builder_phase": result.get("builder_phase"),
        "builder_attempts": result.get("builder_attempts"),
    }
    write_json_artifact(verification_path, verification_payload)
    failure_trace = "\n".join(
        part
        for part in (
            str(result.get("reason") or "").strip(),
            stdout.strip(),
            stderr.strip(),
        )
        if part
    )
    write_text_atomic(failure_trace_path, failure_trace + ("\n" if failure_trace else ""))
    changed_files = _workspace_changed_promotable_files(
        source_root=source_root,
        workspace_root=workspace_root,
    )
    patch_text = _workspace_unified_diff(
        source_root=source_root,
        workspace_root=workspace_root,
        changed_files=changed_files,
    )
    write_text_atomic(patch_path, patch_text)
    return [
        str(path)
        for path in (
            prompt_path,
            command_path,
            stdout_path,
            stderr_path,
            result_path,
            verification_path,
            failure_trace_path,
            patch_path,
        )
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
    try:
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
        notes = result.get("builder_self_check", {}).get("notes", [])
        if isinstance(notes, list) and any(
            note in {"builder_final_report_missing_or_malformed", "builder_self_check_unusable"}
            for note in notes
        ):
            record_event(
                source_module="compiler_builder",
                category="builder",
                action="builder_quality_warning",
                summary=f"builder quality warning thesis={thesis_id}",
                payload={
                    "thesis_id": thesis_id,
                    "notes": notes,
                    "result": result,
                },
                artifact_paths=artifact_paths,
                model_provider="codex",
                model_name=BUILDER_CLI_MODEL,
            )
        if result.get("status") == "error":
            error_code = str(result.get("error_code") or "builder_error")
            record_event(
                source_module="compiler_builder",
                category="builder",
                action="builder_error",
                summary=f"builder error thesis={thesis_id} code={error_code}",
                payload={
                    "thesis_id": thesis_id,
                    "error_code": error_code,
                    "reason": result.get("reason", ""),
                    "result": result,
                },
                artifact_paths=artifact_paths,
                model_provider="codex",
                model_name=BUILDER_CLI_MODEL,
            )
    except Exception as exc:
        log.debug("builder trace event emission failed: %s", exc)


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
    task: BuilderTask,
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
11. Work in explicit phases:
   - classify contract
   - map implementation surface
   - implement mechanism
   - implement diagnostics
   - run narrow validation/tests
   - self-check against the task contract
12. Return a concise final report with:
   - whether implementation succeeded
   - files changed
   - tests run
   - generated config path
   - a final JSON object that includes:
     {{
       "builder_self_check": {{
         "mechanism_implemented": true/false,
         "diagnostics_emitted": true/false,
         "config_surface_valid": true/false,
         "tests_covering_behavior": true/false,
         "notes": ["short notes"]
       }}
     }}

Context:
- Repo root: {root}
- Thesis artifact: {proposal_path}
- Contract artifact: {compilation_path}
- Expected config path: {config_path}
- Base config path: {base_config_path}
- Strategy family: {family_name}
- Missing primitives: {json.dumps(missing_primitives, indent=2)}{extra_block}
- Typed builder task:
{json.dumps(asdict(task), indent=2)}

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
    return bool(result.get("implementation_verification_failures")) or (
        result.get("error_code") == "builder_config_validation_failed"
    )


def _builder_retry_prompt(
    *,
    task: BuilderTask,
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
        task=task,
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
    task: BuilderTask,
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
    builder_self_check: BuilderSelfCheck | None = None,
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
            "error_code": "builder_config_validation_failed",
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
            "error_code": "builder_implementation_contract_failed",
            "reason": verification_reason,
            "generated_config": None,
            "validation_passed": True,
            "implementation_verification_passed": False,
            "implementation_verification_failures": verification.failures,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration_seconds, 3),
        }
    return _result_with_builder_envelope(
        {
            "status": "completed",
            "reason": reason,
            "generated_config": config_path,
            "validation_passed": True,
            "implementation_verification_passed": True,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(duration_seconds, 3),
        },
        task=task,
        phase="completed",
        self_check=_verified_builder_self_check(verification, builder_self_check),
    )


def build_missing_primitives(
    root: Path, thesis_id: str, *, artifact_root: Path | None = None
) -> dict[str, Any]:
    """Dispatch CLI builder to implement missing primitives for a thesis."""
    started_at = time.monotonic()
    if artifact_root is None:
        raise ValueError("job-scoped artifact_root is required for builder dispatch")
    structured = _load_structured_thesis_artifacts(root, thesis_id, artifact_root=artifact_root)
    if structured is not None:
        proposal, compilation, proposal_path, compilation_path = structured
        proposal, proposal_normalized = _normalize_proposal_config_changes(proposal)
        if proposal_normalized:
            write_json_artifact(proposal_path, proposal)
        family_name = proposal.get("strategy_family") or compilation.get("strategy_family")
        if not family_name:
            return {
                "status": "error",
                "error_code": "builder_missing_strategy_family",
                "reason": f"missing strategy_family in thesis artifacts for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
        try:
            load_family(family_name)
        except (KeyError, ValueError) as exc:
            return {
                "status": "error",
                "error_code": "builder_unknown_strategy_family",
                "reason": f"unknown strategy family for structured thesis {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        base_config_path = _resolved_builder_base_config_path(compilation, family_name)
        if not compilation.get("baseline_config_path"):
            compilation = {**compilation, "baseline_config_path": base_config_path}
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = _resolve_missing_primitives(proposal, compilation)
        invalid_contract = _validate_missing_primitives_contract(
            thesis_id=thesis_id, compilation=compilation, missing_primitives=missing_primitives
        )
        if invalid_contract is not None:
            return invalid_contract
        generated_name = (
            artifact_root / "experiments" / thesis_id / "runtime_config.json"
        ).resolve()
        config_path = serialize_config_path(generated_name, code_root=root)
        builder_requests_dir = artifact_root / "builder-requests"
        attempt_dir = _builder_artifact_dir(
            root, family_name, thesis_id, artifact_root=artifact_root
        )
    else:
        compilation_family_name = None
        for candidate_family in sorted(STRATEGIES):
            proposal_path = artifact_root / "proposals" / f"{thesis_id}.json"
            if proposal_path.exists():
                compilation_family_name = candidate_family
                break

        if compilation_family_name is None:
            return {
                "status": "error",
                "error_code": "builder_missing_proposal_artifact",
                "reason": f"missing proposal artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
        load_family(compilation_family_name)
        proposal_path = artifact_root / "proposals" / f"{thesis_id}.json"
        compilation_path = artifact_root / "compilations" / f"{thesis_id}.json"
        if not proposal_path.exists():
            return {
                "status": "error",
                "error_code": "builder_missing_proposal_artifact",
                "reason": f"missing proposal artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
        if not compilation_path.exists():
            return {
                "status": "error",
                "error_code": "builder_missing_compilation_artifact",
                "reason": f"missing compilation artifact for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }

        try:
            proposal = json.loads(proposal_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "status": "error",
                "error_code": "builder_malformed_proposal_artifact",
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
                "error_code": "builder_malformed_compilation_artifact",
                "reason": f"malformed compilation artifact for {thesis_id}: {exc}",
                "generated_config": None,
                "validation_passed": False,
            }
        family_name = proposal.get("strategy_family") or proposal.get("family")
        if not family_name:
            return {
                "status": "error",
                "error_code": "builder_missing_strategy_family",
                "reason": f"missing strategy_family/family field in proposal for {thesis_id}",
                "generated_config": None,
                "validation_passed": False,
            }
        base_config_path = _resolved_builder_base_config_path(compilation, family_name)
        if not compilation.get("baseline_config_path"):
            compilation = {**compilation, "baseline_config_path": base_config_path}
        normalized_contract = compilation.get("normalized_contract") or []
        missing_primitives = _resolve_missing_primitives(proposal, compilation)
        invalid_contract = _validate_missing_primitives_contract(
            thesis_id=thesis_id, compilation=compilation, missing_primitives=missing_primitives
        )
        if invalid_contract is not None:
            return invalid_contract
        generated_name = (
            artifact_root / "experiments" / thesis_id / "runtime_config.json"
        ).resolve()
        config_path = serialize_config_path(generated_name, code_root=root)
        builder_requests_dir = artifact_root / "builder-requests"
        attempt_dir = _builder_artifact_dir(
            root, family_name, thesis_id, artifact_root=artifact_root
        )
    workspace_root = _builder_workspace_dir(attempt_dir)
    _copy_builder_source_tree(root, workspace_root)
    workspace_proposal_path = _copy_file_into_workspace(
        source=proposal_path,
        source_root=root,
        workspace_root=workspace_root,
    )
    workspace_compilation_path = _copy_file_into_workspace(
        source=compilation_path,
        source_root=root,
        workspace_root=workspace_root,
    )
    (workspace_root / Path(config_path).parent).mkdir(parents=True, exist_ok=True)
    builder_task = _build_builder_task(
        thesis_id=thesis_id,
        family_name=family_name,
        proposal_path=workspace_proposal_path,
        compilation_path=workspace_compilation_path,
        config_path=config_path,
        base_config_path=base_config_path,
        proposal=proposal,
        compilation=compilation,
        missing_primitives=missing_primitives,
    )
    seeded_capability = _best_seeded_capability(root, builder_task)
    seeded_files = _seed_workspace_from_capability(
        source_root=root,
        workspace_root=workspace_root,
        seed_entry=seeded_capability,
    )
    request_payload = {
        "thesis_id": thesis_id,
        "family": family_name,
        "proposal_path": serialize_config_path(proposal_path, code_root=root),
        "compilation_path": serialize_config_path(compilation_path, code_root=root),
        "base_config_path": base_config_path,
        "missing_primitives": missing_primitives,
        "normalized_contract": normalized_contract,
        "status": "requested",
        "timestamp": timestamp_now(),
        "model_provider": "codex",
        "model": BUILDER_CLI_MODEL,
        "model_reasoning_effort": BUILDER_CLI_REASONING_EFFORT,
        "builder_workspace_root": workspace_root.as_posix(),
        "seeded_capability": seeded_capability,
        "seeded_files": seeded_files,
        "builder_task": asdict(builder_task),
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

    config_abspath = _ensure_workspace_generated_config(
        source_root=root,
        workspace_root=workspace_root,
        config_path=config_path,
        source_snapshot=_snapshot_promotable_source_state(root),
    )
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
    if seeded_capability and seeded_files:
        prompt_extras.extend(
            [
                "PRESEEDED REUSABLE BUILDER CAPABILITY:",
                json.dumps(
                    {
                        "thesis_id": seeded_capability.get("thesis_id"),
                        "promotion_dir": seeded_capability.get("promotion_dir"),
                        "seeded_files": seeded_files,
                    },
                    indent=2,
                ),
                "Reuse these promoted files if they satisfy the current primitive contract; do not rewrite them unnecessarily.",
            ]
        )
    prompt = _build_builder_prompt(
        task=builder_task,
        thesis_id=thesis_id,
        root=workspace_root,
        proposal_path=workspace_proposal_path,
        compilation_path=workspace_compilation_path,
        config_path=config_path,
        family_name=family_name,
        base_config_path=base_config_path,
        missing_primitives=missing_primitives,
        prompt_extras=prompt_extras,
    )
    cli = _find_cli()
    output_dir_ctx: tempfile.TemporaryDirectory[str] | None = None
    if cli:
        output_path = None
        builder_cmd = [
            cli,
            "exec",
            "--model",
            BUILDER_CLI_MODEL,
        ]
        if _codex_supports_exec_flag(cli, "--json"):
            builder_cmd[2:2] = ["--json"]
        if _codex_supports_exec_flag(cli, "--output-last-message"):
            output_dir_ctx = tempfile.TemporaryDirectory(prefix="autoresearch-builder-")
            output_path = Path(output_dir_ctx.name) / "last_message.txt"
            builder_cmd[2:2] = ["--output-last-message", str(output_path)]
        if _codex_supports_sandbox_flag(cli):
            builder_cmd[2:2] = ["--sandbox", "workspace-write"]
    else:
        builder_cmd = []
        output_path = None
    if BUILDER_CLI_REASONING_EFFORT:
        builder_cmd.extend(["-c", f'model_reasoning_effort="{BUILDER_CLI_REASONING_EFFORT}"'])
    _write_artifacts = functools.partial(
        _persist_builder_attempt_bundle,
        artifact_dir=attempt_dir,
        source_root=root,
        workspace_root=workspace_root,
        prompt=prompt,
        command=builder_cmd,
        cwd=workspace_root,
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
            "builder_task": asdict(builder_task),
            "builder_phase": "queued",
            "builder_self_check": asdict(_empty_builder_self_check()),
        },
    )

    out: dict[str, Any] | None = None
    if config_abspath.exists():
        existing_result = _validated_generated_config_result(
            task=builder_task,
            root=workspace_root,
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
            promotion_manifest = _record_builder_promotion_candidate(
                source_root=root,
                workspace_root=workspace_root,
                artifact_root=artifact_root,
                task=builder_task,
                thesis_id=thesis_id,
            )
            existing_result["execution_root"] = workspace_root.as_posix()
            existing_result["promotion_manifest"] = promotion_manifest
            existing_result["promoted_files"] = promotion_manifest.get("promoted_files", [])
            existing_result["seeded_capability"] = seeded_capability
            existing_result = _ensure_builder_envelope(
                existing_result,
                task=builder_task,
                phase="completed",
                self_check=_builder_self_check_with_note(
                    "builder_final_report_missing_or_malformed"
                ),
            )
            artifact_paths = _write_artifacts(result=existing_result)
            _trace_builder_finish(
                thesis_id=thesis_id, result=existing_result, artifact_paths=artifact_paths
            )
            return existing_result
        out = existing_result

    if not cli:
        result = {
            "status": "error",
            "error_code": (out.get("error_code") if out is not None else "builder_cli_unavailable"),
            "reason": (
                out["reason"] if out is not None else "No CLI available for builder dispatch"
            ),
            "generated_config": None,
            "validation_passed": False,
        }
        result = _result_with_builder_envelope(
            result,
            task=builder_task,
            phase="preflight_failed",
        )
        artifact_paths = _write_artifacts(result=result)
        _trace_builder_finish(thesis_id=thesis_id, result=result, artifact_paths=artifact_paths)
        return result

    try:
        stdout_log = ""
        stderr_log = ""
        last_prompt = prompt
        for attempt_index in range(BUILDER_IMPLEMENTATION_RETRY_LIMIT + 1):
            if output_path is not None and output_path.exists():
                output_path.unlink()
            attempt_prompt = prompt
            if out:
                attempt_prompt = _builder_retry_prompt(
                    task=builder_task,
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
            source_snapshot = _snapshot_promotable_source_state(root)
            try:
                proc = subprocess.run(
                    builder_cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(workspace_root),
                    input=attempt_prompt,
                    timeout=BUILDER_CLI_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                stdout_log, stderr_log = _timeout_output(exc)
                usage_payload = _emit_builder_usage(
                    thesis_id=thesis_id,
                    attempt_number=attempt_index + 1,
                    usage_result=_extract_codex_cli_usage(stdout_log),
                )
                output_text = (
                    output_path.read_text()
                    if output_path is not None and output_path.exists()
                    else ""
                )
                _sync_root_promotable_changes_into_workspace(
                    source_root=root,
                    workspace_root=workspace_root,
                    source_snapshot=source_snapshot,
                )
                config_abspath = _ensure_workspace_generated_config(
                    source_root=root,
                    workspace_root=workspace_root,
                    config_path=config_path,
                    source_snapshot=source_snapshot,
                )
                parsed_self_check = _extract_builder_self_check(
                    "\n".join(part for part in (output_text, stdout_log, stderr_log) if part)
                )
                duration_seconds = time.monotonic() - started_at
                out = _validated_generated_config_result(
                    task=builder_task,
                    root=workspace_root,
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
                    builder_self_check=parsed_self_check,
                )
                if out is not None and usage_payload is not None:
                    out["usage"] = usage_payload
                if out is None:
                    out = {
                        "status": "error",
                        "error_code": "builder_timeout",
                        "reason": f"builder timed out after {BUILDER_CLI_TIMEOUT_SECONDS}s: {exc}",
                        "generated_config": None,
                        "validation_passed": False,
                        "timed_out": True,
                        "exit_code": None,
                        "duration_seconds": round(duration_seconds, 3),
                    }
                    if usage_payload is not None:
                        out["usage"] = usage_payload
                    out = _result_with_builder_envelope(
                        out,
                        task=builder_task,
                        phase="builder_timeout",
                        self_check=parsed_self_check,
                    )
                else:
                    out = _ensure_builder_envelope(
                        out,
                        task=builder_task,
                        phase="completed" if out.get("status") != "error" else "builder_timeout",
                        self_check=parsed_self_check,
                    )
            else:
                stdout_log = proc.stdout or ""
                stderr_log = proc.stderr or ""
                proc_output = stdout_log + stderr_log
                usage_payload = _emit_builder_usage(
                    thesis_id=thesis_id,
                    attempt_number=attempt_index + 1,
                    usage_result=_extract_codex_cli_usage(stdout_log),
                )
                output_text = (
                    output_path.read_text()
                    if output_path is not None and output_path.exists()
                    else ""
                )
                _sync_root_promotable_changes_into_workspace(
                    source_root=root,
                    workspace_root=workspace_root,
                    source_snapshot=source_snapshot,
                )
                config_abspath = _ensure_workspace_generated_config(
                    source_root=root,
                    workspace_root=workspace_root,
                    config_path=config_path,
                    source_snapshot=source_snapshot,
                )
                parsed_self_check = _extract_builder_self_check(
                    "\n".join(part for part in (output_text, stdout_log, stderr_log) if part)
                )
                generated = config_path if config_abspath.exists() else None
                if generated:
                    out = _validated_generated_config_result(
                        task=builder_task,
                        root=workspace_root,
                        thesis=proposal,
                        contract=compilation,
                        config_abspath=config_abspath,
                        config_path=config_path,
                        family_name=family_name,
                        reason=proc_output,
                        exit_code=proc.returncode,
                        timed_out=False,
                        duration_seconds=time.monotonic() - started_at,
                        builder_self_check=parsed_self_check,
                    )
                    assert out is not None
                    if usage_payload is not None:
                        out["usage"] = usage_payload
                    out = _ensure_builder_envelope(
                        out,
                        task=builder_task,
                        phase="completed" if out.get("status") != "error" else "validation_failed",
                        self_check=parsed_self_check,
                    )
                else:
                    out = {
                        "status": "error",
                        "error_code": "builder_missing_generated_config",
                        "reason": proc_output,
                        "generated_config": None,
                        "validation_passed": False,
                        "exit_code": proc.returncode,
                        "timed_out": False,
                        "duration_seconds": round(time.monotonic() - started_at, 3),
                    }
                    if usage_payload is not None:
                        out["usage"] = usage_payload
                    out = _result_with_builder_envelope(
                        out,
                        task=builder_task,
                        phase="missing_generated_config",
                        self_check=parsed_self_check,
                    )
            if out.get("status") == "completed" and out.get("validation_passed"):
                promotion_manifest = _record_builder_promotion_candidate(
                    source_root=root,
                    workspace_root=workspace_root,
                    artifact_root=artifact_root,
                    task=builder_task,
                    thesis_id=thesis_id,
                )
                out["execution_root"] = workspace_root.as_posix()
                out["promotion_manifest"] = promotion_manifest
                out["promoted_files"] = promotion_manifest.get("promoted_files", [])
                out["seeded_capability"] = seeded_capability
            out["builder_attempts"] = attempt_index + 1
            out["builder_task"] = asdict(builder_task)
            _persist_builder_attempt_bundle(
                artifact_dir=_builder_attempt_artifact_dir(attempt_dir, attempt_index + 1),
                source_root=root,
                workspace_root=workspace_root,
                prompt=attempt_prompt,
                command=builder_cmd,
                cwd=workspace_root,
                timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
                result=out,
                stdout=stdout_log,
                stderr=stderr_log,
            )
            if out["status"] != "error" or not _should_retry_builder_result(out):
                break
        assert out is not None
        artifact_paths = _persist_builder_attempt_bundle(
            artifact_dir=attempt_dir,
            source_root=root,
            workspace_root=workspace_root,
            prompt=last_prompt,
            command=builder_cmd,
            cwd=workspace_root,
            timeout_seconds=BUILDER_CLI_TIMEOUT_SECONDS,
            result=out,
            stdout=stdout_log,
            stderr=stderr_log,
        )
        _trace_builder_finish(thesis_id=thesis_id, result=out, artifact_paths=artifact_paths)
        return out
    finally:
        if output_dir_ctx is not None:
            output_dir_ctx.cleanup()
