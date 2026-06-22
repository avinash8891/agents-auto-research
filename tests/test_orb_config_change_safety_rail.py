from __future__ import annotations

from strategies import STRATEGIES
from strategies.orb.defaults import _get_orb_defaults
from strategies.orb.validate import supported_orb_runtime_keys, validate_orb_runtime_config


def test_validator_rejects_unsupported_config_key() -> None:
    violations = validate_orb_runtime_config({"or_minutes": 20, "bogus_unknown_key": 1})
    assert any(
        v.startswith("Unsupported ORB runtime config keys: bogus_unknown_key") for v in violations
    )


def test_validator_accepts_supported_and_metadata_keys() -> None:
    # research_engine is an ORB default (not in the curated SUPPORTED list) — it
    # must NOT be flagged, proving the supported set is derived from defaults and
    # cannot drift the way a hand-maintained list did.
    config = {"or_minutes": 30, "rr_ratio": 3.0, "research_engine": {}, "family": "orb"}
    violations = validate_orb_runtime_config(config)
    assert not any(v.startswith("Unsupported ORB runtime config keys") for v in violations)


def test_orb_defaults_are_all_in_the_supported_set() -> None:
    assert set(_get_orb_defaults()) <= supported_orb_runtime_keys()


def test_validator_rejects_unknown_regime_value() -> None:
    violations = validate_orb_runtime_config({"skip_regimes": ["not-a-real-regime"]})
    assert any(v.startswith("Unsupported regime value: not-a-real-regime") for v in violations)


def test_validator_accepts_known_regime_value() -> None:
    violations = validate_orb_runtime_config({"require_regimes": ["narrow-OR"]})
    assert not any("regime value" in v for v in violations)


def test_orb_strategy_validate_runtime_config_routes_to_validator() -> None:
    violations = STRATEGIES["orb"].validate_runtime_config({"definitely_not_a_key": 1})
    assert any(v.startswith("Unsupported ORB runtime config keys") for v in violations)
