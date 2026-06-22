from __future__ import annotations

import pytest

from compiler_research import _runtime_config_for_registered_strategy
from strategies import STRATEGIES


def test_orb_strategy_rejects_unsupported_config_change_key() -> None:
    orb = STRATEGIES["orb"]
    with pytest.raises(ValueError, match="Unsupported config keys: bogus_unknown_key"):
        orb.normalize_config_changes({"or_minutes": 20, "bogus_unknown_key": 1})


def test_orb_strategy_accepts_supported_config_change_keys() -> None:
    orb = STRATEGIES["orb"]
    assert orb.normalize_config_changes({"or_minutes": 20, "rr_ratio": 3.0}) == {
        "or_minutes": 20,
        "rr_ratio": 3.0,
    }


def test_ema_strategy_passes_config_changes_through_unchanged() -> None:
    ema = STRATEGIES["ema"]
    changes = {"anything_goes": 1, "ema_length": 9}
    assert ema.normalize_config_changes(changes) == changes


def test_runtime_config_merge_enforces_orb_safety_rail() -> None:
    # The live compile seam must reject an unsupported key before it merges into
    # the runtime config and silently no-ops.
    with pytest.raises(ValueError, match="Unsupported config keys"):
        _runtime_config_for_registered_strategy(
            "orb",
            {"not_a_real_orb_key": 5},
            {"or_minutes": 30},
        )


def test_runtime_config_merge_applies_supported_orb_changes() -> None:
    merged = _runtime_config_for_registered_strategy(
        "orb",
        {"rr_ratio": 3.0},
        {"or_minutes": 30, "rr_ratio": 2.0},
    )
    assert merged == {"or_minutes": 30, "rr_ratio": 3.0}
