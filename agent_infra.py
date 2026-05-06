from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI as _AsyncOpenAI

_OPENAI_OAUTH_TOKEN_FILE = Path.home() / ".openai_oauth_token"
_LEGACY_OAUTH_TOKEN_FILE = Path.home() / ".claude_oauth_token"


CLI_TIMEOUT_SECONDS = 180  # Max seconds for a CLI agent call


def cli_fallback_enabled() -> bool:
    return os.environ.get("AUTORESEARCH_AGENT_CLI_FALLBACK", "").lower() in {
        "1",
        "true",
        "yes",
    }


_OAUTH_PROXY_PORT = 10531
_OAUTH_PROXY_URL = f"http://127.0.0.1:{_OAUTH_PROXY_PORT}/v1"


def _ensure_oauth_proxy(timeout_seconds: float = 5.0) -> None:
    """Require the system-managed openai-oauth proxy to be reachable."""
    import socket
    import time

    _ensure_oauth_token()
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

    log.error("openai-oauth proxy unavailable: %s", last_error)
    raise RuntimeError(
        "openai-oauth proxy is not available. Start openai-oauth.service before running research jobs."
    )


SDK_TIMEOUT_SECONDS = 300  # Max seconds for a single SDK agent call (analyst needs Execute time)

from autoresearch_logging import get_logger

log = get_logger(__name__)


def _parse_json(text: str) -> dict[str, Any] | None:
    """Extract JSON from agent output."""
    parsed = _parse_json_detailed(text)
    return parsed.get("parsed") if parsed.get("status") == "ok" else None


def _ensure_oauth_token() -> None:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return
    for token_file in (_OPENAI_OAUTH_TOKEN_FILE, _LEGACY_OAUTH_TOKEN_FILE):
        if not token_file.exists():
            continue
        token = token_file.read_text().strip()
        if token:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
            return


def _structured_error(
    agent: str,
    kind: str,
    message: str,
    *,
    attempt: int | None = None,
    details: str | None = None,
    excerpt: str | None = None,
    fenced: bool | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "status": "error",
        "agent": agent,
        "kind": kind,
        "message": message,
    }
    if attempt is not None:
        error["attempt"] = attempt
    if details:
        error["details"] = details
    if excerpt:
        error["excerpt"] = excerpt
    if fenced is not None:
        error["fenced"] = fenced
    return error


def _is_error_result(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") == "error"


def _run_coroutine_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # wait_for cancels the task at the timeout boundary so httpx
            # connections are cleaned up cooperatively, not abandoned.
            result_box["value"] = loop.run_until_complete(
                asyncio.wait_for(coro, timeout=SDK_TIMEOUT_SECONDS)
            )
        except asyncio.TimeoutError:
            error_box["error"] = TimeoutError(
                f"_run_coroutine_sync: coroutine did not complete within {SDK_TIMEOUT_SECONDS}s"
            )
        except BaseException as exc:
            error_box["error"] = exc
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    # Give the thread SDK_TIMEOUT_SECONDS + 5s for task-cancel cleanup before
    # treating it as a hard hang.
    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=SDK_TIMEOUT_SECONDS + 5)
    if thread.is_alive():
        raise TimeoutError(
            f"_run_coroutine_sync: coroutine did not complete within {SDK_TIMEOUT_SECONDS}s"
        )
    if error_box:
        raise error_box["error"]
    if "value" not in result_box:
        raise RuntimeError("coroutine thread exited without setting a result")
    return result_box["value"]


def _get_openai_client(base_url: str) -> _AsyncOpenAI:
    # New client per call: AsyncOpenAI's httpx transport is loop-bound; caching across
    # asyncio.run() boundaries (each of which closes its loop) triggers "Event loop is closed".
    return _AsyncOpenAI(api_key="unused", base_url=base_url)


def _parse_json_detailed(text: str) -> dict[str, Any]:
    """Extract JSON from agent output with a structured failure kind."""
    raw_text = text or ""
    stripped = raw_text.strip()
    if not stripped:
        return _structured_error("json-parser", "empty", "No agent output was returned")

    fenced = False
    if "```json" in stripped:
        fenced = True
        start = stripped.index("```json") + 7
        try:
            end = stripped.index("```", start)
            stripped = stripped[start:end].strip()
        except ValueError:
            return _structured_error(
                "json-parser",
                "truncated_fenced_json",
                "Fenced JSON response was not closed",
                excerpt=stripped[:200],
                fenced=True,
            )
    elif "```" in stripped:
        fenced = True
        start = stripped.index("```") + 3
        try:
            end = stripped.index("```", start)
            stripped = stripped[start:end].strip()
        except ValueError:
            return _structured_error(
                "json-parser",
                "truncated_fenced_json",
                "Fenced JSON response was not closed",
                excerpt=stripped[:200],
                fenced=True,
            )

    brace_start = stripped.find("{")
    if brace_start == -1:
        return _structured_error(
            "json-parser",
            "no_json",
            "No JSON object found in agent output",
            excerpt=stripped[:200],
        )

    depth = 0
    closing_index: int | None = None
    for i in range(brace_start, len(stripped)):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                closing_index = i
                break

    if closing_index is None:
        return _structured_error(
            "json-parser",
            "truncated_json",
            "JSON object appears truncated",
            excerpt=stripped[brace_start : brace_start + 200],
        )

    candidate = stripped[brace_start : closing_index + 1]
    try:
        return {
            "status": "ok",
            "kind": "parsed",
            "message": "JSON parsed successfully",
            "parsed": json.loads(candidate),
            "excerpt": candidate[:200],
            **({"fenced": True} if fenced else {}),
        }
    except json.JSONDecodeError as exc:
        log.error("AGENT_JSON_PARSE_FAILED error=%s", exc)
        return _structured_error(
            "json-parser",
            "malformed_json",
            "JSON payload could not be decoded",
            details=str(exc),
            excerpt=candidate[:200],
            fenced=fenced,
        )
