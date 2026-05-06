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
    assert {tool.name for tool in captured["agent"].tools} >= {
        "list_past_theses",
        "get_past_thesis",
        "list_experiment_results",
        "get_experiment_result",
    }
    assert captured["input"].startswith("Research round: 3")
    assert captured["kwargs"]["max_turns"] == 50
    assert captured["kwargs"]["run_config"].tracing_disabled is True


def test_conductor_prompt_handles_no_trades_without_calling_it_cold_start(monkeypatch):
    parsed_payload = {"suggested_theses": [], "should_stop": True}
    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)

    captured: dict[str, object] = {}

    class _FakeResult:
        final_output = json.dumps(parsed_payload)
        raw_responses: list[object] = []

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(starting_agent, input, **kwargs):
        captured["input"] = input
        return _FakeResult()

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="",
        experiment_results="total_experiments=12 keep=3 discard=9",
        latest_outcome={"thesis_id": "latest_real_thesis", "metric": 1.8, "decision": "discard"},
        research_round=9,
        family_name="ema",
    )

    assert result == parsed_payload
    prompt = str(captured["input"])
    assert "LATEST EXPERIMENT OUTCOME:" in prompt
    assert "latest_real_thesis" in prompt
    assert "No experiments have been run yet" not in prompt
    assert "propose your first thesis" not in prompt
    assert "No trades file is available for the latest/current experiment" in prompt
    assert "Do not call analyze_trades this round" in prompt


@pytest.mark.parametrize(
    ("suggested_theses", "validation_reason"),
    [
        ({"thesis_id": "not_a_list"}, "suggested_theses must be a list"),
        (["not_an_object"], "suggested_theses[0] must be an object"),
    ],
)
def test_run_research_conductor_sync_returns_error_for_malformed_suggested_theses(
    monkeypatch,
    suggested_theses,
    validation_reason,
):
    parsed_payload = {
        "reasoning": "malformed",
        "suggested_theses": suggested_theses,
        "should_stop": False,
    }
    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)

    class _FakeResult:
        final_output = json.dumps(parsed_payload)
        raw_responses: list[object] = []

        async def stream_events(self):
            if False:
                yield None

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", lambda *a, **k: _FakeResult())

    result = rc.run_research_conductor_sync(
        trades_file="/tmp/trades.csv",
        experiment_results="results",
        latest_outcome={"profit_factor": 1.2},
        research_round=3,
        family_name="ema",
    )

    assert result["status"] == "conductor_error"
    assert result["error"] == "validation_failed"
    assert result["validation_reason"] == validation_reason
    assert result["suggested_theses"] == []


def test_conductor_prompt_keeps_true_cold_start_distinct(monkeypatch):
    parsed_payload = {"suggested_theses": [], "should_stop": True}
    monkeypatch.setattr(rc, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(rc, "trace", lambda *a, **k: None)
    monkeypatch.setattr(rc, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(rc, "trace_agent_response", lambda *a, **k: None)

    captured: dict[str, object] = {}

    class _FakeResult:
        final_output = json.dumps(parsed_payload)
        raw_responses: list[object] = []

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(starting_agent, input, **kwargs):
        captured["input"] = input
        return _FakeResult()

    monkeypatch.setattr(rc.OAIRunner, "run_streamed", fake_run_streamed)

    result = rc.run_research_conductor_sync(
        trades_file="",
        experiment_results="No experiments run yet.",
        latest_outcome={},
        research_round=1,
        family_name="ema",
    )

    assert result == parsed_payload
    prompt = str(captured["input"])
    assert "LATEST EXPERIMENT OUTCOME:" in prompt
    assert "(no results yet)" in prompt
    assert "No current-job experiments have completed yet" in prompt
    assert "propose the first thesis" in prompt


def test_conductor_system_prompt_prioritizes_practical_pf_without_ambiguous_tool_order():
    prompt = rc._build_conductor_system_prompt("Strategy description")

    assert "Write your reasoning in your response before making tool calls" not in prompt
    assert "Final reasoning should cite the evidence gathered from tools" in prompt
    assert "Improve profit_factor" in prompt
    assert "median_expectancy" in prompt
    assert "margin_per_order" in prompt
    assert "trade_count" in prompt
    assert 'list_experiment_results(order="latest")' in prompt
    assert 'list_experiment_results(order="best")' in prompt
    assert "get_experiment_result for the latest result" in prompt
    assert "get_experiment_result for the best profit_factor result" in prompt


def test_conductor_system_prompt_is_not_duplicate_and_labels_soft_requirements():
    prompt = rc._build_conductor_system_prompt("Strategy description")

    assert "WHAT YOU ARE:" not in prompt
    assert "YOU ARE NOT:" not in prompt
    assert "THE RESEARCH PROCESS:" not in prompt
    assert (
        "If you call\n   analyze_trades before web_search, your thesis will be REJECTED"
        not in prompt
    )
    assert "This is workflow guidance; the final thesis validator checks thesis structure" in prompt
    assert "THESIS REQUIREMENTS:" in prompt


def test_conductor_system_prompt_exposes_required_diagnostics_for_non_builtin_metrics():
    prompt = rc._build_conductor_system_prompt("Strategy description")

    assert '"required_diagnostics": []' in prompt
    assert "For non-built-in metrics such as margin_per_order" in prompt
    assert "list them in required_diagnostics" in prompt


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
        "unmetered_calls": 0,
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
            "unmetered_calls": 0,
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
    assert round_usage["by_agent"]["analyst"]["unmetered_calls"] == 0


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
        search_research_findings=memory.search_research_findings,
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


def test_save_research_finding_strips_duplicated_metadata_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_ROOT", tmp_path)
    captured: dict[str, str] = {}

    def fake_add(wing, room, content, added_by="conductor"):
        captured["wing"] = wing
        captured["room"] = room
        captured["content"] = content
        return {"success": True, "drawer_id": "drawer-1"}

    monkeypatch.setattr(memory, "_palace_add", fake_add)

    result = memory.save_research_finding(
        finding=(
            "TYPE:validated_finding | STATUS:validated | EVIDENCE:round_21 analyst | "
            "SCOPE:job_20 | EXPIRES_IF:baseline changes\n"
            "Opening-range CLV separates mid-balance days from extreme days."
        ),
        finding_type="validated_finding",
        status="validated",
        evidence="round_21 analyst",
        scope="job_20",
        expires_if="baseline changes",
    )

    assert result == (
        "SAVED: validated_finding/validated — "
        "Opening-range CLV separates mid-balance days from extreme days."
    )
    assert captured["wing"] == "research_findings"
    assert captured["room"] == "validated_finding"
    assert captured["content"].count("TYPE:") == 1
    assert captured["content"].endswith(
        "Opening-range CLV separates mid-balance days from extreme days."
    )


def test_search_research_findings_reads_local_fallback_when_palace_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_ROOT", tmp_path)
    monkeypatch.setattr(memory, "_palace_search", lambda *a, **k: [])
    log_path = tmp_path / "research_findings.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "finding": "Opening-range CLV separates mid-balance days from extreme days.",
                "type": "validated_finding",
                "status": "validated",
                "evidence": "round_21 analyst",
                "scope": "job_20",
                "expires_if": "baseline changes",
                "timestamp": 1,
            }
        )
        + "\n"
    )

    results = memory.search_research_findings(
        query="CLV mid balance",
        finding_type="validated_finding",
        n_results=5,
    )

    assert len(results) == 1
    assert results[0]["room"] == "validated_finding"
    assert results[0]["source"] == "local_jsonl"
    assert "Opening-range CLV" in results[0]["text"]


def test_search_research_findings_uses_local_fallback_when_palace_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_ROOT", tmp_path)
    monkeypatch.setattr(memory, "_palace_search", lambda *a, **k: [{"error": "palace offline"}])
    (tmp_path / "research_findings.jsonl").write_text(
        json.dumps(
            {
                "finding": "Late opening-window entries underperform.",
                "type": "observation",
                "status": "validated",
                "evidence": "round_19 analyst",
                "scope": "job_20",
                "expires_if": "baseline changes",
                "timestamp": 1,
            }
        )
        + "\n"
    )

    results = memory.search_research_findings(query="opening entries", n_results=5)

    assert results == [
        {
            "text": (
                "TYPE:observation | STATUS:validated | EVIDENCE:round_19 analyst | "
                "SCOPE:job_20 | EXPIRES_IF:baseline changes\n"
                "Late opening-window entries underperform."
            ),
            "room": "observation",
            "distance": "local",
            "source": "local_jsonl",
        }
    ]


def test_mcp_search_findings_uses_fallback_aware_search(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mcp, "track", lambda *a, **k: None)
    calls: dict[str, object] = {}

    def fake_search_research_findings(*, query, finding_type="", n_results=10):
        calls["args"] = {
            "query": query,
            "finding_type": finding_type,
            "n_results": n_results,
        }
        return [
            {
                "text": "TYPE:observation | STATUS:validated\nLocal fallback finding",
                "room": "observation",
                "distance": "local",
            }
        ]

    mcp = tools_mcp._build_research_tools_mcp(
        trades_file="/tmp/trades.csv",
        call_analyst=subagents._call_analyst,
        call_web_researcher=subagents._call_web_researcher,
        save_research_finding=memory.save_research_finding,
        search_research_findings=fake_search_research_findings,
        palace_status=memory._palace_status,
        root=tmp_path,
        list_past_theses_for_root=memory.list_past_theses,
    )
    tool = next(tool for tool in mcp._tool_manager.list_tools() if tool.name == "search_findings")

    output = asyncio.run(tool.fn(query="fallback", finding_type="observation"))

    assert calls["args"] == {
        "query": "fallback",
        "finding_type": "observation",
        "n_results": 10,
    }
    assert "Local fallback finding" in output


def test_experiment_results_summary_tolerates_non_numeric_metric_values():
    from agent_formatters import format_experiment_results_summary

    output = format_experiment_results_summary(
        [
            {"thesis_id": "bad_metric", "metric": "N/A", "status": "discard"},
            {"thesis_id": "good_metric", "metric": 1.7, "status": "keep"},
        ]
    )

    assert "best: good_metric | metric=1.7 | status=keep" in output
    assert "latest: good_metric | metric=1.7 | status=keep" in output


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
    traces: list[tuple[str, str, dict | None]] = []
    monkeypatch.setattr(
        subagents,
        "trace",
        lambda component, message, data=None, **kwargs: traces.append((component, message, data)),
    )
    responses: list[tuple[str, str, str, dict | None]] = []
    monkeypatch.setattr(
        subagents,
        "trace_agent_response",
        lambda agent, trace_id, raw, parsed=None, **kwargs: responses.append(
            (agent, trace_id, raw, parsed)
        ),
    )
    trace_prompts: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        subagents,
        "trace_agent_prompt",
        lambda agent, prompt, system_prompt, **kwargs: trace_prompts.append(
            (agent, prompt, system_prompt)
        )
        or "web-trace-id",
    )

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
            {
                "exit_code": 0,
                "output_len": 10,
                "usage_source": "codex_json_turn_completed",
            },
        )

    monkeypatch.setattr(subagents, "run_codex_web_research", fake_run_codex_web_research)

    result = asyncio.run(subagents._call_web_researcher("prompt", "context"))

    assert "RESEARCH QUESTION: prompt" in captured["prompt"]
    assert "CONTEXT: context" in captured["prompt"]
    assert "Run targeted web searches" in captured["instructions"]
    assert captured["model"] == subagents._CONDUCTOR_MODEL
    assert trace_prompts
    assert trace_prompts[0][0] == "web-researcher"
    assert trace_prompts[0][1] == captured["prompt"]
    assert trace_prompts[0][2] == captured["instructions"]
    assert responses
    assert responses[0][0] == "web-researcher"
    assert responses[0][1] == "web-trace-id"
    structured_completion = next(
        data for _component, message, data in traces if message == "web_search codex_cli completed"
    )
    assert structured_completion == {
        "exit_code": 0,
        "output_len": 10,
        "usage_source": "codex_json_turn_completed",
    }
    structured_success = next(
        data for _component, message, data in traces if message == "web_search OK"
    )
    assert structured_success == {"findings": 1}
    parsed = json.loads(result)
    assert parsed["summary"] == "codex cli text extraction works"
    assert parsed["findings"][0]["finding"] == "codex CLI web search returned valid JSON"


def test_analyst_prompt_uses_configured_data_root_and_warns_tools_are_stateless(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", "/root/autoresearch-data")
    captured: dict[str, str] = {}

    def fake_trace_agent_prompt(agent_name, prompt, system_prompt="", **kwargs):
        captured["agent_name"] = agent_name
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "analyst-trace-id"

    monkeypatch.setattr(subagents, "trace_agent_prompt", fake_trace_agent_prompt)
    monkeypatch.setattr(subagents, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(subagents, "_get_openai_client", lambda *_: object())

    class FakeResult:
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)

        async def stream_events(self):
            if False:
                yield None

        def final_output_as(self, _type):
            return json.dumps(
                {
                    "focus_answer": "ok",
                    "key_anomalies": [],
                    "rejection_insights": [],
                    "overall_diagnosis": "ok",
                    "discovery_questions": [],
                }
            )

    class FakeRunner:
        @staticmethod
        def run_streamed(*args, **kwargs):
            return FakeResult()

    monkeypatch.setitem(sys.modules, "agents", ModuleType("agents"))
    agents_mod = sys.modules["agents"]
    agents_mod.Agent = lambda **kwargs: SimpleNamespace(**kwargs)
    agents_mod.RunConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    agents_mod.Runner = FakeRunner
    agents_mod.function_tool = lambda fn: fn
    models_mod = ModuleType("agents.models.openai_chatcompletions")
    models_mod.OpenAIChatCompletionsModel = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "agents.models", ModuleType("agents.models"))
    monkeypatch.setitem(sys.modules, "agents.models.openai_chatcompletions", models_mod)

    result = asyncio.run(
        subagents._call_analyst(
            str(tmp_path / "trades.csv"),
            "check opening regime",
            strategy_events_file=str(tmp_path / "events.parquet"),
            diagnostics_file=str(tmp_path / "diagnostics.json"),
        )
    )

    assert json.loads(result)["overall_diagnosis"] == "ok"
    assert captured["agent_name"] == "analyst"
    assert "AUTORESEARCH_DATA_ROOT=/root/autoresearch-data" in captured["system_prompt"]
    assert "/root/autoresearch-data/universes/" in captured["system_prompt"]
    assert "Do NOT probe" in captured["system_prompt"]
    assert "data unless AUTORESEARCH_DATA_ROOT is unset" in captured["system_prompt"]
    assert "Each run_python call is stateless" in captured["system_prompt"]


def test_analyst_prompt_includes_market_data_manifest_from_runtime_config(monkeypatch, tmp_path):
    data_root = tmp_path / "autoresearch-data"
    universe_dir = data_root / "universes" / "nasdaq8"
    universe_dir.mkdir(parents=True)
    for name in ("open.parquet", "high.parquet", "low.parquet", "close.parquet", "volume.parquet"):
        (universe_dir / name).write_text("placeholder")
    (universe_dir / "manifest.json").write_text('{"symbols":["AAPL","MSFT"]}')
    monkeypatch.setenv("AUTORESEARCH_DATA_ROOT", str(data_root))

    config_hash = "abc123def456"
    experiment_dir = tmp_path / "experiments" / config_hash
    experiment_dir.mkdir(parents=True)
    (experiment_dir / "runtime_config.json").write_text(
        json.dumps({"runtime_config": {"data_universe": "nasdaq8"}})
    )
    artifact_dir = tmp_path / "ema_autoresearch-runs" / "job-20" / "commitsha" / config_hash
    artifact_dir.mkdir(parents=True)
    trades_file = artifact_dir / "trades.csv"
    events_file = artifact_dir / "strategy_events.parquet"
    diagnostics_file = artifact_dir / "diagnostics.json"

    captured: dict[str, str] = {}

    def fake_trace_agent_prompt(agent_name, prompt, system_prompt="", **kwargs):
        captured["agent_name"] = agent_name
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "analyst-trace-id"

    monkeypatch.setattr(subagents, "trace_agent_prompt", fake_trace_agent_prompt)
    monkeypatch.setattr(subagents, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(subagents, "_get_openai_client", lambda *_: object())

    class FakeResult:
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2)

        async def stream_events(self):
            if False:
                yield None

        def final_output_as(self, _type):
            return json.dumps(
                {
                    "focus_answer": "ok",
                    "key_anomalies": [],
                    "rejection_insights": [],
                    "overall_diagnosis": "ok",
                    "discovery_questions": [],
                }
            )

    class FakeRunner:
        @staticmethod
        def run_streamed(*args, **kwargs):
            return FakeResult()

    monkeypatch.setitem(sys.modules, "agents", ModuleType("agents"))
    agents_mod = sys.modules["agents"]
    agents_mod.Agent = lambda **kwargs: SimpleNamespace(**kwargs)
    agents_mod.RunConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    agents_mod.Runner = FakeRunner
    agents_mod.function_tool = lambda fn: fn
    models_mod = ModuleType("agents.models.openai_chatcompletions")
    models_mod.OpenAIChatCompletionsModel = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "agents.models", ModuleType("agents.models"))
    monkeypatch.setitem(sys.modules, "agents.models.openai_chatcompletions", models_mod)

    result = asyncio.run(
        subagents._call_analyst(
            str(trades_file),
            "check opening regime",
            strategy_events_file=str(events_file),
            diagnostics_file=str(diagnostics_file),
        )
    )

    assert json.loads(result)["overall_diagnosis"] == "ok"
    system_prompt = captured["system_prompt"]
    assert "MARKET DATA MANIFEST:" in system_prompt
    assert f"runtime_config: {experiment_dir / 'runtime_config.json'}" in system_prompt
    assert "data_universe: nasdaq8" in system_prompt
    assert f"universe_path: {universe_dir}" in system_prompt
    assert f"open: {universe_dir / 'open.parquet'}" in system_prompt
    assert f"volume: {universe_dir / 'volume.parquet'}" in system_prompt
    assert "Do NOT run recursive filesystem discovery" in system_prompt
    assert "/root/**" in system_prompt


def test_compact_tool_output_truncates_large_text_with_hash():
    text = "x" * 30_000

    compact, truncated = subagents._compact_tool_output(text, max_chars=12_000)

    assert truncated is True
    assert len(compact) < 13_000
    assert "truncated" in compact
    assert "sha256=" in compact


def test_resolve_tool_max_chars_falls_back_for_bad_llm_tool_arg():
    assert subagents._resolve_tool_max_chars("not-a-number", default=12_000) == 12_000
    assert subagents._resolve_tool_max_chars(None, default=12_000) == 12_000
    assert subagents._resolve_tool_max_chars(999_999, default=12_000) == 20_000
    assert subagents._resolve_tool_max_chars(10, default=12_000) == 1_000


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
        search_research_findings=memory.search_research_findings,
        palace_status=memory._palace_status,
        root=tmp_path,
        list_past_theses_for_root=memory.list_past_theses,
    )
    tool = next(tool for tool in mcp._tool_manager.list_tools() if tool.name == "list_past_theses")

    payload = asyncio.run(tool.fn())
    parsed = json.loads(payload)

    assert {entry["thesis_id"] for entry in parsed["entries"]} == {"ema_one", "orb_two"}
    assert parsed["total"] == 2
    assert parsed["limit"] == 25
    assert parsed["has_more"] is False


def test_mcp_research_history_tools_use_bound_current_job(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mcp, "track", lambda *a, **k: None)
    calls: dict[str, tuple[object, ...] | dict[str, object]] = {}

    def fake_list_past(root, *, job_id=None, offset=0, limit=25):
        calls["list_past"] = {"job_id": job_id, "offset": offset, "limit": limit}
        return json.dumps({"job_id": job_id, "entries": []})

    def fake_get_past(root, thesis_id, *, job_id=None):
        calls["get_past"] = (thesis_id, job_id)
        return json.dumps({"thesis_id": thesis_id, "job_id": job_id})

    def fake_list_results(root, *, job_id=None, order="latest", offset=0, limit=10):
        calls["list_results"] = {
            "job_id": job_id,
            "order": order,
            "offset": offset,
            "limit": limit,
        }
        return json.dumps({"job_id": job_id, "entries": []})

    def fake_get_result(root, thesis_id, *, job_id=None):
        calls["get_result"] = (thesis_id, job_id)
        return json.dumps({"thesis_id": thesis_id, "job_id": job_id})

    mcp = tools_mcp._build_research_tools_mcp(
        trades_file="/tmp/trades.csv",
        call_analyst=subagents._call_analyst,
        call_web_researcher=subagents._call_web_researcher,
        save_research_finding=memory.save_research_finding,
        search_research_findings=memory.search_research_findings,
        palace_status=memory._palace_status,
        root=tmp_path,
        current_job=20,
        list_past_theses_for_root=fake_list_past,
        get_past_thesis_for_root=fake_get_past,
        list_experiment_results_for_root=fake_list_results,
        get_experiment_result_for_root=fake_get_result,
    )
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    list_past_payload = json.loads(asyncio.run(tools["list_past_theses"].fn(limit=3)))
    get_past_payload = json.loads(asyncio.run(tools["get_past_thesis"].fn("prior_thesis")))
    list_results_payload = json.loads(
        asyncio.run(tools["list_experiment_results"].fn(order="best", limit=2))
    )
    get_result_payload = json.loads(asyncio.run(tools["get_experiment_result"].fn("result_thesis")))

    assert list_past_payload["job_id"] == 20
    assert get_past_payload["job_id"] == 20
    assert list_results_payload["job_id"] == 20
    assert get_result_payload["job_id"] == 20
    assert calls["list_past"] == {"job_id": 20, "offset": 0, "limit": 3}
    assert calls["get_past"] == ("prior_thesis", 20)
    assert calls["list_results"] == {"job_id": 20, "order": "best", "offset": 0, "limit": 2}
    assert calls["get_result"] == ("result_thesis", 20)


def test_mcp_research_history_tools_do_not_expose_job_id_override(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_mcp, "track", lambda *a, **k: None)
    mcp = tools_mcp._build_research_tools_mcp(
        trades_file="/tmp/trades.csv",
        call_analyst=subagents._call_analyst,
        call_web_researcher=subagents._call_web_researcher,
        save_research_finding=memory.save_research_finding,
        search_research_findings=memory.search_research_findings,
        palace_status=memory._palace_status,
        root=tmp_path,
        current_job=20,
        list_past_theses_for_root=memory.list_past_theses,
    )
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    for name in (
        "list_past_theses",
        "get_past_thesis",
        "list_experiment_results",
        "get_experiment_result",
    ):
        assert "job_id" not in tools[name].fn.__annotations__


def test_list_past_theses_filters_by_job_and_returns_bounded_index(tmp_path):
    from experiment_db import ExperimentDB

    db = ExperimentDB(tmp_path / "ema_experiments.db")
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-20-round-1",
            "attempt_number": 1,
            "thesis_id": "same_job_thesis",
            "strategy_family": "ema",
            "config_changes": {"entry_window": "09:35"},
            "validator_status": "accepted",
            "mechanism_dimension": "entry_timing",
            "hypothesis": "same job hypothesis " + ("x" * 300),
            "rejection_reason": "",
            "selected_for_execution": 1,
        }
    )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-19-round-1",
            "attempt_number": 1,
            "thesis_id": "other_job_thesis",
            "strategy_family": "ema",
            "config_changes": {"exit_rule": "close"},
            "validator_status": "rejected",
            "mechanism_dimension": "exit_mechanism",
            "hypothesis": "other job hypothesis",
            "rejection_reason": "different job",
            "selected_for_execution": 0,
        }
    )
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-20-round-1",
                20,
                1,
                "run-20",
                "same_job_thesis",
                "same_job_thesis",
                "accepted",
                "2026-05-06T00:00:00+00:00",
                "{}",
            ),
        )
        conn.execute(
            """
            INSERT INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-19-round-1",
                19,
                1,
                "run-19",
                "other_job_thesis",
                "other_job_thesis",
                "rejected",
                "2026-05-06T00:00:00+00:00",
                "{}",
            ),
        )
        conn.commit()

    parsed = json.loads(memory.list_past_theses(tmp_path, job_id=20, limit=1))

    assert parsed["total"] == 1
    assert parsed["job_id"] == 20
    assert parsed["has_more"] is False
    assert parsed["entries"] == [
        {
            "thesis_id": "same_job_thesis",
            "round": "job-20-round-1",
            "round_number": 1,
            "job_id": 20,
            "strategy_family": "ema",
            "outcome": "accepted",
            "mechanism_dimension": "entry_timing",
            "dimension_novelty": "",
            "requires_code_change": False,
            "expected_effect_metrics": [],
            "evidence_count": 0,
            "selected_for_execution": True,
            "config_change_keys": ["entry_window"],
            "short_hypothesis": "same job hypothesis " + ("x" * 160) + "...",
            "short_rejection_reason": "",
            "learning_signal": "accepted thesis in entry_timing; inspect full details before building on it",
        }
    ]


def test_list_past_theses_orders_by_numeric_round_desc(tmp_path):
    from experiment_db import ExperimentDB

    db = ExperimentDB(tmp_path / "ema_experiments.db")
    for round_number in (2, 10):
        db.add_research_thesis_attempt(
            {
                "research_round_id": f"job-20-round-{round_number}",
                "attempt_number": 1,
                "thesis_id": f"thesis_round_{round_number}",
                "strategy_family": "ema",
                "config_changes": {"round": round_number},
                "validator_status": "compiled",
                "mechanism_dimension": "entry_timing",
                "hypothesis": f"hypothesis {round_number}",
                "mechanism": f"mechanism {round_number}",
                "selected_for_execution": 1,
            }
        )
        with db._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_rounds (
                    research_round_id, job_id, round_number, run_id, hypothesis_id,
                    selected_thesis_id, outcome, created_at_utc, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"job-20-round-{round_number}",
                    20,
                    round_number,
                    f"run-{round_number}",
                    f"thesis_round_{round_number}",
                    f"thesis_round_{round_number}",
                    "compiled",
                    "2026-05-06T00:00:00+00:00",
                    "{}",
                ),
            )
            conn.commit()

    parsed = json.loads(memory.list_past_theses(tmp_path, job_id=20, limit=2))

    assert [entry["thesis_id"] for entry in parsed["entries"]] == [
        "thesis_round_10",
        "thesis_round_2",
    ]
    assert [entry["round_number"] for entry in parsed["entries"]] == [10, 2]


def test_get_past_thesis_returns_full_details_for_selected_job(tmp_path):
    from experiment_db import ExperimentDB

    db = ExperimentDB(tmp_path / "ema_experiments.db")
    full_hypothesis = "full hypothesis " + ("kept " * 80)
    full_rejection = "full rejection " + ("reason " * 80)
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-20-round-2",
            "attempt_number": 2,
            "thesis_id": "fetch_me",
            "strategy_family": "ema",
            "config_changes": {"atr_filter": {"min": 1.5}},
            "validator_status": "rejected",
            "mechanism_dimension": "risk_filtering",
            "hypothesis": full_hypothesis,
            "mechanism": "full mechanism detail",
            "rejection_reason": full_rejection,
            "selected_for_execution": 0,
        }
    )
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-20-round-2",
                20,
                2,
                "run-20",
                "fetch_me",
                "fetch_me",
                "rejected",
                "2026-05-06T00:00:00+00:00",
                '{"total":{"total_tokens":123}}',
            ),
        )
        conn.commit()

    parsed = json.loads(memory.get_past_thesis(tmp_path, "fetch_me", job_id=20))

    assert parsed["thesis_id"] == "fetch_me"
    assert parsed["job_id"] == 20
    assert parsed["attempts"][0]["hypothesis"] == full_hypothesis
    assert parsed["attempts"][0]["rejection_reason"] == full_rejection
    assert parsed["attempts"][0]["round_usage"] == {"total": {"total_tokens": 123}}
    assert parsed["attempts"][0]["dimension_novelty"] == ""
    assert parsed["attempts"][0]["evidence"] == []
    assert parsed["attempts"][0]["expected_effects"] == []
    assert parsed["attempts"][0]["disqualifiers"] == []
    assert parsed["attempts"][0]["why_not_overfit"] == ""
    assert parsed["attempts"][0]["requires_code_change"] is False
    assert parsed["attempts"][0]["required_diagnostics"] == []


def test_get_past_thesis_returns_persisted_full_thesis_contract(tmp_path):
    from experiment_db import ExperimentDB

    db = ExperimentDB(tmp_path / "ema_experiments.db")
    thesis_details = {
        "dimension_novelty": "Tests opening-liquidity decay, not a time parameter tweak.",
        "evidence": ["web: opening auction imbalance", "analyst: 215 trades before 10:00"],
        "expected_effects": [
            {"metric": "profit_factor", "direction": "increase", "threshold": 0.05}
        ],
        "disqualifiers": [{"name": "trade_count_collapse", "condition": "trade_count < 150"}],
        "why_not_overfit": "Mechanism is tied to opening auction microstructure.",
        "requires_code_change": True,
        "required_diagnostics": ["margin_per_order"],
    }
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-20-round-3",
            "attempt_number": 1,
            "thesis_id": "full_contract",
            "strategy_family": "ema",
            "config_changes": {"requires_engine_change": True},
            "validator_status": "halted",
            "mechanism_dimension": "market_microstructure",
            "hypothesis": "opening imbalance should explain edge",
            "mechanism": "opening auction liquidity decay",
            "selected_for_execution": 0,
            "thesis_details": thesis_details,
        }
    )
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-20-round-3",
                20,
                3,
                "run-20",
                "full_contract",
                "full_contract",
                "halted",
                "2026-05-06T00:00:00+00:00",
                "{}",
            ),
        )
        conn.commit()

    listed = json.loads(memory.list_past_theses(tmp_path, job_id=20))
    detailed = json.loads(memory.get_past_thesis(tmp_path, "full_contract", job_id=20))

    assert listed["entries"][0]["dimension_novelty"] == thesis_details["dimension_novelty"]
    assert listed["entries"][0]["requires_code_change"] is True
    assert listed["entries"][0]["expected_effect_metrics"] == ["profit_factor"]
    assert listed["entries"][0]["evidence_count"] == 2
    assert detailed["attempts"][0] | thesis_details == detailed["attempts"][0]


def test_list_experiment_results_reaches_latest_and_best_beyond_prompt_cap(tmp_path):
    from experiment_db import ExperimentDB, ExperimentResult

    db = ExperimentDB(tmp_path / "ema_experiments.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    for idx in range(15):
        thesis_id = f"thesis_{idx:02d}"
        metric = 1.0 + idx / 100
        if idx == 4:
            thesis_id = "best_old_thesis"
            metric = 9.9
        if idx == 14:
            thesis_id = "latest_new_thesis"
            metric = 1.2
        db.add(
            ExperimentResult(
                experiment_id=f"exp-{idx}",
                thesis_id=thesis_id,
                config_path=f"experiments/{thesis_id}/runtime_config.json",
                runtime_config={"param": idx},
                code_commit="abc",
                data_hash="data",
                train_metrics={},
                validation_metrics={
                    "profit_factor": metric,
                    "trade_count": 100 + idx,
                    "max_drawdown": 0.1,
                },
                trade_count=100 + idx,
                trades_file=f"/tmp/{thesis_id}/trades.csv",
                strategy_events_file=f"/tmp/{thesis_id}/strategy_events.parquet",
                diagnostics_file=f"/tmp/{thesis_id}/diagnostics.json",
                strategy_diagnostics={"event_counts": {"raw_setup": idx}},
                accepted=idx in {0, 4},
                rejection_reason="" if idx in {0, 4} else "did not beat best",
                verdict_status="accepted" if idx in {0, 4} else "rejected",
                verdict_summary="ok" if idx in {0, 4} else "below best",
                timestamp=f"2026-05-06T00:{idx:02d}:00+00:00",
                family="ema",
                hypothesis=f"hypothesis {idx}",
                mechanism=f"mechanism {idx}",
                job=20,
                usage={"total": {"total_tokens": idx}},
            )
        )

    latest = json.loads(
        memory.list_experiment_results(tmp_path, job_id=20, order="latest", limit=3)
    )
    best = json.loads(memory.list_experiment_results(tmp_path, job_id=20, order="best", limit=3))

    assert latest["total"] == 15
    assert [entry["thesis_id"] for entry in latest["entries"]][:2] == [
        "latest_new_thesis",
        "thesis_13",
    ]
    assert best["entries"][0]["thesis_id"] == "best_old_thesis"
    assert best["entries"][0]["metric"] == 9.9


def test_get_experiment_result_returns_full_detail_for_job_scoped_thesis(tmp_path):
    from experiment_db import ExperimentDB, ExperimentResult

    db = ExperimentDB(tmp_path / "ema_experiments.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(
        ExperimentResult(
            experiment_id="exp-full",
            thesis_id="full_result",
            config_path="experiments/full_result/runtime_config.json",
            runtime_config={"entry_cutoff_time": "10:00"},
            code_commit="abc",
            data_hash="data",
            train_metrics={},
            validation_metrics={"profit_factor": 2.5, "trade_count": 321},
            trade_count=321,
            trades_file="/tmp/trades.csv",
            strategy_events_file="/tmp/strategy_events.parquet",
            diagnostics_file="/tmp/diagnostics.json",
            strategy_diagnostics={"rejection_breakdown": {"entry_cutoff": 12}},
            accepted=True,
            rejection_reason="",
            verdict_status="accepted",
            verdict_summary="beat threshold",
            timestamp="2026-05-06T00:00:00+00:00",
            family="ema",
            hypothesis="full hypothesis",
            mechanism="full mechanism",
            job=20,
            usage={"total": {"total_tokens": 456}},
        )
    )

    parsed = json.loads(memory.get_experiment_result(tmp_path, "full_result", job_id=20))

    assert parsed["status"] == "ok"
    assert parsed["thesis_id"] == "full_result"
    assert parsed["result"]["runtime_config"] == {"entry_cutoff_time": "10:00"}
    assert parsed["result"]["metrics"]["profit_factor"] == 2.5
    assert parsed["result"]["strategy_diagnostics"] == {"rejection_breakdown": {"entry_cutoff": 12}}
    assert parsed["result"]["usage"] == {"total": {"total_tokens": 456}}
