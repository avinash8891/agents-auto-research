from __future__ import annotations

from agent_formatters import format_round_results_summary


def test_format_round_results_summary_respects_lower_is_better_direction() -> None:
    summary = format_round_results_summary(
        [
            {"thesis_id": "high-drawdown", "metric": 0.40, "status": "keep"},
            {"thesis_id": "low-drawdown", "metric": 0.10, "status": "keep"},
        ],
        best_direction="lower",
    )

    assert "best: low-drawdown | metric=0.1 | status=keep" in summary
