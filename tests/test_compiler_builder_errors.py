from __future__ import annotations

from pathlib import Path

import compiler_builder


def _fake_venv(tmp_path: Path) -> Path:
    """A directory that looks like a real virtualenv (pyvenv.cfg marker + bin/python)."""
    venv = tmp_path / "release" / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    return venv


def test_copy_builder_source_tree_symlinks_project_venv_into_workspace(tmp_path, monkeypatch):
    venv = _fake_venv(tmp_path)
    source = tmp_path / "source"
    (source / "strategies").mkdir(parents=True)
    (source / "strategies" / "__init__.py").write_text("")
    monkeypatch.setenv("AUTORESEARCH_PYTHON_BIN", str(venv / "bin" / "python"))

    workspace = tmp_path / "workspace"
    compiler_builder._copy_builder_source_tree(source, workspace)

    linked = workspace / ".venv"
    assert linked.is_symlink()
    assert linked.resolve() == venv.resolve()
    # `.venv/bin/python` resolves through the symlink to the real interpreter.
    assert (linked / "bin" / "python").exists()


def test_copy_builder_source_tree_fail_open_when_no_venv(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_PYTHON_BIN", raising=False)
    monkeypatch.setattr(
        compiler_builder.sys, "executable", str(tmp_path / "no" / "venv" / "python")
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "x.py").write_text("")

    workspace = tmp_path / "workspace"
    compiler_builder._copy_builder_source_tree(source, workspace)  # must not raise

    assert not (workspace / ".venv").exists()


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
