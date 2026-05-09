from __future__ import annotations

import importlib
import pkgutil

from strategies.base import STRATEGIES as STRATEGIES


def _discover_strategy_plugins() -> None:
    package_prefix = f"{__name__}."
    for module in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if not module.ispkg or module.name == "base":
            continue
        importlib.import_module(f"{package_prefix}{module.name}")


_discover_strategy_plugins()
