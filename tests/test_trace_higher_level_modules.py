from __future__ import annotations

import importlib
import json
import sys
import warnings
from pathlib import Path


def _load_modules(monkeypatch, tmp_path: Path):
    for module_name in [
        "trace_logger",
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
        trace_logger = importlib.import_module("trace_logger")
    return {
        "trace_logger": importlib.reload(trace_logger),
        "ledger": importlib.reload(importlib.import_module("trace_autonomy_ledger")),
        "quality": importlib.reload(importlib.import_module("trace_quality_history")),
        "refinement": importlib.reload(importlib.import_module("trace_refinement")),
        "rules": importlib.reload(importlib.import_module("trace_rule_proposals")),
    }


def _read_events(trace_logger) -> list[dict]:
    event_file = trace_logger.get_event_file()
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
        event for event in _read_events(modules["trace_logger"]) if event["category"] == "autonomy"
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
        event for event in _read_events(modules["trace_logger"]) if event["category"] == "quality"
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
        for event in _read_events(modules["trace_logger"])
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
        event
        for event in _read_events(modules["trace_logger"])
        if event["category"] == "refinement"
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
