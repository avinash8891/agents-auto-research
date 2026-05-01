from __future__ import annotations

from typing import Any

_ROUND_USAGE: dict[str, dict[str, float]] = {}
_SEEN_DEDUPE_KEYS: set[str] = set()


def _accumulate_usage(
    agent_type: str,
    usage: dict[str, Any] | None,
    cost_usd: float | None = None,
    *,
    dedupe_key: str | None = None,
) -> None:
    """Accumulate token usage for the current round."""
    if dedupe_key:
        if dedupe_key in _SEEN_DEDUPE_KEYS:
            return
        _SEEN_DEDUPE_KEYS.add(dedupe_key)
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


def _accumulate_result_usage(
    agent_type: str,
    result: Any,
    *,
    dedupe_key: str | None = None,
) -> None:
    """Accumulate usage from an SDK result object.

    If the SDK result exposes raw response usage, aggregate those fields.
    Otherwise fall back to total_cost_usd when available and still count the call.
    """
    if result is None:
        _accumulate_usage(agent_type, None, cost_usd=0.0, dedupe_key=dedupe_key)
        return

    total_input = 0
    total_output = 0
    total_total = 0
    saw_usage = False

    raw_responses = getattr(result, "raw_responses", None) or []
    for resp in raw_responses:
        usage = getattr(resp, "usage", None) or getattr(resp, "model_usage", None)
        if not usage:
            continue
        saw_usage = True
        total_input += getattr(usage, "input_tokens", 0) or getattr(usage, "input", 0) or 0
        total_output += getattr(usage, "output_tokens", 0) or getattr(usage, "output", 0) or 0
        total_total += getattr(usage, "total_tokens", 0) or getattr(usage, "total", 0) or 0

    if not saw_usage:
        usage = getattr(result, "usage", None) or getattr(result, "model_usage", None)
        if usage:
            saw_usage = True
            total_input += getattr(usage, "input_tokens", 0) or getattr(usage, "input", 0) or 0
            total_output += getattr(usage, "output_tokens", 0) or getattr(usage, "output", 0) or 0
            total_total += getattr(usage, "total_tokens", 0) or getattr(usage, "total", 0) or 0

    cost_usd = getattr(result, "total_cost_usd", None)
    if cost_usd is None and raw_responses:
        cost_usd = sum((getattr(resp, "total_cost_usd", 0.0) or 0.0) for resp in raw_responses)
    if cost_usd is None and not saw_usage:
        cost_usd = 0.0

    _accumulate_usage(
        agent_type,
        (
            {
                "input_tokens": total_input,
                "output_tokens": total_output,
                "total_tokens": total_total,
            }
            if saw_usage
            else None
        ),
        cost_usd=cost_usd,
        dedupe_key=dedupe_key,
    )


def reset_round_usage() -> None:
    """Reset usage counters at the start of a new round."""
    _ROUND_USAGE.clear()
    _SEEN_DEDUPE_KEYS.clear()


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
