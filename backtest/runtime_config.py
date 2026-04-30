from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from strategies import STRATEGIES


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
    return strategy.validate_runtime_config_scope(config, source_path=p)


def validate_runtime_config_scope(
    config: dict[str, Any], *, source_path: Path | None = None, strategy_name: str
) -> dict[str, Any]:
    return STRATEGIES[strategy_name].validate_runtime_config_scope(config, source_path=source_path)
