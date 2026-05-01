from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps_strict(payload: Any, *, indent: int = 2) -> str:
    return json.dumps(_json_safe_value(payload), indent=indent)


def json_loads_metric_sentinels(payload: str) -> Any:
    return _json_relax_value(json.loads(payload))


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(path, json_dumps_strict(payload) + "\n")


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _json_relax_value(value: Any) -> Any:
    if value == "Infinity":
        return float("inf")
    if value == "-Infinity":
        return float("-inf")
    if value == "NaN":
        return float("nan")
    if isinstance(value, dict):
        return {key: _json_relax_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_relax_value(item) for item in value]
    return value


def write_json_atomic_strict(path: Path, payload: Any) -> None:
    """Write JSON after converting non-finite floats into string sentinels."""
    write_text_atomic(path, json_dumps_strict(payload) + "\n")
