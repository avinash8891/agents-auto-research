from __future__ import annotations

from backtest_run_db import BacktestRunDB
from thesis_validator import (
    config_key_overlap,
    infer_rejection_code,
    load_prior_theses,
)


def test_load_prior_theses_reads_runtime_root_when_split_from_code_root(
    tmp_path, monkeypatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime-home"
    code_root.mkdir()
    runtime_root.mkdir()
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(runtime_root))
    db = BacktestRunDB(runtime_root / "ema_backtest_runs.db")
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-1-round-1",
            "attempt_number": 1,
            "thesis_id": "ema-runtime-root",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 10},
            "validator_status": "compiled",
            "mechanism_dimension": "entry_timing",
            "hypothesis": "EMA crossover predicts short-term momentum",
            "mechanism": "Trend persistence after crossover is the causal driver",
            "thesis_details": {},
            "created_at_utc": "2026-05-01T00:00:00+00:00",
        }
    )

    priors = load_prior_theses(code_root)

    assert [prior["thesis_id"] for prior in priors] == ["ema-runtime-root"]


def test_load_prior_theses_filters_by_strategy_family(tmp_path) -> None:
    ema_db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    ema_db.add_research_thesis_attempt(
        {
            "research_round_id": "job-1-round-1",
            "attempt_number": 1,
            "thesis_id": "ema-prior",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 10},
            "validator_status": "compiled",
            "mechanism_dimension": "entry_timing",
            "hypothesis": "EMA crossover predicts short-term momentum",
            "mechanism": "Trend persistence after crossover is the causal driver",
            "thesis_details": {},
            "created_at_utc": "2026-05-01T00:00:00+00:00",
        }
    )
    orb_db = BacktestRunDB(tmp_path / "orb_backtest_runs.db")
    orb_db.add_research_thesis_attempt(
        {
            "research_round_id": "job-1-round-1",
            "attempt_number": 1,
            "thesis_id": "orb-prior",
            "strategy_family": "orb",
            "config_changes": {"opening_skip_minutes": 5},
            "validator_status": "compiled",
            "mechanism_dimension": "entry_timing",
            "hypothesis": "Opening range momentum persists after impulse bars",
            "mechanism": "Opening impulse continuation is the causal driver",
            "thesis_details": {},
            "created_at_utc": "2026-05-01T00:00:00+00:00",
        }
    )

    priors = load_prior_theses(tmp_path, strategy_family="ema")

    assert [prior["thesis_id"] for prior in priors] == ["ema-prior"]


def test_config_key_overlap_ignores_engine_change_sentinel_only() -> None:
    is_duplicate, reason = config_key_overlap(
        {"requires_engine_change": True},
        [
            {
                "thesis_id": "prior_engine_change",
                "config_changes": {"requires_engine_change": True},
            }
        ],
    )

    assert is_duplicate is False
    assert reason == ""


def test_config_key_overlap_still_rejects_real_overlapping_keys_with_sentinel() -> None:
    is_duplicate, reason = config_key_overlap(
        {"requires_engine_change": True, "entry_cutoff_time": "10:00"},
        [
            {
                "thesis_id": "prior_entry_cutoff",
                "config_changes": {
                    "requires_engine_change": True,
                    "entry_cutoff_time": "09:45",
                },
            }
        ],
    )

    assert is_duplicate is True
    assert "entry_cutoff_time" in reason
    assert "requires_engine_change" not in reason


def test_config_key_overlap_ignores_requires_new_config_keys_sentinel() -> None:
    """Production data showed false 100% overlap on `requires_new_config_keys` sentinel.

    Two unrelated theses both setting `requires_new_config_keys: True` (a metadata
    flag indicating "this thesis needs new config keys built") were flagged as
    100% duplicates. The flag is bookkeeping, not a real config key.
    """
    is_duplicate, reason = config_key_overlap(
        {"requires_new_config_keys": True},
        [
            {
                "thesis_id": "prior_thesis_with_same_sentinel",
                "config_changes": {"requires_new_config_keys": True},
            }
        ],
    )

    assert is_duplicate is False, f"sentinel-only theses should not overlap; got: {reason}"
    assert reason == ""


def test_config_key_overlap_ignores_requires_engine_change_prefix_variants() -> None:
    """Production data showed false 100% overlap on `requires_engine_change__<descriptor>`.

    Two theses both setting `requires_engine_change__<same_descriptor>: True` as
    their sole config_change get flagged as duplicates. The descriptor is a label
    for what engine change is needed, not a real config key.
    """
    is_duplicate, reason = config_key_overlap(
        {"requires_engine_change__bucket_stop_distance_filter": True},
        [
            {
                "thesis_id": "prior_with_same_engine_change_label",
                "config_changes": {
                    "requires_engine_change__bucket_stop_distance_filter": True,
                },
            }
        ],
    )

    assert (
        is_duplicate is False
    ), f"requires_engine_change__* prefix is a sentinel; should not overlap; got: {reason}"
    assert reason == ""


def test_config_key_overlap_compares_nested_engine_change_keys() -> None:
    is_duplicate, reason = config_key_overlap(
        {
            "requires_engine_change": True,
            "new_config_keys_needed": {
                "entry_confirmation_mode": "close_beyond_break",
                "entry_acceptance_buffer_pct": 0.0001,
            },
        },
        [
            {
                "thesis_id": "momentum_gated_trailing_activation",
                "config_changes": {
                    "requires_engine_change": True,
                    "new_config_keys_needed": {
                        "momentum_activation_enabled": True,
                        "trail_activation_r": 1.5,
                    },
                },
            }
        ],
    )

    assert is_duplicate is False
    assert reason == ""


def test_config_key_overlap_rejects_same_nested_engine_change_key() -> None:
    is_duplicate, reason = config_key_overlap(
        {
            "requires_engine_change": True,
            "new_config_keys_needed": {
                "entry_confirmation_mode": "close_beyond_break",
            },
        },
        [
            {
                "thesis_id": "prior_entry_confirmation",
                "config_changes": {
                    "requires_engine_change": True,
                    "new_config_keys_needed": {
                        "entry_confirmation_mode": "touch_then_close",
                    },
                },
            }
        ],
    )

    assert is_duplicate is True
    assert "new_config_keys_needed.entry_confirmation_mode" in reason


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
