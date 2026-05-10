"""Tests for L12: teaching tools — get_dimension_examples + get_tuning_examples."""

from __future__ import annotations


def test_dimension_examples_constant_lists_all_core_dimensions() -> None:
    from research_doctrine import DIMENSION_EXAMPLES

    for name in (
        "ENTRY TIMING",
        "EXIT MECHANISM",
        "SIGNAL QUALITY",
        "REGIME CONDITIONING",
        "PORTFOLIO CONSTRUCTION",
        "RISK STRUCTURE",
        "MARKET MICROSTRUCTURE",
        "EMERGENT",
    ):
        assert name in DIMENSION_EXAMPLES, f"missing {name}"


def test_dimension_examples_constant_includes_questions_and_examples() -> None:
    from research_doctrine import DIMENSION_EXAMPLES

    assert "Questions:" in DIMENSION_EXAMPLES
    assert "Example:" in DIMENSION_EXAMPLES


def test_parameter_tuning_examples_constant_includes_rejected_and_accepted() -> None:
    from research_doctrine import PARAMETER_TUNING_EXAMPLES

    assert "REJECTED" in PARAMETER_TUNING_EXAMPLES
    assert "ACCEPTED" in PARAMETER_TUNING_EXAMPLES
    assert "gap_exclude_pct" in PARAMETER_TUNING_EXAMPLES
    assert "structural" in PARAMETER_TUNING_EXAMPLES.lower()
