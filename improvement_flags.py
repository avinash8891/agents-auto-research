"""Feature flags for the self-improvement loop.

All flags default off. Each gates one arrow: HALO mining, HALO
auto-apply, Reflexion, Ratchet. With every flag off, the round loop
runs byte-identically to pre-improvement-loop code.
"""

from __future__ import annotations

import os

from autoresearch_constants import (
    ENV_IMPROVEMENT_HALO,
    ENV_IMPROVEMENT_HALO_APPLY,
    ENV_IMPROVEMENT_RATCHET,
    ENV_IMPROVEMENT_REFLEXION,
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

KNOWN_FLAGS = (
    ENV_IMPROVEMENT_HALO,
    ENV_IMPROVEMENT_HALO_APPLY,
    ENV_IMPROVEMENT_REFLEXION,
    ENV_IMPROVEMENT_RATCHET,
)


def enabled(name: str) -> bool:
    """Return True iff env var `name` is a truthy string.

    Truthy values: "1", "true", "yes", "on" (case-insensitive).
    """
    if name not in KNOWN_FLAGS:
        raise ValueError(f"unknown improvement flag: {name!r}")
    raw = os.environ.get(name, "")
    return raw.strip().lower() in _TRUTHY


def halo_enabled() -> bool:
    return enabled(ENV_IMPROVEMENT_HALO)


def halo_apply_enabled() -> bool:
    return enabled(ENV_IMPROVEMENT_HALO_APPLY)


def reflexion_enabled() -> bool:
    return enabled(ENV_IMPROVEMENT_REFLEXION)


def ratchet_enabled() -> bool:
    return enabled(ENV_IMPROVEMENT_RATCHET)
