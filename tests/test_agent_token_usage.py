"""Tests for agent_token_usage — failure calls must be distinguishable from zero-token successes.

Reproduces the bug where _accumulate_result_usage(result=None) emits a zero-token
trace event, making token_audit.py unable to distinguish timeouts from legitimate calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_token_usage
from agent_token_usage import (
    _accumulate_result_usage,
    _record_failed_call,
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
        _accumulate_result_usage("web-researcher", None, provider="openai", model="gpt-4o")

    assert emitted == [], (
        "A failed call (result=None) must not emit a trace event — "
        "zero-token events inflate calls count in token_audit.py"
    )


def test_failed_call_is_counted_in_failed_calls_not_calls():
    """Failed calls must appear in failed_calls, not in the calls counter.

    Before fix: calls counter is incremented for failures, masking actual
    successful call counts in per-agent and per-model aggregations.
    """
    _accumulate_result_usage("web-researcher", None, provider="openai", model="gpt-4o")

    usage = get_round_usage()
    agent = usage["by_agent"]["web-researcher"]

    assert agent.get("failed_calls", 0) == 1, "failure must be counted in failed_calls"
    assert agent["calls"] == 0, "calls must not include failures"


def test_failed_calls_do_not_contribute_to_total_calls():
    """Total call count in get_round_usage must exclude failed calls."""
    _accumulate_result_usage("web-researcher", None, provider="openai", model="gpt-4o")
    _accumulate_result_usage("codex-analyst", None, provider="openai", model="gpt-4o")

    total = get_round_usage()["total"]
    assert total["calls"] == 0, "total calls must exclude failures"
    assert total.get("failed_calls", 0) == 2, "total failed_calls must count all failures"


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
        _accumulate_result_usage("web-researcher", result, provider="openai", model="gpt-4o")

    assert len(emitted) == 1
    assert emitted[0]["input_tokens"] == 1500
    assert emitted[0]["output_tokens"] == 800
    assert emitted[0]["total_tokens"] == 2300


def test_successful_call_increments_calls_not_failed_calls():
    """Successful calls must increment calls, not failed_calls."""
    result = _make_result(input_tokens=1200, output_tokens=600, total_tokens=1800)
    _accumulate_result_usage("web-researcher", result, provider="openai", model="gpt-4o")

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["calls"] == 1
    assert agent.get("failed_calls", 0) == 0


def test_mixed_successful_and_failed_calls_tracked_independently():
    """Successful and failed calls for the same agent must be tracked in separate counters."""
    result = _make_result(input_tokens=2000, output_tokens=1000, total_tokens=3000)

    _accumulate_result_usage("web-researcher", result, provider="openai", model="gpt-4o")
    _accumulate_result_usage("web-researcher", None, provider="openai", model="gpt-4o")
    _accumulate_result_usage("web-researcher", result, provider="openai", model="gpt-4o")

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
