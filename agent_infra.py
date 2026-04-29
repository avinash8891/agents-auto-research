from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Load Claude OAuth token if not already set (bypasses keychain)
_OAUTH_TOKEN_FILE = Path.home() / ".claude_oauth_token"
if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and _OAUTH_TOKEN_FILE.exists():
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = _OAUTH_TOKEN_FILE.read_text().strip()


CLI_TIMEOUT_SECONDS = 180  # Max seconds for a CLI agent call


_OAUTH_PROXY_PORT = 10531
_OAUTH_PROXY_URL = f"http://127.0.0.1:{_OAUTH_PROXY_PORT}/v1"
_oauth_proxy_proc = None


def _ensure_oauth_proxy(timeout_seconds: float = 5.0) -> None:
    """Require the system-managed openai-oauth proxy to be reachable."""
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


SDK_TIMEOUT_SECONDS = 300  # Max seconds for a single SDK agent call (analyst needs Execute time)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Extract JSON from agent output."""
    text = text.strip()
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    brace_start = text.find("{")
    if brace_start == -1:
        return None
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None
