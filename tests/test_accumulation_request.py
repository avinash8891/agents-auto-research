"""Tests for the typed AccumulationRequest seam between the SDK adapter and
round accounting.

``parse_agents_sdk_result`` is the pure parser (no counter mutation, no trace
emission); ``accumulate_tokens`` dispatches a normalized request to the public
``accumulate_usage`` / ``record_unmetered_call`` / ``record_failed_call``
primitives. These tests pin the request the parser returns for each SDK result
shape and confirm the parser does not mutate round counters.
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

import agent_sdk_token_usage
import agent_token_usage
from agent_sdk_token_usage import parse_agents_sdk_result
from agent_token_usage import (
    AccumulationOutcome,
    AccumulationRequest,
    accumulate_tokens,
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
# parse_agents_sdk_result returns the correct request for each SDK result shape
# ---------------------------------------------------------------------------


def test_parse_none_result_yields_failed_request_before_model_derivation():
    request = parse_agents_sdk_result(
        "web-researcher", None, provider="openai", model="gpt-4o", dedupe_key="run-7-call-3"
    )

    assert request == AccumulationRequest(
        agent_type="web-researcher",
        outcome=AccumulationOutcome.FAILED,
        dedupe_key="run-7-call-3",
    )


def test_parse_successful_result_yields_accumulate_request_with_sdk_reported_source():
    result = _make_result(input_tokens=1500, output_tokens=800, total_tokens=2300, cost_usd=0.42)

    request = parse_agents_sdk_result(
        "conductor",
        result,
        provider="openai",
        model="gpt-5.2",
        input_text="prompt text",
        output_text="json",
        trace_id="trace-conductor-001",
        thesis_id="ema-thesis-42",
        dedupe_key="run-7-call-1",
    )

    assert request.agent_type == "conductor"
    assert request.outcome is AccumulationOutcome.ACCUMULATE
    assert request.provider == "openai"
    assert request.model == "gpt-5.2"
    assert request.trace_id == "trace-conductor-001"
    assert request.thesis_id == "ema-thesis-42"
    assert request.dedupe_key == "run-7-call-1"
    assert request.cost_usd == pytest.approx(0.42)
    assert request.usage == {
        "input_tokens": 1500,
        "output_tokens": 800,
        "total_tokens": 2300,
        "cached_input_tokens": 0,
        "estimated_input_tokens": 3,
        "estimated_output_tokens": 1,
        "estimated_total_tokens": 4,
        "usage_source": "sdk_reported",
    }


def test_parse_zero_usage_result_labels_sdk_reported_zero_with_estimate(monkeypatch):
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, total_tokens=0)
    result = SimpleNamespace(usage=usage, raw_responses=[], total_cost_usd=0.0)
    monkeypatch.setattr(
        agent_sdk_token_usage,
        "_count_tokens",
        lambda text, model=None: 7 if "system" in text else 3,
    )

    request = parse_agents_sdk_result(
        "conductor",
        result,
        provider="openai",
        model="gpt-5.2",
        input_text="system prompt",
        output_text="json",
    )

    assert request.outcome is AccumulationOutcome.ACCUMULATE
    assert request.usage["total_tokens"] == 0
    assert request.usage["estimated_total_tokens"] == 10
    assert request.usage["usage_source"] == "sdk_reported_zero_with_estimate"


def test_parse_missing_usage_with_estimate_labels_missing_sdk_usage(monkeypatch):
    result = SimpleNamespace(usage=None, raw_responses=[], total_cost_usd=0.0)
    monkeypatch.setattr(agent_sdk_token_usage, "_count_tokens", lambda text, model=None: len(text))

    request = parse_agents_sdk_result(
        "analyst",
        result,
        provider="openai",
        model="gpt-5.2",
        input_text="abc",
        output_text="de",
    )

    assert request.outcome is AccumulationOutcome.ACCUMULATE
    assert request.usage["estimated_total_tokens"] == 5
    assert request.usage["usage_source"] == "missing_sdk_usage_with_estimate"


def test_parse_cost_only_result_labels_sdk_cost_only_missing_tokens():
    result = SimpleNamespace(usage=None, raw_responses=[], total_cost_usd=0.42)

    request = parse_agents_sdk_result("analyst", result, provider="openai", model="gpt-5.2")

    assert request.outcome is AccumulationOutcome.ACCUMULATE
    assert request.cost_usd == pytest.approx(0.42)
    assert request.usage["total_tokens"] == 0
    assert request.usage["usage_source"] == "sdk_cost_only_missing_tokens"


def test_parse_none_cost_without_usage_yields_unmetered_request():
    result = SimpleNamespace(usage=None, raw_responses=[], total_cost_usd=None)

    request = parse_agents_sdk_result("analyst", result, provider="openai", model="gpt-5.2")

    assert request == AccumulationRequest(
        agent_type="analyst",
        outcome=AccumulationOutcome.UNMETERED,
    )


def test_parse_sums_tokens_across_raw_responses_into_accumulate_request():
    raw_responses = [
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=500, output_tokens=200, total_tokens=700)
        ),
        SimpleNamespace(
            usage=SimpleNamespace(input_tokens=300, output_tokens=100, total_tokens=400)
        ),
    ]
    result = SimpleNamespace(usage=None, raw_responses=raw_responses, total_cost_usd=0.0)

    request = parse_agents_sdk_result("web-researcher", result, provider="openai", model="gpt-5.5")

    assert request.outcome is AccumulationOutcome.ACCUMULATE
    assert request.usage["input_tokens"] == 800
    assert request.usage["output_tokens"] == 300
    assert request.usage["total_tokens"] == 1100


def test_parse_is_pure_no_counter_mutation_no_trace_emission():
    """The parser must not touch round counters or emit trace events."""
    emitted = []
    result = _make_result(input_tokens=1500, output_tokens=800, total_tokens=2300)

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        parse_agents_sdk_result("web-researcher", result, provider="openai", model="gpt-4o")

    assert emitted == []
    assert get_round_usage()["by_agent"] == {}


# ---------------------------------------------------------------------------
# accumulate_tokens dispatches each outcome to the right public primitive
# ---------------------------------------------------------------------------


def test_accumulate_tokens_failed_routes_to_record_failed_call():
    emitted = []
    request = AccumulationRequest(
        agent_type="web-researcher",
        outcome=AccumulationOutcome.FAILED,
        dedupe_key="run-7-call-3",
    )

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_tokens(request)

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["failed_calls"] == 1
    assert agent["calls"] == 0
    assert emitted == []


def test_accumulate_tokens_unmetered_routes_to_record_unmetered_call():
    emitted = []
    request = AccumulationRequest(
        agent_type="analyst",
        outcome=AccumulationOutcome.UNMETERED,
        dedupe_key="run-7-call-4",
    )

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_tokens(request)

    agent = get_round_usage()["by_agent"]["analyst"]
    assert agent["unmetered_calls"] == 1
    assert agent["calls"] == 0
    assert emitted == []


def test_accumulate_tokens_accumulate_routes_to_accumulate_usage():
    emitted = []
    request = AccumulationRequest(
        agent_type="conductor",
        outcome=AccumulationOutcome.ACCUMULATE,
        usage={
            "input_tokens": 1500,
            "output_tokens": 800,
            "total_tokens": 2300,
            "cached_input_tokens": 0,
            "estimated_input_tokens": 3,
            "estimated_output_tokens": 1,
            "estimated_total_tokens": 4,
            "usage_source": "sdk_reported",
        },
        cost_usd=0.42,
        provider="openai",
        model="gpt-5.2",
        trace_id="trace-conductor-001",
        thesis_id="ema-thesis-42",
    )

    with patch.object(
        agent_token_usage, "_emit_trace_usage", side_effect=lambda *a, **kw: emitted.append(kw)
    ):
        accumulate_tokens(request)

    agent = get_round_usage()["by_agent"]["conductor"]
    assert agent["calls"] == 1
    assert agent["input_tokens"] == 1500
    assert agent["output_tokens"] == 800
    assert agent["total_tokens"] == 2300
    assert agent["cost_usd"] == pytest.approx(0.42)
    assert emitted[0]["trace_id"] == "trace-conductor-001"
    assert emitted[0]["thesis_id"] == "ema-thesis-42"
    assert emitted[0]["usage_source"] == "sdk_reported"


def test_parse_then_accumulate_matches_legacy_adapter_accounting():
    """End-to-end: parse -> accumulate_tokens reproduces the adapter's accounting."""
    result = _make_result(input_tokens=2000, output_tokens=1000, total_tokens=3000, cost_usd=0.05)

    request = parse_agents_sdk_result(
        "web-researcher",
        result,
        provider="openai",
        model="gpt-4o",
        dedupe_key="run-7-call-5",
    )
    accumulate_tokens(request)

    agent = get_round_usage()["by_agent"]["web-researcher"]
    assert agent["calls"] == 1
    assert agent["failed_calls"] == 0
    assert agent["input_tokens"] == 2000
    assert agent["output_tokens"] == 1000
    assert agent["total_tokens"] == 3000
    assert agent["cost_usd"] == pytest.approx(0.05)
