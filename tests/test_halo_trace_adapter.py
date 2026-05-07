from __future__ import annotations

import importlib
import json
import sys
import warnings
from pathlib import Path

import pytest

from trace_adapters.halo import (
    export_halo_trace_jsonl,
    read_canonical_trace,
    verify_halo_trace_jsonl,
)


def _load_trace_sdk(monkeypatch, tmp_path: Path):
    sys.modules.pop("trace_sdk", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Support for class-based `config` is deprecated, use ConfigDict instead\\.",
            category=Warning,
        )
        warnings.filterwarnings(
            "ignore",
            message="'asyncio\\.iscoroutinefunction' is deprecated and slated for removal in Python 3\\.16; use inspect\\.iscoroutinefunction\\(\\) instead",
            category=DeprecationWarning,
        )
        module = importlib.import_module("trace_sdk")
    return importlib.reload(module)


def _canonical_event(**overrides):
    event = {
        "event_id": "evt-00000001",
        "timestamp": "2026-05-07T11:16:40.851Z",
        "otel_trace_id": "0" * 31 + "1",
        "span_id": "0" * 15 + "1",
        "parent_span_id": "",
        "span_name": "agent.tool_call",
        "span_kind": "SPAN_KIND_INTERNAL",
        "span_start_time": "2026-05-07T11:16:40.851000000Z",
        "span_end_time": "2026-05-07T11:16:40.852000000Z",
        "span_status_code": "STATUS_CODE_OK",
        "span_status_message": "",
        "resource_attributes": {
            "service.name": "agents-auto-research",
            "service.namespace": "autoresearch",
            "service.instance.id": "20260507-104940",
        },
        "scope": {"name": "agents-auto-research.trace_sdk", "version": ""},
        "source_module": "trace_sdk",
        "run_id": "R-ema-job-20-round-40-20260507-111640",
        "session_id": "20260507-104940",
        "family": "ema",
        "job": 20,
        "model_provider": "openai",
        "model_name": "gpt-5.2",
        "hypothesis_id": "H001",
        "hypothesis_name": "research-round-40",
        "seq": 1,
        "category": "agent",
        "action": "tool_call",
        "summary": "analyst called read_file",
        "payload": {
            "agent_name": "analyst",
            "trace_id": "H001-analyst-00010",
            "tool_name": "read_file",
            "tool_input_preview": "diagnostics.json",
            "tool_input_length": 16,
        },
        "artifact_paths": [],
    }
    event.update(overrides)
    return event


def test_export_halo_trace_jsonl_maps_canonical_events_to_openinference_spans(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "trace-events.jsonl"
    canonical.write_text(
        "\n".join(
            [
                json.dumps(_canonical_event()),
                json.dumps(
                    _canonical_event(
                        event_id="evt-00000002",
                        action="tool_result",
                        summary="analyst read_file result ok",
                        payload={
                            "agent_name": "analyst",
                            "trace_id": "H001-analyst-00010",
                            "tool_name": "read_file",
                            "status": "ok",
                            "tool_output_preview": "rows=10",
                            "tool_output_length": 7,
                        },
                    )
                ),
                json.dumps(
                    _canonical_event(
                        event_id="evt-00000003",
                        category="usage",
                        action="accumulate",
                        source_module="agent_token_usage",
                        payload={
                            "agent": "analyst",
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "total_tokens": 120,
                        },
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "round-040-traces.halo.jsonl"

    assert read_canonical_trace(canonical)[0]["event_id"] == "evt-00000001"
    assert export_halo_trace_jsonl(canonical, output) == output
    verify_halo_trace_jsonl(output)

    spans = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    first = spans[0]
    assert {
        "trace_id",
        "span_id",
        "parent_span_id",
        "name",
        "kind",
        "start_time",
        "end_time",
        "status",
        "attributes",
        "resource",
        "scope",
    } <= set(first)
    assert isinstance(first["parent_span_id"], str)
    assert first["scope"]["name"] == "agents-auto-research.trace_sdk"
    assert "attributes" in first["resource"]
    assert first["attributes"]["inference.project_id"] == "agents-auto-research"
    assert first["attributes"]["inference.observation_kind"] == "TOOL"
    assert first["attributes"]["openinference.span.kind"] == "TOOL"
    assert first["attributes"]["tool.name"] == "read_file"
    assert first["trace_id"] == "0" * 31 + "1"
    assert first["span_id"] == "0" * 15 + "1"
    assert first["name"] == "agent.tool_call"
    assert first["start_time"] == "2026-05-07T11:16:40.851000000Z"
    assert first["end_time"] == "2026-05-07T11:16:40.852000000Z"
    assert first["status"]["code"] == "STATUS_CODE_OK"
    assert first["scope"]["name"] == "agents-auto-research.trace_sdk"
    assert first["resource"]["attributes"]["service.name"] == "agents-auto-research"

    usage = spans[-1]
    assert usage["attributes"]["inference.observation_kind"] == "LLM"
    assert usage["attributes"]["inference.llm.input_tokens"] == 100
    assert usage["attributes"]["inference.llm.output_tokens"] == 20


def test_halo_llm_messages_are_read_from_artifacts_not_canonical_payload(
    tmp_path: Path,
) -> None:
    prompt_artifact = tmp_path / "prompt.txt"
    prompt_artifact.write_text(
        "\n".join(
            [
                "=== TRACE_ID: trace-1 ===",
                "--- SYSTEM PROMPT ---",
                "You are an analyst.",
                "--- USER PROMPT ---",
                "Inspect diagnostics.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    response_artifact = tmp_path / "response.txt"
    response_artifact.write_text(
        "\n".join(
            [
                "=== TRACE_ID: trace-1 ===",
                "--- RAW RESPONSE ---",
                '{"ok": true}',
                "--- PARSED JSON ---",
                '{"ok": true}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    canonical = tmp_path / "trace-events.jsonl"
    canonical.write_text(
        "\n".join(
            [
                json.dumps(
                    _canonical_event(
                        action="prompt",
                        payload={
                            "agent_name": "analyst",
                            "trace_id": "trace-1",
                            "prompt_length": 20,
                        },
                        artifact_paths=[str(prompt_artifact)],
                    )
                ),
                json.dumps(
                    _canonical_event(
                        action="response",
                        payload={
                            "agent_name": "analyst",
                            "trace_id": "trace-1",
                            "response_length": 12,
                        },
                        artifact_paths=[str(response_artifact)],
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "trace.halo.jsonl"

    export_halo_trace_jsonl(canonical, output)
    spans = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    input_messages = json.loads(spans[0]["attributes"]["llm.input_messages"])
    output_messages = json.loads(spans[1]["attributes"]["llm.output_messages"])

    assert input_messages == [
        {"role": "system", "content": "You are an analyst."},
        {"role": "user", "content": "Inspect diagnostics."},
    ]
    assert output_messages == [{"role": "assistant", "content": '{"ok": true}'}]


def test_halo_artifact_reader_rejects_paths_outside_trace_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside-secret.txt"
    outside.write_text(
        "\n".join(["--- USER PROMPT ---", "secret outside trace dir", ""]),
        encoding="utf-8",
    )
    trace_dir = tmp_path / "logs"
    trace_dir.mkdir()
    canonical = trace_dir / "trace-events.jsonl"
    canonical.write_text(
        json.dumps(
            _canonical_event(
                action="prompt",
                payload={"agent_name": "analyst", "trace_id": "trace-1", "prompt_length": 10},
                artifact_paths=[str(outside)],
            )
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "trace.halo.jsonl"

    export_halo_trace_jsonl(canonical, output)
    span = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    input_messages = json.loads(span["attributes"]["llm.input_messages"])

    assert input_messages == [{"role": "user", "content": ""}]


def test_halo_tool_previews_are_redacted_before_export(tmp_path: Path) -> None:
    canonical = tmp_path / "trace-events.jsonl"
    canonical.write_text(
        "\n".join(
            [
                json.dumps(
                    _canonical_event(
                        action="tool_call",
                        payload={
                            "agent_name": "analyst",
                            "trace_id": "trace-1",
                            "tool_name": "run_python",
                            "tool_input_preview": "OPENAI_API_KEY=sk-secret123456789",
                        },
                    )
                ),
                json.dumps(
                    _canonical_event(
                        action="tool_result",
                        payload={
                            "agent_name": "analyst",
                            "trace_id": "trace-1",
                            "tool_name": "run_python",
                            "status": "error",
                            "error_type": "token=abc123",
                            "tool_output_preview": "secret=abc123",
                        },
                    )
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "trace.halo.jsonl"

    export_halo_trace_jsonl(canonical, output)
    spans = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert spans[0]["attributes"]["input.value"] == "OPENAI_API_KEY=<redacted>"
    assert spans[1]["attributes"]["output.value"] == "secret=<redacted>"
    assert spans[1]["attributes"]["error.type"] == "token=<redacted>"
    assert spans[1]["status"]["code"] == "STATUS_CODE_ERROR"
    assert spans[1]["status"]["message"] == "token=<redacted>"


def test_halo_non_llm_summary_is_redacted(tmp_path: Path) -> None:
    canonical = tmp_path / "trace-events.jsonl"
    canonical.write_text(
        json.dumps(
            _canonical_event(
                category="state",
                action="transition",
                summary="OPENAI_API_KEY=sk-secret123456789",
                payload={"old_state": "running", "new_state": "blocked"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "trace.halo.jsonl"

    export_halo_trace_jsonl(canonical, output)
    span = json.loads(output.read_text(encoding="utf-8").splitlines()[0])

    assert span["attributes"]["input.value"] == "OPENAI_API_KEY=<redacted>"


def test_verify_halo_trace_jsonl_rejects_missing_required_shape(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"event_id": "evt-1"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required keys"):
        verify_halo_trace_jsonl(bad)


def test_trace_sdk_real_events_export_to_halo_shape(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)
    trace_sdk.set_family("ema", job=20)
    trace_sdk.begin_round(41)
    trace_sdk.begin_hypothesis("contract-test")
    trace_id = trace_sdk.trace_agent_prompt(
        "analyst",
        "Inspect diagnostics and trades.",
        "You are a focused analyst.",
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.trace_agent_tool_call(
        "analyst",
        trace_id,
        "read_file",
        "diagnostics.json",
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.trace_agent_tool_result(
        "analyst",
        trace_id,
        "read_file",
        '{"trade_count": 3123}',
        duration_ms=12,
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.trace_agent_response(
        "analyst",
        trace_id,
        '{"focus_answer": "ok"}',
        {"focus_answer": "ok"},
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.record_usage_event(
        "analyst",
        model_provider="openai",
        model_name="gpt-5.2",
        input_tokens=100,
        cached_input_tokens=40,
        output_tokens=20,
        total_tokens=120,
        usage_source="test",
    )
    trace_sdk.trace_state_change("research", "blocked", "needs_code")

    canonical = trace_sdk.get_event_file()
    halo_jsonl = tmp_path / "trace.halo.jsonl"
    export_halo_trace_jsonl(canonical, halo_jsonl)
    verify_halo_trace_jsonl(halo_jsonl)

    spans = [json.loads(line) for line in halo_jsonl.read_text(encoding="utf-8").splitlines()]
    canonical_events = [
        json.loads(line) for line in canonical.read_text(encoding="utf-8").splitlines()
    ]
    canonical_by_span = {event["span_id"]: event for event in canonical_events}
    span_ids = {span["span_id"] for span in spans}
    for span in spans:
        canonical_event = canonical_by_span[span["span_id"]]
        assert span["trace_id"] == canonical_event["otel_trace_id"]
        assert span["span_id"] == canonical_event["span_id"]
        assert span["name"] == canonical_event["span_name"]
        assert span["kind"] == canonical_event["span_kind"]
        assert span["start_time"] == canonical_event["span_start_time"]
        assert span["end_time"] == canonical_event["span_end_time"]
        assert span["status"]["code"] == canonical_event["span_status_code"]
        assert span["scope"] == canonical_event["scope"]
        assert span["resource"]["attributes"]["service.name"] == "agents-auto-research"
        assert isinstance(span["parent_span_id"], str)
        if span["parent_span_id"]:
            assert span["parent_span_id"] in span_ids

    tool_call = next(
        span
        for span in spans
        if span["attributes"].get("tool.name") == "read_file"
        and span["attributes"].get("input.value") == "diagnostics.json"
    )
    assert tool_call["attributes"]["openinference.span.kind"] == "TOOL"

    tool_result = next(
        span
        for span in spans
        if span["attributes"].get("tool.name") == "read_file"
        and span["attributes"].get("output.value") == '{"trade_count": 3123}'
    )
    assert tool_result["attributes"]["tool.output_length"] == len('{"trade_count": 3123}')

    prompt_span = next(
        span
        for span in spans
        if span["attributes"].get("agent.name") == "analyst"
        and span["attributes"].get("llm.input_messages")
    )
    input_messages = json.loads(prompt_span["attributes"]["llm.input_messages"])
    assert input_messages == [
        {"role": "system", "content": "You are a focused analyst."},
        {"role": "user", "content": "Inspect diagnostics and trades."},
    ]
    assert prompt_span["attributes"]["llm.input_messages.0.message.role"] == "system"

    response_span = next(
        span
        for span in spans
        if span["attributes"].get("agent.name") == "analyst"
        and span["attributes"].get("llm.output_messages")
    )
    output_messages = json.loads(response_span["attributes"]["llm.output_messages"])
    assert output_messages == [{"role": "assistant", "content": '{"focus_answer": "ok"}'}]

    usage = next(
        span
        for span in spans
        if span["attributes"].get("agent.name") == "analyst"
        and span["attributes"].get("inference.llm.total_tokens") == 120
    )
    assert usage["attributes"]["inference.llm.input_tokens"] == 100
    assert usage["attributes"]["inference.llm.cached_input_tokens"] == 40
    assert usage["attributes"]["inference.llm.output_tokens"] == 20
