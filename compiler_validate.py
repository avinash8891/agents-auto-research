from __future__ import annotations

from typing import Any

from strategies.orb.validate import (
    validate_orb_runtime_config as _strategy_validate_orb_runtime_config,
)


def validate_orb_runtime_config(config: dict[str, Any]) -> list[str]:
    return _strategy_validate_orb_runtime_config(config)
