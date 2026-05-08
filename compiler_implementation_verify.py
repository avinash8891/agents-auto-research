from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backtest.data_universe import data_universe_path
from strategies import STRATEGIES
from strategy_family import load_family

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
THESIS_METADATA_CONFIG_KEYS = frozenset({"requires_code_change", "new_config_keys_needed"})


@dataclass(frozen=True)
class ImplementationVerification:
    passed: bool
    failures: list[str]


def verify_builder_implementation_contract(
    *,
    root: Path,
    thesis: dict[str, Any],
    contract: dict[str, Any] | None = None,
    generated_config_path: str,
    family_name: str,
) -> ImplementationVerification:
    """Verify builder output satisfies the deterministic thesis implementation contract."""
    config_path = root / generated_config_path
    config = _read_runtime_config(config_path)
    failures: list[str] = []

    failures.extend(_verify_config_changes(thesis, config))
    failures.extend(_verify_no_undeclared_config_drift(root, contract, thesis, config, family_name))
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
        if key in THESIS_METADATA_CONFIG_KEYS:
            failures.append(f"metadata_config_change_not_allowed:{key}")
            continue
        if key not in config:
            failures.append(f"config_change_missing:{key}")
            continue
        actual = config[key]
        if not _json_equal(actual, expected):
            failures.append(
                "config_change_mismatch:"
                f"{key}: expected={_json_repr(expected)} "
                f"actual={_json_repr(actual)}"
            )
    return failures


def _verify_no_undeclared_config_drift(
    root: Path,
    contract: dict[str, Any] | None,
    thesis: dict[str, Any],
    config: dict[str, Any],
    family_name: str,
) -> list[str]:
    if not isinstance(contract, dict):
        return []
    loaded = _load_baseline_config(root, contract, family_name)
    if loaded is None:
        return []
    if isinstance(loaded, str):
        return [loaded]
    base_config = loaded
    config_changes = thesis.get("config_changes") or {}
    if not isinstance(config_changes, dict):
        return []
    allowed_drift = set(_runtime_config_changes(config_changes))
    failures: list[str] = []
    for key, base_value in sorted(base_config.items()):
        if key in allowed_drift:
            continue
        generated_value = config.get(key)
        if not _json_equal(generated_value, base_value):
            failures.append(
                "unexpected_config_drift:"
                f"{key} base={_json_repr(base_value)} "
                f"generated={_json_repr(generated_value)}"
            )
    for key in sorted(set(config) - set(base_config) - allowed_drift):
        failures.append(f"unexpected_config_key:{key}")
    return failures


def _load_baseline_config(
    root: Path,
    contract: dict[str, Any] | None,
    family_name: str,
) -> dict[str, Any] | str | None:
    if not isinstance(contract, dict):
        return None
    base_config_path = contract.get("baseline_config_path")
    if not isinstance(base_config_path, str) or not base_config_path:
        return None
    base_path = root / base_config_path
    if not base_path.exists():
        try:
            expected_baseline = load_family(family_name).baseline_config_path
        except ValueError:
            return f"base_config_missing:{base_config_path}"
        if base_config_path == expected_baseline:
            return STRATEGIES[family_name].get_defaults()
        return f"base_config_missing:{base_config_path}"
    try:
        return _read_runtime_config(base_path)
    except Exception as exc:
        return f"base_config_unreadable:{base_config_path}:{exc}"


def _json_equal(left: Any, right: Any) -> bool:
    return _json_repr(left) == _json_repr(right)


def _json_repr(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


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
    text, _failures = _runtime_code_text_with_failures(strategy_dir)
    return text


def _runtime_code_text_with_failures(strategy_dir: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    failures: list[str] = []
    if not strategy_dir.exists():
        return "", failures
    for path in sorted(strategy_dir.glob("*.py")):
        if path.name in SCHEMA_ONLY_FILES:
            continue
        text, failure = _read_source_text(path)
        if failure:
            failures.append(failure)
        if text is not None:
            chunks.append(text)
    return "\n".join(chunks), failures


def _read_source_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"source_read_failed:{path.name}:{type(exc).__name__}"


def _verify_required_diagnostics(root: Path, family_name: str, thesis: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    required = thesis.get("required_diagnostics") or []
    if not isinstance(required, list):
        return ["required_diagnostics must be a list"]
    runtime_text, source_failures = _diagnostic_emission_text(root, family_name)
    failures.extend(source_failures)
    for item in required:
        if not isinstance(item, str):
            continue
        normalized = _diagnostic_key_from_requirement(item)
        if not normalized:
            continue
        if normalized not in runtime_text:
            failures.append(f"required_diagnostic_not_emitted:{normalized}")
    return failures


def _diagnostic_emission_text(root: Path, family_name: str) -> tuple[str, list[str]]:
    """Code surfaces allowed to emit required diagnostics.

    Some diagnostics are emitted by the family runtime/event logger, while
    aggregate PF buckets are emitted by the post-backtest metrics layer.
    """
    strategy_text, failures = _runtime_code_text_with_failures(root / "strategies" / family_name)
    chunks = [strategy_text]
    for rel in ("metrics.py", "strategy_event_logger.py", "backtest/runner.py"):
        path = root / rel
        if path.exists():
            text, failure = _read_source_text(path)
            if failure:
                failures.append(failure)
            if text is not None:
                chunks.append(text)
    return "\n".join(chunks), failures


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
