"""The single home for a backtest result's keep/discard verdict.

``decide_verdict`` owns the decision logic that used to live inline in
``autoresearch_experiment._build_db_record``: the registered-verdict pass-through,
the primary-metric fallback when no verdict was computed, and the duplicate
override (flip to ``discard`` and map the status to ``invalid_duplicate_result``
vs ``invalid_noop_config``). It is pure — inject the inputs, assert the Decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backtest_run_db import BacktestRunRecord


@dataclass(frozen=True)
class Decision:
    """The resolved verdict for a backtest result."""

    decision: str  # "keep" | "reject" | "discard"
    verdict_status: str
    verdict_summary: str


def decide_verdict(
    *,
    decision: str,
    registered_verdict: dict[str, Any],
    duplicate: "BacktestRunRecord | None",
    runtime_config: dict[str, Any],
    duplicate_summary: str = "",
) -> Decision:
    """Resolve the final keep/discard verdict for one backtest result.

    ``registered_verdict`` is ``analysis['trade_analysis']['verdict']``. When it
    carries no status, fall back to the primary-metric outcome. When a duplicate
    prior run is supplied, override to a discard and map the status by whether the
    runtime configs match; ``duplicate_summary`` is the caller-rendered message.
    """
    verdict_status = registered_verdict.get("status")
    verdict_summary = registered_verdict.get("summary", "")
    if not verdict_status:
        verdict_status = "accepted" if decision == "keep" else "rejected"
    if not verdict_summary:
        verdict_summary = (
            "kept by primary metric improvement"
            if decision == "keep"
            else "rejected by primary metric comparison"
        )

    if duplicate is not None:
        return Decision(
            decision="discard",
            verdict_status=(
                "invalid_duplicate_result"
                if duplicate.runtime_config == runtime_config
                else "invalid_noop_config"
            ),
            verdict_summary=duplicate_summary,
        )

    return Decision(
        decision=decision,
        verdict_status=verdict_status,
        verdict_summary=verdict_summary,
    )
