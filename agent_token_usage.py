from __future__ import annotations

from typing import Any

_ROUND_USAGE: dict[str, dict[str, float]] = {}


def _accumulate_usage(
    agent_type: str, usage: dict[str, Any] | None, cost_usd: float | None = None
) -> None:
    """Accumulate token usage for the current round."""
    if agent_type not in _ROUND_USAGE:
        _ROUND_USAGE[agent_type] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        }
    entry = _ROUND_USAGE[agent_type]
    entry["calls"] += 1
    if usage:
        entry["input_tokens"] += usage.get("input_tokens") or usage.get("input") or 0
        entry["output_tokens"] += usage.get("output_tokens") or usage.get("output") or 0
        entry["total_tokens"] += usage.get("total_tokens") or usage.get("total") or 0
    if cost_usd:
        entry["cost_usd"] += cost_usd


def reset_round_usage() -> None:
    """Reset usage counters at the start of a new round."""
    _ROUND_USAGE.clear()


def get_round_usage() -> dict[str, Any]:
    """Get accumulated usage for the current round."""
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
    }
    for agent_usage in _ROUND_USAGE.values():
        for k in total:
            total[k] += agent_usage[k]
    return {"by_agent": dict(_ROUND_USAGE), "total": total}
