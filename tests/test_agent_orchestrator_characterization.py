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

import agent_formatters
import agent_infra
import agent_memory
import agent_openai_calls
import agent_orchestrator as ao
import agent_prompts
import agent_runners
import agent_token_usage as usage


def test_run_diagnostic_analysis_returns_validated_result_and_writes_memory(monkeypatch):
    result_payload = {
        "key_anomalies": [
            {
                "pattern": "Open-drive losses after 09:35",
                "numbers": "PF=0.62",
                "sample_size": "142",
                "suggested_exploit": "Tighten entry window",
                "confidence": "high",
            }
        ],
        "overall_diagnosis": "The edge decays after the open.",
        "discovery_questions": ["Does this persist by year?"],
    }
    writes: list[tuple[str, str, str]] = []
    diary_entries: list[tuple[str, str, str]] = []

    monkeypatch.setattr(ao, "trace", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "_mempalace_search", lambda *a, **k: "prior memory")

    async def fake_run(prompt: str):
        assert "RAW TRADES FILE: /tmp/trades.csv" in prompt
        return result_payload

    monkeypatch.setattr(agent_openai_calls, "_run_diagnostic_analyst_openai", fake_run)
    monkeypatch.setattr(
        agent_memory,
        "_mempalace_write",
        lambda wing, room, content: writes.append((wing, room, content)) or True,
    )
    monkeypatch.setattr(
        agent_memory,
        "_mempalace_diary",
        lambda agent_name, topic, entry: diary_entries.append((agent_name, topic, entry)) or True,
    )

    result = ao.analyze_diagnostics_sync(
        trades_file="/tmp/trades.csv",
        config="ema_fast",
        metric=1.23,
        config_contents={"ema_length": 5},
        baseline_results={"profit_factor": 1.23, "trades_file": "/tmp/trades.csv"},
        family="ema",
    )

    assert result == result_payload
    assert writes == [
        (
            "autoresearch",
            "ema-diagnostics",
            "DIAGNOSTIC ANALYSIS: ema_fast PF=1.23\n"
            "DIAGNOSIS: The edge decays after the open.\n"
            "ANOMALIES (1):\n"
            "  [high] Open-drive losses after 09:35 -> Tighten entry window",
        )
    ]
    assert diary_entries == [
        (
            "diagnostic-analyst",
            "ema-analysis",
            "CONFIG:ema_fast|PF:1.23|ANOMALIES:1|The edge decays after the open.",
        )
    ]


def test_run_web_research_returns_findings_and_writes_memory(monkeypatch):
    result_payload = {
        "findings": [
            {
                "topic": "Opening range drift",
                "finding": "Study found weaker continuation after 09:35.",
                "source": "https://example.com/paper",
                "label": "Sourced",
                "source_quality": "academic",
                "actionable_idea": "Limit participation after 09:35",
            }
        ],
        "sources_consulted": ["https://example.com/paper"],
        "confidence_and_gaps": "Limited evidence on short-only variants",
        "summary": "External work supports narrowing the entry window.",
    }
    writes: list[tuple[str, str, str]] = []
    diary_entries: list[tuple[str, str, str]] = []

    monkeypatch.setattr(ao, "trace", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "_mempalace_search", lambda *a, **k: "prior research")

    async def fake_run(prompt: str):
        assert "DIAGNOSTIC INSIGHTS:" in prompt
        return result_payload

    monkeypatch.setattr(agent_openai_calls, "_run_web_research_openai", fake_run)
    monkeypatch.setattr(
        agent_memory,
        "_mempalace_write",
        lambda wing, room, content: writes.append((wing, room, content)) or True,
    )
    monkeypatch.setattr(
        agent_memory,
        "_mempalace_diary",
        lambda agent_name, topic, entry: diary_entries.append((agent_name, topic, entry)) or True,
    )

    result = ao.run_web_research_sync(
        strategy_label="EMA open",
        analyst_brief="Late entries lose money",
        result_summary="PF 1.1",
        research_round=2,
        family="ema",
    )

    assert result == result_payload
    assert writes == [
        (
            "autoresearch",
            "ema-web-research",
            "WEB RESEARCH round=2: External work supports narrowing the entry window.\n"
            "  [Sourced/academic] Opening range drift: Limit participation after 09:35\n"
            "GAPS: Limited evidence on short-only variants",
        )
    ]
    assert diary_entries == [
        (
            "web-researcher",
            "ema-research",
            "ROUND:2|FINDINGS:1|External work supports narrowing the entry window.",
        )
    ]


def test_run_research_agent_validates_thesis_structure(monkeypatch):
    monkeypatch.setattr(ao, "trace", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "_mempalace_search", lambda *a, **k: "prior theses")
    monkeypatch.setattr(agent_memory, "_mempalace_write", lambda *a, **k: True)
    monkeypatch.setattr(agent_memory, "_mempalace_diary", lambda *a, **k: True)
    monkeypatch.setattr(agent_prompts, "MAX_RETRIES", 2)
    monkeypatch.setattr(agent_infra, "SDK_TIMEOUT_SECONDS", 300)

    class FakeSpec:
        strategy_label = "EMA strategy"
        config_rules = ["Only vary approved config keys"]
        config_schema = '{"entry_cutoff_time": "HH:MM"}'
        thesis_json_hint = '"expected_metric": "improve PF"'

    monkeypatch.setitem(
        sys.modules,
        "family_research_spec",
        SimpleNamespace(get_family_research_spec=lambda family_name: FakeSpec()),
    )

    monkeypatch.setattr(
        agent_prompts,
        "_research_agent",
        lambda **kwargs: SimpleNamespace(prompt="system", model="gpt-5.5", maxTurns=15, tools=[]),
    )

    payloads = iter(
        [
            {
                "reasoning": "Missing thesis id",
                "suggested_theses": [
                    {
                        "hypothesis": "test",
                        "mechanism": "too vague",
                        "config_changes": {"entry_cutoff_time": "09:31"},
                    }
                ],
                "should_stop": False,
            },
            {
                "reasoning": "Valid thesis",
                "suggested_theses": [
                    {
                        "thesis_id": "open_window",
                        "hypothesis": "narrow the entry window",
                        "mechanism": "Restrict entries to a narrower opening window.",
                        "mechanism_dimension": "entry_timing",
                        "dimension_novelty": "Tests a narrower open window rather than a simple parameter sweep.",
                        "config_changes": {"entry_cutoff_time": "09:31"},
                        "expected_effects": [
                            {
                                "metric": "profit_factor",
                                "direction": "increase",
                                "threshold": 0.05,
                                "rationale": "Reducing open noise should improve PF.",
                            },
                            {
                                "metric": "trade_count",
                                "direction": "increase_or_same",
                                "rationale": "The narrower window should keep enough setups.",
                            },
                        ],
                        "disqualifiers": [
                            {
                                "name": "trade_count_collapse",
                                "condition": "trade_count decreases by more than 30 percent versus baseline",
                                "severity": "hard_fail",
                            }
                        ],
                        "requires_code_change": False,
                    }
                ],
                "should_stop": False,
            },
        ]
    )

    captured: dict[str, object] = {}

    class Completed:
        final_output = None

        def __init__(self, text: str):
            self.final_output = text
            self.raw_responses = []

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(starting_agent, input, **kwargs):
        captured["agent"] = starting_agent
        captured["input"] = input
        captured["kwargs"] = kwargs
        return Completed("```json\n" + json.dumps(next(payloads)) + "\n```")

    monkeypatch.setattr(agent_runners.OAIRunner, "run_streamed", fake_run_streamed)

    result = ao.run_research_agent_sync(
        {
            "current_best": {
                "config": "best",
                "metric": 1.2,
                "config_contents": {"entry_cutoff_time": "10:00"},
            },
            "result_history": "history",
            "research_round": 3,
            "analyst_brief": "brief",
            "web_findings": "findings",
        },
        family_name="ema",
    )

    assert result == {
        "reasoning": "Valid thesis",
        "suggested_theses": [
            {
                "thesis_id": "open_window",
                "hypothesis": "narrow the entry window",
                "mechanism": "Restrict entries to a narrower opening window.",
                "mechanism_dimension": "entry_timing",
                "dimension_novelty": "Tests a narrower open window rather than a simple parameter sweep.",
                "config_changes": {"entry_cutoff_time": "09:31"},
                "expected_effects": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "threshold": 0.05,
                        "rationale": "Reducing open noise should improve PF.",
                    },
                    {
                        "metric": "trade_count",
                        "direction": "increase_or_same",
                        "rationale": "The narrower window should keep enough setups.",
                    },
                ],
                "disqualifiers": [
                    {
                        "name": "trade_count_collapse",
                        "condition": "trade_count decreases by more than 30 percent versus baseline",
                        "severity": "hard_fail",
                    }
                ],
                "requires_code_change": False,
            }
        ],
        "should_stop": False,
    }
    assert captured["agent"].name == "research-agent"
    assert "Based on all the above" in captured["input"]


def test_run_single_agent_uses_openai_agents_sdk(monkeypatch):
    cli_payload = {
        "reasoning": "Valid thesis",
        "suggested_theses": [
            {
                "thesis_id": "openai_sdk",
                "hypothesis": "narrow the entry window",
                "mechanism": "Restrict entries to a narrower opening window.",
                "mechanism_dimension": "entry_timing",
                "dimension_novelty": "Tests a distinct open window instead of tuning an existing one.",
                "config_changes": {"entry_cutoff_time": "09:31"},
                "expected_effects": [
                    {
                        "metric": "profit_factor",
                        "direction": "increase",
                        "rationale": "Should improve PF by reducing noise.",
                    }
                ],
                "disqualifiers": [
                    {
                        "name": "trade_count_collapse",
                        "condition": "trade_count decreases materially versus baseline",
                        "severity": "hard_fail",
                    }
                ],
            }
        ],
    }

    payloads = iter(["not-json", json.dumps(cli_payload)])

    class Completed:
        final_output = None

        def __init__(self, text: str):
            self.final_output = text
            self.raw_responses = []

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(*args, **kwargs):
        return Completed(next(payloads))

    monkeypatch.setattr(agent_runners.OAIRunner, "run_streamed", fake_run_streamed)

    result = asyncio.run(
        agent_runners._run_single_agent(
            "research-agent",
            "user prompt",
            SimpleNamespace(prompt="system prompt", model="gpt-5.5", maxTurns=15, tools=[]),
            retries=2,
        )
    )

    assert result == cli_payload


def test_run_single_agent_returns_structured_parse_error_without_stdout(monkeypatch, capsys):
    import trace_sdk

    monkeypatch.setattr(trace_sdk, "trace", lambda *a, **k: None)
    monkeypatch.setattr(trace_sdk, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(trace_sdk, "trace_agent_response", lambda *a, **k: None)

    class Completed:
        def __init__(self):
            self.final_output = "not-json"
            self.raw_responses = [
                SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5)
                )
            ]

        async def stream_events(self):
            if False:
                yield None

    monkeypatch.setattr(agent_runners.OAIRunner, "run_streamed", lambda *a, **k: Completed())

    usage.reset_round_usage()
    result = asyncio.run(
        agent_runners._run_single_agent(
            "research-agent",
            "user prompt",
            SimpleNamespace(prompt="system prompt", model="gpt-5.5", maxTurns=15, tools=[]),
            retries=1,
        )
    )
    captured = capsys.readouterr()

    assert result["status"] == "error"
    assert result["kind"] == "no_json"
    assert captured.out == ""
    assert captured.err == ""
    assert usage.get_round_usage()["by_agent"]["research-agent"]["calls"] == 1


def test_run_web_research_openai_returns_structured_error_without_stdout(monkeypatch, capsys):
    import agents

    import trace_sdk

    monkeypatch.setattr(agent_infra, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(trace_sdk, "trace", lambda *a, **k: None)
    monkeypatch.setattr(trace_sdk, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(trace_sdk, "trace_agent_response", lambda *a, **k: None)

    class Completed:
        def __init__(self):
            self.final_output = "not-json"
            self.raw_responses = [
                SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=4, output_tokens=1, total_tokens=5)
                )
            ]

        async def stream_events(self):
            if False:
                yield None

    monkeypatch.setattr(agents.Runner, "run_streamed", lambda *a, **k: Completed())

    usage.reset_round_usage()
    result = asyncio.run(agent_openai_calls._run_web_research_openai("prompt", retries=1))
    captured = capsys.readouterr()

    assert result["status"] == "error"
    assert result["kind"] == "no_json"
    assert "WEB_RESEARCH parse/validate failed" not in captured.out
    assert "WEB_RESEARCH error" not in captured.out
    assert captured.err == ""
    assert usage.get_round_usage()["by_agent"]["web-researcher"]["calls"] == 1


def test_run_web_research_openai_uses_responses_model_for_web_search(monkeypatch):
    import agents

    import trace_sdk

    monkeypatch.setattr(agent_infra, "_ensure_oauth_proxy", lambda: None)
    monkeypatch.setattr(trace_sdk, "trace", lambda *a, **k: None)
    monkeypatch.setattr(trace_sdk, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(trace_sdk, "trace_agent_response", lambda *a, **k: None)

    captured: dict[str, object] = {}

    class Completed:
        def __init__(self):
            self.final_output = "not-json"
            self.raw_responses = []

        async def stream_events(self):
            if False:
                yield None

    def fake_run_streamed(agent, *args, **kwargs):
        captured["model_type"] = type(agent.model).__name__
        captured["tool_type"] = type(agent.tools[0]).__name__
        return Completed()

    monkeypatch.setattr(agents.Runner, "run_streamed", fake_run_streamed)

    result = asyncio.run(agent_openai_calls._run_web_research_openai("prompt", retries=1))

    assert captured["model_type"] == "OpenAIResponsesModel"
    assert captured["tool_type"] == "WebSearchTool"
    assert result["status"] == "error"
    assert result["kind"] == "no_json"


def test_run_research_agent_propagates_error_result_without_memory_writes(monkeypatch):
    error_result = {
        "status": "error",
        "agent": "research-agent",
        "kind": "parse",
        "message": "Agent response could not be parsed",
    }

    monkeypatch.setattr(ao, "trace", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "_mempalace_search", lambda *a, **k: "prior theses")

    writes: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        agent_memory,
        "_mempalace_write",
        lambda *a, **k: writes.append(("write", "", "")) or True,
    )
    monkeypatch.setattr(
        agent_memory,
        "_mempalace_diary",
        lambda *a, **k: writes.append(("diary", "", "")) or True,
    )
    monkeypatch.setattr(agent_prompts, "MAX_RETRIES", 1)
    monkeypatch.setattr(agent_infra, "SDK_TIMEOUT_SECONDS", 300)
    monkeypatch.setattr(
        agent_prompts,
        "_research_agent",
        lambda **kwargs: SimpleNamespace(prompt="system", model="gpt-5.5", maxTurns=15, tools=[]),
    )

    async def fake_run_single_agent(*args, **kwargs):
        return error_result

    monkeypatch.setattr(agent_runners, "_run_single_agent", fake_run_single_agent)
    monkeypatch.setitem(
        sys.modules,
        "family_research_spec",
        SimpleNamespace(
            get_family_research_spec=lambda family_name: SimpleNamespace(
                strategy_label="EMA strategy",
                config_rules=[],
                config_schema="{}",
                thesis_json_hint="",
            )
        ),
    )

    result = ao.run_research_agent_sync(
        {
            "current_best": {
                "config": "best",
                "metric": 1.2,
                "config_contents": {"entry_cutoff_time": "10:00"},
            },
            "result_history": "history",
            "research_round": 3,
            "analyst_brief": "brief",
            "web_findings": "findings",
        },
        family_name="ema",
    )

    assert result == error_result
    assert writes == []


def test_sync_wrappers_work_from_running_event_loop(monkeypatch):
    async def fake_run_research_agent(context, family_name):
        return {"status": "ok", "family": family_name, "context": context}

    monkeypatch.setattr(ao, "run_research_agent", fake_run_research_agent)

    async def main():
        return ao.run_research_agent_sync({"seed": 1}, "ema")

    assert asyncio.run(main()) == {"status": "ok", "family": "ema", "context": {"seed": 1}}


def test_run_diagnostic_analysis_reports_persistence_failures(monkeypatch):
    result_payload = {
        "key_anomalies": [
            {
                "pattern": "Open-drive losses after 09:35",
                "numbers": "PF=0.62",
                "sample_size": "142",
                "suggested_exploit": "Tighten entry window",
                "confidence": "high",
            }
        ],
        "overall_diagnosis": "The edge decays after the open.",
        "discovery_questions": ["Does this persist by year?"],
    }

    monkeypatch.setattr(ao, "trace", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "_mempalace_search", lambda *a, **k: "prior memory")

    async def fake_run(prompt: str):
        return result_payload

    monkeypatch.setattr(agent_openai_calls, "_run_diagnostic_analyst_openai", fake_run)
    monkeypatch.setattr(agent_memory, "_mempalace_write", lambda *a, **k: False)
    monkeypatch.setattr(agent_memory, "_mempalace_diary", lambda *a, **k: False)

    result = ao.analyze_diagnostics_sync(
        trades_file="/tmp/trades.csv",
        config="ema_fast",
        metric=1.23,
        config_contents={"ema_length": 5},
        baseline_results={"profit_factor": 1.23, "trades_file": "/tmp/trades.csv"},
        family="ema",
    )

    assert result["persistence_errors"] == ["memory_write_failed", "diary_write_failed"]


def test_mempalace_helpers_propagate_unexpected_exceptions(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("bad subprocess shim")

    monkeypatch.setattr(agent_memory.subprocess, "run", boom)

    with pytest.raises(ValueError, match="bad subprocess shim"):
        agent_memory._mempalace_search("query")

    with pytest.raises(ValueError, match="bad subprocess shim"):
        agent_memory._mempalace_write("wing", "room", "content")

    with pytest.raises(ValueError, match="bad subprocess shim"):
        agent_memory._mempalace_diary("agent", "topic", "entry")


def test_validate_output_rejects_diagnostic_without_pattern():
    assert (
        agent_runners._validate_output(
            "diagnostic-analyst",
            {"key_anomalies": [{}], "overall_diagnosis": "x"},
        )
        is False
    )


def test_validate_output_rejects_research_agent_without_mechanism():
    assert (
        agent_runners._validate_output(
            "research-agent",
            {
                "reasoning": "ok",
                "suggested_theses": [
                    {
                        "thesis_id": "x",
                        "hypothesis": "y",
                        "config_changes": {"entry_cutoff_time": "09:31"},
                    }
                ],
                "should_stop": False,
            },
        )
        is False
    )


def test_validate_output_rejects_research_agent_without_full_schema_contract():
    assert (
        agent_runners._validate_output(
            "research-agent",
            {
                "reasoning": "ok",
                "suggested_theses": [
                    {
                        "thesis_id": "x",
                        "hypothesis": "y",
                        "mechanism": "z",
                        "config_changes": {"entry_cutoff_time": "09:31"},
                    }
                ],
                "should_stop": False,
            },
        )
        is False
    )


def test_run_single_agent_times_out_and_closes_stream(monkeypatch):
    import trace_sdk

    monkeypatch.setattr(trace_sdk, "trace", lambda *a, **k: None)
    monkeypatch.setattr(trace_sdk, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(trace_sdk, "trace_agent_response", lambda *a, **k: None)

    class StalledStream:
        def __init__(self):
            self.closed = False
            self.step = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.step == 0:
                self.step = 1
                return SimpleNamespace(event="first")
            await asyncio.sleep(1)
            return SimpleNamespace(event="never")

        async def aclose(self):
            self.closed = True

    class Completed:
        def __init__(self):
            self.final_output = ""
            self.raw_responses = []
            self.stream = StalledStream()

        def stream_events(self):
            return self.stream

    completed = Completed()
    monkeypatch.setattr(agent_runners.OAIRunner, "run_streamed", lambda *a, **k: completed)

    result = asyncio.run(
        agent_runners._run_single_agent(
            "research-agent",
            "user prompt",
            SimpleNamespace(prompt="system prompt", model="gpt-5.5", maxTurns=15, tools=[]),
            retries=1,
            timeout=0.01,
        )
    )

    assert result["status"] == "error"
    assert result["kind"] == "timeout"
    assert completed.stream.closed is True


def test_parse_json_extracts_from_fenced_block():
    text = """Before
```json
{"answer": 42}
```
After"""

    detailed = agent_infra._parse_json_detailed(text)

    assert detailed["status"] == "ok"
    assert detailed["parsed"] == {"answer": 42}
    assert detailed["fenced"] is True
    assert agent_infra._parse_json(text) == {"answer": 42}


def test_parse_json_detailed_distinguishes_failure_modes():
    no_json = agent_infra._parse_json_detailed("plain text only")
    malformed = agent_infra._parse_json_detailed('prefix {"answer": ')

    assert no_json["status"] == "error"
    assert no_json["kind"] == "no_json"
    assert malformed["status"] == "error"
    assert malformed["kind"] in {"truncated_json", "malformed_json"}


def test_format_result_history_renders_thesis_with_changes():
    rendered = agent_formatters.format_result_history(
        [
            {
                "config": "baseline",
                "metric": 1.4,
                "status": "ok",
                "thesis_id": "open_window",
                "config_changes": {"entry_cutoff_time": "09:31", "max_trades_per_day": 1},
            }
        ]
    )

    assert (
        "open_window (entry_cutoff_time=09:31, max_trades_per_day=1): metric=1.4 | status=ok"
        in rendered
    )


def test_formatters_truncate_large_payloads():
    long_text = "x" * 5000
    result_history = [
        {
            "config": long_text,
            "metric": 1.4,
            "status": "ok",
            "thesis_id": f"thesis_{idx}",
            "config_changes": {"entry_cutoff_time": long_text},
            "why": long_text,
            "regime_insight": long_text,
            "next_thesis_suggestion": long_text,
            "insight_brief": "\n".join([long_text] * 12),
        }
        for idx in range(20)
    ]
    rendered_history = agent_formatters.format_result_history(result_history)
    assert len(rendered_history) <= agent_formatters.MAX_RESULT_HISTORY_CHARS + 50
    assert "... (truncated)" in rendered_history

    analysis = {
        "overall_diagnosis": long_text,
        "key_anomalies": [
            {
                "confidence": "high",
                "pattern": long_text,
                "numbers": long_text,
                "sample_size": long_text,
                "suggested_exploit": long_text,
            }
        ]
        * 15,
        "discovery_questions": [long_text] * 10,
    }
    rendered_insight = agent_formatters.format_insight_brief(analysis)
    assert len(rendered_insight) <= agent_formatters.MAX_INSIGHT_BRIEF_CHARS + 50
    assert (
        "... (truncated)" in rendered_insight
        or len(rendered_insight) <= agent_formatters.MAX_INSIGHT_BRIEF_CHARS
    )

    research = {
        "summary": long_text,
        "findings": [
            {
                "label": "Sourced",
                "source_quality": "academic",
                "topic": long_text,
                "finding": long_text,
                "source": long_text,
                "actionable_idea": long_text,
            }
        ]
        * 15,
        "confidence_and_gaps": long_text,
    }
    rendered_web = agent_formatters.format_web_findings(research)
    assert len(rendered_web) <= agent_formatters.MAX_WEB_FINDINGS_CHARS + 50
    assert (
        "... (truncated)" in rendered_web
        or len(rendered_web) <= agent_formatters.MAX_WEB_FINDINGS_CHARS
    )
