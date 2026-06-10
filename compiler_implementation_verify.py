from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backtest.data_universe import data_universe_path
from diagnostic_contracts import build_required_diagnostic_specs
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

BUILDER_SENTINEL_CONFIG_KEYS = frozenset({"requires_engine_change"})
THESIS_METADATA_CONFIG_KEYS = frozenset({"requires_code_change", "new_config_keys_needed"})


@dataclass(frozen=True)
class ImplementationVerification:
    passed: bool
    failures: list[str]
    tests_covering_behavior: bool = False


def verify_builder_implementation_contract(
    *,
    root: Path,
    thesis: dict[str, Any],
    contract: dict[str, Any] | None = None,
    generated_config_path: str,
    family_name: str,
) -> ImplementationVerification:
    """Verify builder output satisfies the deterministic thesis implementation contract."""
    config_path = Path(generated_config_path)
    if not config_path.is_absolute():
        config_path = root / config_path
    config = _read_runtime_config(config_path)
    failures: list[str] = []

    failures.extend(_verify_config_changes(thesis, config))
    failures.extend(_verify_no_undeclared_config_drift(root, contract, thesis, config, family_name))
    failures.extend(_verify_config_key_consumption(root, family_name, thesis))
    failures.extend(_verify_required_diagnostics(root, family_name, thesis))
    failures.extend(_verify_data_dependencies(root, family_name, thesis, config))
    test_failures = _verify_tests_cover_behavior(root, thesis, contract)
    failures.extend(test_failures)

    return ImplementationVerification(
        passed=not failures,
        failures=failures,
        tests_covering_behavior=not test_failures,
    )


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
    runtime_modules, runtime_failures = _runtime_code_modules_with_failures(strategy_dir)
    failures.extend(runtime_failures)
    for key in sorted(_runtime_config_changes(config_changes)):
        if key in default_keys:
            continue
        if not any(
            _config_key_consumed_by_runtime(module_text, key) for module_text in runtime_modules
        ):
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


def _runtime_code_modules_with_failures(strategy_dir: Path) -> tuple[list[str], list[str]]:
    chunks: list[str] = []
    failures: list[str] = []
    if not strategy_dir.exists():
        return chunks, failures
    for path in sorted(strategy_dir.rglob("*.py")):
        if path.name in SCHEMA_ONLY_FILES:
            continue
        text, failure = _read_source_text(path)
        if failure:
            failures.append(f"{failure}:{path.relative_to(strategy_dir)}")
        if text is not None:
            chunks.append(text)
    return chunks, failures


def _runtime_code_text_with_failures(strategy_dir: Path) -> tuple[str, list[str]]:
    chunks, failures = _runtime_code_modules_with_failures(strategy_dir)
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
    specs = build_required_diagnostic_specs(required, thesis.get("required_diagnostic_specs") or [])
    diagnostic_texts, source_failures = _diagnostic_emission_texts(root, family_name)
    failures.extend(source_failures)
    for spec in specs:
        if not _diagnostic_spec_emitted(diagnostic_texts, spec.model_dump()):
            failures.append(f"required_diagnostic_not_emitted:{spec.key}")
    return failures


def _diagnostic_emission_texts(
    root: Path, family_name: str
) -> tuple[dict[str, list[str]], list[str]]:
    """Code surfaces allowed to emit required diagnostics.

    Some diagnostics are emitted by the family runtime/event logger, while
    aggregate PF buckets are emitted by the post-backtest metrics layer.
    """
    strategy_modules, failures = _runtime_code_modules_with_failures(
        root / "strategies" / family_name
    )
    texts: dict[str, list[str]] = {
        "strategy_runtime": strategy_modules,
    }
    for surface, rel in (
        ("metrics", "metrics.py"),
        ("strategy_diagnostics", "strategy_event_logger.py"),
        ("runtime_output", "backtest/runner.py"),
        ("experiment_evaluation", "autoresearch_experiment.py"),
        ("experiment_evaluator", "experiment_evaluator.py"),
        ("experiment_evaluation_registry", "diagnostic_contracts.py"),
    ):
        path = root / rel
        if path.exists():
            text, failure = _read_source_text(path)
            if failure:
                failures.append(failure)
            if text is not None:
                texts[surface] = [text]
    return texts, failures


def _diagnostic_spec_emitted(texts: dict[str, list[str]], spec: dict[str, Any]) -> bool:
    key = str(spec.get("key") or "").strip()
    if not key:
        return False
    aliases = [str(alias).strip() for alias in spec.get("aliases") or [] if str(alias).strip()]
    tokens = [key, *aliases]
    surface = str(spec.get("surface") or "any")
    emission_surfaces: list[str]
    allow_registry = False
    if surface == "metrics":
        emission_surfaces = ["metrics"]
    elif surface == "strategy_diagnostics":
        emission_surfaces = ["strategy_diagnostics", "strategy_runtime", "runtime_output"]
    elif surface == "experiment_evaluation":
        emission_surfaces = ["experiment_evaluation"]
        allow_registry = True
    else:
        emission_surfaces = [
            "metrics",
            "strategy_diagnostics",
            "strategy_runtime",
            "runtime_output",
            "experiment_evaluation",
        ]
        allow_registry = True

    for token in tokens:
        if any(
            _payload_key_token_present(text, token)
            for name in emission_surfaces
            for text in texts.get(name, [])
        ):
            return True
        if allow_registry and _registered_spec_key_present(
            "\n".join(texts.get("experiment_evaluation_registry", [])), token
        ):
            return True
    return False


def _reachable_ast_nodes(node: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def walk(current: ast.AST) -> None:
        nodes.append(current)
        if isinstance(current, ast.If):
            resolved = _constant_truthiness(current.test)
            if resolved is not None:
                branch = current.body if resolved else current.orelse
                for child in branch:
                    walk(child)
                return
        for child in ast.iter_child_nodes(current):
            walk(child)

    walk(node)
    return nodes


def _constant_truthiness(node: ast.AST) -> bool | None:
    try:
        literal = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        literal = None
    else:
        return bool(literal)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        operand = _constant_truthiness(node.operand)
        return None if operand is None else not operand
    if isinstance(node, ast.BoolOp):
        values = [_constant_truthiness(value) for value in node.values]
        if any(value is None for value in values):
            return None
        bool_values = [bool(value) for value in values]
        if isinstance(node.op, ast.And):
            return all(bool_values)
        if isinstance(node.op, ast.Or):
            return any(bool_values)
    if isinstance(node, ast.Compare):
        left = _constant_value(node.left)
        comparators = [_constant_value(comp) for comp in node.comparators]
        if left is None or any(comp is None for comp in comparators):
            return None
        current = left
        for op, right in zip(node.ops, comparators):
            comparison = _compare_constants(current, op, right)
            if comparison is None:
                return None
            if not comparison:
                return False
            current = right
        return True
    return None


def _constant_value(node: ast.AST) -> Any | None:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _compare_constants(left: Any, op: ast.cmpop, right: Any) -> bool | None:
    try:
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
        if isinstance(op, ast.Is):
            return left is right
        if isinstance(op, ast.IsNot):
            return left is not right
        if isinstance(op, ast.In):
            return left in right
        if isinstance(op, ast.NotIn):
            return left not in right
    except Exception:
        return None
    return None


def _config_key_consumed_by_runtime(text: str, token: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in _reachable_ast_nodes(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value == token:
                    return True
        if isinstance(node, ast.Subscript):
            subscript_key = _subscript_string_key(node)
            if subscript_key == token:
                return True
    return False


def _payload_key_token_present(text: str, token: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in _reachable_ast_nodes(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == token:
                    return True
        if isinstance(node, ast.Subscript) and _subscript_string_key(node) == token:
            return True
    return False


def _registered_spec_key_present(text: str, token: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "DiagnosticRequirementSpec":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "key"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == token
            ):
                return True
    return False


def _subscript_string_key(node: ast.Subscript) -> str | None:
    slice_node = node.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return slice_node.value
    return None


def _verify_data_dependencies(
    root: Path, family_name: str, thesis: dict[str, Any], config: dict[str, Any]
) -> list[str]:
    relevant_tokens: list[str] = []
    config_changes = thesis.get("config_changes") or {}
    if isinstance(config_changes, dict):
        relevant_tokens.extend(str(key).lower() for key in config_changes)
    requested_primitives = thesis.get("requested_primitives") or []
    if isinstance(requested_primitives, list):
        relevant_tokens.extend(str(item).lower() for item in requested_primitives)
    required_specs = build_required_diagnostic_specs(
        thesis.get("required_diagnostics") or [],
        thesis.get("required_diagnostic_specs") or [],
    )
    for spec in required_specs:
        relevant_tokens.append(spec.key.lower())
        relevant_tokens.extend(alias.lower() for alias in spec.aliases)
    relevant_tokens.extend(str(key).lower() for key in config)
    runtime_modules, _runtime_failures = _runtime_code_modules_with_failures(
        root / "strategies" / family_name
    )
    if any(_runtime_mentions_vwap(text) for text in runtime_modules):
        relevant_tokens.append("vwap")
    if not any("vwap" in token for token in relevant_tokens):
        return []
    universe = config.get("data_universe")
    if not universe:
        return ["vwap_data_dependency_missing:data_universe"]
    vwap_path = data_universe_path(str(universe)) / "vwap.parquet"
    if not vwap_path.exists():
        return [f"vwap_data_dependency_missing:{vwap_path}"]
    return []


def _runtime_mentions_vwap(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in _reachable_ast_nodes(tree):
        if isinstance(node, ast.Name) and node.id.lower() == "vwap":
            return True
        if isinstance(node, ast.Attribute) and node.attr.lower() == "vwap":
            return True
        if isinstance(node, ast.Subscript):
            key = _subscript_string_key(node)
            if isinstance(key, str) and key.lower() == "vwap":
                return True
    return False


def _verify_tests_cover_behavior(
    root: Path,
    thesis: dict[str, Any],
    contract: dict[str, Any] | None,
) -> list[str]:
    requested_primitives = thesis.get("requested_primitives") or []
    if not isinstance(requested_primitives, list):
        requested_primitives = []
    contract_missing = []
    if isinstance(contract, dict):
        raw_missing = contract.get("missing_primitives") or []
        if isinstance(raw_missing, list):
            contract_missing = raw_missing
    requires_code_change = bool(
        thesis.get("requires_code_change") or requested_primitives or contract_missing
    )
    if not requires_code_change:
        return []

    tokens: list[str] = []
    tokens.extend(str(item) for item in requested_primitives if str(item).strip())
    tokens.extend(str(item) for item in contract_missing if str(item).strip())
    config_changes = thesis.get("config_changes") or {}
    if isinstance(config_changes, dict):
        tokens.extend(
            str(key)
            for key in _runtime_config_changes(config_changes)
            if str(key).strip() and key not in THESIS_METADATA_CONFIG_KEYS
        )
    required_specs = build_required_diagnostic_specs(
        thesis.get("required_diagnostics") or [],
        thesis.get("required_diagnostic_specs") or [],
    )
    for spec in required_specs:
        tokens.append(spec.key)
    unique_tokens = sorted({token for token in tokens if token})
    if not unique_tokens:
        return []

    tests_dir = root / "tests"
    if not tests_dir.exists():
        return [f"tests_covering_behavior_missing:{unique_tokens[0]}"]
    test_texts: list[str] = []
    for path in sorted(tests_dir.rglob("test*.py")):
        text, _failure = _read_source_text(path)
        if text is not None:
            test_texts.append(text)
    if not test_texts:
        return [f"tests_covering_behavior_missing:{unique_tokens[0]}"]

    def test_mentions_token(text: str, token: str) -> bool:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return False
        for node in _reachable_ast_nodes(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and key.value == token:
                        return True
            if isinstance(node, ast.Call):
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    if isinstance(arg, ast.Constant) and arg.value == token:
                        return True
            if isinstance(node, ast.Subscript) and _subscript_string_key(node) == token:
                return True
        return False

    if not any(
        test_mentions_token(test_text, token) for token in unique_tokens for test_text in test_texts
    ):
        return [f"tests_covering_behavior_missing:{unique_tokens[0]}"]
    return []
