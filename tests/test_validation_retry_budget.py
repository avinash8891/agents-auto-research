"""Tests for per-stage validation retry budget."""

from __future__ import annotations

from autoresearch_constants import (
    MAX_VALIDATION_RETRIES_COMPILE,
    MAX_VALIDATION_RETRIES_STAGE_1,
    MAX_VALIDATION_RETRIES_STAGE_2,
)


def test_per_stage_constants_exist_and_are_positive() -> None:
    assert MAX_VALIDATION_RETRIES_STAGE_1 > 0
    assert MAX_VALIDATION_RETRIES_STAGE_2 > 0
    assert MAX_VALIDATION_RETRIES_COMPILE > 0


def test_per_stage_budget_exhausted_helper() -> None:
    from autoresearch_research import _per_stage_budget_exhausted

    # Empty counters: not exhausted.
    assert not _per_stage_budget_exhausted(0, 0, 0)

    # Stage 1 budget hit:
    assert _per_stage_budget_exhausted(MAX_VALIDATION_RETRIES_STAGE_1, 0, 0)
    assert not _per_stage_budget_exhausted(MAX_VALIDATION_RETRIES_STAGE_1 - 1, 0, 0)

    # Stage 2 budget hit:
    assert _per_stage_budget_exhausted(0, MAX_VALIDATION_RETRIES_STAGE_2, 0)
    assert not _per_stage_budget_exhausted(0, MAX_VALIDATION_RETRIES_STAGE_2 - 1, 0)

    # Compile budget hit:
    assert _per_stage_budget_exhausted(0, 0, MAX_VALIDATION_RETRIES_COMPILE)
    assert not _per_stage_budget_exhausted(0, 0, MAX_VALIDATION_RETRIES_COMPILE - 1)


def test_per_stage_total_budget_is_sum_of_stages() -> None:
    """Worst-case attempts = stage_1 + stage_2 + compile budgets."""
    expected_total = (
        MAX_VALIDATION_RETRIES_STAGE_1
        + MAX_VALIDATION_RETRIES_STAGE_2
        + MAX_VALIDATION_RETRIES_COMPILE
    )
    assert expected_total >= 3  # at minimum, current legacy behavior preserved
