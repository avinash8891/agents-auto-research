from __future__ import annotations

import json
from pathlib import Path

from trace_improvement_report import (
    analyze_trace_events,
    load_trace_events,
    render_markdown_report,
    write_trace_improvement_report,
)


def _event(
    *,
    action: str,
    category: str = "agent",
    payload: dict | None = None,
    source_module: str = "research_subagents",
) -> dict:
    return {
        "event_id": f"evt-{action}",
        "source_module": source_module,
        "category": category,
        "action": action,
        "summary": action,
        "payload": payload or {},
    }


def test_analyze_trace_events_flags_large_failed_and_repeated_tool_use() -> None:
    events = [
        _event(
            action="tool_call",
            payload={"agent": "analyst", "tool": "read_file", "input": {"path": "missing.csv"}},
        ),
        _event(
            action="tool_call",
            payload={"agent": "analyst", "tool": "read_file", "input": {"path": "missing.csv"}},
        ),
        _event(
            action="tool_result",
            payload={
                "agent": "analyst",
                "tool": "read_file",
                "status": "error",
                "error": "not found",
                "output_len": 20,
            },
        ),
        _event(
            action="tool_result",
            payload={
                "agent": "analyst",
                "tool": "read_file",
                "status": "ok",
                "output": "x" * 13_000,
            },
        ),
        _event(
            action="transition",
            category="state",
            payload={"old_state": "running", "new_state": "blocked"},
            source_module="trace_sdk",
        ),
        _event(
            action="transition",
            category="state",
            payload={"old_state": "running", "new_state": "blocked"},
            source_module="trace_sdk",
        ),
    ]

    analysis = analyze_trace_events(events)

    analyst = analysis["roles"]["analyst"]
    assert analyst.tool_calls == 2
    assert analyst.tool_results == 2
    assert analyst.failed_tool_results == 1
    assert analyst.failed_tool_breakdown["read_file:not found"] == 1
    assert analyst.large_tool_results[0]["output_len"] == 13_000
    issues = {finding["issue"] for finding in analysis["findings"]}
    assert "failed tool results" in issues
    assert "large tool output re-entering agent context" in issues
    assert "repeated identical tool input" in issues
    assert "repeated running-to-blocked transitions" in issues


def test_load_and_write_trace_improvement_report(tmp_path: Path) -> None:
    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    _event(
                        action="research_round",
                        category="halo",
                        source_module="trace_adapters.halo",
                        payload={
                            "loop_state": {
                                "outcome": "compiled",
                                "thesis_id": "t1",
                                "strategy_family": "ema",
                            }
                        },
                    )
                ),
                json.dumps(
                    _event(
                        action="usage",
                        category="usage",
                        payload={"agent": "conductor", "usage": {"total_tokens": 123}},
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.md"

    loaded = load_trace_events([trace])
    assert len(loaded) == 2
    report = render_markdown_report(analyze_trace_events(loaded), [trace])
    assert "`compiled`: 1" in report
    assert "conductor" in report

    assert write_trace_improvement_report([trace], output) == output
    assert "Trace Improvement Report" in output.read_text(encoding="utf-8")


def test_tool_output_length_is_used_for_large_tool_outputs() -> None:
    analysis = analyze_trace_events(
        [
            _event(
                action="tool_result",
                payload={
                    "agent": "analyst",
                    "tool": "run_python",
                    "status": "ok",
                    "tool_output_length": 20_000,
                    "tool_output_preview": "large dataframe",
                },
            )
        ]
    )

    assert analysis["roles"]["analyst"].large_tool_results[0]["output_len"] == 20_000


def test_usage_analysis_preserves_fractional_costs() -> None:
    analysis = analyze_trace_events(
        [
            _event(
                action="usage",
                category="usage",
                payload={
                    "agent": "conductor",
                    "usage": {"total_tokens": 123, "cost_usd": 0.42},
                },
            )
        ]
    )

    usage = analysis["roles"]["conductor"].usage
    assert usage["total_tokens"] == 123
    assert usage["cost_usd"] == 0.42


def test_analysis_normalizes_roles_and_deduplicates_bridge_outcomes() -> None:
    events = [
        _event(
            action="research_round",
            category="halo",
            source_module="trace_adapters.halo",
            payload={"loop_state": {"research_round": 1, "thesis_id": "t1", "outcome": "compiled"}},
        ),
        _event(
            action="research_round",
            category="recursive_improve",
            source_module="trace_adapters.recursive_improve",
            payload={"iteration_context": {"round": 1, "candidate_id": "t1", "status": "compiled"}},
        ),
        _event(
            action="tool_call",
            payload={
                "agent": "research-conductor",
                "tool": "get_past_thesis",
                "input": {"id": "t0"},
            },
        ),
        _event(
            action="tool_call",
            payload={"agent": "web-researcher", "tool": "web_search", "input": {"q": "ema"}},
        ),
    ]

    analysis = analyze_trace_events(events)

    assert analysis["outcomes"]["compiled"] == 1
    assert "conductor" in analysis["roles"]
    assert "web_researcher" in analysis["roles"]
