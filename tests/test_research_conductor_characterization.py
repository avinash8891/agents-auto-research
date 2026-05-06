from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_sdk_token_usage as sdk_usage
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
                "hypothesis": "narrow the entry window to reduce weak early signals.",
                "mechanism": "Tightening the EMA entry window removes noisier open-driven setups.",
                "mechanism_dimension": "entry_timing",
                "dimension_novelty": "This is a distinct timing regime rather than a simple parameter sweep.",
                "config_changes": {"entry_cutoff_time": "09:35"},
                "expected_effects": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "threshold": 0.05,
                        "rationale": "A narrower opening window should remove low-quality trades.",
                    },
                    {
                        "metric": "trade_count",
                        "direction": "increase_or_same",
                        "rationale": "The change should preserve most opportunities.",
                    },
                ],
                "disqualifiers": [
                    {
                        "name": "trade_count_collapse",
                        "condition": "trade_count decreases by more than 30 percent versus baseline",
                        "severity": "hard_fail",
                    }
                ],
            }
        ],
        "should_stop": False,
    }

    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda thesis: None)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda thesis: None)

    captured: dict[str, object] = {}

    class _FakeResult:
        def __init__(self, payload: str):
            self.final_output = payload
            self.raw_responses = [
                SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=5,
                        output_tokens=7,
                        total_tokens=12,
                    ),
                    total_cost_usd=0.25,
                )
            ]

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(starting_agent, input, **kwargs):
        captured["agent"] = starting_agent
        captured["input"] = input
        captured["kwargs"] = kwargs
        return _FakeResult("```json\n" + json.dumps(parsed_payload) + "\n```")

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    rc.reset_round_usage()
    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={"profit_factor": 1.2},
        research_round=3,
        family_name="ema",
    )

    assert result == parsed_payload
    assert "5 EMA PULLBACK/REVERSAL STRATEGY" in captured["agent"].instructions
    assert captured["input"].startswith("Research round: 3")
    assert captured["kwargs"]["max_turns"] == 50
    assert captured["kwargs"]["run_config"].tracing_disabled is True


def test_run_research_conductor_sync_records_top_level_usage_when_raw_usage_missing(
    monkeypatch,
):
    parsed_payload = {
        "reasoning": "grounded",
        "suggested_theses": [
            {
                "thesis_id": "entry_window_test",
                "hypothesis": "narrow the entry window to reduce weak early signals.",
                "mechanism": "Tightening the EMA entry window removes noisier open-driven setups.",
                "mechanism_dimension": "entry_timing",
                "dimension_novelty": "This is a distinct timing regime rather than a simple parameter sweep.",
                "config_changes": {"entry_cutoff_time": "09:35"},
                "expected_effects": [],
                "disqualifiers": [],
            }
        ],
        "should_stop": False,
    }

    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)
    monkeypatch.setattr(rc, "validate_thesis_dict", lambda thesis: None)

    class _FakeResult:
        def __init__(self, payload: str):
            self.final_output = payload
            self.raw_responses = [SimpleNamespace()]
            self.usage = SimpleNamespace(
                input_tokens=5,
                output_tokens=7,
                total_tokens=12,
            )
            self.total_cost_usd = 0.25

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(*args, **kwargs):
        return _FakeResult(json.dumps(parsed_payload))

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    rc.reset_round_usage()
    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={"profit_factor": 1.2},
        research_round=3,
        family_name="ema",
    )

    assert result == parsed_payload
    round_usage = rc.get_round_usage()
    assert round_usage["by_agent"]["conductor"]["calls"] == 1
    assert round_usage["by_agent"]["conductor"]["total_tokens"] == 12
    assert round_usage["by_agent"]["conductor"]["cost_usd"] == pytest.approx(0.25)


def test_run_research_conductor_sync_returns_conductor_error_on_timeout(monkeypatch):
    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)

    def fake_run_streamed(*args, **kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)
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
        "cached_input_tokens": 0,
        "cost_usd": pytest.approx(0.16),
        "calls": 3,
        "failed_calls": 0,
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
        "estimated_total_tokens": 0,
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
            "cached_input_tokens": 0,
            "cost_usd": 0.0,
            "calls": 0,
            "failed_calls": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_total_tokens": 0,
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


def test_agents_sdk_usage_uses_reported_total_cost_when_raw_usage_missing():
    usage.reset_round_usage()

    result = SimpleNamespace(
        raw_responses=[],
        total_cost_usd=0.42,
    )

    sdk_usage.accumulate_agents_sdk_result_usage("analyst", result)

    round_usage = usage.get_round_usage()
    assert round_usage["by_agent"]["analyst"]["calls"] == 1
    assert round_usage["by_agent"]["analyst"]["cost_usd"] == pytest.approx(0.42)


def test_orb_research_spec_resolves_from_strategy_registry() -> None:
    assert get_family_research_spec("orb") is STRATEGIES["orb"].research_spec


def test_ema_research_spec_matches_supported_operational_keys() -> None:
    spec = get_family_research_spec("ema")
    for key in {"gap_filter", "gap_pct", "use_range_shift", "range_shift_lookback"}:
        assert key in spec.allowed_config_keys
        assert key in spec.config_schema
        assert any(key in rule for rule in spec.config_rules)


def test_save_research_finding_rejects_bad_type(monkeypatch):
    tracked: dict[str, object] = {}

    def fake_track(server, org_id, cfg):
        tracked["calls"] = tracked.get("calls", 0) + 1
        tracked["server"] = server
        tracked["org_id"] = org_id
        tracked["cfg"] = cfg

    monkeypatch.setattr(tools_mcp, "track", fake_track)

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
    assert tracked["calls"] == 1
    assert tracked["org_id"] == "a042226c-b858-46f3-9756-b1e675c03c13"
    identity = tracked["cfg"].identify(
        {"headers": {"x-user-id": "user-123", "mcp-session-id": "session-456"}},
        {"USER": "fallback-user"},
    )
    assert identity == {
        "userId": "user-123",
        "sessionId": "session-456",
        "conversationId": "session-456",
        "email": None,
        "clientId": None,
    }
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


def test_save_research_finding_falls_back_to_local_log(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_ROOT", tmp_path)
    monkeypatch.setattr(memory, "_PALACE_DIR", str(tmp_path / "palace"))
    monkeypatch.setattr(
        memory,
        "_palace_add",
        lambda *a, **k: {"success": False, "error": "palace unavailable"},
    )

    result = memory.save_research_finding(
        finding="window tightened",
        finding_type="observation",
        status="validated",
        evidence="round_001",
        scope="full_sample",
        expires_if="baseline drift",
    )

    assert result == "SAVED (local): observation/validated — window tightened"
    log_path = tmp_path / "research_findings.jsonl"
    assert log_path.exists()
    payload = json.loads(log_path.read_text().strip())
    assert payload["finding"] == "window tightened"
    assert payload["type"] == "observation"
    assert payload["status"] == "validated"


def test_palace_helpers_return_error_objects_when_unavailable(monkeypatch):
    fake_mempalace = ModuleType("mempalace")
    fake_mempalace.__path__ = []  # type: ignore[attr-defined]
    fake_palace = ModuleType("mempalace.palace")
    fake_searcher = ModuleType("mempalace.searcher")
    fake_layers = ModuleType("mempalace.layers")

    def boom(*args, **kwargs):
        raise RuntimeError("palace offline")

    class _BoomStack:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("palace offline")

    fake_palace.get_collection = boom
    fake_searcher.search_memories = boom
    fake_layers.MemoryStack = _BoomStack
    fake_mempalace.searcher = fake_searcher
    fake_mempalace.layers = fake_layers
    fake_mempalace.palace = fake_palace

    monkeypatch.setitem(sys.modules, "mempalace", fake_mempalace)
    monkeypatch.setitem(sys.modules, "mempalace.palace", fake_palace)
    monkeypatch.setitem(sys.modules, "mempalace.searcher", fake_searcher)
    monkeypatch.setitem(sys.modules, "mempalace.layers", fake_layers)

    assert memory._palace_add("wing", "room", "content") == {
        "success": False,
        "error": "palace offline",
    }
    assert memory._palace_search("query") == [{"error": "palace offline"}]
    assert memory._palace_status() == {"error": "palace offline"}


def test_resolve_palace_dir_prefers_existing_configured_path(monkeypatch, tmp_path):
    configured = tmp_path / "configured-palace"
    configured.mkdir()
    monkeypatch.setenv("AUTORESEARCH_MEMPALACE_PALACE", str(configured))
    monkeypatch.setattr(memory, "_PALACE_DIR", str(tmp_path / "repo-palace"))

    assert memory._resolve_palace_dir() == str(configured)


def test_resolve_palace_dir_creates_missing_configured_path(monkeypatch, tmp_path):
    configured = tmp_path / "missing-palace"
    monkeypatch.setenv("AUTORESEARCH_MEMPALACE_PALACE", str(configured))
    monkeypatch.setattr(memory, "_PALACE_DIR", str(tmp_path / "repo-palace"))

    resolved = memory._resolve_palace_dir()

    assert resolved == str(configured)
    assert configured.is_dir()


def test_resolve_palace_dir_prefers_existing_repo_palace(monkeypatch, tmp_path):
    repo_palace = tmp_path / "repo-palace"
    repo_palace.mkdir()
    home_palace = tmp_path / "home-palace"
    home_palace.mkdir()
    monkeypatch.delenv("AUTORESEARCH_MEMPALACE_PALACE", raising=False)
    monkeypatch.setattr(memory, "_PALACE_DIR", str(repo_palace))
    monkeypatch.setattr(memory.Path, "home", classmethod(lambda cls: home_palace.parent))

    assert memory._resolve_palace_dir() == str(repo_palace)


def test_call_web_researcher_uses_codex_cli_web_search(monkeypatch):
    import trace_sdk

    monkeypatch.setattr(trace_sdk, "trace", lambda *a, **k: None)
    monkeypatch.setattr(trace_sdk, "trace_agent_response", lambda *a, **k: None)

    captured: dict[str, object] = {}

    def fake_run_codex_web_research(prompt, *, instructions, model):
        captured["prompt"] = prompt
        captured["instructions"] = instructions
        captured["model"] = model
        return (
            json.dumps(
                {
                    "findings": [
                        {
                            "topic": "microstructure",
                            "finding": "codex CLI web search returned valid JSON",
                            "source": None,
                            "source_quality": "practitioner",
                            "actionable_idea": "use the verified CLI web-search boundary",
                        }
                    ],
                    "summary": "codex cli text extraction works",
                }
            ),
            {"exit_code": 0, "output_len": 10},
        )

    monkeypatch.setattr(subagents, "run_codex_web_research", fake_run_codex_web_research)

    result = asyncio.run(subagents._call_web_researcher("prompt", "context"))

    assert "RESEARCH QUESTION: prompt" in captured["prompt"]
    assert "CONTEXT: context" in captured["prompt"]
    assert "Run targeted web searches" in captured["instructions"]
    assert captured["model"] == subagents._CONDUCTOR_MODEL
    parsed = json.loads(result)
    assert parsed["summary"] == "codex cli text extraction works"
    assert parsed["findings"][0]["finding"] == "codex CLI web search returned valid JSON"


def test_extract_runner_output_text_uses_raw_response_output_text(monkeypatch):
    import research_paths

    class _Result:
        def __init__(self):
            self.final_output = ""
            self.new_items = []
            self.raw_responses = [
                type(
                    "RawResponse",
                    (),
                    {
                        "output_text": '{"findings":[{"topic":"x","finding":"y","source":null,"source_quality":"practitioner","actionable_idea":"z"}],"summary":"ok"}',
                        "output": [],
                    },
                )()
            ]

    assert (
        research_paths._extract_runner_output_text(_Result())
        == '{"findings":[{"topic":"x","finding":"y","source":null,"source_quality":"practitioner","actionable_idea":"z"}],"summary":"ok"}'
    )


def test_extract_runner_output_text_uses_raw_response_output_items(monkeypatch):
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    import research_paths

    class _Response:
        def __init__(self):
            self.output = [
                ResponseOutputMessage.model_construct(
                    id="msg_1",
                    content=[
                        ResponseOutputText.model_construct(
                            annotations=[],
                            text='{"findings":[{"topic":"x","finding":"y","source":null,"source_quality":"practitioner","actionable_idea":"z"}],"summary":"ok"}',
                            type="output_text",
                        )
                    ],
                    role="assistant",
                    status="completed",
                    type="message",
                )
            ]

    class _Result:
        def __init__(self):
            self.final_output = ""
            self.new_items = []
            self.raw_responses = [_Response()]

    assert (
        research_paths._extract_runner_output_text(_Result())
        == '{"findings":[{"topic":"x","finding":"y","source":null,"source_quality":"practitioner","actionable_idea":"z"}],"summary":"ok"}'
    )


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
