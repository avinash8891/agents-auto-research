"""Regression test for the byte-identical flag-off invariant of
``autoresearch_research._run_improvement_hooks``.

If a future refactor moves any improvement-loop import above the flag
gate, the loop will pay the import cost on every round even with all
flags off. Worse, if a side-effecting call slips above the gate, the
controller root grows artifacts that pre-improvement-loop code never
produced — breaking the byte-identical guarantee. This test fails
loudly in either case.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from autoresearch_constants import (
    ENV_IMPROVEMENT_HALO,
    ENV_IMPROVEMENT_HALO_APPLY,
    ENV_IMPROVEMENT_RATCHET,
    ENV_IMPROVEMENT_RECURSIVE_IMPROVE,
    ENV_IMPROVEMENT_REFLEXION,
)

_IMPROVEMENT_MODULES = (
    "improvement_halo",
    "improvement_halo_apply",
    "improvement_ratchet",
    "improvement_reflexion",
    "improvement_recursive_improve",
    "eval_harness",
    "eval_metrics",
)


@pytest.fixture(autouse=True)
def _all_flags_off(monkeypatch):
    for env in (
        ENV_IMPROVEMENT_HALO,
        ENV_IMPROVEMENT_HALO_APPLY,
        ENV_IMPROVEMENT_RATCHET,
        ENV_IMPROVEMENT_REFLEXION,
        ENV_IMPROVEMENT_RECURSIVE_IMPROVE,
    ):
        monkeypatch.delenv(env, raising=False)
    yield


def _purge_improvement_modules():
    for name in list(sys.modules):
        if name in _IMPROVEMENT_MODULES:
            del sys.modules[name]


def test_flag_off_writes_nothing_to_controller_root(tmp_path):
    _purge_improvement_modules()
    from autoresearch_research import _run_improvement_hooks

    controller = SimpleNamespace(root=tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    _run_improvement_hooks(controller, research_round=1, result={"compiled_config_path": "x"})
    after = sorted(p.name for p in tmp_path.iterdir())
    assert (
        before == after
    ), f"flag-off path created files in controller.root: {set(after) - set(before)}"


def test_flag_off_does_not_import_improvement_modules(tmp_path):
    _purge_improvement_modules()
    from autoresearch_research import _run_improvement_hooks

    controller = SimpleNamespace(root=tmp_path)
    _run_improvement_hooks(controller, research_round=1, result={})
    leaked = [m for m in _IMPROVEMENT_MODULES if m in sys.modules]
    # `improvement_flags` is allowed (the gate itself); none of the gated modules.
    forbidden = {
        "improvement_halo",
        "improvement_halo_apply",
        "improvement_ratchet",
        "improvement_reflexion",
        "improvement_recursive_improve",
    }
    leaked_forbidden = [m for m in leaked if m in forbidden]
    assert not leaked_forbidden, (
        f"flag-off path eagerly imported gated modules: {leaked_forbidden}. "
        f"This breaks the byte-identical guarantee — gate the import behind the flag check."
    )


def test_recursive_improve_flag_runs_scheduled_report(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RECURSIVE_IMPROVE, "1")
    from autoresearch_research import _run_improvement_hooks

    calls = []

    def fake_run(root, *, research_round, job):
        calls.append((root, research_round, job))
        return SimpleNamespace(
            status="prepared",
            output_dir=tmp_path / "improvement_reports" / "recursive_improve",
            reason="",
        )

    monkeypatch.setattr(
        "improvement_recursive_improve.run_scheduled_recursive_improve_reports",
        fake_run,
    )
    controller = SimpleNamespace(root=tmp_path, read_state=lambda: {"job": 22})

    _run_improvement_hooks(controller, research_round=10, result={})

    assert calls == [(tmp_path, 10, 22)]


def test_ratchet_uses_cold_start_when_current_round_did_not_run_eval(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    from autoresearch_research import _run_improvement_hooks

    captured = {}

    def fail_latest_eval_path(_output_dir):
        raise FileNotFoundError("stale eval directory rotated")

    def fake_record(controller, round_number, outcome, eval_result_path, *, apply_decision=None):
        captured["eval_result_path"] = eval_result_path
        captured["outcome"] = outcome
        return "keep"

    monkeypatch.setattr("eval_harness.latest_eval_result_path", fail_latest_eval_path)
    monkeypatch.setattr("improvement_ratchet.record_round_decision", fake_record)

    controller = SimpleNamespace(root=tmp_path)
    _run_improvement_hooks(controller, research_round=4, result={"generated_config": "x"})

    assert captured == {"eval_result_path": None, "outcome": "compiled"}
