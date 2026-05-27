from __future__ import annotations

import pytest

from thesis_validator import (
    ThesisValidationError,
    check_hypothesis_alignment,
    config_key_overlap,
    generate_variants,
    validate_thesis_dict,
)


def _base_engine_change_thesis(thesis_id: str, dimension: str) -> dict:
    return {
        "thesis_id": thesis_id,
        "strategy_family": "ema",
        "hypothesis": "Require close confirmation to reduce intrabar false-break entries.",
        "mechanism": "A close-confirmed entry gate should avoid wick-only stop triggers.",
        "mechanism_dimension": dimension,
        "dimension_novelty": (
            "This tests confirmation logic in engine behavior, not a numeric "
            "parameter variation of a previous thesis."
        ),
        "causal_cluster": "close-confirmed adverse-selection reduction",
        "dominant_cluster_overlap": "medium",
        "underexplored_dimensions_considered": [
            "portfolio_construction",
            "regime_conditioning",
        ],
        "novel_connection": (
            "Connects wick-only false-break evidence with engine-level close "
            "confirmation rather than another nearby threshold change."
        ),
        "closest_prior_theses_considered": ["prior_signal_quality_baseline"],
        "orthogonality_defense": (
            "This thesis changes engine logic for entry confirmation, a different "
            "lever family than threshold-tuning the existing entry timing."
        ),
        "evidence_strength": "mixed",
        "alternatives_considered": [
            {
                "mechanism": "wider stop-distance cap",
                "why_rejected": "would not address the wick-only false-break root cause directly",
            },
            {
                "mechanism": "session-time entry filter",
                "why_rejected": "is a proxy for the wick problem rather than the structural fix",
            },
        ],
        "evidence_citations": [
            {"source": "web_search", "citation": "Cont et al on order-flow imbalance"},
            {
                "source": "analyst",
                "citation": "round-3 analyst: wick-only stop-outs are 37% of total stops",
            },
        ],
        "source_code_verification": (
            "strategies/ema/signals.py:detect_pullback contains the entry filter; "
            "the close-confirmation gate would be added here as an additional check."
        ),
        "config_changes": {"requires_engine_change": True},
        "expected_effects": [
            {
                "metric": "profit_factor",
                "direction": "increase",
                "rationale": "Fewer false-break entries should improve realized edge.",
            },
            {
                "metric": "trade_count",
                "direction": "decrease_or_same",
                "rationale": "Tighter entry filtering should not collapse trade frequency.",
            },
        ],
        "disqualifiers": [
            {
                "name": "trade_count_collapse",
                "condition": "trade_count decreases by more than 50 percent",
                "severity": "hard_fail",
                "kind": "metric_threshold",
            },
            {
                "name": "no_close_confirmation_separation",
                "condition": (
                    "If wick-only stop-out rate is no lower for close-confirmed entries, "
                    "the close-confirmation mechanism does not hold."
                ),
                "severity": "hard_fail",
                "kind": "mechanism_evidence",
            },
        ],
        "requires_code_change": True,
        "requested_primitives": ["close_confirmed_entry_gate"],
        "why_not_overfit": "Mechanism is structural and evaluated across all train years.",
        # falsification_or_alternative is unconditionally required by the
        # validator (was optional pre-refactor; doctrine never enforced).
        "falsification_or_alternative": (
            "If close-confirmed entries show the same wick-only stop-out rate as "
            "intrabar entries, the close-confirmation mechanism does not hold."
        ),
    }


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


def test_validate_base_config_path_rejects_runtime_paths_with_clear_hint() -> None:
    """Production agent constructed paths like `runtime/jobs/job-25/experiments/.../runtime_config.json`.

    The error must explicitly flag runtime/ paths and tell the agent not to
    construct paths from runtime artifacts.
    """
    from thesis_validator import _validate_base_config_path

    bad_path = "runtime/jobs/job-25/experiments/open_subregime/runtime_config.json"
    with pytest.raises(ThesisValidationError) as excinfo:
        _validate_base_config_path(bad_path)
    msg = str(excinfo.value)
    assert "runtime/" in msg, f"error must mention runtime/ explicitly; got: {msg}"
    assert (
        "construct" in msg.lower() or "do not" in msg.lower()
    ), f"error must instruct the agent not to construct paths; got: {msg}"


def test_validate_base_config_path_accepts_configs_path() -> None:
    from thesis_validator import _validate_base_config_path

    _validate_base_config_path("configs/ema_base.yaml")  # should not raise


# NOTE: A historical rejection cited shared keys ['new_config_keys_needed'] as
# a sentinel-only false positive. The forward fix lives upstream of overlap:
# `validate_thesis_dict` rejects `new_config_keys_needed` at top-level of
# `config_changes` via the CONFIG_CHANGES_METADATA_KEYS check (covered by
# `test_validate_thesis_rejects_new_config_keys_needed_inside_config_changes`).
# When `new_config_keys_needed` is a parent dict of real config keys, recursion
# into its children is intentional (covered by
# `test_config_key_overlap_rejects_same_nested_engine_change_key`).


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


def test_validate_thesis_dict_requires_requested_primitives_for_code_change() -> None:
    thesis = _base_engine_change_thesis("missing_primitives", "signal_quality")
    thesis["requested_primitives"] = []

    with pytest.raises(
        ThesisValidationError, match="requires_code_change theses must declare requested_primitives"
    ):
        validate_thesis_dict(thesis)


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


def test_validate_engine_change_thesis_not_rejected_for_sentinel_overlap() -> None:
    thesis = _base_engine_change_thesis("close_confirmed_break_entry_gate", "signal_quality")
    prior = [
        {
            "thesis_id": "time_of_day_slippage_model_open_penalty",
            "config_changes": {"requires_engine_change": True},
            "mechanism_dimension": "market_microstructure",
        }
    ]

    validated = validate_thesis_dict(thesis, prior_theses=prior)

    assert validated.thesis_id == "close_confirmed_break_entry_gate"


def test_validate_thesis_rejects_metadata_keys_inside_config_changes() -> None:
    thesis = _base_engine_change_thesis("metadata_leaked_to_config_changes", "signal_quality")
    thesis["config_changes"] = {
        "requires_code_change": True,
    }

    with pytest.raises(
        ThesisValidationError,
        match="config_changes contains thesis metadata key 'requires_code_change'",
    ):
        validate_thesis_dict(thesis, prior_theses=[])


def test_validate_thesis_rejects_new_config_keys_needed_inside_config_changes() -> None:
    thesis = _base_engine_change_thesis("metadata_leaked_new_config_keys", "signal_quality")
    thesis["config_changes"] = {
        "new_config_keys_needed": {
            "entry_confirmation_mode": "close_beyond_break",
        },
    }

    with pytest.raises(
        ThesisValidationError,
        match="config_changes contains thesis metadata key 'new_config_keys_needed'",
    ):
        validate_thesis_dict(thesis, prior_theses=[])


def test_prior_winner_language_alone_no_longer_rejected() -> None:
    """The prior-winner language regex gate (thesis_quality_prior_winner_inheritance_language)
    was removed. Behavioral text detection had high false-negative rate; actual
    inheritance is caught structurally via base_config_path / base_contract_id
    gates. A thesis using formerly-banned language WITHOUT actually setting an
    inheritance path now passes thesis_quality (other gates still apply).

    This test pins the removal so the regex gate is not accidentally
    re-introduced. See `test_validate_thesis_rejects_prior_best_language_with_base_config_path`
    for the case where actual inheritance IS attempted — that still rejects
    via the structural base_config_path check.
    """
    thesis = _base_engine_change_thesis("preserve_best_without_base", "market_microstructure")
    thesis["mechanism"] = (
        "Preserve the current best trailing-stop edge while adding a new execution filter."
    )
    # No base_config_path / base_contract_id set — there is no actual
    # inheritance to detect. Should pass.
    validate_thesis_dict(thesis, prior_theses=[])


def test_validate_thesis_rejects_actual_inheritance_via_base_config_path() -> None:
    """Actual inheritance (structured fields set) is rejected by the
    config_validity layer regardless of mechanism prose. The text-detection
    regex was removed; the structural check is the authoritative enforcement.
    """
    thesis = _base_engine_change_thesis("preserve_best_with_base", "market_microstructure")
    thesis["mechanism"] = (
        "Preserve the current best trailing-stop edge while adding a new execution filter."
    )
    thesis["base_contract_id"] = "05287d64f61f"
    thesis["base_config_path"] = "experiments/05287d64f61f/runtime_config.json"

    with pytest.raises(
        ThesisValidationError,
        match="(must be empty or the family baseline|base_contract_id is not allowed)",
    ):
        validate_thesis_dict(thesis, prior_theses=[])


def test_validate_thesis_rejects_legacy_experiments_base_config_path() -> None:
    thesis = _base_engine_change_thesis("legacy_experiments_base_path", "market_microstructure")
    thesis["base_config_path"] = "experiments/05287d64f61f/runtime_config.json"

    with pytest.raises(ThesisValidationError, match="must be empty or the family baseline"):
        validate_thesis_dict(thesis, prior_theses=[])


def test_validate_thesis_rejects_job_scoped_experiment_base_config_path() -> None:
    thesis = _base_engine_change_thesis("preserve_job_scoped_best", "market_microstructure")
    thesis["mechanism"] = (
        "Preserve the current best trailing-stop edge while adding a new execution filter."
    )
    thesis["base_contract_id"] = "05287d64f61f"
    thesis["base_config_path"] = "runtime/jobs/job-26/experiments/05287d64f61f/runtime_config.json"

    # Stage 1 helper order is structural → thesis_quality → config_validity. The
    # banned-language (thesis_quality) check fires first when both conditions are
    # present; the runtime/ message asserts when only the path is bad.
    with pytest.raises(
        ThesisValidationError,
        match="(Do not construct|current-best/prior-winner inheritance)",
    ):
        validate_thesis_dict(thesis, prior_theses=[])


def test_validate_thesis_accepts_quality_accounting_fields() -> None:
    thesis = _base_engine_change_thesis("quality_accounting_fields", "market_microstructure")
    thesis["closest_prior_theses_considered"] = [
        "prior_opening_whipsaw_filter",
        "prior_trailing_stop_follow_up",
    ]
    thesis["orthogonality_defense"] = (
        "This thesis changes the mechanism family from entry filtering to execution "
        "payoff capture rather than relabeling the same open-whipsaw story."
    )
    thesis["evidence_strength"] = "mixed"
    thesis["thesis_role"] = "orthogonal_discovery"
    thesis["falsification_or_alternative"] = (
        "If the observed improvement disappears when compared to the current best "
        "trailing configuration, the right-tail story is weaker than a simpler "
        "risk-compression alternative."
    )

    validated = validate_thesis_dict(thesis, prior_theses=[])

    assert validated.closest_prior_theses_considered == [
        "prior_opening_whipsaw_filter",
        "prior_trailing_stop_follow_up",
    ]
    assert validated.evidence_strength == "mixed"
    assert validated.thesis_role == "orthogonal_discovery"


def test_validate_thesis_does_not_require_base_config_for_unrelated_best_or_preserve_language() -> (
    None
):
    thesis = _base_engine_change_thesis("best_practices_not_inheritance", "market_microstructure")
    thesis["mechanism"] = (
        "Use market microstructure best practices to preserve capital during noisy open prints."
    )

    validated = validate_thesis_dict(thesis, prior_theses=[])

    assert validated.base_config_path == ""


def test_validate_thesis_wraps_unknown_strategy_family_as_validation_error() -> None:
    thesis = _base_engine_change_thesis("unknown_family", "market_microstructure")
    thesis["strategy_family"] = "does_not_exist"

    with pytest.raises(ThesisValidationError, match="Unknown strategy family"):
        validate_thesis_dict(thesis, prior_theses=[])


def test_validate_real_config_overlap_still_rejected_with_sentinel() -> None:
    thesis = _base_engine_change_thesis("duplicate_entry_cutoff", "entry_timing")
    thesis["config_changes"] = {
        "requires_engine_change": True,
        "entry_cutoff_time": "10:00",
    }
    prior = [
        {
            "thesis_id": "prior_entry_cutoff",
            "config_changes": {
                "requires_engine_change": True,
                "entry_cutoff_time": "09:45",
            },
            "mechanism_dimension": "risk_structure",
        }
    ]

    with pytest.raises(ThesisValidationError, match="Config-key overlap"):
        validate_thesis_dict(thesis, prior_theses=prior)


def test_validate_thesis_requires_diversity_reasoning_when_prior_context_exists() -> None:
    thesis = _base_engine_change_thesis("new_signal_quality_gate", "signal_quality")
    thesis["causal_cluster"] = ""
    thesis["underexplored_dimensions_considered"] = []
    prior = [
        {
            "thesis_id": "prior_opening_gate",
            "config_changes": {"entry_cutoff_time": "09:45"},
            "mechanism_dimension": "entry_timing",
        }
    ]

    with pytest.raises(ThesisValidationError, match="causal_cluster is required"):
        validate_thesis_dict(thesis, prior_theses=prior)


def test_validate_thesis_rejects_high_overlap_without_novel_connection() -> None:
    """Overlap is now COMPUTED from theme_keywords data, not LLM self-reported.

    The thesis's `dominant_cluster_overlap` field is informational only after
    the refactor — the validator computes overlap from theme_keywords vs
    prior theme_keywords. Setup: 100% of priors share at least one keyword
    with the proposal → computed overlap = "high" → novel_connection
    required to be substantive.
    """
    thesis = _base_engine_change_thesis("opening_gate_followup", "signal_quality")
    thesis.update(
        {
            "causal_cluster": "opening-session short adverse-selection filters",
            # Field is ignored by the gate now; left blank to prove the gate
            # fires regardless of what the LLM reports.
            "dominant_cluster_overlap": "",
            "underexplored_dimensions_considered": [
                "portfolio_construction",
                "regime_conditioning",
            ],
            "theme_keywords": ["opening_session", "alpha"],
            "novel_connection": "",
        }
    )
    prior = [
        {
            "thesis_id": "prior_opening_gate",
            "config_changes": {"entry_cutoff_time": "09:45"},
            "mechanism_dimension": "entry_timing",
            "thesis_details": {"theme_keywords": ["opening_session", "beta"]},
        }
    ]

    with pytest.raises(ThesisValidationError, match="novel_connection"):
        validate_thesis_dict(thesis, prior_theses=prior)


def test_validate_thesis_allows_high_overlap_with_novel_connection() -> None:
    thesis = _base_engine_change_thesis("opening_gate_followup", "signal_quality")
    thesis.update(
        {
            "causal_cluster": "opening-session short adverse-selection filters",
            "dominant_cluster_overlap": "",
            "underexplored_dimensions_considered": [
                "portfolio_construction",
                "regime_conditioning",
            ],
            "theme_keywords": ["opening_session", "alpha"],
            "novel_connection": (
                "Connects previously separate opening adverse-selection evidence "
                "with symbol-level confirmation, not another nearby time cutoff."
            ),
        }
    )
    prior = [
        {
            "thesis_id": "prior_opening_gate",
            "config_changes": {"entry_cutoff_time": "09:45"},
            "mechanism_dimension": "entry_timing",
            "thesis_details": {"theme_keywords": ["opening_session", "beta"]},
        }
    ]

    validated = validate_thesis_dict(thesis, prior_theses=prior)

    assert validated.causal_cluster == "opening-session short adverse-selection filters"


def test_validate_emergent_mechanism_dimension_requires_autonomous_definition() -> None:
    thesis = _base_engine_change_thesis("liquidity_decay_dimension", "emergent")
    thesis.update(
        {
            "new_dimension_name": "liquidity_decay",
            "why_existing_dimensions_do_not_fit": (
                "This studies persistence decay after an entry event, not only entry "
                "timing, signal quality, risk sizing, or exit behavior."
            ),
            "mechanism_family_definition": (
                "Liquidity decay mechanisms test whether the market impact and "
                "fill-quality environment around recent activity changes future edge."
            ),
            "expected_reuse_across_future_theses": (
                "Future theses can reuse this dimension for decay windows, queue "
                "recovery, and post-event liquidity normalization."
            ),
        }
    )

    validated = validate_thesis_dict(thesis)

    assert validated.mechanism_dimension == "emergent"
    assert validated.new_dimension_name == "liquidity_decay"


def test_validate_emergent_mechanism_dimension_rejects_missing_definition() -> None:
    thesis = _base_engine_change_thesis("undefined_new_dimension", "emergent")
    thesis["new_dimension_name"] = "liquidity_decay"

    with pytest.raises(ThesisValidationError, match="why_existing_dimensions_do_not_fit"):
        validate_thesis_dict(thesis)


def test_validate_reuses_prior_emergent_dimension_name() -> None:
    thesis = _base_engine_change_thesis(
        "liquidity_decay_followup",
        "liquidity_decay",
    )
    prior = [
        {
            "thesis_id": "liquidity_decay_dimension",
            "mechanism_dimension": "emergent",
            "thesis_details": {
                "new_dimension_name": "liquidity_decay",
                "why_existing_dimensions_do_not_fit": (
                    "This studies persistence decay after an entry event, not only "
                    "entry timing, signal quality, risk sizing, or exit behavior."
                ),
                "mechanism_family_definition": (
                    "Liquidity decay mechanisms test whether the market impact and "
                    "fill-quality environment around recent activity changes future edge."
                ),
                "expected_reuse_across_future_theses": (
                    "Future theses can reuse this dimension for decay windows and "
                    "post-event liquidity normalization."
                ),
            },
        }
    ]

    validated = validate_thesis_dict(thesis, prior_theses=prior)

    assert validated.mechanism_dimension == "liquidity_decay"


def test_validate_reuses_prior_emergent_dimension_when_prior_dimension_is_alias() -> None:
    thesis = _base_engine_change_thesis(
        "liquidity_decay_followup",
        "liquidity_decay",
    )
    prior = [
        {
            "thesis_id": "liquidity_decay_dimension",
            "mechanism_dimension": "other",
            "thesis_details": {"new_dimension_name": "liquidity_decay"},
        }
    ]

    validated = validate_thesis_dict(thesis, prior_theses=prior)

    assert validated.mechanism_dimension == "liquidity_decay"


def test_validate_normalizes_reused_prior_emergent_dimension_name() -> None:
    thesis = _base_engine_change_thesis(
        "liquidity_decay_followup",
        "Liquidity Decay",
    )
    prior = [
        {
            "thesis_id": "liquidity_decay_dimension",
            "mechanism_dimension": "emergent",
            "thesis_details": {"new_dimension_name": "Liquidity Decay"},
        }
    ]

    validated = validate_thesis_dict(thesis, prior_theses=prior)

    assert validated.mechanism_dimension == "liquidity_decay"


def test_generate_variants_does_not_extrapolate_max_trades_below_valid_bound() -> None:
    variants = generate_variants(
        {"max_trades_per_day": 1},
        {"max_trades_per_day": 3},
    )

    by_label = {variant["_variant_label"]: variant for variant in variants}

    assert by_label["conservative"]["max_trades_per_day"] == 2
    assert by_label["proposed"]["max_trades_per_day"] == 1
    assert "aggressive" not in by_label
    assert all(variant["max_trades_per_day"] >= 1 for variant in variants)


def test_generate_variants_does_not_extrapolate_max_hold_bars_below_valid_bound() -> None:
    variants = generate_variants(
        {"max_hold_bars": 40},
        {"max_hold_bars": 78},
    )

    assert all(variant["max_hold_bars"] >= 1 for variant in variants)


def test_hypothesis_alignment_accepts_first_trade_only_for_max_trades_per_day() -> None:
    score, explanation = check_hypothesis_alignment(
        hypothesis=(
            "If the strategy's edge is concentrated in the first executed trade per symbol per day, "
            "then taking only that first trade should improve profit factor."
        ),
        mechanism=(
            "Capture the opening dislocation only and avoid lower-quality subsequent setups later in the day."
        ),
        config_changes={"max_trades_per_day": 1},
        family_name="ema",
    )

    assert score == 1.0
    assert explanation == "Config changes align with hypothesis."
