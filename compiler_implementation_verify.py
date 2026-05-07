from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backtest.data_universe import data_universe_path
from strategies import STRATEGIES

SCHEMA_ONLY_FILES = {
    "__init__.py",
    "contract.py",
    "defaults.py",
    "prompt.py",
    "research.py",
    "validate.py",
}

ANALYSIS_ONLY_DIAGNOSTIC_PREFIXES = (
    "definition_check:",
    "implementation:",
)
BUILDER_SENTINEL_CONFIG_KEYS = frozenset({"requires_engine_change"})


@dataclass(frozen=True)
class ImplementationVerification:
    passed: bool
    failures: list[str]


def verify_builder_implementation_contract(
    *,
    root: Path,
    thesis: dict[str, Any],
    generated_config_path: str,
    family_name: str,
) -> ImplementationVerification:
    """Verify builder output satisfies the deterministic thesis implementation contract."""
    config_path = root / generated_config_path
    config = _read_runtime_config(config_path)
    failures: list[str] = []

    failures.extend(_verify_config_changes(thesis, config))
    failures.extend(_verify_config_key_consumption(root, family_name, thesis))
    failures.extend(_verify_required_diagnostics(root, family_name, thesis))
    failures.extend(_verify_data_dependencies(root, thesis, config))

    return ImplementationVerification(passed=not failures, failures=failures)


def _read_runtime_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Generated config must be a mapping: {path}")
    runtime_config = payload.get("runtime_config", payload)
    if not isinstance(runtime_config, dict):
        raise ValueError(f"Generated runtime_config must be a mapping: {path}")
    return runtime_config


def _verify_config_changes(thesis: dict[str, Any], config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    config_changes = thesis.get("config_changes") or {}
    if not isinstance(config_changes, dict):
        return ["thesis config_changes must be a JSON object"]
    for key, expected in _runtime_config_changes(config_changes).items():
        if key not in config:
            failures.append(f"config_change_missing:{key}")
            continue
        actual = config[key]
        if not _json_equal(actual, expected):
            failures.append(
                "config_change_mismatch:"
                f"{key}: expected={json.dumps(expected, sort_keys=True)} "
                f"actual={json.dumps(actual, sort_keys=True)}"
            )
    return failures


def _json_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def _verify_config_key_consumption(
    root: Path, family_name: str, thesis: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    strategy_dir = root / "strategies" / family_name
    config_changes = thesis.get("config_changes") or {}
    if not isinstance(config_changes, dict) or not config_changes:
        return failures
    default_keys = _default_runtime_keys(family_name)
    runtime_text = _runtime_code_text(strategy_dir)
    for key in sorted(_runtime_config_changes(config_changes)):
        if key in default_keys:
            continue
        if key not in runtime_text:
            failures.append(f"config_key_not_consumed_by_runtime:{key}")
    return failures


def _runtime_config_changes(config_changes: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config_changes.items()
        if key not in BUILDER_SENTINEL_CONFIG_KEYS
    }


def _default_runtime_keys(family_name: str) -> set[str]:
    try:
        return set(STRATEGIES[family_name].get_defaults())
    except Exception:
        return set()


def _runtime_code_text(strategy_dir: Path) -> str:
    chunks: list[str] = []
    if not strategy_dir.exists():
        return ""
    for path in sorted(strategy_dir.glob("*.py")):
        if path.name in SCHEMA_ONLY_FILES:
            continue
        text = _read_source_text(path)
        if text is not None:
            chunks.append(text)
    return "\n".join(chunks)


def _read_source_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def _verify_required_diagnostics(root: Path, family_name: str, thesis: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    required = thesis.get("required_diagnostics") or []
    if not isinstance(required, list):
        return ["required_diagnostics must be a list"]
    runtime_text = _diagnostic_emission_text(root, family_name)
    for item in required:
        if not isinstance(item, str):
            continue
        normalized = _diagnostic_key_from_requirement(item)
        if not normalized:
            continue
        if normalized not in runtime_text:
            failures.append(f"required_diagnostic_not_emitted:{normalized}")
    return failures


def _diagnostic_emission_text(root: Path, family_name: str) -> str:
    """Code surfaces allowed to emit required diagnostics.

    Some diagnostics are emitted by the family runtime/event logger, while
    aggregate PF buckets are emitted by the post-backtest metrics layer.
    """
    chunks = [_runtime_code_text(root / "strategies" / family_name)]
    for rel in ("metrics.py", "strategy_event_logger.py", "backtest/runner.py"):
        path = root / rel
        if path.exists():
            text = _read_source_text(path)
            if text is not None:
                chunks.append(text)
    return "\n".join(chunks)


def _diagnostic_key_from_requirement(requirement: str) -> str | None:
    lowered = requirement.strip().lower()
    if not lowered:
        return None
    if lowered.startswith(ANALYSIS_ONLY_DIAGNOSTIC_PREFIXES):
        return None
    before_paren = lowered.split("(", 1)[0].strip()
    key = re.sub(r"[^a-z0-9]+", "_", before_paren).strip("_")
    if not key:
        return None
    return key


def _verify_data_dependencies(
    root: Path, thesis: dict[str, Any], config: dict[str, Any]
) -> list[str]:
    haystack = json.dumps(thesis, sort_keys=True).lower()
    if "vwap" not in haystack:
        return []
    universe = config.get("data_universe")
    if not universe:
        return ["vwap_data_dependency_missing:data_universe"]
    vwap_path = data_universe_path(str(universe)) / "vwap.parquet"
    if not vwap_path.exists():
        return [f"vwap_data_dependency_missing:{vwap_path}"]
    return []
