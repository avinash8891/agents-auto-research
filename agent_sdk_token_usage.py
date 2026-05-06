from __future__ import annotations

from math import ceil
from typing import Any

from agent_token_usage import _accumulate_usage, _record_failed_call, _record_unmetered_call
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


def _estimate_usage(
    *,
    input_text: Any = None,
    output_text: Any = None,
    model: str | None = None,
) -> dict[str, int] | None:
    input_tokens = _count_tokens(_coerce_text(input_text), model=model)
    output_tokens = _count_tokens(_coerce_text(output_text), model=model)
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _get_usage_attr(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, dict):
            value = value.get("cached_tokens") or value.get("cache_read_tokens")
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    details = getattr(usage, "input_tokens_details", None) or getattr(
        usage, "inputTokenDetails", None
    )
    if isinstance(details, dict):
        value = details.get("cached_tokens") or details.get("cacheReadTokens")
    else:
        value = getattr(details, "cached_tokens", None) or getattr(details, "cacheReadTokens", None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _add_usage_totals(totals: dict[str, int], usage: Any) -> None:
    totals["input_tokens"] += getattr(usage, "input_tokens", 0) or getattr(usage, "input", 0) or 0
    totals["output_tokens"] += (
        getattr(usage, "output_tokens", 0) or getattr(usage, "output", 0) or 0
    )
    totals["total_tokens"] += getattr(usage, "total_tokens", 0) or getattr(usage, "total", 0) or 0
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
) -> None:
    """Extract and record usage from an OpenAI Agents SDK result object.

    This adapter owns SDK result-shape details. Generic round accounting remains
    in ``agent_token_usage`` so future Claude/other SDK adapters can record the
    same normalized fields without inheriting OpenAI Agents SDK assumptions.
    """
    if result is None:
        _record_failed_call(agent_type, dedupe_key=dedupe_key)
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

    estimated = _estimate_usage(input_text=input_text, output_text=output_text, model=model)
    usage_source = "sdk_reported"
    if saw_usage and not any(
        totals[key] for key in ("input_tokens", "output_tokens", "total_tokens")
    ):
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
    if cost_usd is None and raw_responses:
        cost_usd = sum((getattr(resp, "total_cost_usd", 0.0) or 0.0) for resp in raw_responses)
    has_cost_only_usage = bool(cost_usd) and not saw_usage and not estimated

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
        _record_unmetered_call(agent_type, dedupe_key=dedupe_key)
        return

    _accumulate_usage(
        agent_type,
        normalized_usage,
        cost_usd=cost_usd,
        dedupe_key=dedupe_key,
        provider=provider,
        model=model,
    )
