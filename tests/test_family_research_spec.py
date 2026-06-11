from __future__ import annotations

from family_research_spec import proposed_change_is_single_or_coupled


def test_proposed_change_single_key_passes_for_any_family() -> None:
    assert proposed_change_is_single_or_coupled("ema", {"gap_filter": True}) is True


def test_proposed_change_rejects_undeclared_multi_key_change() -> None:
    assert (
        proposed_change_is_single_or_coupled(
            "ema",
            {"gap_filter": True, "gap_pct": 0.01},
        )
        is False
    )


def test_proposed_change_allows_declared_family_coupled_pair() -> None:
    assert (
        proposed_change_is_single_or_coupled(
            "orb",
            {"use_volatility_trail": True, "vol_trail_atr_mult": 1.2},
        )
        is True
    )


def test_proposed_change_rejects_unsupported_orb_trail_alias() -> None:
    assert (
        proposed_change_is_single_or_coupled(
            "orb",
            {"use_volatility_trail": True, "trail_atr_multiple": 1.2},
        )
        is False
    )
