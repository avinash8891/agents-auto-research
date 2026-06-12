from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


DELETED_SYMBOLS = (
    "_check" + "_neighboring_threshold",
    "_validate_expected_effects" + "_metrics_backed",
    "generate" + "_variants",
    "_NUMERIC" + "_VARIANT_BOUNDS",
    "_validate" + "_structural",
    "_validate" + "_thesis_quality",
    "_validate" + "_config_validity",
    "_runtime" + "_code_text",
    "_active_string" + "_token_present",
)


DELETED_FILES = (
    "compiler" + "_validate.py",
    "orb_experiment" + "_schema.py",
)


def test_step0_deleted_symbols_do_not_remain_in_live_source() -> None:
    live_files = [
        ROOT / "thesis_validator.py",
        ROOT / "compiler_implementation_verify.py",
        ROOT / "scripts" / "check_prompt_drift.py",
        ROOT / "tests" / "test_thesis_validator.py",
    ]

    remaining: list[str] = []
    for path in live_files:
        text = path.read_text()
        for symbol in DELETED_SYMBOLS:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text):
                remaining.append(f"{path.relative_to(ROOT)}:{symbol}")

    assert remaining == []


def test_step0_deleted_files_and_dead_orb_validator_are_absent() -> None:
    remaining = [path for path in DELETED_FILES if (ROOT / path).exists()]
    schema_text = (ROOT / "strategies" / "orb" / "schema.py").read_text()

    assert remaining == []
    assert "def validate_runtime_config(" not in schema_text


def test_expected_effects_and_disqualifier_schema_is_removed() -> None:
    """T2.12 cleanup: the evaluator deletion left these schema items inert."""

    import research_types
    import thesis_validator

    for class_name in ("ExpectedEffect", "Disqualifier"):
        assert not hasattr(research_types, class_name), class_name

    for field_name in ("expected_effects", "disqualifiers"):
        assert field_name not in research_types.ResearchThesis.model_fields, field_name

    for normalizer in (
        "_normalize_expected_effect",
        "_normalize_disqualifier",
        "_infer_effect_metric",
        "_infer_effect_direction",
    ):
        assert not hasattr(thesis_validator, normalizer), normalizer


def test_orphaned_legacy_validation_surface_is_removed() -> None:
    import behavior_signals
    import research_conductor
    import research_types
    import thesis_validator

    orphaned_validator_symbols = (
        "validate_research_thesis",
        "validate_thesis_dict",
        "_collect_mechanical_failures",
        "_collect_research_contract_failures",
        "_collect_source_code_verification_failures",
        "_run_behavioral_pass",
        "_validate_process",
        "_process_signal",
        "_validate_base_config_path",
        "_normalize_mechanism_dimension_name",
        "BUILTIN_METRICS",
    )
    for symbol in orphaned_validator_symbols:
        assert not hasattr(thesis_validator, symbol), symbol

    assert not hasattr(research_conductor, "_extract_thesis")
    assert not hasattr(behavior_signals, "decide")

    legacy_prose_fields = {
        "mechanism_dimension",
        "dimension_novelty",
        "causal_cluster",
        "underexplored_dimensions_considered",
        "novel_connection",
        "closest_prior_theses_considered",
        "orthogonality_defense",
        "evidence_strength",
        "falsification_or_alternative",
        "new_dimension_name",
        "why_existing_dimensions_do_not_fit",
        "mechanism_family_definition",
        "expected_reuse_across_future_theses",
        "theme_keywords",
        "prior_lever_outcomes",
        "alternatives_considered",
        "evidence_citations",
        "source_code_verification",
        "why_not_overfit",
        "evidence",
    }
    assert legacy_prose_fields.isdisjoint(research_types.ResearchThesis.model_fields)
    for class_name in ("Alternative", "EvidenceCitation", "PriorLeverOutcome"):
        assert not hasattr(research_types, class_name), class_name
