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
