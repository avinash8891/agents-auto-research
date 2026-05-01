from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from strategies import STRATEGIES


def _resolve_relative_data_dir(config: dict[str, Any], source_path: Path) -> dict[str, Any]:
    data_dir = config.get("data_dir")
    if not isinstance(data_dir, str) or not data_dir:
        return config
    data_path = Path(data_dir)
    if data_path.is_absolute():
        return config

    module_root = Path(__file__).resolve().parents[1]
    search_roots = [source_path.parent, *source_path.parents, Path.cwd(), module_root]
    for root in search_roots:
        candidate = root / data_path
        if candidate.exists():
            resolved = dict(config)
            resolved["data_dir"] = str(candidate)
            return resolved
    return config


def load_runtime_config(path: str, strategy_name: str) -> dict[str, Any]:
    p = Path(path)
    strategy = STRATEGIES[strategy_name]
    if p.suffix in (".yaml", ".yml"):
        import yaml

        payload = yaml.safe_load(p.read_text())
    else:
        payload = json.loads(p.read_text())
    if isinstance(payload, dict) and "runtime_config" in payload:
        config = payload["runtime_config"]
    elif isinstance(payload, dict):
        config = payload
    else:
        compilation = strategy.compile_contract(payload)
        if compilation.status != "ready_to_run":
            raise ValueError(
                f"{strategy_name} contract is not runnable: status={compilation.status} "
                f"missing={compilation.missing_primitives}"
            )
        config = compilation.runtime_config
    config = _resolve_relative_data_dir(config, p)
    config = strategy.validate_runtime_config_scope(config, source_path=p)
    violations = strategy.validate_runtime_config(config)
    if violations:
        raise ValueError(f"Config validation failed for {p}: " + "; ".join(violations))
    return config


def validate_runtime_config_scope(
    config: dict[str, Any], *, source_path: Path | None = None, strategy_name: str
) -> dict[str, Any]:
    return STRATEGIES[strategy_name].validate_runtime_config_scope(config, source_path=source_path)
