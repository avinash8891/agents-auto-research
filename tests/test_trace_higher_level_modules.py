from __future__ import annotations

import importlib
import json
import sys
import warnings
from pathlib import Path


def _load_modules(monkeypatch, tmp_path: Path):
    for module_name in [
        "trace_sdk",
        "trace_autonomy_ledger",
        "trace_quality_history",
        "trace_refinement",
        "trace_rule_proposals",
        "trace_adapters",
        "trace_adapters.halo",
        "trace_adapters.recursive_improve",
        "trace_adapters.reflexio",
    ]:
        sys.modules.pop(module_name, None)
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
        trace_sdk = importlib.import_module("trace_sdk")
    return {
        "trace_sdk": importlib.reload(trace_sdk),
        "ledger": importlib.reload(importlib.import_module("trace_autonomy_ledger")),
        "quality": importlib.reload(importlib.import_module("trace_quality_history")),
        "refinement": importlib.reload(importlib.import_module("trace_refinement")),
        "rules": importlib.reload(importlib.import_module("trace_rule_proposals")),
    }


def _read_events(trace_sdk) -> list[dict]:
    event_file = trace_sdk.get_event_file()
    return [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()]


def test_autonomy_ledger_records_decisions_and_graduation_status(
    monkeypatch, tmp_path: Path
) -> None:
    modules = _load_modules(monkeypatch, tmp_path)
    ledger = modules["ledger"].AutonomyLedger()

    decision = ledger.record_decision(
        summary="advance strategy automatically",
        decision_type="promotion",
        score=0.91,
        graduation_status="graduated",
        outcome="approved",
        rationale="beat baseline twice",
        constraints=["must pass validator"],
        evidence_event_ids=["evt-1", "evt-2"],
        artifact_paths=["/tmp/evidence.json"],
    )

    audit = ledger.record_audit(
        summary="controller executed autonomous promotion",
        action="apply_change",
        approval_status="approved",
        actor="controller",
        related_decision_id=decision["decision_id"],
    )

    events = [
        event for event in _read_events(modules["trace_sdk"]) if event["category"] == "autonomy"
    ]
    assert [event["action"] for event in events[-2:]] == ["decision", "audit"]
    assert events[-2]["payload"]["decision_id"] == decision["decision_id"]
    assert events[-2]["payload"]["graduation_status"] == "graduated"
    assert events[-2]["artifact_paths"] == ["/tmp/evidence.json"]
    assert events[-1]["payload"]["related_decision_id"] == decision["decision_id"]
    assert events[-1]["payload"]["approval_status"] == "approved"
    assert audit["related_decision_id"] == decision["decision_id"]


def test_quality_history_tracks_runs_and_detects_regression_and_trend(
    monkeypatch, tmp_path: Path
) -> None:
    modules = _load_modules(monkeypatch, tmp_path)
    history = modules["quality"].QualityHistory()

    history.append_run(
        summary="baseline quality",
        run_label="run-1",
        dimension_scores={"accuracy": 0.80, "stability": 0.70},
        overall_score=0.75,
    )
    history.append_run(
        summary="second quality check",
        run_label="run-2",
        dimension_scores={"accuracy": 0.76, "stability": 0.72},
        overall_score=0.74,
    )

    events = [
        event for event in _read_events(modules["trace_sdk"]) if event["category"] == "quality"
    ]
    assert len(events) >= 2
    assert events[-1]["payload"]["regressions"] == ["accuracy", "overall"]
    assert events[-1]["payload"]["trend"] == "down"
    assert events[-1]["payload"]["delta"]["accuracy"] == -0.04
    assert events[-1]["payload"]["delta"]["stability"] == 0.02


def test_rule_proposals_capture_rationale_evidence_and_status_updates(
    monkeypatch, tmp_path: Path
) -> None:
    modules = _load_modules(monkeypatch, tmp_path)
    proposals = modules["rules"].RuleProposalRegistry()

    proposal = proposals.create_proposal(
        title="Require validator pass before promotion",
        rationale="bad promotions followed skipped validation",
        evidence_event_ids=["evt-5"],
        expected_impact="reduce regressions",
        proposed_rule="block promotion on validator failure",
    )
    updated = proposals.update_status(
        proposal_id=proposal["proposal_id"],
        status="accepted",
        resolution_note="adopted by controller",
    )

    events = [
        event
        for event in _read_events(modules["trace_sdk"])
        if event["category"] == "rule_proposal"
    ]
    assert events[-2]["action"] == "create"
    assert events[-2]["payload"]["status"] == "proposed"
    assert events[-2]["payload"]["evidence_event_ids"] == ["evt-5"]
    assert events[-1]["action"] == "status_update"
    assert events[-1]["payload"]["proposal_id"] == proposal["proposal_id"]
    assert events[-1]["payload"]["status"] == "accepted"
    assert updated["status"] == "accepted"


def test_refinement_recorder_tracks_iteration_chain_and_stopping_reason(
    monkeypatch, tmp_path: Path
) -> None:
    modules = _load_modules(monkeypatch, tmp_path)
    recorder = modules["refinement"].RefinementRecorder()

    session = recorder.start_session(
        summary="improve prompt",
        objective="increase pass rate",
        initial_context={"prompt_version": 1},
    )
    iteration = recorder.record_iteration(
        session_id=session["session_id"],
        iteration=1,
        generate={"candidate": "prompt-v2"},
        critique={"issue": "too verbose"},
        revise={"candidate": "prompt-v3"},
        evaluate={"score": 0.84},
    )
    completed = recorder.finish_session(
        session_id=session["session_id"],
        stopping_reason="plateau",
        final_outcome="accepted",
    )

    events = [
        event for event in _read_events(modules["trace_sdk"]) if event["category"] == "refinement"
    ]
    assert [event["action"] for event in events[-3:]] == [
        "session_start",
        "iteration",
        "session_finish",
    ]
    assert events[-2]["payload"]["iteration"] == 1
    assert events[-2]["payload"]["generate"]["candidate"] == "prompt-v2"
    assert events[-1]["payload"]["stopping_reason"] == "plateau"
    assert iteration["session_id"] == session["session_id"]
    assert completed["final_outcome"] == "accepted"


def test_adapter_payload_builders_produce_target_specific_shapes() -> None:
    from trace_adapters.halo import build_halo_payload
    from trace_adapters.recursive_improve import build_recursive_improve_payload
    from trace_adapters.reflexio import build_reflexio_payload

    quality = {"trend": "up"}
    usage = {"total": {"total_tokens": 42}}

    halo = build_halo_payload(
        research_round=3,
        thesis_id="t-1",
        outcome="compiled",
        family="ema",
        reasoning="good",
        quality=quality,
        usage=usage,
    )
    recursive_improve = build_recursive_improve_payload(
        research_round=3,
        thesis_id="t-1",
        outcome="compiled",
        family="ema",
        reasoning="good",
        quality=quality,
        usage=usage,
    )
    reflexio = build_reflexio_payload(
        research_round=3,
        thesis_id="t-1",
        outcome="compiled",
        family="ema",
        reasoning="good",
        quality=quality,
        usage=usage,
    )

    assert halo["loop_state"]["research_round"] == 3
    assert recursive_improve["iteration_context"]["candidate_id"] == "t-1"
    assert reflexio["episode"]["outcome"] == "compiled"
    quality["trend"] = "down"
    assert halo["evaluation"]["quality"]["trend"] == "up"


def test_adapter_export_packages_include_target_files_and_metadata() -> None:
    from trace_adapters.halo import build_halo_export_package
    from trace_adapters.recursive_improve import build_recursive_improve_export_package
    from trace_adapters.reflexio import build_reflexio_export_package

    halo = build_halo_export_package(
        research_round=2,
        thesis_id="th-1",
        outcome="compiled",
        family="ema",
        reasoning="solid",
        quality={"trend": "up"},
        usage={"total": {"total_tokens": 12}},
    )
    recursive_improve = build_recursive_improve_export_package(
        research_round=2,
        thesis_id="th-1",
        outcome="compiled",
        family="ema",
        reasoning="solid",
        quality={"trend": "up"},
        usage={"total": {"total_tokens": 12}},
    )
    reflexio = build_reflexio_export_package(
        research_round=2,
        thesis_id="th-1",
        outcome="compiled",
        family="ema",
        reasoning="solid",
        quality={"trend": "up"},
        usage={"total": {"total_tokens": 12}},
    )

    assert halo["target"] == "halo"
    assert halo["files"]["halo-event.json"]["loop_state"]["thesis_id"] == "th-1"
    assert (
        recursive_improve["files"]["recursive-improve-event.json"]["iteration_context"]["round"]
        == 2
    )
    assert reflexio["files"]["reflexio-event.json"]["episode"]["family"] == "ema"


def test_recursive_improve_and_reflexio_exports_include_canonical_trace_details(
    monkeypatch, tmp_path: Path
) -> None:
    modules = _load_modules(monkeypatch, tmp_path)
    trace_sdk = modules["trace_sdk"]
    trace_sdk.set_family("ema", job=20)
    trace_sdk.begin_round(7)
    trace_sdk.begin_hypothesis("adapter-contract")
    trace_id = trace_sdk.trace_agent_prompt(
        "analyst",
        "Inspect prior failures.",
        "Stay concrete.",
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
        '{"pf": 1.8}',
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.trace_agent_response(
        "analyst",
        trace_id,
        '{"lesson": "avoid duplicate baseline"}',
        {"lesson": "avoid duplicate baseline"},
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.record_usage_event(
        "analyst",
        model_provider="openai",
        model_name="gpt-5.2",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
    )

    from trace_adapters.recursive_improve import build_recursive_improve_export_package
    from trace_adapters.reflexio import build_reflexio_export_package

    recursive_improve = build_recursive_improve_export_package(
        research_round=7,
        thesis_id="th-7",
        outcome="rejected",
        family="ema",
        reasoning="baseline duplicated",
        rejection_reason="no behavior change",
        quality={"trend": "down"},
        usage={"total": {"total_tokens": 15}},
        canonical_trace_path=trace_sdk.get_event_file(),
    )
    reflexio = build_reflexio_export_package(
        research_round=7,
        thesis_id="th-7",
        outcome="rejected",
        family="ema",
        reasoning="baseline duplicated",
        rejection_reason="no behavior change",
        quality={"trend": "down"},
        usage={"total": {"total_tokens": 15}},
        canonical_trace_path=trace_sdk.get_event_file(),
    )

    ri_trace = recursive_improve["files"]["recursive-improve-trace.json"]
    assert ri_trace["session_id"] == trace_sdk.get_run_id()
    assert ri_trace["success"] is False
    assert ri_trace["feedback"] == "no behavior change"
    assert ri_trace["metadata"]["candidate_id"] == "th-7"
    assert ri_trace["duration_s"] > 0
    assert any(message["role"] == "system" for message in ri_trace["messages"])
    assert any(message["content"] == "Inspect prior failures." for message in ri_trace["messages"])
    assert any(message["kind"] == "tool_result" for message in ri_trace["messages"])
    assert any(message["kind"] == "usage" for message in ri_trace["messages"])

    reflexio_event = reflexio["files"]["reflexio-event.json"]
    trajectory = reflexio["files"]["reflexio-trajectory.json"]
    assert reflexio_event["memory_key"] == "ema:round-7:thesis-th-7"
    assert reflexio_event["feedback_signal"]["rejection_reason"] == "no behavior change"
    assert reflexio_event["trajectory"] == trajectory
    assert any(step["action"] == "tool_result" for step in trajectory)
    assert any("duplicate baseline" in step["content"] for step in trajectory)


def test_recursive_improve_trace_attaches_token_usage_to_assistant_messages(
    monkeypatch, tmp_path: Path
) -> None:
    modules = _load_modules(monkeypatch, tmp_path)
    trace_sdk = modules["trace_sdk"]
    trace_sdk.set_family("ema", job=20)
    trace_sdk.begin_round(8)
    trace_id = trace_sdk.trace_agent_prompt(
        "conductor",
        "Find the next thesis.",
        "Return JSON.",
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.trace_agent_response(
        "conductor",
        trace_id,
        '{"thesis": "delay entries"}',
        {"thesis": "delay entries"},
        model_provider="openai",
        model_name="gpt-5.2",
    )
    trace_sdk.record_usage_event(
        "conductor",
        model_provider="openai",
        model_name="gpt-5.2",
        input_tokens=100,
        output_tokens=25,
        total_tokens=125,
        cached_input_tokens=40,
        estimated_input_tokens=90,
        estimated_output_tokens=20,
        estimated_total_tokens=110,
    )

    from trace_adapters.recursive_improve import build_recursive_improve_trace

    recursive_trace = build_recursive_improve_trace(
        trace_sdk.get_event_file(),
        {
            "iteration_context": {
                "round": 8,
                "family": "ema",
                "candidate_id": "th-8",
                "status": "compiled",
            },
            "feedback": {},
        },
    )

    assistant_messages = [
        message for message in recursive_trace["messages"] if message["role"] == "assistant"
    ]
    assert assistant_messages
    assert assistant_messages[-1]["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
        "cached_input_tokens": 40,
        "estimated_prompt_tokens": 90,
        "estimated_completion_tokens": 20,
        "estimated_total_tokens": 110,
    }


def test_recursive_improve_trace_attaches_usage_when_usage_precedes_response(
    tmp_path: Path,
) -> None:
    from trace_adapters.recursive_improve import build_recursive_improve_trace

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "timestamp": "2026-05-07T00:00:00.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "usage",
                        "action": "accumulate",
                        "model_name": "gpt-5.2",
                        "payload": {
                            "agent": "web_researcher",
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_id": "evt-2",
                        "timestamp": "2026-05-07T00:00:01.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "agent",
                        "action": "response",
                        "model_name": "gpt-5.2",
                        "payload": {
                            "agent_name": "web-researcher",
                            "parsed": {"ok": True},
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    recursive_trace = build_recursive_improve_trace(
        trace,
        {
            "iteration_context": {
                "round": 1,
                "family": "ema",
                "candidate_id": "th-1",
                "status": "compiled",
            },
            "feedback": {},
        },
    )

    assistant_messages = [
        message for message in recursive_trace["messages"] if message["role"] == "assistant"
    ]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["usage"]["total_tokens"] == 15


def test_recursive_improve_trace_keeps_unmatched_usage_as_metadata_only(
    tmp_path: Path,
) -> None:
    from trace_adapters.recursive_improve import build_recursive_improve_trace

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        json.dumps(
            {
                "event_id": "evt-1",
                "timestamp": "2026-05-07T00:00:00.000Z",
                "run_id": "run-1",
                "family": "ema",
                "job": 20,
                "category": "usage",
                "action": "accumulate",
                "model_name": "gpt-5.2",
                "payload": {
                    "agent": "conductor",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    recursive_trace = build_recursive_improve_trace(
        trace,
        {
            "iteration_context": {
                "round": 1,
                "family": "ema",
                "candidate_id": "th-1",
                "status": "compiled",
            },
            "feedback": {},
        },
    )

    assert [message["role"] for message in recursive_trace["messages"]] == ["metadata"]
    assert recursive_trace["usage"]["total_tokens"] == 15
    metrics = recursive_trace["metadata"]["usage_summary"]
    assert metrics["trace_total_tokens"] == 15
    assert metrics["assistant_matched_total_tokens"] == 0
    assert metrics["unmatched_total_tokens"] == 15
    assert metrics["assistant_usage_message_count"] == 0


def test_recursive_improve_usage_metrics_separate_matched_and_trace_level_usage(
    tmp_path: Path,
) -> None:
    from trace_adapters.recursive_improve import (
        build_recursive_improve_export_package,
        build_recursive_improve_trace,
    )

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "timestamp": "2026-05-07T00:00:00.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "agent",
                        "action": "response",
                        "model_name": "gpt-5.2",
                        "payload": {"agent_name": "conductor", "parsed": {"ok": True}},
                    }
                ),
                json.dumps(
                    {
                        "event_id": "evt-2",
                        "timestamp": "2026-05-07T00:00:01.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "usage",
                        "action": "accumulate",
                        "model_name": "gpt-5.2",
                        "payload": {
                            "agent": "conductor",
                            "input_tokens": 100,
                            "output_tokens": 25,
                            "total_tokens": 125,
                            "cached_input_tokens": 40,
                            "estimated_input_tokens": 90,
                            "estimated_output_tokens": 20,
                            "estimated_total_tokens": 110,
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_id": "evt-3",
                        "timestamp": "2026-05-07T00:00:02.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "usage",
                        "action": "accumulate",
                        "model_name": "gpt-5.2",
                        "payload": {
                            "agent": "web_researcher",
                            "input_tokens": 80,
                            "output_tokens": 20,
                            "total_tokens": 100,
                            "cached_input_tokens": 30,
                            "estimated_input_tokens": 70,
                            "estimated_output_tokens": 10,
                            "estimated_total_tokens": 80,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = {
        "iteration_context": {
            "round": 1,
            "family": "ema",
            "candidate_id": "th-1",
            "status": "compiled",
        },
        "feedback": {},
    }
    recursive_trace = build_recursive_improve_trace(trace, payload)

    metrics = recursive_trace["metadata"]["usage_summary"]
    assert metrics == {
        "source": "recursive_improve_adapter_trace_usage",
        "trace_total_tokens": 225,
        "trace_prompt_tokens": 180,
        "trace_completion_tokens": 45,
        "trace_cached_input_tokens": 70,
        "trace_estimated_total_tokens": 190,
        "assistant_matched_total_tokens": 125,
        "assistant_matched_prompt_tokens": 100,
        "assistant_matched_completion_tokens": 25,
        "assistant_usage_message_count": 1,
        "unmatched_total_tokens": 100,
        "unmatched_prompt_tokens": 80,
        "unmatched_completion_tokens": 20,
    }

    export_package = build_recursive_improve_export_package(
        research_round=1,
        thesis_id="th-1",
        outcome="compiled",
        family="ema",
        reasoning="solid",
        canonical_trace_path=trace,
    )
    assert "recursive-improve-usage-metrics.json" not in export_package["files"]
    assert (
        export_package["files"]["recursive-improve-trace.json"]["metadata"]["usage_summary"]
        == metrics
    )


def test_recursive_improve_usage_matching_prefers_trace_id_over_agent_alias(
    tmp_path: Path,
) -> None:
    from trace_adapters.recursive_improve import build_recursive_improve_trace

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "timestamp": "2026-05-07T00:00:00.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "agent",
                        "action": "response",
                        "model_name": "gpt-5.2",
                        "payload": {
                            "agent_name": "openai-web-researcher",
                            "trace_id": "trace-web-001",
                            "parsed": {"ok": True},
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_id": "evt-2",
                        "timestamp": "2026-05-07T00:00:01.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "usage",
                        "action": "accumulate",
                        "model_name": "gpt-5.2",
                        "payload": {
                            "agent": "web_researcher",
                            "trace_id": "trace-web-001",
                            "input_tokens": 80,
                            "output_tokens": 20,
                            "total_tokens": 100,
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    recursive_trace = build_recursive_improve_trace(
        trace,
        {
            "iteration_context": {
                "round": 1,
                "family": "ema",
                "candidate_id": "th-1",
                "status": "compiled",
            },
            "feedback": {},
        },
    )

    assistant_messages = [
        message for message in recursive_trace["messages"] if message["role"] == "assistant"
    ]
    assert assistant_messages[0]["usage"]["total_tokens"] == 100
    assert recursive_trace["metadata"]["usage_summary"]["unmatched_total_tokens"] == 0


def test_trace_export_adapters_skip_malformed_canonical_jsonl(tmp_path: Path) -> None:
    from trace_adapters.recursive_improve import build_recursive_improve_trace
    from trace_adapters.reflexio import build_reflexio_trajectory

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                "{bad json",
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "timestamp": "2026-05-07T00:00:00.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "agent",
                        "action": "tool_result",
                        "summary": "tool returned",
                        "payload": {
                            "agent_name": "analyst",
                            "tool_name": "read_file",
                            "tool_output_preview": "ok",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    recursive_trace = build_recursive_improve_trace(
        trace,
        {
            "iteration_context": {
                "round": 1,
                "family": "ema",
                "candidate_id": "th-1",
                "status": "rejected",
            },
            "feedback": {"rejection_reason": "bad"},
        },
    )
    trajectory = build_reflexio_trajectory(trace)

    assert recursive_trace["messages"][0]["content"] == "ok"
    assert trajectory[0]["content"] == "ok"


def test_recursive_improve_and_reflexio_redact_tool_previews(tmp_path: Path) -> None:
    from trace_adapters.recursive_improve import build_recursive_improve_trace
    from trace_adapters.reflexio import build_reflexio_trajectory

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "timestamp": "2026-05-07T00:00:00.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "agent",
                        "action": "tool_call",
                        "summary": "tool called",
                        "payload": {
                            "agent_name": "analyst",
                            "tool_name": "run_python",
                            "tool_input_preview": "OPENAI_API_KEY=sk-secret123456789",
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_id": "evt-2",
                        "timestamp": "2026-05-07T00:00:01.000Z",
                        "run_id": "run-1",
                        "family": "ema",
                        "job": 20,
                        "category": "agent",
                        "action": "tool_result",
                        "summary": "tool returned",
                        "payload": {
                            "agent_name": "analyst",
                            "tool_name": "run_python",
                            "tool_output_preview": "token=abc123",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    recursive_trace = build_recursive_improve_trace(
        trace,
        {
            "iteration_context": {
                "round": 1,
                "family": "ema",
                "candidate_id": "th-1",
                "status": "rejected",
            },
            "feedback": {"rejection_reason": "bad"},
        },
    )
    trajectory = build_reflexio_trajectory(trace)

    assert recursive_trace["messages"][0]["content"] == "OPENAI_API_KEY=<redacted>"
    assert recursive_trace["messages"][1]["content"] == "token=<redacted>"
    assert trajectory[0]["content"] == "OPENAI_API_KEY=<redacted>"
    assert trajectory[1]["content"] == "token=<redacted>"


def test_reflexio_redacts_summary_fields(tmp_path: Path) -> None:
    from trace_adapters.reflexio import build_reflexio_trajectory

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        json.dumps(
            {
                "event_id": "evt-1",
                "timestamp": "2026-05-07T00:00:00.000Z",
                "category": "state",
                "action": "transition",
                "summary": "token=abc123",
                "payload": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    trajectory = build_reflexio_trajectory(trace)

    assert trajectory[0]["summary"] == "token=<redacted>"
    assert trajectory[0]["content"] == "token=<redacted>"


def test_recursive_improve_duration_handles_mixed_timezone_timestamps(tmp_path: Path) -> None:
    from trace_adapters.recursive_improve import build_recursive_improve_trace

    trace = tmp_path / "trace-events.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_id": "evt-1",
                        "timestamp": "2026-05-07T00:00:00.000",
                        "category": "agent",
                        "action": "tool_call",
                        "payload": {"tool_input_preview": "a"},
                    }
                ),
                json.dumps(
                    {
                        "event_id": "evt-2",
                        "timestamp": "2026-05-07T00:00:01.000Z",
                        "category": "agent",
                        "action": "tool_result",
                        "payload": {"tool_output_preview": "b"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    recursive_trace = build_recursive_improve_trace(
        trace,
        {
            "iteration_context": {
                "round": 1,
                "family": "ema",
                "candidate_id": "th-1",
                "status": "rejected",
            },
            "feedback": {"rejection_reason": "bad"},
        },
    )

    assert recursive_trace["duration_s"] == 1.0
