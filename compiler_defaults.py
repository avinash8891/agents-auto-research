from __future__ import annotations

from typing import Any

from strategies.orb.defaults import _get_orb_defaults as _strategy_get_orb_defaults


def _get_orb_defaults() -> dict[str, Any]:
    return _strategy_get_orb_defaults()
