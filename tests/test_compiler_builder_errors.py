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


def test_copy_file_into_workspace_handles_source_outside_source_root(tmp_path) -> None:
    """On the VPS, builder_request artifacts live under the runtime root, not the
    code/release root, so source.relative_to(source_root) raised ValueError and
    the builder died before codegen. Files outside source_root must still copy."""
    from pathlib import Path

    from compiler_builder import _copy_file_into_workspace

    runtime_req = (
        tmp_path / "runtime" / "jobs" / "job-8" / "research" / "round-1" / "builder_request"
    )
    runtime_req.mkdir(parents=True)
    source = runtime_req / "thesis.json"
    source.write_text('{"mechanism_rule": "side == \'short\'"}')
    code_root = tmp_path / "releases" / "abc123"
    code_root.mkdir(parents=True)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    dest = _copy_file_into_workspace(source=source, source_root=code_root, workspace_root=workspace)
    assert dest.exists()
    assert dest.read_text() == '{"mechanism_rule": "side == \'short\'"}'
    assert Path(workspace) in dest.parents
