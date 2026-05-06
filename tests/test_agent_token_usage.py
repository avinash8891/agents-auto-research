"""Tests for token usage accounting and OpenAI Agents SDK usage extraction."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_infra
import agent_sdk_token_usage
import agent_token_usage
from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from agent_token_usage import (
    _infer_provider,
    _record_failed_call,
    _record_unmetered_call,
    get_round_usage,
    reset_round_usage,
)


@pytest.fixture(autouse=True)
def reset_usage_state():
    reset_round_usage()
    yield
    reset_round_usage()


def _make_result(input_tokens: int, output_tokens: int, total_tokens: int, cost_usd: float = 0.001):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )
    return SimpleNamespace(usage=usage, raw_responses=[], total_cost_usd=cost_usd)


def test_agent_token_usage_does_not_export_sdk_result_adapter():
    assert not hasattr(agent_token_usage, "_accumulate_result_usage")


# ---------------------------------------------------------------------------
# RED: reproduce the bug
# ---------------------------------------------------------------------------


def test_failed_call_does_not_emit_trace_event():
    """result=None (timeout/transport error) must NOT emit a zero-token trace event.

    Before fix: _emit_trace_usage is called with all-zero tokens, inflating the
    calls count in token_audit.py groupings (e.g. --by model).
    After fix: no trace event is emitted; the failure is tracked separately.
    """
    emitted = []
    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "web-researcher", None, provider="openai", model="gpt-4o"
        )

    assert emitted == [], (
        "A failed call (result=None) must not emit a trace event — "
        "zero-token events inflate calls count in token_audit.py"
    )


def test_failed_call_is_counted_in_failed_calls_not_calls():
    """Failed calls must appear in failed_calls, not in the calls counter.

    Before fix: calls counter is incremented for failures, masking actual
    successful call counts in per-agent and per-model aggregations.
    """
    accumulate_agents_sdk_result_usage("web-researcher", None, provider="openai", model="gpt-4o")

    usage = get_round_usage()
    agent = usage["by_agent"]["web-researcher"]

    assert agent.get("failed_calls", 0) == 1, "failure must be counted in failed_calls"
    assert agent["calls"] == 0, "calls must not include failures"


def test_failed_calls_do_not_contribute_to_total_calls():
    """Total call count in get_round_usage must exclude failed calls."""
    accumulate_agents_sdk_result_usage("web-researcher", None, provider="openai", model="gpt-4o")
    accumulate_agents_sdk_result_usage("codex-analyst", None, provider="openai", model="gpt-4o")

    total = get_round_usage()["total"]
    assert total["calls"] == 0, "total calls must exclude failures"
    assert total.get("failed_calls", 0) == 2, "total failed_calls must count all failures"
    assert total.get("unmetered_calls", 0) == 0


def test_sdk_result_with_no_usage_and_no_estimate_is_unmetered_not_zero_trace():
    emitted = []
    result = SimpleNamespace(usage=None, raw_responses=[], total_cost_usd=0.0)

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "analyst",
            result,
            provider="openai",
            model="gpt-5.2",
        )

    agent = get_round_usage()["by_agent"]["analyst"]
    assert agent["calls"] == 0
    assert agent["failed_calls"] == 0
    assert agent["unmetered_calls"] == 1
    assert emitted == []


def test_sdk_cost_only_result_is_labeled_not_unmetered():
    emitted = []
    result = SimpleNamespace(usage=None, raw_responses=[], total_cost_usd=0.42)

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "analyst",
            result,
            provider="openai",
            model="gpt-5.2",
        )

    agent = get_round_usage()["by_agent"]["analyst"]
    assert agent["calls"] == 1
    assert agent["unmetered_calls"] == 0
    assert agent["cost_usd"] == pytest.approx(0.42)
    assert emitted[0]["usage_source"] == "sdk_cost_only_missing_tokens"
    assert emitted[0]["total_tokens"] == 0


# ---------------------------------------------------------------------------
# GREEN: successful calls must still work as before
# ---------------------------------------------------------------------------


def test_successful_call_emits_trace_event_with_real_tokens():
    """Successful results must still emit a trace event with correct token counts."""
    emitted = []
    result = _make_result(input_tokens=1500, output_tokens=800, total_tokens=2300)

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "web-researcher", result, provider="openai", model="gpt-4o"
        )

    assert len(emitted) == 1
    assert emitted[0]["input_tokens"] == 1500
    assert emitted[0]["output_tokens"] == 800
    assert emitted[0]["total_tokens"] == 2300


def test_successful_call_records_actual_and_parallel_estimate(monkeypatch):
    emitted = []
    result = _make_result(input_tokens=1500, output_tokens=800, total_tokens=2300)
    monkeypatch.setattr(
        agent_sdk_token_usage,
        "_count_tokens",
        lambda text, model=None: 11 if "prompt" in text else 4,
    )

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "conductor",
            result,
            provider="openai",
            model="gpt-5.2",
            input_text="prompt text",
            output_text="json",
        )

    agent = get_round_usage()["by_agent"]["conductor"]
    assert agent["input_tokens"] == 1500
    assert agent["output_tokens"] == 800
    assert agent["total_tokens"] == 2300
    assert agent["estimated_input_tokens"] == 11
    assert agent["estimated_output_tokens"] == 4
    assert agent["estimated_total_tokens"] == 15
    assert emitted[0]["usage_source"] == "sdk_reported"


def test_successful_zero_usage_call_records_labeled_estimate(monkeypatch):
    """OAuth Chat can return valid output with all-zero usage; do not store silent zeros."""
    emitted = []
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0)
    result = SimpleNamespace(usage=usage, raw_responses=[], total_cost_usd=0.0)
    monkeypatch.setattr(
        agent_sdk_token_usage,
        "_count_tokens",
        lambda text, model=None: 7 if "system" in text else 3,
    )

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "conductor",
            result,
            provider="openai",
            model="gpt-5.2",
            input_text="system prompt",
            output_text="json",
        )

    agent = get_round_usage()["by_agent"]["conductor"]
    assert agent["input_tokens"] == 0
    assert agent["output_tokens"] == 0
    assert agent["total_tokens"] == 0
    assert agent["estimated_input_tokens"] == 7
    assert agent["estimated_output_tokens"] == 3
    assert agent["estimated_total_tokens"] == 10
    assert emitted[0]["total_tokens"] == 0
    assert emitted[0]["estimated_total_tokens"] == 10
    assert emitted[0]["usage_source"] == "sdk_reported_zero_with_estimate"


def test_missing_usage_call_records_labeled_estimate(monkeypatch):
    emitted = []
    result = SimpleNamespace(usage=None, raw_responses=[], total_cost_usd=0.0)
    monkeypatch.setattr(agent_sdk_token_usage, "_count_tokens", lambda text, model=None: len(text))

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_agents_sdk_result_usage(
            "analyst",
            result,
            provider="openai",
            model="gpt-5.2",
            input_text="abc",
            output_text="de",
        )

    agent = get_round_usage()["by_agent"]["analyst"]
    assert agent["total_tokens"] == 0
    assert agent["estimated_total_tokens"] == 5
    assert emitted[0]["usage_source"] == "missing_sdk_usage_with_estimate"


def test_cli_usage_emits_cached_reasoning_and_usage_source_metadata():
    emitted = []

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        agent_token_usage._accumulate_usage(
            "web-researcher",
            {
                "input_tokens": 101,
                "cached_input_tokens": 20,
                "output_tokens": 11,
                "reasoning_output_tokens": 3,
                "total_tokens": 112,
                "usage_source": "codex_json_last_token_usage",
            },
            provider="openai",
            model="gpt-5.2",
        )

    assert len(emitted) == 1
    assert emitted[0]["input_tokens"] == 101
    assert emitted[0]["cached_input_tokens"] == 20
    assert emitted[0]["reasoning_output_tokens"] == 3
    assert emitted[0]["usage_source"] == "codex_json_last_token_usage"


def test_cached_input_tokens_are_actual_only_and_aggregated():
    result = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=25,
            total_tokens=125,
            cached_input_tokens=40,
        ),
        raw_responses=[],
        total_cost_usd=0.0,
    )

    accumulate_agents_sdk_result_usage("analyst", result, provider="openai", model="gpt-5.2")

    agent = get_round_usage()["by_agent"]["analyst"]
    assert agent["input_tokens"] == 100
    assert agent["cached_input_tokens"] == 40
    assert agent["estimated_total_tokens"] == 0


def test_successful_call_increments_calls_not_failed_calls():
    """Successful calls must increment calls, not failed_calls."""
    result = _make_result(input_tokens=1200, output_tokens=600, total_tokens=1800)
    accumulate_agents_sdk_result_usage("web-researcher", result, provider="openai", model="gpt-4o")

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["calls"] == 1
    assert agent.get("failed_calls", 0) == 0


def test_mixed_successful_and_failed_calls_tracked_independently():
    """Successful and failed calls for the same agent must be tracked in separate counters."""
    result = _make_result(input_tokens=2000, output_tokens=1000, total_tokens=3000)

    accumulate_agents_sdk_result_usage("web-researcher", result, provider="openai", model="gpt-4o")
    accumulate_agents_sdk_result_usage("web-researcher", None, provider="openai", model="gpt-4o")
    accumulate_agents_sdk_result_usage("web-researcher", result, provider="openai", model="gpt-4o")

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["calls"] == 2, "two successful calls"
    assert agent.get("failed_calls", 0) == 1, "one failed call"
    assert agent["input_tokens"] == 4000
    assert agent["total_tokens"] == 6000


def test_record_failed_call_skips_trace():
    """_record_failed_call must not emit a trace event and must increment failed_calls."""
    emitted = []
    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        _record_failed_call("codex-analyst")

    assert emitted == []
    agent = get_round_usage()["by_agent"]["codex-analyst"]
    assert agent.get("failed_calls", 0) == 1
    assert agent["calls"] == 0


def test_record_unmetered_call_skips_trace():
    emitted = []
    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        _record_unmetered_call("codex-analyst")

    assert emitted == []
    agent = get_round_usage()["by_agent"]["codex-analyst"]
    assert agent.get("unmetered_calls", 0) == 1
    assert agent["calls"] == 0


# ---------------------------------------------------------------------------
# _infer_provider — real model names, not toy names
# ---------------------------------------------------------------------------


def test_infer_provider_recognises_claude_models():
    assert _infer_provider("claude-sonnet-4-6") == "anthropic"
    assert _infer_provider("claude-opus-4-7") == "anthropic"
    assert _infer_provider("claude-haiku-4-5-20251001") == "anthropic"


def test_infer_provider_recognises_openai_models():
    assert _infer_provider("gpt-4o") == "openai"
    assert _infer_provider("gpt-5.5") == "openai"
    assert _infer_provider("o3-mini") == "openai"


def test_infer_provider_recognises_gemini_models():
    assert _infer_provider("gemini-1.5-pro") == "google"


def test_infer_provider_returns_none_for_unknown_or_missing():
    assert _infer_provider(None) is None
    assert _infer_provider("") is None
    assert _infer_provider("unknown-model-xyz") is None


# ---------------------------------------------------------------------------
# raw_responses — multi-chunk streamed token counting
# ---------------------------------------------------------------------------


def _make_result_with_raw_responses(chunks: list[tuple[int, int, int]]):
    """Build a fake SDK result where usage comes from raw_responses, not result.usage."""
    raw_responses = [
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=inp, output_tokens=out, total_tokens=total)
        )
        for inp, out, total in chunks
    ]
    return SimpleNamespace(usage=None, raw_responses=raw_responses, total_cost_usd=0.0)


def test_agents_sdk_usage_sums_tokens_across_raw_responses():
    result = _make_result_with_raw_responses([(500, 200, 700), (300, 100, 400)])
    accumulate_agents_sdk_result_usage("web-researcher", result, provider="openai", model="gpt-5.5")

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["input_tokens"] == 800
    assert agent["output_tokens"] == 300
    assert agent["total_tokens"] == 1100
    assert agent["calls"] == 1


def test_agents_sdk_usage_single_raw_response_matches_direct_usage():
    result = _make_result_with_raw_responses([(1200, 600, 1800)])
    accumulate_agents_sdk_result_usage("codex-analyst", result, provider="openai", model="gpt-5.5")

    agent = get_round_usage()["by_agent"]["codex-analyst"]
    assert agent["input_tokens"] == 1200
    assert agent["output_tokens"] == 600
    assert agent["total_tokens"] == 1800


# ---------------------------------------------------------------------------
# dedupe_key — double-count prevention
# ---------------------------------------------------------------------------


def test_agents_sdk_usage_deduplication_prevents_double_count():
    result = _make_result(input_tokens=1000, output_tokens=500, total_tokens=1500)
    accumulate_agents_sdk_result_usage(
        "web-researcher", result, provider="openai", model="gpt-5.5", dedupe_key="run-001-call-1"
    )
    accumulate_agents_sdk_result_usage(
        "web-researcher", result, provider="openai", model="gpt-5.5", dedupe_key="run-001-call-1"
    )

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["calls"] == 1, "second call with same dedupe_key must be ignored"
    assert agent["input_tokens"] == 1000, "tokens must not be doubled"


def test_agents_sdk_usage_different_dedupe_keys_both_counted():
    result = _make_result(input_tokens=1000, output_tokens=500, total_tokens=1500)
    accumulate_agents_sdk_result_usage(
        "web-researcher", result, provider="openai", model="gpt-5.5", dedupe_key="run-001-call-1"
    )
    accumulate_agents_sdk_result_usage(
        "web-researcher", result, provider="openai", model="gpt-5.5", dedupe_key="run-001-call-2"
    )

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["calls"] == 2, "distinct dedupe_keys must each be counted"
    assert agent["input_tokens"] == 2000


# ---------------------------------------------------------------------------
# _get_openai_client — per-call semantics (no global cache)
# ---------------------------------------------------------------------------


def test_get_openai_client_returns_new_object_each_call():
    # Global caching is intentionally absent: AsyncOpenAI's httpx transport is
    # event-loop-bound; reusing across asyncio.run() calls (each closes its loop)
    # causes "Event loop is closed". A new instance per call is the correct pattern.
    url = "http://127.0.0.1:10531/v1"
    client_a = agent_infra._get_openai_client(url)
    client_b = agent_infra._get_openai_client(url)
    assert client_a is not client_b, "_get_openai_client must return a new instance each call"


def test_get_openai_client_uses_given_base_url():
    url = "http://127.0.0.1:10531/v1"
    client = agent_infra._get_openai_client(url)
    assert str(client.base_url).rstrip("/") == url.rstrip("/")


def test_ensure_oauth_token_prefers_openai_named_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openai_token = tmp_path / ".openai_oauth_token"
    legacy_token = tmp_path / ".claude_oauth_token"
    openai_token.write_text("openai-token\n")
    legacy_token.write_text("legacy-token\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent_infra, "_OPENAI_OAUTH_TOKEN_FILE", openai_token)
    monkeypatch.setattr(agent_infra, "_LEGACY_OAUTH_TOKEN_FILE", legacy_token)

    try:
        agent_infra._ensure_oauth_token()

        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "openai-token"
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)


def test_ensure_oauth_token_falls_back_to_legacy_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openai_token = tmp_path / ".openai_oauth_token"
    legacy_token = tmp_path / ".claude_oauth_token"
    legacy_token.write_text("legacy-token\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent_infra, "_OPENAI_OAUTH_TOKEN_FILE", openai_token)
    monkeypatch.setattr(agent_infra, "_LEGACY_OAUTH_TOKEN_FILE", legacy_token)

    try:
        agent_infra._ensure_oauth_token()

        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "legacy-token"
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)


def test_ensure_oauth_token_ignores_empty_openai_named_token_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    openai_token = tmp_path / ".openai_oauth_token"
    legacy_token = tmp_path / ".claude_oauth_token"
    openai_token.write_text("\n")
    legacy_token.write_text("legacy-token\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent_infra, "_OPENAI_OAUTH_TOKEN_FILE", openai_token)
    monkeypatch.setattr(agent_infra, "_LEGACY_OAUTH_TOKEN_FILE", legacy_token)

    try:
        agent_infra._ensure_oauth_token()

        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "legacy-token"
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)


def test_ensure_oauth_token_falls_back_when_openai_token_file_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _UnreadableTokenFile:
        def exists(self) -> bool:
            return True

        def read_text(self) -> str:
            raise OSError("permission denied")

    legacy_token = tmp_path / ".claude_oauth_token"
    legacy_token.write_text("legacy-token\n")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent_infra, "_OPENAI_OAUTH_TOKEN_FILE", _UnreadableTokenFile())
    monkeypatch.setattr(agent_infra, "_LEGACY_OAUTH_TOKEN_FILE", legacy_token)

    try:
        agent_infra._ensure_oauth_token()

        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "legacy-token"
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
