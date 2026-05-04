from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import research_conductor as rc
from agent_infra import _run_coroutine_sync

# ── Minimal thesis dict that passes the struct check ─────────────────────────

_THESIS = {
    "thesis_id": "entry_window_test",
    "hypothesis": "narrow the entry window to reduce weak early signals.",
    "mechanism": "Tightening the EMA entry window removes noisier open-driven setups.",
    "mechanism_dimension": "entry_timing",
    "dimension_novelty": "Distinct timing regime, not a simple parameter sweep.",
    "config_changes": {"entry_cutoff_time": "09:35"},
    "expected_effects": [
        {
            "metric": "profit_factor",
            "direction": "increase",
            "threshold": 0.05,
            "rationale": "Narrower window should remove low-quality trades.",
        }
    ],
    "disqualifiers": [
        {
            "name": "trade_count_collapse",
            "condition": "trade_count decreases by more than 30 percent versus baseline",
            "severity": "hard_fail",
        }
    ],
}

_VALID_PAYLOAD = {
    "reasoning": "grounded",
    "suggested_theses": [_THESIS],
    "should_stop": False,
}

_STOP_PAYLOAD = {
    "reasoning": "exhausted all viable theses",
    "suggested_theses": [],
    "should_stop": True,
}


# ── Fake result helpers ───────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, payload: str):
        self.final_output = payload
        self.raw_responses = [
            SimpleNamespace(
                usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
                total_cost_usd=0.01,
            )
        ]

    async def stream_events(self):
        if False:
            yield None


class _FakeResultWithOutputAs(_FakeResult):
    def final_output_as(self, typ):
        return self.final_output


class _FakeResultWithFailingOutputAs(_FakeResult):
    def final_output_as(self, typ):
        raise RuntimeError("final_output_as intentionally broken")


def _stub_common(monkeypatch):
    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)


# ── _strategy_description_for ─────────────────────────────────────────────────


def test_strategy_description_for_unknown_family_returns_fallback():
    # Lines 44-45: ValueError from load_family is caught, fallback returned
    result = rc._strategy_description_for("nonexistent_xyz_999")
    assert result == "Strategy family: nonexistent_xyz_999"


def test_strategy_description_for_known_family_returns_real_description():
    result = rc._strategy_description_for("ema")
    assert len(result) > 20
    assert "nonexistent" not in result


# ── No-trades cold-start prompt ───────────────────────────────────────────────


def test_no_trades_cold_start_prompt_contains_expected_guidance(monkeypatch):
    # Lines 87-93: trades_file="" takes the else branch with cold-start wording
    _stub_common(monkeypatch)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda t: None)

    captured: dict[str, str] = {}

    def fake_run_streamed(agent, input_text, **kwargs):
        captured["input"] = input_text
        return _FakeResult(json.dumps(_VALID_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    rc.run_research_conductor_sync(
        trades_file="",
        experiment_results="no results yet",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert "No experiments have been run yet" in captured["input"]
    assert "Check memory for data facts" in captured["input"]
    assert "propose your first thesis" in captured["input"]


# ── strategy_events_file + diagnostics_file evidence lines ───────────────────


def test_strategy_events_and_diagnostics_appear_in_evidence_lines(monkeypatch):
    # Lines 69, 75: optional evidence files appended to user prompt
    _stub_common(monkeypatch)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda t: None)

    captured: dict[str, str] = {}

    def fake_run_streamed(agent, input_text, **kwargs):
        captured["input"] = input_text
        return _FakeResult(json.dumps(_VALID_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={"profit_factor": 1.2},
        research_round=2,
        family_name="ema",
        strategy_events_file="/tmp/events.parquet",
        diagnostics_file="/tmp/diagnostics.json",
    )

    assert "Strategy events file: /tmp/events.parquet" in captured["input"]
    assert "Diagnostics file: /tmp/diagnostics.json" in captured["input"]


# ── rejection_feedback injection ──────────────────────────────────────────────


def test_rejection_feedback_appended_to_user_prompt(monkeypatch):
    # Lines 95-101: rejection_feedback block appended when provided
    _stub_common(monkeypatch)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda t: None)

    captured: dict[str, str] = {}

    def fake_run_streamed(agent, input_text, **kwargs):
        captured["input"] = input_text
        return _FakeResult(json.dumps(_VALID_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=2,
        family_name="ema",
        rejection_feedback="thesis_id must not repeat a past thesis",
    )

    assert "YOUR PREVIOUS THESIS WAS REJECTED BY THE VALIDATOR" in captured["input"]
    assert "thesis_id must not repeat a past thesis" in captured["input"]
    assert "Propose a DIFFERENT thesis" in captured["input"]


# ── final_output_as path ──────────────────────────────────────────────────────


def test_final_output_as_used_when_method_exists(monkeypatch):
    # Lines 229-235: result.final_output_as(str) path
    _stub_common(monkeypatch)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda t: None)

    def fake_run_streamed(agent, input_text, **kwargs):
        return _FakeResultWithOutputAs(json.dumps(_VALID_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result == _VALID_PAYLOAD


def test_final_output_as_exception_falls_back_to_final_output(monkeypatch):
    # Lines 233-235: final_output_as raises → fall through to final_output
    _stub_common(monkeypatch)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda t: None)

    def fake_run_streamed(agent, input_text, **kwargs):
        return _FakeResultWithFailingOutputAs(json.dumps(_VALID_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result == _VALID_PAYLOAD


# ── Generic exception handler ─────────────────────────────────────────────────


def test_generic_exception_returns_conductor_error_kind_exception(monkeypatch):
    # Lines 263-278: non-timeout, non-proxy exception → error="exception"
    _stub_common(monkeypatch)

    def fake_run_streamed(*args, **kwargs):
        raise ValueError("unexpected internal error")

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result["status"] == "conductor_error"
    assert result["error"] == "exception"
    assert result["details"] == "ValueError"
    assert result["suggested_theses"] == []
    assert result["should_stop"] is False


def test_proxy_unavailable_exception_returns_proxy_kind(monkeypatch):
    # Lines 265: "openai-oauth proxy" in message → error="proxy_unavailable"
    _stub_common(monkeypatch)

    def fake_run_streamed(*args, **kwargs):
        raise RuntimeError("openai-oauth proxy is not reachable on port 10531")

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result["status"] == "conductor_error"
    assert result["error"] == "proxy_unavailable"


# ── should_stop branch ────────────────────────────────────────────────────────


def test_should_stop_true_returns_parsed_without_validation(monkeypatch):
    # Lines 315-327: parsed["should_stop"] is True → return immediately
    _stub_common(monkeypatch)

    def fake_run_streamed(*args, **kwargs):
        return _FakeResult(json.dumps(_STOP_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=5,
        family_name="ema",
    )

    assert result == _STOP_PAYLOAD
    assert result["should_stop"] is True


# ── validation_failed branch ──────────────────────────────────────────────────


def test_validation_failed_returns_conductor_error_when_validate_raises(monkeypatch):
    # Lines 334-335, 355-367: validate_thesis_dict raises → validation_failed
    _stub_common(monkeypatch)

    def _raise(t):
        raise ValueError("thesis schema violation")

    monkeypatch.setattr(rc, "validate_thesis_dict", _raise)

    def fake_run_streamed(*args, **kwargs):
        return _FakeResult(json.dumps(_VALID_PAYLOAD))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result["status"] == "conductor_error"
    assert result["error"] == "validation_failed"
    assert result["suggested_theses"] == []
    assert result["should_stop"] is False


# ── parse_failed branch ───────────────────────────────────────────────────────


def test_parse_failed_returns_conductor_error_when_no_json(monkeypatch):
    # Lines 368-381: _parse_json returns None → parse_failed
    _stub_common(monkeypatch)

    def fake_run_streamed(*args, **kwargs):
        return _FakeResult("Sorry, I cannot provide a thesis at this time.")

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result["status"] == "conductor_error"
    assert result["error"] == "parse_failed"
    assert result["suggested_theses"] == []
    assert result["should_stop"] is False


# ── _run_coroutine_sync threading path ───────────────────────────────────────


def test_run_coroutine_sync_uses_thread_when_event_loop_is_running():
    # Lines 107-121 of agent_infra.py: threading path when loop is running
    async def _inner():
        async def _coro():
            return 42

        return _run_coroutine_sync(_coro())

    result = asyncio.run(_inner())
    assert result == 42


def test_run_coroutine_sync_propagates_exception_from_threaded_coroutine():
    # error_box path: coroutine raises inside the daemon thread; must re-raise in caller
    async def _inner():
        async def _failing_coro():
            raise ValueError("coroutine failed inside thread")

        return _run_coroutine_sync(_failing_coro())

    with pytest.raises(ValueError, match="coroutine failed inside thread"):
        asyncio.run(_inner())


def test_run_coroutine_sync_timeout_raises_when_coroutine_hangs():
    import agent_infra

    original_timeout = agent_infra.SDK_TIMEOUT_SECONDS
    try:
        agent_infra.SDK_TIMEOUT_SECONDS = 0.1  # make timeout almost instant

        async def _inner():
            async def _hanging_coro():
                import asyncio as _asyncio

                await _asyncio.sleep(999)

            return _run_coroutine_sync(_hanging_coro())

        with pytest.raises(TimeoutError, match="did not complete within"):
            asyncio.run(_inner())
    finally:
        agent_infra.SDK_TIMEOUT_SECONDS = original_timeout
