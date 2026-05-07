from __future__ import annotations

import importlib
import json
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace


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


def test_trace_sdk_uses_openai_agents_not_openai_sdk_instrumentation(
    monkeypatch, tmp_path: Path
) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)
    instrument_names = {instrument.name for instrument in trace_sdk._TRACELOOP_INSTRUMENTS}
    assert "OPENAI" not in instrument_names
    assert "OPENAI_AGENTS" in instrument_names


def test_trace_sdk_writes_local_artifacts_and_otel_events(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    hypothesis_id = trace_sdk.begin_hypothesis("baseline")
    trace_id = trace_sdk.trace_agent_prompt(
        "diagnostic-analyst",
        "user prompt",
        "system prompt",
        model_provider="openai",
        model_name="gpt-5.5",
    )
    trace_sdk.trace_agent_response(
        "diagnostic-analyst",
        trace_id,
        '{"ok": true}',
        {"ok": True},
        model_provider="openai",
        model_name="gpt-5.5",
    )

    log_text = trace_sdk.get_log_file().read_text(encoding="utf-8")
    assert f"BEGIN {hypothesis_id} name=baseline" in log_text
    assert f"[AGENT->diagnostic-analyst] PROMPT sent (len={len('user prompt')})" in log_text
    assert "[AGENT<-diagnostic-analyst] RESPONSE PARSED_OK" in log_text

    prompt_file = (
        trace_sdk.get_log_file().parent
        / f"agents-{trace_sdk.get_run_id()}"
        / hypothesis_id
        / f"{trace_id}-prompt.txt"
    )
    response_file = (
        trace_sdk.get_log_file().parent
        / f"agents-{trace_sdk.get_run_id()}"
        / hypothesis_id
        / f"{trace_id}-response.txt"
    )
    assert prompt_file.exists()
    assert response_file.exists()

    canonical_file = response_file.parents[1] / "trace-events.jsonl"
    events = [json.loads(line) for line in canonical_file.read_text(encoding="utf-8").splitlines()]
    prompt_event = next(event for event in events if event["action"] == "prompt")
    response_event = next(event for event in events if event["action"] == "response")
    assert prompt_event["artifact_paths"] == [str(prompt_file)]
    assert response_event["artifact_paths"] == [str(response_file)]
    assert prompt_event["payload"]["prompt_preview"] == "user prompt"
    assert prompt_event["payload"]["system_prompt_preview"] == "system prompt"
    assert response_event["payload"]["response_preview"] == '{"ok": true}'
    assert prompt_event["model_provider"] == "openai"
    assert prompt_event["model_name"] == "gpt-5.5"
    assert response_event["model_provider"] == "openai"
    assert response_event["model_name"] == "gpt-5.5"
    for event in (prompt_event, response_event):
        assert event["schema_version"] == 1
        assert event["session_id"] == trace_sdk.get_session_id()
        assert event["run_id"] == trace_sdk.get_run_id()
        assert len(event["otel_trace_id"]) == 32
        assert len(event["span_id"]) == 16
        assert isinstance(event["parent_span_id"], str)
        assert event["span_name"]
        assert event["span_kind"].startswith("SPAN_KIND_")
        assert event["span_start_time"].endswith("Z")
        assert event["span_end_time"].endswith("Z")
        assert event["span_status_code"].startswith("STATUS_CODE_")
        assert isinstance(event["resource_attributes"], dict)
        assert event["resource_attributes"]["service.name"] == "agents-auto-research"
        assert isinstance(event["scope"], dict)
        assert event["scope"]["name"]
        assert isinstance(event["payload"], dict)
        assert isinstance(event["artifact_paths"], list)
        assert {
            "event_id",
            "schema_version",
            "timestamp",
            "otel_trace_id",
            "span_id",
            "parent_span_id",
            "span_name",
            "span_kind",
            "span_start_time",
            "span_end_time",
            "span_status_code",
            "span_status_message",
            "resource_attributes",
            "scope",
            "source_module",
            "run_id",
            "session_id",
            "family",
            "job",
            "model_provider",
            "model_name",
            "hypothesis_id",
            "hypothesis_name",
            "seq",
            "category",
            "action",
            "summary",
            "payload",
            "artifact_paths",
        }.issubset(event.keys())


def test_trace_sdk_records_tool_call_and_result_sizes(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.begin_hypothesis("tool-audit")
    trace_sdk.trace_agent_tool_call(
        "analyst",
        "trace-001",
        "run_python",
        "print('hello')",
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.trace_agent_tool_result(
        "analyst",
        "trace-001",
        "run_python",
        "hello\n",
        truncated=False,
        duration_ms=12,
        model_provider="openai",
        model_name="gpt-5.2",
    )

    events = [
        json.loads(line)
        for line in trace_sdk.get_event_file().read_text(encoding="utf-8").splitlines()
    ]
    call_event = next(event for event in events if event["action"] == "tool_call")
    result_event = next(event for event in events if event["action"] == "tool_result")

    assert call_event["payload"]["tool_input_length"] == len("print('hello')")
    assert call_event["payload"]["tool_input_hash"]
    assert result_event["payload"]["tool_output_length"] == len("hello\n")
    assert result_event["payload"]["tool_output_hash"]
    assert result_event["payload"]["duration_ms"] == 12
    assert result_event["payload"]["truncated"] is False
    assert result_event["model_name"] == "gpt-5.2"


def test_trace_sdk_exporter_skips_spans_without_autoresearch_event_id(
    monkeypatch, tmp_path: Path
) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)
    exporter = trace_sdk.JsonLineTraceExporter(tmp_path / "events.jsonl")

    skipped = SimpleNamespace(
        name="openai.chat",
        attributes={
            "autoresearch.summary": "openai.chat",
            "autoresearch.category": "",
        },
        start_time=0,
    )
    written = SimpleNamespace(
        name="trace.main",
        attributes={
            "autoresearch.event_id": "evt-1",
            "autoresearch.schema_version": 1,
            "autoresearch.timestamp": "2026-05-03T00:00:00.000Z",
            "autoresearch.source_module": "trace_sdk",
            "autoresearch.run_id": "R-test",
            "autoresearch.session_id": "20260503-000000",
            "autoresearch.family": "ema",
            "autoresearch.job": 1,
            "autoresearch.hypothesis_id": "",
            "autoresearch.hypothesis_name": "",
            "autoresearch.seq": 1,
            "autoresearch.category": "trace",
            "autoresearch.action": "main",
            "autoresearch.summary": "main",
            "autoresearch.payload_json": "{}",
            "autoresearch.artifact_paths": [],
        },
        start_time=0,
    )

    exporter.export([skipped, written])

    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-1"
    assert events[0]["summary"] == "main"


def test_trace_sdk_event_schema_matches_dataclass_and_payload_shape(
    monkeypatch, tmp_path: Path
) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.set_family("ema", job=3)
    trace_sdk.begin_round(1)
    trace_sdk.trace("LOOP", "round started", {"source": "test"})
    trace_sdk.trace_state_change("running", "blocked", "research_required")
    trace_sdk.trace_ssh("echo hello", 0, stdout="hello\n", stderr="")

    events = [
        json.loads(line)
        for line in trace_sdk.get_event_file().read_text(encoding="utf-8").splitlines()
    ]

    assert len(events) == 3
    for event in events:
        assert event["schema_version"] == 1
        assert event["session_id"] == trace_sdk.get_session_id()
        assert event["run_id"] == trace_sdk.get_run_id()
        assert isinstance(event["event_id"], str) and event["event_id"].startswith("evt-")
        assert isinstance(event["timestamp"], str) and event["timestamp"]
        assert isinstance(event["source_module"], str)
        assert isinstance(event["family"], str)
        assert isinstance(event["job"], int)
        assert isinstance(event["seq"], int) and event["seq"] > 0
        assert isinstance(event["category"], str)
        assert isinstance(event["action"], str)
        assert isinstance(event["summary"], str)
        assert isinstance(event["payload"], dict)
        assert isinstance(event["artifact_paths"], list)


def test_trace_sdk_trace_events_can_carry_model_metadata(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.set_family("ema", job=1)
    trace_sdk.begin_round(1)
    trace_sdk.trace(
        "BUILDER",
        "builder started",
        {"thesis_id": "ema5"},
        model_provider="codex",
        model_name="gpt-5.4-mini",
    )

    events = [
        json.loads(line)
        for line in trace_sdk.get_event_file().read_text(encoding="utf-8").splitlines()
    ]
    event = events[-1]
    assert event["category"] == "trace"
    assert event["model_provider"] == "codex"
    assert event["model_name"] == "gpt-5.4-mini"
    assert event["payload"] == {"component": "BUILDER", "data": {"thesis_id": "ema5"}}

    trace_event = next(
        event
        for event in events
        if event["category"] == "trace" and event["payload"]["component"] == "BUILDER"
    )
    assert trace_event["payload"] == {"component": "BUILDER", "data": {"thesis_id": "ema5"}}


def test_begin_round_preserves_layout_and_rotates_event_store(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.set_family("ema", job=2)
    trace_sdk.begin_round(3)
    trace_sdk.trace("LOOP", "round started")

    log_file = trace_sdk.get_log_file()
    assert log_file.name.startswith("trace-R-ema-job-2-round-3-")
    agent_dir = log_file.parent / f"agents-{trace_sdk.get_run_id()}"
    assert agent_dir.exists()
    canonical_file = agent_dir / "trace-events.jsonl"
    events = [json.loads(line) for line in canonical_file.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["run_id"] == trace_sdk.get_run_id()
    assert events[-1]["summary"] == "round started"


def test_begin_round_keeps_exporting_events_after_multiple_round_resets(
    monkeypatch, tmp_path: Path
) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.set_family("ema", job=2)
    trace_sdk.begin_round(1)
    trace_sdk.trace("LOOP", "first round started")
    first_file = trace_sdk.get_event_file()
    first_events = [
        json.loads(line) for line in first_file.read_text(encoding="utf-8").splitlines()
    ]
    assert first_events[-1]["summary"] == "first round started"

    trace_sdk.begin_round(2)
    trace_sdk.trace("LOOP", "second round started")
    second_file = trace_sdk.get_event_file()
    second_events = [
        json.loads(line) for line in second_file.read_text(encoding="utf-8").splitlines()
    ]
    assert second_events[-1]["summary"] == "second round started"
    assert second_events[-1]["run_id"] == trace_sdk.get_run_id()


def test_begin_round_refreshes_provider_without_openai_instrumentation(
    monkeypatch, tmp_path: Path
) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)
    original_provider = trace_sdk._PROVIDER
    original_instruments = trace_sdk._TRACELOOP_INSTRUMENTS
    trace_sdk.begin_round(5)

    assert trace_sdk._PROVIDER is not original_provider
    assert trace_sdk._TRACELOOP_INSTRUMENTS is original_instruments


def test_trace_agent_response_accepts_external_trace_id_and_links_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.trace_agent_response("analyst", "analyst-custom-id", "hello", {"k": 1})

    agent_dir = trace_sdk.get_log_file().parent / f"agents-{trace_sdk.get_run_id()}"
    response_file = agent_dir / "analyst-custom-id-response.txt"
    assert response_file.exists()

    events = [
        json.loads(line)
        for line in (agent_dir / "trace-events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["payload"]["trace_id"] == "analyst-custom-id"
    assert events[-1]["artifact_paths"] == [str(response_file)]


def test_record_usage_event_writes_usage_category_to_jsonl(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    trace_sdk.record_usage_event(
        "web-researcher",
        model_provider="openai",
        model_name="gpt-5.5",
        input_tokens=1200,
        output_tokens=600,
        total_tokens=1800,
        cached_input_tokens=300,
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        estimated_total_tokens=0,
        cost_usd=0.0045,
        dedupe_key="test-run-001",
    )

    events = [
        json.loads(line)
        for line in trace_sdk.get_event_file().read_text(encoding="utf-8").splitlines()
    ]
    usage_events = [e for e in events if e["category"] == "usage"]
    assert len(usage_events) == 1, "record_usage_event must write exactly one usage event"
    ev = usage_events[0]
    assert ev["model_provider"] == "openai"
    assert ev["model_name"] == "gpt-5.5"
    assert ev["payload"]["agent"] == "web-researcher"
    assert ev["payload"]["input_tokens"] == 1200
    assert ev["payload"]["output_tokens"] == 600
    assert ev["payload"]["total_tokens"] == 1800
    assert ev["payload"]["cached_input_tokens"] == 300
    assert ev["payload"]["estimated_total_tokens"] == 0
    assert abs(ev["payload"]["cost_usd"] - 0.0045) < 1e-9
    assert ev["payload"]["dedupe_key"] == "test-run-001"
    assert ev["schema_version"] == 1
    assert ev["action"] == "accumulate"


def test_trace_ignores_broken_stdout_pipe(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    class BrokenStdout:
        def write(self, _line: str) -> None:
            raise BrokenPipeError()

        def flush(self) -> None:
            raise BrokenPipeError()

    monkeypatch.setattr(trace_sdk.sys, "stdout", BrokenStdout())

    trace_sdk.trace("COMMAND", "still writes file")

    log_text = trace_sdk.get_log_file().read_text(encoding="utf-8")
    assert "[COMMAND] still writes file" in log_text


def test_trace_ignores_closed_stdout(monkeypatch, tmp_path: Path) -> None:
    trace_sdk = _load_trace_sdk(monkeypatch, tmp_path)

    class ClosedStdout:
        def write(self, _line: str) -> None:
            raise ValueError("I/O operation on closed file")

        def flush(self) -> None:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(trace_sdk.sys, "stdout", ClosedStdout())

    trace_sdk.trace("COMMAND", "closed stdout still writes file")

    log_text = trace_sdk.get_log_file().read_text(encoding="utf-8")
    assert "[COMMAND] closed stdout still writes file" in log_text
