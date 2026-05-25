from __future__ import annotations

import os
from pathlib import Path


def resolve_runtime_root(code_root: Path) -> Path:
    raw = os.environ.get("AUTORESEARCH_RUNTIME_ROOT", "").strip()
    if not raw:
        return code_root.resolve()
    return Path(raw).expanduser().resolve()


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
