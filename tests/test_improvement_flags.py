"""Unit tests for improvement_flags.

The flags must default to OFF — that is the only invariant that
guarantees byte-identical behavior to today.
"""

from __future__ import annotations

import pytest

from autoresearch_constants import (
    ENV_IMPROVEMENT_HALO,
    ENV_IMPROVEMENT_HALO_APPLY,
    ENV_IMPROVEMENT_RATCHET,
    ENV_IMPROVEMENT_REFLEXION,
)
from improvement_flags import (
    KNOWN_FLAGS,
    enabled,
    halo_apply_enabled,
    halo_enabled,
    ratchet_enabled,
    reflexion_enabled,
)


@pytest.fixture(autouse=True)
def _clear_flag_env(monkeypatch):
    for name in KNOWN_FLAGS:
        monkeypatch.delenv(name, raising=False)
    yield


def test_all_flags_default_off():
    for name in KNOWN_FLAGS:
        assert enabled(name) is False, f"{name} must default to off"


def test_each_helper_default_off():
    assert halo_enabled() is False
    assert halo_apply_enabled() is False
    assert reflexion_enabled() is False
    assert ratchet_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "Yes", "on", "ON"])
def test_truthy_strings_enable(monkeypatch, value):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, value)
    assert halo_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "maybe"])
def test_falsy_strings_disable(monkeypatch, value):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO, value)
    assert halo_enabled() is False


def test_unknown_flag_raises():
    with pytest.raises(ValueError, match="unknown improvement flag"):
        enabled("AUTORESEARCH_NOT_A_REAL_FLAG")


def test_each_flag_independently_routable(monkeypatch):
    # Setting one flag must not enable any other.
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    assert reflexion_enabled() is True
    assert halo_enabled() is False
    assert halo_apply_enabled() is False
    assert ratchet_enabled() is False


def test_known_flags_constant_covers_all_four():
    assert set(KNOWN_FLAGS) == {
        ENV_IMPROVEMENT_HALO,
        ENV_IMPROVEMENT_HALO_APPLY,
        ENV_IMPROVEMENT_REFLEXION,
        ENV_IMPROVEMENT_RATCHET,
    }
