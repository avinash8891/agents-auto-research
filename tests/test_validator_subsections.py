"""Tests for Stage 1 rejection_code namespacing.

The live validator routes all Stage 1 checks through validate_research_thesis.
Rejection codes stay prefixed by rule family (`structural_*`,
`thesis_quality_*`, `config_validity_*`) even though private compatibility
entry points no longer exist.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import thesis_validator
from backtest_run_db import research_thesis_attempt_id
from thesis_validator import VALID_PROCESS_TOOLS as _VALID_PROCESS_TOOLS
from thesis_validator import (
    ThesisValidationError,
    infer_rejection_code,
)
from thesis_validator import validate_research_thesis as _validate_research_thesis
from thesis_validator import validate_thesis_dict as _validate_thesis_dict


def validate_research_thesis(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    return _validate_research_thesis(*args, **kwargs)


def validate_thesis_dict(*args: object, **kwargs: object) -> object:
    kwargs.setdefault("tools_called", _VALID_PROCESS_TOOLS)
    kwargs.setdefault("research_round_id", "job-test-round-1")
    kwargs.setdefault("attempt_number", 1)
    kwargs.setdefault("assign_thesis_id", research_thesis_attempt_id)
    return _validate_thesis_dict(*args, **kwargs)


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
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": (
                    "This would change risk after entry rather than test whether "
                    "opening volatility separates signal quality."
                ),
            },
            {
                "mechanism": "later session entry filter",
                "why_rejected": (
                    "This would test time-of-day effects rather than the volatility "
                    "quality mechanism."
                ),
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "Volatility filters are common in entries."},
            {"source": "analyst", "citation": "round-1 analyst found PF varied by volatility."},
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:generate_signals_for_frame builds EMA entry signals."
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


def test_validate_research_thesis_dispatches_to_tier_helpers_in_order() -> None:
    """validate_research_thesis calls process -> behavioral -> mechanical tiers in order."""
    raw = _base_thesis()
    validated = validate_thesis_dict(raw)

    call_order: list[str] = []

    def record(name: str):
        def _wrapper(*args, **kwargs):
            call_order.append(name)

        return _wrapper

    with (
        patch.object(thesis_validator, "_validate_process", side_effect=record("process")),
        patch.object(thesis_validator, "_run_behavioral_pass", side_effect=record("behavioral")),
        patch.object(
            thesis_validator,
            "_collect_mechanical_failures",
            side_effect=lambda *args, **kwargs: record("mechanical_collect")(),
        ),
        patch.object(
            thesis_validator,
            "_raise_mechanical_batch",
            side_effect=record("mechanical_raise"),
        ),
    ):
        validate_research_thesis(validated, prior_theses=[], tools_called=_VALID_PROCESS_TOOLS)

    assert call_order == ["process", "behavioral", "mechanical_collect", "mechanical_raise"]


def test_validate_research_thesis_helpers_are_module_level_functions() -> None:
    """The tier helpers must exist as importable callables on the module."""
    assert callable(getattr(thesis_validator, "_validate_process", None))
    assert callable(getattr(thesis_validator, "_run_behavioral_pass", None))
    assert callable(getattr(thesis_validator, "_collect_mechanical_failures", None))
    assert callable(getattr(thesis_validator, "_raise_mechanical_batch", None))


# ── Structural section: at least one rejection in the namespace ──────────


def test_structural_section_assigns_missing_llm_thesis_id() -> None:
    raw = _base_thesis()
    raw["thesis_id"] = ""
    validated = validate_thesis_dict(raw)
    assert validated.thesis_id == "job-test-round-1-attempt-1"


def test_structural_section_rejects_missing_mechanism_with_prefixed_code() -> None:
    raw = _base_thesis()
    raw["mechanism"] = ""
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw)
    assert excinfo.value.rejection_code.startswith("structural_")


# ── Thesis-quality section: prefixed codes ───────────────────────────────


def test_thesis_quality_section_no_longer_rejects_theme_cluster_fixation() -> None:
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

    obj = validate_thesis_dict(raw, prior_theses=prior)
    assert obj.thesis_id == "job-test-round-1-attempt-1"


def test_structural_section_ignores_repeated_llm_thesis_id() -> None:
    prior = [_prior("repeated_id", theme_keywords=["x"])]
    raw = _base_thesis("repeated_id")
    raw["theme_keywords"] = ["unrelated_topic"]
    raw["causal_cluster"] = "unrelated"
    raw["underexplored_dimensions_considered"] = ["portfolio_construction", "regime_conditioning"]

    validated = validate_thesis_dict(raw, prior_theses=prior)
    assert validated.thesis_id == "job-test-round-1-attempt-1"


# ── Config-validity section: prefixed codes ──────────────────────────────


def test_config_validity_section_rejects_jaccard_overlap_with_prefixed_code() -> None:
    """Jaccard overlap fires for non-trivial key overlap when no shared numeric
    key falls within the neighboring-threshold band.

    Gate ordering note: the behavioral pass checks neighboring-threshold
    before Jaccard overlap so single-key value-tweak theses get the more
    specific finding. To isolate Jaccard, the shared numeric key here uses
    a value well outside the 2x neighboring band (10.0 → 2.5 = 0.25x), so
    the neighboring gate passes and Jaccard fires next.
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


def test_infer_rejection_code_unknown_retired_thesis_quality_message() -> None:
    assert (
        infer_rejection_code(
            "Theme-cluster fixation: 4 of last 7 theses share keywords ['x'] (overlapping...)"
        )
        == "unspecified_validation_error"
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
