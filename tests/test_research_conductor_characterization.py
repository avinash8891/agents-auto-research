from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_token_usage as usage
import research_conductor as rc
import research_memory as memory
import research_paths as infra
import research_subagents as subagents
import research_tools_mcp as tools_mcp
from family_research_spec import get_family_research_spec
from strategies import STRATEGIES


def _assistant_message(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


def _result_message(*, usage=None, model_usage=None, result=None, total_cost_usd=None):
    return SimpleNamespace(
        usage=usage,
        model_usage=model_usage,
        result=result,
        total_cost_usd=total_cost_usd,
    )


async def _async_empty_iter():
    if False:
        yield None


def test_run_research_conductor_sync_returns_parsed_thesis_on_valid_json(monkeypatch):
    parsed_payload = {
        "reasoning": "grounded",
        "suggested_theses": [
            {
                "thesis_id": "entry_window_test",
                "hypothesis": "Later entries reduce weak early signals.",
                "mechanism": "Tightening the EMA entry window removes noisier open-driven setups.",
                "config_changes": {"entry_cutoff_time": "09:35"},
            }
        ],
        "should_stop": False,
    }

    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)

    class AssistantMessage:
        def __init__(self, text):
            self.content = [SimpleNamespace(text=text)]

    class ResultMessage:
        def __init__(self, usage=None, model_usage=None, result=None, total_cost_usd=None):
            self.usage = usage
            self.model_usage = model_usage
            self.result = result
            self.total_cost_usd = total_cost_usd

    captured_query: dict[str, object] = {}

    async def fake_query(*args, **kwargs):
        captured_query.update(kwargs)
        yield AssistantMessage("```json\n" + json.dumps(parsed_payload) + "\n```")
        yield ResultMessage(
            usage={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
            total_cost_usd=0.25,
        )

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(
            AssistantMessage=AssistantMessage,
            ClaudeAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
            ResultMessage=ResultMessage,
            query=fake_query,
        ),
    )

    captured = {}

    def fake_build_research_tools_mcp(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(_mcp_server=object())

    monkeypatch.setattr(rc, "_build_research_tools_mcp", fake_build_research_tools_mcp)

    rc.reset_round_usage()
    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={"profit_factor": 1.2},
        research_round=3,
        family_name="ema",
    )

    assert result == parsed_payload
    options = captured_query["options"]
    assert "5 EMA PULLBACK/REVERSAL STRATEGY" in options.system_prompt
    assert rc.run_research_conductor.__code__.co_names.count("_ROOT") == 1
    assert "__globals__" not in rc.run_research_conductor.__code__.co_names


def test_run_research_conductor_sync_returns_conductor_error_on_timeout(monkeypatch):
    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(
        rc,
        "_build_research_tools_mcp",
        lambda **kwargs: SimpleNamespace(_mcp_server=object()),
    )
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)

    class AssistantMessage:
        pass

    class ResultMessage:
        pass

    async def fake_query(*args, **kwargs):
        raise asyncio.TimeoutError
        yield  # pragma: no cover

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(
            AssistantMessage=AssistantMessage,
            ClaudeAgentOptions=lambda **kwargs: SimpleNamespace(**kwargs),
            ResultMessage=ResultMessage,
            query=fake_query,
        ),
    )

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={},
        research_round=4,
        family_name="ema",
    )

    assert result == {
        "status": "conductor_error",
        "error": "timeout",
        "suggested_theses": [],
        "should_stop": False,
    }


def test_accumulate_usage_tracks_tokens_across_agents():
    usage.reset_round_usage()

    usage._accumulate_usage(
        "analyst",
        {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        cost_usd=0.11,
    )
    usage._accumulate_usage("web_researcher", {"input": 7, "output": 2, "total": 9}, cost_usd=0.05)
    usage._accumulate_usage("conductor", {"input_tokens": 5, "output_tokens": 4, "total_tokens": 9})

    round_usage = usage.get_round_usage()

    assert round_usage["total"] == {
        "input_tokens": 22,
        "output_tokens": 9,
        "total_tokens": 31,
        "cost_usd": pytest.approx(0.16),
        "calls": 3,
    }
    assert round_usage["by_agent"]["analyst"]["calls"] == 1
    assert round_usage["by_agent"]["web_researcher"]["total_tokens"] == 9
    assert round_usage["by_agent"]["conductor"]["output_tokens"] == 4

    usage.reset_round_usage()
    assert usage.get_round_usage() == {
        "by_agent": {},
        "total": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
        },
    }


def test_accumulate_usage_dedupes_repeated_message_key():
    usage.reset_round_usage()

    usage._accumulate_usage(
        "conductor",
        {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
        cost_usd=0.25,
        dedupe_key="message-1",
    )
    usage._accumulate_usage(
        "conductor",
        {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
        cost_usd=0.25,
        dedupe_key="message-1",
    )

    round_usage = usage.get_round_usage()
    assert round_usage["by_agent"]["conductor"]["calls"] == 1
    assert round_usage["by_agent"]["conductor"]["total_tokens"] == 12
    assert round_usage["by_agent"]["conductor"]["cost_usd"] == 0.25


def test_orb_research_spec_resolves_from_strategy_registry() -> None:
    assert get_family_research_spec("orb") is STRATEGIES["orb"].research_spec


def test_ema_research_spec_matches_supported_operational_keys() -> None:
    spec = get_family_research_spec("ema")
    for key in {"gap_filter", "gap_pct", "use_range_shift", "range_shift_lookback"}:
        assert key in spec.allowed_config_keys
        assert key in spec.config_schema
        assert any(key in rule for rule in spec.config_rules)


def test_save_research_finding_rejects_bad_type(monkeypatch):
    mcp = tools_mcp._build_research_tools_mcp(
        trades_file="/tmp/trades.csv",
        call_analyst=subagents._call_analyst,
        call_web_researcher=subagents._call_web_researcher,
        save_research_finding=memory.save_research_finding,
        palace_search=memory._palace_search,
        palace_status=memory._palace_status,
        root=infra._ROOT,
        list_past_theses_for_root=memory.list_past_theses,
    )
    tool = next(tool for tool in mcp._tool_manager.list_tools() if tool.name == "save_finding")

    result = asyncio.run(
        tool.fn(
            finding="test finding",
            finding_type="garbage",
            status="validated",
            evidence="round_001",
            scope="full_sample",
            expires_if="baseline changes",
        )
    )

    assert "REJECTED" in result


def test_list_past_theses_reads_sqlite_history(monkeypatch, tmp_path):
    from experiment_db import ExperimentDB

    db_one = ExperimentDB(tmp_path / "ema_experiments.db")
    db_two = ExperimentDB(tmp_path / "orb_experiments.db")
    db_one.add_research_thesis_attempt(
        {
            "research_round_id": "job-1-round-1",
            "attempt_number": 1,
            "thesis_id": "ema_one",
            "validator_status": "compiled",
        }
    )
    db_two.add_research_thesis_attempt(
        {
            "research_round_id": "job-2-round-2",
            "attempt_number": 1,
            "thesis_id": "orb_two",
            "validator_status": "rejected",
        }
    )

    mcp = tools_mcp._build_research_tools_mcp(
        trades_file="/tmp/trades.csv",
        call_analyst=subagents._call_analyst,
        call_web_researcher=subagents._call_web_researcher,
        save_research_finding=memory.save_research_finding,
        palace_search=memory._palace_search,
        palace_status=memory._palace_status,
        root=tmp_path,
        list_past_theses_for_root=memory.list_past_theses,
    )
    tool = next(tool for tool in mcp._tool_manager.list_tools() if tool.name == "list_past_theses")

    payload = asyncio.run(tool.fn())
    parsed = json.loads(payload)

    assert {entry["thesis_id"] for entry in parsed} == {"ema_one", "orb_two"}
