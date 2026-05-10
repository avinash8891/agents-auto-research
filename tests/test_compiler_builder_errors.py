from __future__ import annotations

import compiler_builder


def test_trace_builder_finish_emits_explicit_builder_error_event(monkeypatch):
    events = []
    traces = []
    monkeypatch.setattr(compiler_builder, "trace", lambda *args, **kwargs: traces.append(args))
    monkeypatch.setattr(
        compiler_builder,
        "record_event",
        lambda **kwargs: events.append(kwargs),
    )

    compiler_builder._trace_builder_finish(
        thesis_id="bad-builder-thesis",
        result={
            "status": "error",
            "error_code": "builder_implementation_contract_failed",
            "reason": "implementation_contract_failed: config_key_not_consumed_by_runtime:x",
        },
        artifact_paths=["builder-requests/bad-builder-thesis/result.json"],
    )

    actions = [event["action"] for event in events]
    assert "finish" in actions
    assert "builder_error" in actions
    error_event = next(event for event in events if event["action"] == "builder_error")
    assert error_event["payload"]["error_code"] == "builder_implementation_contract_failed"
    assert error_event["payload"]["thesis_id"] == "bad-builder-thesis"
