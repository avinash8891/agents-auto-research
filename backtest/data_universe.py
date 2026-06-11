from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from data_loader import load_data

WIDE_DATA_FILES = ("open.parquet", "high.parquet", "low.parquet", "close.parquet", "volume.parquet")
DATA_ROOT_ENV = "AUTORESEARCH_DATA_ROOT"
LEGACY_PATH_SELECTOR = "data" + "_" + "dir"


def default_data_root() -> Path:
    return Path(os.environ.get(DATA_ROOT_ENV, "~/autoresearch-data").strip()).expanduser()


def resolve_data_universe(config: dict[str, Any]) -> dict[str, Any]:
    if LEGACY_PATH_SELECTOR in config:
        raise ValueError(
            "Path-based market data selectors are not supported in runtime configs; "
            "use data_universe."
        )
    universe = _normalize_universe_name(config.get("data_universe"))

    universe_dir = data_universe_path(universe)
    manifest_path = manifest_for_universe(universe)
    manifest = _read_manifest(manifest_path)
    provenance = _build_data_provenance(universe, universe_dir, manifest_path, manifest)
    resolved = dict(config)
    resolved["data_universe"] = universe
    resolved["data_provenance"] = provenance
    return resolved


def load_universe_data(
    config: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    universe = _normalize_universe_name(config.get("data_universe"))
    return load_data(
        str(data_universe_dir(universe)),
        symbols=config.get("symbols"),
        start_date=config.get("validation_start"),
        end_date=config.get("validation_end"),
    )


def data_universe_dir(universe: str) -> Path:
    universe_dir = data_universe_path(universe)
    if not universe_dir.exists():
        data_root = default_data_root()
        available = _available_universes(data_root)
        available_text = ", ".join(available) if available else "none"
        raise FileNotFoundError(
            f"Data universe {universe!r} not found at {universe_dir}. "
            f"Set {DATA_ROOT_ENV}={data_root} or create "
            f"{data_root / 'universes' / universe}. "
            f"Available universes: {available_text}"
        )
    missing = [filename for filename in WIDE_DATA_FILES if not (universe_dir / filename).exists()]
    if missing:
        raise FileNotFoundError(
            f"Data universe {universe!r} is incomplete at {universe_dir}: "
            f"missing {', '.join(missing)}"
        )
    return universe_dir


def data_universe_path(universe: str) -> Path:
    return default_data_root() / "universes" / universe


def manifest_for_universe(universe: str) -> Path:
    return data_universe_path(universe) / "manifest.json"


def _normalize_universe_name(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("data_universe must be a non-empty string or integer universe name.")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value:
        raise ValueError("data_universe must be a non-empty string or integer universe name.")
    if "/" in value or "\\" in value or value in {".", ".."} or ".." in value:
        raise ValueError(f"data_universe={value!r} must be a simple universe name.")
    return value


def _available_universes(data_root: Path) -> list[str]:
    universes_dir = data_root / "universes"
    if not universes_dir.exists():
        return []
    return sorted(path.name for path in universes_dir.iterdir() if path.is_dir())


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Data manifest must be a JSON object: {path}")
    return payload


def _build_data_provenance(
    universe: str, universe_dir: Path, manifest_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    symbols = manifest.get("symbols")
    if symbols is not None and not isinstance(symbols, list):
        raise ValueError(f"Data manifest symbols must be a list when present: {manifest_path}")
    symbol_count = manifest.get("symbol_count")
    if symbol_count is None and symbols is not None:
        symbol_count = len(symbols)
    provenance = {
        "data_universe": universe,
        "universe_path": str(universe_dir),
        "symbol_count": symbol_count,
        "symbols_hash": _symbols_hash(symbols),
        "start": manifest.get("start"),
        "end": manifest.get("end"),
        "manifest_path": str(manifest_path),
    }
    return {key: value for key, value in provenance.items() if value is not None}


def _symbols_hash(symbols: list[Any] | None) -> str:
    if symbols is None:
        return ""
    normalized = [str(symbol) for symbol in symbols]
    blob = json.dumps(normalized, separators=(",", ":"), sort_keys=False).encode()
    return hashlib.sha256(blob).hexdigest()[:12]
