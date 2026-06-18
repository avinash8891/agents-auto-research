"""Per-stage retry budget for the research validation loop.

``RetryBudget`` is the one home for the bookkeeping that used to leak into
``execute_research_sdk``: the overall attempt cap plus the independent
stage-1 and compile failure budgets, and the rule for which failures advance
which counter. The loop asks ``exhausted`` and reports each failed stage via
``record_failure``; it never tracks counters itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoresearch_constants import (
    MAX_VALIDATION_RETRIES_COMPILE,
    MAX_VALIDATION_RETRIES_STAGE_1,
)


@dataclass
class RetryBudget:
    """Tracks attempt count and per-stage failure budgets for one research round."""

    max_retries: int
    stage_1_failures: int = 0
    compile_failures: int = 0
    attempt: int = 0

    @property
    def exhausted(self) -> bool:
        """True once the attempt cap or any per-stage failure budget is hit."""
        return (
            self.attempt >= self.max_retries
            or self.stage_1_failures >= MAX_VALIDATION_RETRIES_STAGE_1
            or self.compile_failures >= MAX_VALIDATION_RETRIES_COMPILE
        )

    def record_failure(self, failed_stage: str) -> None:
        """Advance the attempt count, charging the matching per-stage budget.

        Only ``stage_1`` and ``compile`` failures charge a per-stage counter;
        every failed attempt advances ``attempt``.
        """
        if failed_stage == "stage_1":
            self.stage_1_failures += 1
        elif failed_stage == "compile":
            self.compile_failures += 1
        self.attempt += 1
