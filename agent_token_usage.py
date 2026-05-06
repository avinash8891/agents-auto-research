from __future__ import annotations

from typing import Any

from autoresearch_logging import get_logger

logger = get_logger(__name__)

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


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return 0


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
    cached_input_tokens: int = 0,
    reasoning_output_tokens: int = 0,
    estimated_input_tokens: int = 0,
    estimated_output_tokens: int = 0,
    estimated_total_tokens: int = 0,
    usage_source: str = "",
) -> None:
    """Forward per-call usage to trace_sdk's event stream. Fail-open."""
    try:
        from trace_sdk import record_usage_event
    except Exception as exc:  # trace_sdk unavailable in some contexts (tests)
        logger.warning("trace_sdk unavailable, usage events will not be emitted: %s", exc)
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
            cached_input_tokens=cached_input_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            estimated_total_tokens=estimated_total_tokens,
            usage_source=usage_source,
        )
    except Exception as exc:
        logger.debug("usage trace emission failed: %s", exc)


def _ensure_entry(agent_type: str) -> dict[str, Any]:
    if agent_type not in _ROUND_USAGE:
        _ROUND_USAGE[agent_type] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
            "failed_calls": 0,
            "unmetered_calls": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_total_tokens": 0,
        }
    return _ROUND_USAGE[agent_type]


def _record_failed_call(agent_type: str, dedupe_key: str | None = None) -> None:
    if dedupe_key:
        if dedupe_key in _SEEN_DEDUPE_KEYS:
            return
        _SEEN_DEDUPE_KEYS.add(dedupe_key)
    _ensure_entry(agent_type)["failed_calls"] += 1


def _record_unmetered_call(agent_type: str, dedupe_key: str | None = None) -> None:
    if dedupe_key:
        if dedupe_key in _SEEN_DEDUPE_KEYS:
            return
        _SEEN_DEDUPE_KEYS.add(dedupe_key)
    _ensure_entry(agent_type)["unmetered_calls"] += 1


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
    entry = _ensure_entry(agent_type)
    entry["calls"] += 1

    in_tok = out_tok = tot_tok = 0
    cached_input_tokens = reasoning_output_tokens = 0
    estimated_input_tokens = estimated_output_tokens = estimated_total_tokens = 0
    usage_source = ""
    if usage:
        in_tok = _first_present(usage, "input_tokens", "input")
        out_tok = _first_present(usage, "output_tokens", "output")
        tot_tok = _first_present(usage, "total_tokens", "total")
        cached_input_tokens = _first_present(usage, "cached_input_tokens")
        reasoning_output_tokens = _first_present(usage, "reasoning_output_tokens")
        estimated_input_tokens = _first_present(usage, "estimated_input_tokens")
        estimated_output_tokens = _first_present(usage, "estimated_output_tokens")
        estimated_total_tokens = _first_present(usage, "estimated_total_tokens")
        usage_source = _first_present(usage, "usage_source") or ""
        entry["input_tokens"] += in_tok
        entry["output_tokens"] += out_tok
        entry["total_tokens"] += tot_tok
        entry["cached_input_tokens"] += cached_input_tokens
        entry["estimated_input_tokens"] += estimated_input_tokens
        entry["estimated_output_tokens"] += estimated_output_tokens
        entry["estimated_total_tokens"] += estimated_total_tokens
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
        cached_input_tokens=int(cached_input_tokens or 0),
        reasoning_output_tokens=int(reasoning_output_tokens or 0),
        estimated_input_tokens=int(estimated_input_tokens or 0),
        estimated_output_tokens=int(estimated_output_tokens or 0),
        estimated_total_tokens=int(estimated_total_tokens or 0),
        usage_source=str(usage_source or ""),
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
        "cached_input_tokens": 0,
        "cost_usd": 0.0,
        "calls": 0,
        "failed_calls": 0,
        "unmetered_calls": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
        "estimated_total_tokens": 0,
    }
    for agent_usage in _ROUND_USAGE.values():
        for k in total:
            total[k] += agent_usage.get(k, 0)
    return {"by_agent": dict(_ROUND_USAGE), "total": total}
