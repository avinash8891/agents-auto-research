from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_infra import _OAUTH_PROXY_PORT, _OAUTH_PROXY_URL, _ensure_oauth_proxy, _get_openai_client
from autoresearch_constants import DEFAULT_AGENT_MODEL as _CONDUCTOR_MODEL
from autoresearch_logging import get_logger

_ROOT = Path(__file__).resolve().parent
log = get_logger(__name__)

__all__ = [
    "_CONDUCTOR_MODEL",
    "_get_openai_client",
    "_OAUTH_PROXY_PORT",
    "_OAUTH_PROXY_URL",
    "_ROOT",
    "_ensure_oauth_proxy",
    "_parse_json",
]


def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    for fence in ("```json", "```"):
        start = text.find(fence)
        if start != -1:
            start += len(fence)
            end = text.find("```", start)
            if end != -1:
                text = text[start:end].strip()
            else:
                text = text[start:].strip()
            break
    brace = text.find("{")
    if brace == -1:
        return None
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace : i + 1])
                except json.JSONDecodeError as exc:
                    log.error(
                        "RESEARCH_JSON_PARSE_FAILED error=%s | hint=repair the research JSON payload or fenced response",
                        exc,
                    )
                    return None
    return None


def _extract_runner_output_text(result: Any) -> str:
    """Normalize Agents SDK runner output to a text payload.

    Some runner/model combinations populate ``final_output_as(str)`` while
    leaving ``final_output`` empty or structured. Prefer the explicit string
    extraction first, then fall back to the raw attribute.
    """
    text = ""
    if hasattr(result, "final_output_as"):
        try:
            text = result.final_output_as(str) or ""
        except Exception as exc:
            final_output = getattr(result, "final_output", None)
            new_items = getattr(result, "new_items", None) or []
            log.warning(
                "final_output_as failed for runner: %s final_output_type=%s new_items=%d",
                exc.__class__.__name__,
                type(final_output).__name__,
                len(new_items),
            )
            text = ""
    if not text:
        final_output = getattr(result, "final_output", None)
        if isinstance(final_output, str):
            text = final_output
        elif final_output is not None:
            text = json.dumps(final_output, default=str)
    if not text:
        new_items = getattr(result, "new_items", None) or []
        if new_items:
            try:
                from agents.items import ItemHelpers

                text = ItemHelpers.text_message_outputs(list(new_items))
            except Exception as exc:
                log.warning(
                    "message output extraction failed for runner: %s new_items=%d",
                    exc.__class__.__name__,
                    len(new_items),
                )
    if not text:
        raw_responses = getattr(result, "raw_responses", None) or []
        if raw_responses:
            try:
                from agents.items import ItemHelpers

                parts: list[str] = []
                for response in raw_responses:
                    for item in getattr(response, "output", None) or []:
                        if isinstance(item, dict):
                            part = ""
                            if item.get("type") == "message":
                                for content_item in item.get("content") or []:
                                    if (
                                        isinstance(content_item, dict)
                                        and content_item.get("type") == "output_text"
                                    ):
                                        part += content_item.get("text") or ""
                        else:
                            part = ItemHelpers.extract_text(item)
                        if part:
                            parts.append(part)
                text = "".join(parts)
            except Exception as exc:
                log.warning(
                    "raw response output extraction failed for runner: %s raw_responses=%d",
                    exc.__class__.__name__,
                    len(raw_responses),
                )
    return text
