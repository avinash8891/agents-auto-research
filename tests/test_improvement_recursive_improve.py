from __future__ import annotations

import json
from pathlib import Path

import pytest

import improvement_recursive_improve as ri
from autoresearch_constants import ENV_IMPROVEMENT_RECURSIVE_IMPROVE


def _write_trace(root: Path, round_id: int, thesis_id: str = "T1") -> Path:
    trace_dir = (
        root
        / "runtime"
        / "jobs"
        / "job-1"
        / "research"
        / f"round-{round_id}"
        / "trace_exports"
        / f"round-{round_id:03d}-{thesis_id}"
        / "recursive_improve"
    )
    trace_dir.mkdir(parents=True)
    trace = {
        "session_id": f"round-{round_id}",
        "success": True,
        "metadata": {"job": 22, "research_round": round_id, "candidate_id": thesis_id},
        "messages": [
            {
                "role": "user",
                "agent": "research-conductor",
                "content": "conductor prompt",
                "kind": "prompt",
            },
            {
                "role": "assistant",
                "agent": "research-conductor",
                "content": "conductor response",
                "kind": "response",
            },
            {
                "role": "tool",
                "agent": "analyst",
                "content": "analyst tool",
                "kind": "tool_result",
            },
            {
                "role": "assistant",
                "agent": "openai-web-researcher",
                "content": "web response",
                "kind": "response",
            },
        ],
    }
    target = trace_dir / "recursive-improve-trace.json"
    target.write_text(json.dumps(trace), encoding="utf-8")
    return target


def _write_job_trace(root: Path, job: int, round_id: int, thesis_id: str = "T1") -> Path:
    trace_dir = (
        root
        / "runtime"
        / "jobs"
        / f"job-{job}"
        / "research"
        / f"round-{round_id}"
        / "trace_exports"
        / f"round-{round_id:03d}-{thesis_id}"
        / "recursive_improve"
    )
    trace_dir.mkdir(parents=True)
    trace = {
        "session_id": f"round-{round_id}",
        "success": True,
        "metadata": {"job": job, "research_round": round_id, "candidate_id": thesis_id},
        "messages": [
            {
                "role": "user",
                "agent": "research-conductor",
                "content": "conductor prompt",
                "kind": "prompt",
            },
            {
                "role": "assistant",
                "agent": "research-conductor",
                "content": "conductor response",
                "kind": "response",
            },
            {
                "role": "tool",
                "agent": "analyst",
                "content": "analyst tool",
                "kind": "tool_result",
            },
            {
                "role": "assistant",
                "agent": "openai-web-researcher",
                "content": "web response",
                "kind": "response",
            },
        ],
    }
    target = trace_dir / "recursive-improve-trace.json"
    target.write_text(json.dumps(trace), encoding="utf-8")
    return target


def test_prepare_report_batch_slices_traces_per_agent(tmp_path):
    _write_job_trace(tmp_path, 22, 1)
    _write_job_trace(tmp_path, 22, 2)

    result = ri.prepare_recursive_improve_report_batch(
        tmp_path,
        start_round=1,
        end_round=2,
        job=22,
        agents=("conductor", "analyst", "web-researcher"),
    )

    assert result.status == "prepared"
    assert result.rounds == (1, 2)
    conductor_trace = next((result.output_dir / "conductor" / "eval" / "traces").glob("*.json"))
    analyst_trace = next((result.output_dir / "analyst" / "eval" / "traces").glob("*.json"))
    web_trace = next((result.output_dir / "web-researcher" / "eval" / "traces").glob("*.json"))

    conductor = json.loads(conductor_trace.read_text(encoding="utf-8"))
    analyst = json.loads(analyst_trace.read_text(encoding="utf-8"))
    web = json.loads(web_trace.read_text(encoding="utf-8"))

    assert {m["content"] for m in conductor["messages"]} == {
        "conductor prompt",
        "conductor response",
    }
    assert [m["content"] for m in analyst["messages"]] == ["analyst tool"]
    assert [m["content"] for m in web["messages"]] == ["web response"]


def test_scheduled_report_skips_non_interval_round(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RECURSIVE_IMPROVE, "1")
    _write_job_trace(tmp_path, 22, 9)

    result = ri.run_scheduled_recursive_improve_reports(
        tmp_path,
        research_round=9,
        job=22,
        execute=False,
    )

    assert result.status == "skipped"
    assert result.reason == "round_not_on_interval"


def test_scheduled_report_prepares_interval_window(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RECURSIVE_IMPROVE, "1")
    for round_id in range(1, 11):
        _write_job_trace(tmp_path, 22, round_id, thesis_id=f"T{round_id}")

    result = ri.run_scheduled_recursive_improve_reports(
        tmp_path,
        research_round=10,
        job=22,
        execute=False,
    )

    assert result.status == "prepared"
    assert result.rounds == (1, 10)
    assert result.output_dir == (
        tmp_path / "improvement_reports" / "recursive_improve" / "job-22" / "rounds-001-010"
    )


def test_manual_report_can_run_any_range_without_flag(tmp_path):
    _write_job_trace(tmp_path, 22, 3)
    _write_job_trace(tmp_path, 22, 4)

    result = ri.run_manual_recursive_improve_reports(
        tmp_path,
        start_round=3,
        end_round=4,
        job=22,
        execute=False,
    )

    assert result.status == "prepared"
    assert result.rounds == (3, 4)


def test_execute_report_invokes_command_per_agent(tmp_path, monkeypatch):
    _write_job_trace(tmp_path, 22, 1)
    calls: list[dict[str, object]] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        return type("Completed", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr(ri.subprocess, "run", fake_run)

    result = ri.run_manual_recursive_improve_reports(
        tmp_path,
        start_round=1,
        end_round=1,
        job=22,
        agents=("conductor", "analyst"),
        execute=True,
    )

    assert result.status == "completed"
    assert [call["kwargs"]["cwd"].name for call in calls] == ["conductor", "analyst"]
    assert all("codex" in call["cmd"][0] for call in calls)


def test_bad_manual_range_raises(tmp_path):
    with pytest.raises(ValueError, match="start_round"):
        ri.run_manual_recursive_improve_reports(
            tmp_path,
            start_round=5,
            end_round=4,
            job=22,
            execute=False,
        )


def test_prepare_report_batch_uses_job_scoped_recursive_improve_exports(tmp_path):
    _write_trace(tmp_path, 1, thesis_id="stale-global")
    _write_job_trace(tmp_path, 22, 1, thesis_id="job-trace")

    result = ri.prepare_recursive_improve_report_batch(
        tmp_path,
        start_round=1,
        end_round=1,
        job=22,
        agents=("conductor",),
    )

    assert result.status == "prepared"
    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert any(
        "job-22/research/round-1/trace_exports/round-001-job-trace" in path
        for path in manifest["source_traces"]
    )
    assert all("stale-global" not in path for path in manifest["source_traces"])
