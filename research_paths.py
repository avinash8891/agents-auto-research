from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_OAUTH_PROXY_PORT = 10531
_OAUTH_PROXY_URL = f"http://127.0.0.1:{_OAUTH_PROXY_PORT}/v1"
log = logging.getLogger(__name__)


def _ensure_oauth_proxy(timeout_seconds: float = 5.0) -> None:
    import socket
    import time

    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while True:
        try:
            with socket.create_connection(("127.0.0.1", _OAUTH_PROXY_PORT), timeout=1):
                return
        except OSError as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    raise RuntimeError(
        f"openai-oauth proxy is not listening at {_OAUTH_PROXY_URL}. "
        "Start openai-oauth.service before running research jobs. "
        f"Last error: {last_error}"
    )


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
