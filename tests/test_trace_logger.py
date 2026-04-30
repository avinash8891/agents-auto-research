from __future__ import annotations

import importlib
import json
import warnings
import sys
from pathlib import Path
from unittest.mock import patch


def _load_trace_logger(monkeypatch, tmp_path: Path):
    sys.modules.pop("trace_logger", None)
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
        module = importlib.import_module("trace_logger")
    return importlib.reload(module)


def test_trace_logger_writes_local_artifacts_and_otel_events(monkeypatch, tmp_path: Path) -> None:
    trace_logger = _load_trace_logger(monkeypatch, tmp_path)

    hypothesis_id = trace_logger.begin_hypothesis("baseline")
    trace_id = trace_logger.trace_agent_prompt("diagnostic-analyst", "user prompt", "system prompt")
    trace_logger.trace_agent_response(
        "diagnostic-analyst",
        trace_id,
        '{"ok": true}',
        {"ok": True},
    )

    log_text = trace_logger.get_log_file().read_text(encoding="utf-8")
    assert f"BEGIN {hypothesis_id} name=baseline" in log_text
    assert f"[AGENT->diagnostic-analyst] PROMPT sent (len={len('user prompt')})" in log_text
    assert "[AGENT<-diagnostic-analyst] RESPONSE PARSED_OK" in log_text

    prompt_file = (
        trace_logger.get_log_file().parent
        / f"agents-{trace_logger.get_run_id()}"
        / hypothesis_id
        / f"{trace_id}-prompt.txt"
    )
    response_file = (
        trace_logger.get_log_file().parent
        / f"agents-{trace_logger.get_run_id()}"
        / hypothesis_id
        / f"{trace_id}-response.txt"
    )
    assert prompt_file.exists()
    assert response_file.exists()
    assert "=== TRACE_ID:" in prompt_file.read_text(encoding="utf-8")
    assert "--- USER PROMPT ---\nuser prompt" in prompt_file.read_text(encoding="utf-8")
    assert '--- RAW RESPONSE ---\n{"ok": true}' in response_file.read_text(encoding="utf-8")

    canonical_file = response_file.parents[1] / "trace-events.jsonl"
    events = [json.loads(line) for line in canonical_file.read_text(encoding="utf-8").splitlines()]
    prompt_event = next(event for event in events if event["action"] == "prompt")
    response_event = next(event for event in events if event["action"] == "response")
    lifecycle_event = next(
        event
        for event in events
        if event["action"] == "hypothesis" and event["payload"].get("status") == "begin"
    )
    assert lifecycle_event["payload"]["hypothesis_id"] == hypothesis_id
    assert prompt_event["artifact_paths"] == [str(prompt_file)]
    assert response_event["artifact_paths"] == [str(response_file)]
    assert prompt_event["payload"]["trace_id"] == trace_id
    assert response_event["payload"]["trace_id"] == trace_id


def test_begin_round_preserves_layout_and_rotates_event_store(monkeypatch, tmp_path: Path) -> None:
    trace_logger = _load_trace_logger(monkeypatch, tmp_path)

    trace_logger.set_family("ema", job=2)
    trace_logger.begin_round(3)
    trace_logger.trace("LOOP", "round started")

    log_file = trace_logger.get_log_file()
    assert log_file.name.startswith("trace-R-ema-job-2-round-3-")
    agent_dir = log_file.parent / f"agents-{trace_logger.get_run_id()}"
    assert agent_dir.exists()
    canonical_file = agent_dir / "trace-events.jsonl"
    events = [json.loads(line) for line in canonical_file.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["run_id"] == trace_logger.get_run_id()
    assert events[-1]["summary"] == "round started"


def test_begin_round_keeps_exporting_events_after_multiple_round_resets(
    monkeypatch, tmp_path: Path
) -> None:
    trace_logger = _load_trace_logger(monkeypatch, tmp_path)

    trace_logger.set_family("ema", job=2)
    trace_logger.begin_round(1)
    trace_logger.trace("LOOP", "first round started")
    first_file = trace_logger.get_event_file()
    first_events = [json.loads(line) for line in first_file.read_text(encoding="utf-8").splitlines()]
    assert first_events[-1]["summary"] == "first round started"

    trace_logger.begin_round(2)
    trace_logger.trace("LOOP", "second round started")
    second_file = trace_logger.get_event_file()
    second_events = [json.loads(line) for line in second_file.read_text(encoding="utf-8").splitlines()]
    assert second_events[-1]["summary"] == "second round started"
    assert second_events[-1]["run_id"] == trace_logger.get_run_id()


def test_begin_round_rebinds_openai_instrumentation_to_new_provider(monkeypatch, tmp_path: Path) -> None:
    trace_logger = _load_trace_logger(monkeypatch, tmp_path)
    original_provider = trace_logger._PROVIDER

    with (
        patch.object(trace_logger.OpenAIInstrumentor, "uninstrument") as uninstrument,
        patch.object(trace_logger.OpenAIInstrumentor, "instrument") as instrument,
    ):
        trace_logger.begin_round(5)

    assert trace_logger._PROVIDER is not original_provider
    uninstrument.assert_called_once_with()
    instrument.assert_called_once()
    assert instrument.call_args.kwargs["tracer_provider"] is trace_logger._PROVIDER


def test_trace_agent_response_accepts_external_trace_id_and_links_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    trace_logger = _load_trace_logger(monkeypatch, tmp_path)

    trace_logger.trace_agent_response("analyst", "analyst-custom-id", "hello", {"k": 1})

    agent_dir = trace_logger.get_log_file().parent / f"agents-{trace_logger.get_run_id()}"
    response_file = agent_dir / "analyst-custom-id-response.txt"
    assert response_file.exists()

    events = [
        json.loads(line)
        for line in (agent_dir / "trace-events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["payload"]["trace_id"] == "analyst-custom-id"
    assert events[-1]["artifact_paths"] == [str(response_file)]
