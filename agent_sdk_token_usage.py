from __future__ import annotations

from math import ceil
from typing import Any, Literal

from agent_token_usage import accumulate_usage, record_failed_call, record_unmetered_call
from autoresearch_logging import get_logger

logger = get_logger(__name__)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _count_tokens(text: str, model: str | None = None) -> int:
    """Best-effort local token count for parallel SDK usage estimates."""
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore[import-not-found]

        try:
            encoding = tiktoken.encoding_for_model(model or "")
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        # Conservative fallback for environments without tiktoken.
        return max(1, ceil(len(text) / 4))


def _estimate_tokens_fast(text: str) -> int:
    """Cheap parallel estimate used when provider actual usage is already present."""
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))


def _estimate_usage(
    *,
    input_text: Any = None,
    output_text: Any = None,
    model: str | None = None,
    mode: Literal["fast", "precise"] = "precise",
) -> dict[str, int] | None:
    input_str = _coerce_text(input_text)
    output_str = _coerce_text(output_text)
    if mode == "fast":
        input_tokens = _estimate_tokens_fast(input_str)
        output_tokens = _estimate_tokens_fast(output_str)
    else:
        input_tokens = _count_tokens(input_str, model=model)
        output_tokens = _count_tokens(output_str, model=model)
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _get_usage_attr(usage: Any, *names: str) -> int:
    if isinstance(usage, dict):
        for name in names:
            if name in usage and usage[name] is not None:
                value = usage[name]
                if isinstance(value, dict):
                    value = _first_present(value, "cached_tokens", "cache_read_tokens")
                return _to_int(value)
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, dict):
            value = _first_present(value, "cached_tokens", "cache_read_tokens")
        if value is not None:
            return _to_int(value)
    if isinstance(usage, dict):
        details = usage.get("input_tokens_details") or usage.get("inputTokenDetails")
    else:
        details = getattr(usage, "input_tokens_details", None) or getattr(
            usage, "inputTokenDetails", None
        )
    if isinstance(details, dict):
        value = _first_present(details, "cached_tokens", "cacheReadTokens")
    else:
        value = _first_present_attr(details, "cached_tokens", "cacheReadTokens")
    return _to_int(value)


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return 0


def _first_present_attr(obj: Any, *names: str) -> Any:
    if isinstance(obj, dict):
        return _first_present(obj, *names)
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return 0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _add_usage_totals(totals: dict[str, int], usage: Any) -> None:
    totals["input_tokens"] += _to_int(_first_present_attr(usage, "input_tokens", "input"))
    totals["output_tokens"] += _to_int(_first_present_attr(usage, "output_tokens", "output"))
    totals["total_tokens"] += _to_int(_first_present_attr(usage, "total_tokens", "total"))
    totals["cached_input_tokens"] += _get_usage_attr(
        usage, "cached_input_tokens", "cachedInputTokens"
    )


def accumulate_agents_sdk_result_usage(
    agent_type: str,
    result: Any,
    *,
    dedupe_key: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_text: Any = None,
    output_text: Any = None,
    trace_id: str = "",
    thesis_id: str | None = None,
) -> None:
    """Extract and record usage from an OpenAI Agents SDK result object.

    This adapter owns SDK result-shape details. Generic round accounting remains
    in ``agent_token_usage`` so future Claude/other SDK adapters can record the
    same normalized fields without inheriting OpenAI Agents SDK assumptions.
    """
    if result is None:
        record_failed_call(agent_type, dedupe_key=dedupe_key)
        return

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
    }
    saw_usage = False
    if not model:
        model = getattr(result, "model", None) or None

    raw_responses = getattr(result, "raw_responses", None) or []
    for resp in raw_responses:
        if not model:
            model = getattr(resp, "model", None) or None
        usage = getattr(resp, "usage", None) or getattr(resp, "model_usage", None)
        if not usage:
            continue
        saw_usage = True
        _add_usage_totals(totals, usage)

    if not saw_usage:
        usage = getattr(result, "usage", None) or getattr(result, "model_usage", None)
        if usage:
            saw_usage = True
            _add_usage_totals(totals, usage)

    has_actual_tokens = saw_usage and any(
        totals[key] for key in ("input_tokens", "output_tokens", "total_tokens")
    )
    estimated = _estimate_usage(
        input_text=input_text,
        output_text=output_text,
        model=model,
        mode="fast" if has_actual_tokens else "precise",
    )
    usage_source = "sdk_reported"
    if saw_usage and not has_actual_tokens:
        if estimated:
            usage_source = "sdk_reported_zero_with_estimate"
            logger.warning(
                "provider reported zero SDK usage for successful %s call; "
                "parallel local estimate is %s tokens",
                agent_type,
                estimated["total_tokens"],
            )
    elif not saw_usage:
        usage_source = "missing_sdk_usage_with_estimate" if estimated else ""

    cost_usd = getattr(result, "total_cost_usd", None)
    cost_usd_present = cost_usd is not None
    if cost_usd is None and raw_responses:
        raw_costs = [getattr(resp, "total_cost_usd", None) for resp in raw_responses]
        cost_usd_present = any(raw_cost is not None for raw_cost in raw_costs)
        cost_usd = sum((raw_cost or 0.0) for raw_cost in raw_costs)
    has_cost_only_usage = cost_usd_present and not saw_usage and not estimated

    normalized_usage: dict[str, Any] | None = None
    if saw_usage or estimated or has_cost_only_usage:
        normalized_usage = {
            **totals,
            "estimated_input_tokens": (estimated or {}).get("input_tokens", 0),
            "estimated_output_tokens": (estimated or {}).get("output_tokens", 0),
            "estimated_total_tokens": (estimated or {}).get("total_tokens", 0),
            "usage_source": usage_source or "sdk_cost_only_missing_tokens",
        }
    else:
        logger.warning("SDK result for %s had no provider usage and no estimate text", agent_type)
        record_unmetered_call(agent_type, dedupe_key=dedupe_key)
        return

    accumulate_usage(
        agent_type,
        normalized_usage,
        cost_usd=cost_usd,
        dedupe_key=dedupe_key,
        provider=provider,
        model=model,
        trace_id=trace_id,
        thesis_id=thesis_id,
    )
