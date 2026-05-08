from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_EXPORTED_CONTENT_CHARS = 2000

_SECRET_PATTERNS = [
    re.compile(
        r"(?i)\b(openai_api_key|api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[^'\"\s,}]+"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
]

_LOCAL_PATH_PATTERNS = [
    (re.compile(r"/Users/[A-Za-z0-9._-]+/[^\s\"',)]+"), "<local-user-path>"),
    (re.compile(r"/private/var/[^\s\"',)]+"), "<local-temp-path>"),
    (re.compile(r"/tmp/[^\s\"',)]+"), "<tmp-path>"),
    (
        re.compile(
            r"(?<!\S)/(?:Applications|Library|Network|System|Volumes|home|opt|root|var)/[^\s\"',)]+"
        ),
        "<local-abs-path>",
    ),
]


def content_from_artifacts(
    event: dict[str, Any], section: str, *, trace_path: Path | None = None
) -> str:
    """Read bounded prompt/response content from trace artifacts at export time."""
    for raw_path in event.get("artifact_paths") or []:
        try:
            path = _safe_artifact_path(raw_path, trace_path=trace_path)
            if path is None:
                continue
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        extracted = _section(text, section)
        if extracted:
            return redact_text(extracted[:MAX_EXPORTED_CONTENT_CHARS])
    return ""


def _safe_artifact_path(raw_path: Any, *, trace_path: Path | None) -> Path | None:
    path = Path(str(raw_path))
    if trace_path is None:
        return path if not path.is_absolute() else None

    base = trace_path.resolve().parent
    candidate = path if path.is_absolute() else base / path
    try:
        resolved = candidate.resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return None
    return resolved


def _section(text: str, section: str) -> str:
    marker = f"--- {section} ---"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    next_marker = text.find("\n--- ", start)
    if next_marker < 0:
        next_marker = len(text)
    return text[start:next_marker].strip()


def redact_text(text: str, *, max_chars: int | None = None) -> str:
    if max_chars is not None:
        text = text[:max_chars]
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_redact_match, redacted)
    for pattern, replacement in _LOCAL_PATH_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def portable_path(path: str | Path) -> str:
    """Return a non-machine-specific path for exported third-party traces."""
    value = Path(str(path))
    parts = value.parts
    for anchor in ("logs", "trace_exports", "improvement_reports"):
        if anchor in parts:
            idx = parts.index(anchor)
            return Path(*parts[idx:]).as_posix()
    text = value.as_posix()
    return redact_text(text)


def _redact_match(match: re.Match[str]) -> str:
    text = match.group(0)
    for separator in ("=", ":"):
        if separator in text:
            return text.split(separator, 1)[0].rstrip() + f"{separator}<redacted>"
    return "<redacted>"
