from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

# ruff: noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent_infra
import agent_orchestrator as ao


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
    monkeypatch.setattr(ao, "_mempalace_search", lambda *a, **k: "prior memory")

    async def fake_run(prompt: str):
        assert "RAW TRADES FILE: /tmp/trades.csv" in prompt
        return result_payload

    monkeypatch.setattr(ao, "_run_diagnostic_analyst_openai", fake_run)
    monkeypatch.setattr(
        ao,
        "_mempalace_write",
        lambda wing, room, content: writes.append((wing, room, content)) or True,
    )
    monkeypatch.setattr(
        ao,
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
    monkeypatch.setattr(ao, "_mempalace_search", lambda *a, **k: "prior research")

    async def fake_run(prompt: str):
        assert "DIAGNOSTIC INSIGHTS:" in prompt
        return result_payload

    monkeypatch.setattr(ao, "_run_web_research_openai", fake_run)
    monkeypatch.setattr(
        ao,
        "_mempalace_write",
        lambda wing, room, content: writes.append((wing, room, content)) or True,
    )
    monkeypatch.setattr(
        ao,
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
    monkeypatch.setattr(ao, "trace_agent_prompt", lambda *a, **k: "trace-id")
    monkeypatch.setattr(ao, "trace_agent_response", lambda *a, **k: None)
    monkeypatch.setattr(ao, "_mempalace_search", lambda *a, **k: "prior theses")
    monkeypatch.setattr(ao, "_mempalace_write", lambda *a, **k: True)
    monkeypatch.setattr(ao, "_mempalace_diary", lambda *a, **k: True)
    monkeypatch.setattr(ao, "MAX_RETRIES", 2)
    monkeypatch.setattr(agent_infra, "SDK_TIMEOUT_SECONDS", 300)

    class FakeSpec:
        strategy_label = "EMA strategy"
        config_rules = ["Only vary approved config keys"]
        config_schema = '{"entry_cutoff_time": "HH:MM"}'
        thesis_json_hint = '"expected_metric": "improve PF"'

    monkeypatch.setitem(
        sys.modules,
        "family_research",
        SimpleNamespace(get_family_research_spec=lambda family_name: FakeSpec()),
    )

    monkeypatch.setattr(
        ao,
        "_research_agent",
        lambda **kwargs: SimpleNamespace(prompt="system", model="claude", maxTurns=15),
    )

    payloads = iter(
        [
            {
                "reasoning": "Missing thesis id",
                "suggested_theses": [
                    {"hypothesis": "test", "config_changes": {"entry_cutoff_time": "09:31"}}
                ],
                "should_stop": False,
            },
            {
                "reasoning": "Valid thesis",
                "suggested_theses": [
                    {
                        "thesis_id": "open_window",
                        "hypothesis": "narrow the window",
                        "config_changes": {"entry_cutoff_time": "09:31"},
                        "requires_code_change": False,
                    }
                ],
                "should_stop": False,
            },
        ]
    )

    AssistantMessage = type("AssistantMessage", (), {})
    ResultMessage = type("ResultMessage", (), {})

    async def fake_query(*args, **kwargs):
        payload = next(payloads)
        message = AssistantMessage()
        message.content = [SimpleNamespace(text=json.dumps(payload))]
        yield message

    monkeypatch.setattr(ao, "query", fake_query)
    monkeypatch.setattr(ao, "AssistantMessage", AssistantMessage)
    monkeypatch.setattr(ao, "ResultMessage", ResultMessage)
    monkeypatch.setattr(ao, "ClaudeAgentOptions", lambda **kwargs: SimpleNamespace(**kwargs))

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
                "hypothesis": "narrow the window",
                "config_changes": {"entry_cutoff_time": "09:31"},
                "requires_code_change": False,
            }
        ],
        "should_stop": False,
    }


def test_validate_output_rejects_diagnostic_without_pattern():
    assert (
        ao._validate_output(
            "diagnostic-analyst",
            {"key_anomalies": [{}], "overall_diagnosis": "x"},
        )
        is False
    )


def test_parse_json_extracts_from_fenced_block():
    text = """Before
```json
{"answer": 42}
```
After"""

    assert agent_infra._parse_json(text) == {"answer": 42}


def test_format_result_history_renders_thesis_with_changes():
    rendered = ao.format_result_history(
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
