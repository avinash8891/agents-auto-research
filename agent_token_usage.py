from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ROUND_USAGE: dict[str, dict[str, float]] = {}
_SEEN_DEDUPE_KEYS: set[str] = set()


def _infer_provider(model: str | None) -> str | None:
    """Best-effort provider inference from a model name."""
    if not model:
        return None
    m = model.lower()
    if m.startswith(("claude", "anthropic")):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "openai")):
        return "openai"
    if m.startswith("gemini"):
        return "google"
    return None


def _emit_trace_usage(
    agent_type: str,
    *,
    provider: str | None,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost_usd: float,
    dedupe_key: str | None,
) -> None:
    """Forward per-call usage to trace_sdk's event stream. Fail-open."""
    try:
        from trace_sdk import record_usage_event
    except Exception:  # trace_sdk unavailable in some contexts (tests)
        return
    try:
        record_usage_event(
            agent_type,
            model_provider=provider or "",
            model_name=model or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            dedupe_key=dedupe_key,
        )
    except Exception as exc:
        logger.debug("usage trace emission failed: %s", exc)


def _accumulate_usage(
    agent_type: str,
    usage: dict[str, Any] | None,
    cost_usd: float | None = None,
    *,
    dedupe_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Accumulate token usage for the current round.

    Two consumers from this single entry point:
    - in-memory ``_ROUND_USAGE`` -> per-round/per-experiment DB rollup via ``get_round_usage``
    - per-call trace event (category=usage) -> trace-events.jsonl, carrying
      provider/model and full run_id/hypothesis_id correlation
    """
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

    in_tok = out_tok = tot_tok = 0
    if usage:
        in_tok = usage.get("input_tokens") or usage.get("input") or 0
        out_tok = usage.get("output_tokens") or usage.get("output") or 0
        tot_tok = usage.get("total_tokens") or usage.get("total") or 0
        entry["input_tokens"] += in_tok
        entry["output_tokens"] += out_tok
        entry["total_tokens"] += tot_tok
    if cost_usd:
        entry["cost_usd"] += cost_usd

    if provider is None:
        provider = _infer_provider(model)

    _emit_trace_usage(
        agent_type,
        provider=provider,
        model=model,
        input_tokens=int(in_tok or 0),
        output_tokens=int(out_tok or 0),
        total_tokens=int(tot_tok or 0),
        cost_usd=float(cost_usd or 0.0),
        dedupe_key=dedupe_key,
    )


def _accumulate_result_usage(
    agent_type: str,
    result: Any,
    *,
    dedupe_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Accumulate usage from an SDK result object.

    If the SDK result exposes raw response usage, aggregate those fields.
    Otherwise fall back to total_cost_usd when available and still count the call.
    """
    if result is None:
        _accumulate_usage(
            agent_type,
            None,
            cost_usd=0.0,
            dedupe_key=dedupe_key,
            provider=provider,
            model=model,
        )
        return

    total_input = 0
    total_output = 0
    total_total = 0
    saw_usage = False
    if model is None:
        model = getattr(result, "model", None)

    raw_responses = getattr(result, "raw_responses", None) or []
    for resp in raw_responses:
        if model is None:
            model = getattr(resp, "model", None)
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
        provider=provider,
        model=model,
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
