from __future__ import annotations

import json
import math
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_config_payload(path: Path) -> Any:
    """Load a config file, dispatching on suffix: YAML for .yaml/.yml, else JSON."""
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        return yaml.safe_load(text)
    return json.loads(text)


def json_dumps_strict(payload: Any, *, indent: int = 2) -> str:
    return json.dumps(_json_safe_value(payload), indent=indent)


def json_loads_metric_sentinels(payload: str) -> Any:
    return _json_relax_value(json.loads(payload))


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        pass
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
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: Any) -> None:
    write_text_atomic(path, json_dumps_strict(payload) + "\n")


def write_yaml_atomic(path: Path, payload: Any) -> None:
    import yaml

    write_text_atomic(path, yaml.dump(payload, default_flow_style=False))


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


def safe_stat_mtime(path: Path) -> float:
    """Return ``path.stat().st_mtime`` or ``0.0`` on OSError.

    Prevents FileNotFoundError when a file is deleted between a glob call
    and the subsequent stat inside max()/sorted() lambdas (TOCTOU race).
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def parse_positive_int_env(env_key: str, default: int, *, logger: Any | None = None) -> int:
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        if logger is not None:
            logger.warning("invalid value %r for %s; using default %d", raw, env_key, default)
        return default
    if value <= 0:
        if logger is not None:
            logger.warning("non-positive value %r for %s; using default %d", raw, env_key, default)
        return default
    return value


def require_positive_int_env(env_key: str, default: int) -> int:
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"invalid value {raw!r} for {env_key}; expected a positive integer"
        ) from None
    if value <= 0:
        raise ValueError(f"{env_key}={value} must be > 0") from None
    return value
