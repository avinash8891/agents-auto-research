"""Tests for the neighboring-threshold detector (live on the mechanism path).

Reject when proposal A sets X=0.005 and prior B set X=0.01 — same key with a
small numeric delta is parameter tuning, not a new mechanism. The detector is
consumed by `autoresearch_research._validate_mechanism_dedupe`.
"""

from __future__ import annotations

from types import SimpleNamespace

from thesis_validator import _detect_neighboring_threshold


def _thesis_view(config_changes: dict) -> SimpleNamespace:
    return SimpleNamespace(config_changes=config_changes)


def _prior(thesis_id: str, config_changes: dict) -> dict:
    return {
        "thesis_id": thesis_id,
        "config_changes": config_changes,
        "outcome": "compiled",
    }


def test_detector_fires_on_same_key_small_numeric_delta() -> None:
    signal = _detect_neighboring_threshold(
        _thesis_view({"gap_exclude_pct": 0.005}),
        [_prior("prior_gap_filter", {"gap_exclude_pct": 0.01})],
    )

    assert signal is not None
    assert signal.code == "config_validity_neighboring_threshold"
    assert "gap_exclude_pct" in signal.summary


def test_detector_allows_large_numeric_ratio() -> None:
    signal = _detect_neighboring_threshold(
        _thesis_view({"gap_exclude_pct": 0.10}),
        [_prior("prior_gap_filter", {"gap_exclude_pct": 0.01})],
    )

    assert signal is None


def test_detector_allows_disjoint_keys() -> None:
    signal = _detect_neighboring_threshold(
        _thesis_view({"opening_skip_minutes": 5}),
        [_prior("prior_gap_filter", {"gap_exclude_pct": 0.01})],
    )

    assert signal is None


def test_detector_ignores_non_numeric_values() -> None:
    signal = _detect_neighboring_threshold(
        _thesis_view({"require_regimes": ["wide-OR"]}),
        [_prior("prior_regimes", {"require_regimes": ["narrow-OR"]})],
    )

    assert signal is None
