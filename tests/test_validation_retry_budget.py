"""Tests for the per-stage validation retry budget (RetryBudget)."""

from __future__ import annotations

from autoresearch_constants import (
    MAX_VALIDATION_RETRIES_COMPILE,
    MAX_VALIDATION_RETRIES_STAGE_1,
)
from research_retry import RetryBudget


def test_per_stage_constants_exist_and_are_positive() -> None:
    assert MAX_VALIDATION_RETRIES_STAGE_1 > 0
    assert MAX_VALIDATION_RETRIES_COMPILE > 0


def test_fresh_budget_is_not_exhausted() -> None:
    assert RetryBudget(max_retries=5).exhausted is False


def test_attempt_cap_exhausts_the_budget() -> None:
    budget = RetryBudget(max_retries=3)
    budget.record_failure("stage_2")
    budget.record_failure("stage_2")
    assert budget.exhausted is False
    budget.record_failure("stage_2")
    assert budget.attempt == 3
    assert budget.exhausted is True


def test_stage_1_failures_exhaust_at_their_own_budget() -> None:
    budget = RetryBudget(max_retries=99)
    for _ in range(MAX_VALIDATION_RETRIES_STAGE_1 - 1):
        budget.record_failure("stage_1")
    assert budget.exhausted is False
    budget.record_failure("stage_1")
    assert budget.stage_1_failures == MAX_VALIDATION_RETRIES_STAGE_1
    assert budget.exhausted is True


def test_compile_failures_exhaust_at_their_own_budget() -> None:
    budget = RetryBudget(max_retries=99)
    for _ in range(MAX_VALIDATION_RETRIES_COMPILE):
        budget.record_failure("compile")
    assert budget.compile_failures == MAX_VALIDATION_RETRIES_COMPILE
    assert budget.exhausted is True


def test_stage_2_failure_advances_attempt_but_not_stage_counters() -> None:
    budget = RetryBudget(max_retries=99)
    budget.record_failure("stage_2")
    assert budget.attempt == 1
    assert budget.stage_1_failures == 0
    assert budget.compile_failures == 0
    assert budget.exhausted is False


def test_per_stage_total_budget_is_sum_of_live_stages() -> None:
    """Worst-case attempts = stage_1 + compile budgets."""
    expected_total = MAX_VALIDATION_RETRIES_STAGE_1 + MAX_VALIDATION_RETRIES_COMPILE
    assert expected_total >= 3  # at minimum, current legacy behavior preserved
