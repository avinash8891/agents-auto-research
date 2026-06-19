from __future__ import annotations

import os
from pathlib import Path


def resolve_runtime_root(code_root: Path) -> Path:
    raw = os.environ.get("AUTORESEARCH_RUNTIME_ROOT", "").strip()
    if not raw:
        return code_root.resolve()
    return Path(raw).expanduser().resolve()


# The live family baseline can be promoted past the committed seed config. The
# promoted copy lives under the runtime root at a path that does NOT exist in the
# (read-only) code root, so resolve_config_path's runtime-root fallback finds it
# without clobbering the committed configs/<family>_base.yaml on local runs.
PROMOTED_BASELINE_DIRNAME = "promoted_baselines"


def promoted_baseline_path(runtime_root: Path, base_config_filename: str) -> Path:
    return runtime_root / PROMOTED_BASELINE_DIRNAME / base_config_filename


def serialize_config_path(path: Path, *, code_root: Path) -> str:
    resolved = path.resolve()
    code_root_resolved = code_root.resolve()
    try:
        return resolved.relative_to(code_root_resolved).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_config_path(
    config: str | Path,
    *,
    code_root: Path,
    runtime_root: Path,
    execution_root: Path | None = None,
) -> Path:
    raw = Path(config)
    code_root_resolved = code_root.resolve()
    runtime_root_resolved = runtime_root.resolve()
    execution_root_resolved = execution_root.resolve() if execution_root is not None else None

    def _ensure_allowed(path: Path) -> Path:
        resolved = path.resolve()
        if not path_within_allowed_roots(
            resolved,
            code_root=code_root_resolved,
            runtime_root=runtime_root_resolved,
            execution_root=execution_root_resolved,
        ):
            raise ValueError(f"config path escapes allowed roots: {config!r}")
        return resolved

    if raw.is_absolute():
        return _ensure_allowed(raw)

    candidate_roots: list[Path] = []
    if execution_root is not None:
        candidate_roots.append(execution_root_resolved)
    candidate_roots.extend([code_root_resolved, runtime_root_resolved])

    for base in candidate_roots:
        candidate = base / raw
        if candidate.exists():
            return _ensure_allowed(candidate)

    preferred_root = (
        execution_root_resolved if execution_root_resolved is not None else code_root_resolved
    )
    if raw.parts[:1] == ("configs",):
        preferred_root = code_root_resolved
    return _ensure_allowed(preferred_root / raw)


def path_within_allowed_roots(
    path: Path, *, code_root: Path, runtime_root: Path, execution_root: Path | None = None
) -> bool:
    resolved = path.resolve()
    allowed = [code_root.resolve(), runtime_root.resolve()]
    if execution_root is not None:
        allowed.append(execution_root.resolve())
    for base in allowed:
        try:
            resolved.relative_to(base)
            return True
        except ValueError:
            continue
    return False


def resolve_runtime_path(
    raw: str | Path,
    *,
    code_root: Path,
    runtime_root: Path,
    execution_root: Path | None = None,
    must_exist: bool = True,
) -> Path | None:
    """Resolve a persisted runtime-artifact path against the allowed roots.

    The canonical resolver for paths stored in state/asi (trade files, artifact
    dirs). Artifacts live under the runtime root when execution and code roots are
    split, but legacy entries may be code-root-relative or absolute — accept any
    path that lands within the allowed roots. Runtime-first for relative paths
    (that is where artifacts live). Best-effort: returns None on escape or, when
    ``must_exist``, absence; never raises. This is the one home for artifact path
    resolution — callers must not re-derive it with ``is_relative_to(code_root)``.
    """
    raw_path = Path(raw)
    code_root_r = code_root.resolve()
    runtime_root_r = runtime_root.resolve()
    exec_r = execution_root.resolve() if execution_root is not None else None
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        bases = ([exec_r] if exec_r is not None else []) + [runtime_root_r, code_root_r]
        candidates.extend(base / raw_path for base in bases)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not path_within_allowed_roots(
            resolved, code_root=code_root_r, runtime_root=runtime_root_r, execution_root=exec_r
        ):
            continue
        if must_exist and not resolved.exists():
            continue
        return resolved
    return None
