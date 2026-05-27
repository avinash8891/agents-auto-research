"""Tests for Stage 1 sub-section reorganization + rejection_code namespacing.

Stage 1 is split into three helpers, each owning one category of rules:
  _validate_structural        — missing fields / schema invariants
  _validate_thesis_quality    — pattern of reasoning (B1/B2/B3/B5, banned
                                 language, dimension novelty, repeated id)
  _validate_config_validity   — base_config_path, Jaccard overlap, metadata
                                 leak, neighboring threshold (L5)

Each helper raises ThesisValidationError with rejection_code prefixed by its
section name (`structural_*`, `thesis_quality_*`, `config_validity_*`).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import thesis_validator
from thesis_validator import (
    ThesisValidationError,
    infer_rejection_code,
    validate_stage_1,
    validate_thesis_dict,
)


def _base_thesis(thesis_id: str = "subsection_test") -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Filter setups by minimum opening volatility to avoid noise.",
        "mechanism": "Low-volatility opens have weaker microstructure signals.",
        "mechanism_dimension": "signal_quality",
        "dimension_novelty": (
            "This tests a volatility floor on the alert candle, a fundamentally "
            "different lever than entry-time gating which has been explored before."
        ),
        "config_changes": {"alert_min_atr_pct": 0.001},
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_volatility_separation",
                "condition": (
                    "If average bar-volatility quintile PF spread is below 0.2, "
                    "the volatility-quality mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "theme_keywords": ["volatility_floor"],
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Higher-volatility setups follow through more reliably.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "Filtering reduces but does not collapse counts.",
            },
        ],
        # Required field post-refactor; doctrine was always there but the
        # validator only enforced length when set, not presence.
        "falsification_or_alternative": (
            "If high-volatility setups do not show higher PF than low-volatility ones, "
            "the volatility-quality mechanism does not hold."
        ),
    }


def _prior(thesis_id: str, *, theme_keywords: list[str]) -> dict:
    return {
        "thesis_id": thesis_id,
        "config_changes": {f"key_{thesis_id}": True},
        "outcome": "compiled",
        "mechanism_dimension": "signal_quality",
        "thesis_details": {"theme_keywords": theme_keywords},
    }


# ── Helper existence + ordering ──────────────────────────────────────────


def test_validate_stage_1_dispatches_to_three_named_helpers_in_order() -> None:
    """validate_stage_1 calls structural → thesis_quality → config_validity, in that order."""
    raw = _base_thesis()
    validated = validate_thesis_dict(raw)

    call_order: list[str] = []

    def record(name: str):
        def _wrapper(*args, **kwargs):
            call_order.append(name)

        return _wrapper

    with (
        patch.object(thesis_validator, "_validate_structural", side_effect=record("structural")),
        patch.object(
            thesis_validator, "_validate_thesis_quality", side_effect=record("thesis_quality")
        ),
        patch.object(
            thesis_validator, "_validate_config_validity", side_effect=record("config_validity")
        ),
    ):
        validate_stage_1(validated, prior_theses=[])

    assert call_order == ["structural", "thesis_quality", "config_validity"]


def test_validate_stage_1_helpers_are_module_level_functions() -> None:
    """The three helpers must exist as importable callables on the module."""
    assert callable(getattr(thesis_validator, "_validate_structural", None))
    assert callable(getattr(thesis_validator, "_validate_thesis_quality", None))
    assert callable(getattr(thesis_validator, "_validate_config_validity", None))


# ── Structural section: at least one rejection in the namespace ──────────


def test_structural_section_rejects_missing_thesis_id_with_prefixed_code() -> None:
    raw = _base_thesis()
    raw["thesis_id"] = ""
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw)
    assert excinfo.value.rejection_code == "structural_missing_thesis_id"


def test_structural_section_rejects_missing_mechanism_with_prefixed_code() -> None:
    raw = _base_thesis()
    raw["mechanism"] = ""
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw)
    assert excinfo.value.rejection_code.startswith("structural_")


# ── Thesis-quality section: prefixed codes ───────────────────────────────


def test_thesis_quality_section_rejects_theme_cluster_fixation_with_prefixed_code() -> None:
    prior = [
        _prior("p1", theme_keywords=["volatility_floor"]),
        _prior("p2", theme_keywords=["other_a"]),
        _prior("p3", theme_keywords=["volatility_floor"]),
        _prior("p4", theme_keywords=["other_b"]),
        _prior("p5", theme_keywords=["volatility_floor"]),
        _prior("p6", theme_keywords=["other_c"]),
    ]
    raw = _base_thesis("p7")
    raw["theme_keywords"] = ["volatility_floor"]
    raw["causal_cluster"] = "alert-candle volatility filter"
    raw["underexplored_dimensions_considered"] = [
        "portfolio_construction",
        "regime_conditioning",
    ]
    # 3 of 6 priors share `volatility_floor` → computed overlap = "high"
    # (≥50%). The structural novel_connection gate would fire before the
    # theme_quality gate under test. Provide a substantive novel_connection
    # so the gate-of-interest is reached.
    raw["novel_connection"] = (
        "Recasts the volatility floor as a regime-detection signal rather than "
        "an absolute threshold, which prior theses on this theme did not test."
    )

    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw, prior_theses=prior)

    assert excinfo.value.rejection_code == "thesis_quality_theme_cluster_fixation"


def test_structural_section_rejects_repeated_thesis_id_with_prefixed_code() -> None:
    prior = [_prior("repeated_id", theme_keywords=["x"])]
    raw = _base_thesis("repeated_id")
    raw["theme_keywords"] = ["unrelated_topic"]
    raw["causal_cluster"] = "unrelated"
    raw["underexplored_dimensions_considered"] = ["portfolio_construction", "regime_conditioning"]

    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw, prior_theses=prior)

    assert excinfo.value.rejection_code == "structural_thesis_id_repeated"


# ── Config-validity section: prefixed codes ──────────────────────────────


def test_config_validity_section_rejects_jaccard_overlap_with_prefixed_code() -> None:
    """Jaccard overlap fires for non-trivial key overlap when no shared numeric
    key falls within the neighboring-threshold band.

    Gate ordering note: `_check_neighboring_threshold` now runs BEFORE the
    Jaccard check so single-key value-tweak theses get the more specific
    finding. To isolate Jaccard, the shared numeric key here uses a value
    well outside the 2x neighboring band (10.0 → 2.5 = 0.25x), so the
    neighboring gate passes and Jaccard fires next.
    """
    raw = _base_thesis("dup_keys")
    raw["theme_keywords"] = ["unrelated"]
    raw["causal_cluster"] = "unrelated"
    raw["underexplored_dimensions_considered"] = ["portfolio_construction", "regime_conditioning"]
    raw["config_changes"] = {"entry_cutoff_time": "10:00", "rr_ratio": 2.5}
    prior = [
        {
            "thesis_id": "prior_dup",
            # rr_ratio prior=10.0, current=2.5 → ratio 0.25 (outside [0.5, 2.0]
            # band) → neighboring threshold gate doesn't fire on this key.
            # entry_cutoff_time is non-numeric so doesn't trip neighboring.
            # Both keys still 100% shared → Jaccard fires.
            "config_changes": {"entry_cutoff_time": "09:45", "rr_ratio": 10.0},
            "mechanism_dimension": "entry_timing",
            "thesis_details": {"theme_keywords": ["other"]},
        }
    ]

    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw, prior_theses=prior)

    assert excinfo.value.rejection_code == "config_validity_config_key_overlap_real"


def test_config_validity_section_rejects_runtime_base_config_path_with_prefixed_code() -> None:
    """The base_config_path runtime/ check is in the config-validity section."""
    from thesis_validator import _validate_base_config_path

    with pytest.raises(ThesisValidationError) as excinfo:
        _validate_base_config_path("runtime/jobs/job-25/runtime_config.json")

    assert excinfo.value.rejection_code == "config_validity_base_config_path_runtime_construction"


def test_config_validity_section_rejects_metadata_leak_with_prefixed_code() -> None:
    raw = _base_thesis("metadata_leak")
    raw["config_changes"] = {"requires_code_change": True}

    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw, prior_theses=[])

    assert excinfo.value.rejection_code == "config_validity_config_changes_metadata_leak"


# ── infer_rejection_code returns prefixed codes ──────────────────────────


def test_infer_rejection_code_structural_section() -> None:
    assert infer_rejection_code("Missing thesis_id") == "structural_missing_thesis_id"
    assert infer_rejection_code("Missing hypothesis") == "structural_missing_hypothesis"
    assert infer_rejection_code("Missing mechanism") == "structural_missing_mechanism"


def test_infer_rejection_code_thesis_quality_section() -> None:
    assert (
        infer_rejection_code(
            "Theme-cluster fixation: 4 of last 7 theses share keywords ['x'] (overlapping...)"
        )
        == "thesis_quality_theme_cluster_fixation"
    )


def test_infer_rejection_code_config_validity_section() -> None:
    assert (
        infer_rejection_code("Config-key overlap: shared keys ...")
        == "config_validity_config_key_overlap_real"
    )
    assert (
        infer_rejection_code(
            "base_config_path 'runtime/...' points into runtime/. Do not construct..."
        )
        == "config_validity_base_config_path_runtime_construction"
    )
    assert (
        infer_rejection_code("config_changes contains thesis metadata key 'requires_code_change'")
        == "config_validity_config_changes_metadata_leak"
    )


# ── Escalation directive uses the new prefixed code ──────────────────────


def test_compute_escalation_directive_triggers_on_prefixed_thesis_quality_code(tmp_path) -> None:
    """compute_escalation_directive must key on thesis_quality_theme_cluster_fixation."""
    from rejection_artifact import compute_escalation_directive, write_rejection
    from research_types import StructuredRejection

    for r in range(1, 5):
        write_rejection(
            tmp_path,
            job=7,
            rejection=StructuredRejection(
                rejected_at="2026-05-09T13:45:22Z",
                round=r,
                thesis_id=f"t{r}",
                stage="stage_1",
                rejection_code="thesis_quality_theme_cluster_fixation",
                rule_violated="",
                evidence={},
                remediation_hint="",
            ),
        )

    out = compute_escalation_directive(tmp_path, job=7)
    assert "ESCALATION" in out
    assert "thesis_quality_theme_cluster_fixation" in out


def test_compute_escalation_directive_does_not_trigger_on_legacy_unprefixed_code(
    tmp_path,
) -> None:
    """Legacy unprefixed `theme_cluster_fixation` codes must NOT trigger the
    escalation — the directive only counts the new prefixed namespace."""
    from rejection_artifact import compute_escalation_directive, write_rejection
    from research_types import StructuredRejection

    for r in range(1, 7):
        write_rejection(
            tmp_path,
            job=7,
            rejection=StructuredRejection(
                rejected_at="2026-05-09T13:45:22Z",
                round=r,
                thesis_id=f"legacy{r}",
                stage="stage_1",
                rejection_code="theme_cluster_fixation",
                rule_violated="",
                evidence={},
                remediation_hint="",
            ),
        )

    out = compute_escalation_directive(tmp_path, job=7)
    assert out == ""
