"""Tests for StrategyFamily variant-prefix helpers added in PR 5.

These pin the family-aware behavior that the planner will rely on after
the orb_/ema_ hardcoding is removed.
"""

from __future__ import annotations

from strategy_family import StrategyFamily, load_family


def test_orb_family_has_orb_prefix_and_default_variants() -> None:
    fam = load_family("orb")
    assert fam.variant_prefix == "orb_"
    assert "configs/variants/orb_spy_only.yaml" in fam.default_variants
    assert "configs/variants/orb_trailing_stop.yaml" in fam.default_variants


def test_ema_family_has_ema_prefix_and_no_default_variants_yet() -> None:
    fam = load_family("ema")
    assert fam.variant_prefix == "ema_"
    assert fam.default_variants == ()


def test_baseline_config_path_uses_base_config_filename() -> None:
    assert load_family("orb").baseline_config_path == "configs/orb_base.yaml"
    assert load_family("ema").baseline_config_path == "configs/ema_base.yaml"


def test_variant_config_path_uses_family_prefix() -> None:
    assert (
        load_family("orb").variant_config_path("spy_only") == "configs/variants/orb_spy_only.yaml"
    )
    assert (
        load_family("ema").variant_config_path("aggressive")
        == "configs/variants/ema_aggressive.yaml"
    )


def test_slug_from_config_strips_family_prefix() -> None:
    orb = load_family("orb")
    assert orb.slug_from_config("configs/variants/orb_spy_only.yaml") == "spy_only"
    # Non-prefixed input passes through unchanged.
    assert orb.slug_from_config("configs/variants/something_else.yaml") == "something_else"

    ema = load_family("ema")
    assert ema.slug_from_config("configs/variants/ema_aggressive.yaml") == "aggressive"
    # ema family does NOT strip orb_ — that's a different family's prefix.
    assert ema.slug_from_config("configs/variants/orb_spy_only.yaml") == "orb_spy_only"


def test_strategy_family_default_variant_prefix_keeps_back_compat() -> None:
    """A StrategyFamily constructed with only required fields must have a
    sensible default variant_prefix so existing call sites do not break."""
    fam = StrategyFamily(name="x", benchmark_script="x.py")
    assert fam.variant_prefix == "orb_"
    assert fam.default_variants == ()
