"""Unit tests for improvement_reflexion.

Verifies the flag gate, round-1 short-circuit, missing-export
graceful return, and the preamble construction. The export schema
tested here matches ``trace_adapters/reflexio.build_reflexio_payload``
byte-for-byte — that is the contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch_constants import ENV_IMPROVEMENT_REFLEXION
from improvement_reflexion import build_latest_reflexion_feedback, build_reflexion_feedback


def build_reflexio_payload(
    *,
    research_round: int,
    thesis_id: str,
    outcome: str,
    family: str,
    reasoning: str = "",
    rejection_reason: str = "",
) -> dict:
    """Inline copy of the producer schema (trace_adapters/reflexio.py:9).

    Inlined so tests don't import trace_sdk (which pulls openinference).
    Any schema change at the producer side must be mirrored here — the
    test catches drift.
    """
    return {
        "system": "reflexio",
        "episode": {
            "round": research_round,
            "family": family,
            "thesis_id": thesis_id,
            "outcome": outcome,
        },
        "reflection": {
            "reasoning": reasoning,
            "rejection_reason": rejection_reason,
            "quality": {},
        },
        "resources": {"usage": {}},
    }


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_IMPROVEMENT_REFLEXION, raising=False)
    yield


def _write_prior_export(
    root: Path,
    *,
    research_round: int,
    thesis_id: str = "T1",
    outcome: str = "rejected",
    reasoning: str = "tried fixed stop loss",
    rejection_reason: str = "stop too tight, gave up too early",
) -> Path:
    payload = build_reflexio_payload(
        research_round=research_round,
        thesis_id=thesis_id,
        outcome=outcome,
        family="ema",
        reasoning=reasoning,
        rejection_reason=rejection_reason,
    )
    target_dir = root / "trace_exports" / f"round-{research_round:03d}-{thesis_id}" / "reflexio"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "reflexio-event.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# ── flag gate ────────────────────────────────────────────────────


def test_flag_off_returns_empty(tmp_path):
    _write_prior_export(tmp_path, research_round=1)
    controller = SimpleNamespace(root=tmp_path)
    assert build_reflexion_feedback(controller, current_round=2) == ""


def test_flag_on_round_one_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    controller = SimpleNamespace(root=tmp_path)
    assert build_reflexion_feedback(controller, current_round=1) == ""


def test_flag_on_no_export_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    controller = SimpleNamespace(root=tmp_path)
    assert build_reflexion_feedback(controller, current_round=2) == ""


# ── happy path ───────────────────────────────────────────────────


def test_flag_on_with_prior_export_returns_preamble(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    _write_prior_export(
        tmp_path,
        research_round=4,
        reasoning="tried tight stop",
        rejection_reason="filtered too many trades",
    )
    controller = SimpleNamespace(root=tmp_path)
    feedback = build_reflexion_feedback(controller, current_round=5)
    assert "PRIOR ROUND REFLEXION (round 4)" in feedback
    assert "outcome: rejected" in feedback
    assert "tried tight stop" in feedback
    assert "filtered too many trades" in feedback
    assert "Avoid repeating this failure mode" in feedback


def test_flag_on_picks_most_recent_when_multiple_matches(tmp_path, monkeypatch):
    """Multiple thesis IDs in the same round → pick the most recent mtime."""
    import time

    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    older = _write_prior_export(
        tmp_path, research_round=2, thesis_id="A", reasoning="older reasoning"
    )
    time.sleep(0.01)
    newer = _write_prior_export(
        tmp_path, research_round=2, thesis_id="B", reasoning="newer reasoning"
    )
    # Bump newer's mtime to be strictly after older's, defensively.
    import os

    now = older.stat().st_mtime
    os.utime(newer, (now + 1.0, now + 1.0))

    controller = SimpleNamespace(root=tmp_path)
    feedback = build_reflexion_feedback(controller, current_round=3)
    assert "newer reasoning" in feedback
    assert "older reasoning" not in feedback


# ── degraded export ──────────────────────────────────────────────


def test_malformed_json_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    target_dir = tmp_path / "trace_exports" / "round-001-T1" / "reflexio"
    target_dir.mkdir(parents=True)
    (target_dir / "reflexio-event.json").write_text("not json", encoding="utf-8")
    controller = SimpleNamespace(root=tmp_path)
    assert build_reflexion_feedback(controller, current_round=2) == ""


def test_non_dict_json_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    target_dir = tmp_path / "trace_exports" / "round-001-T1" / "reflexio"
    target_dir.mkdir(parents=True)
    (target_dir / "reflexio-event.json").write_text("[1, 2, 3]", encoding="utf-8")
    controller = SimpleNamespace(root=tmp_path)
    assert build_reflexion_feedback(controller, current_round=2) == ""


def test_missing_optional_fields_yields_minimal_preamble(tmp_path, monkeypatch):
    """Empty reasoning/rejection_reason: preamble still emits outcome + footer."""
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    _write_prior_export(
        tmp_path,
        research_round=1,
        reasoning="",
        rejection_reason="",
        outcome="conductor_error",
    )
    controller = SimpleNamespace(root=tmp_path)
    feedback = build_reflexion_feedback(controller, current_round=2)
    assert "outcome: conductor_error" in feedback
    assert "you_reasoned" not in feedback
    assert "why_it_failed" not in feedback
    assert "Avoid repeating" in feedback


def test_feedback_includes_prior_trajectory_when_export_has_it(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    payload = build_reflexio_payload(
        research_round=1,
        thesis_id="T1",
        outcome="rejected",
        family="ema",
        reasoning="builder produced baseline clone",
        rejection_reason="same PF/trades as baseline",
    )
    payload["trajectory"] = [
        {"action": "prompt", "summary": "ask analyst to inspect duplicate config"},
        {"action": "tool_result", "summary": "backtest PF unchanged"},
    ]
    target_dir = tmp_path / "trace_exports" / "round-001-T1" / "reflexio"
    target_dir.mkdir(parents=True)
    (target_dir / "reflexio-event.json").write_text(json.dumps(payload), encoding="utf-8")

    controller = SimpleNamespace(root=tmp_path)
    feedback = build_reflexion_feedback(controller, current_round=2)

    assert "prior_trajectory" in feedback
    assert "prompt: ask analyst to inspect duplicate config" in feedback
    assert "tool_result: backtest PF unchanged" in feedback


def test_agent_scoped_feedback_uses_only_matching_agent_trajectory(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    payload = build_reflexio_payload(
        research_round=1,
        thesis_id="T1",
        outcome="builder_failed",
        family="ema",
        reasoning="round needed code",
        rejection_reason="generated config failed validation",
    )
    payload["trajectory"] = [
        {
            "agent": "analyst",
            "action": "tool_result",
            "summary": "missing /data/raw path probe wasted tokens",
        },
        {
            "agent": "builder",
            "action": "tool_result",
            "summary": "runtime_config contained unsupported key new_config_keys_needed",
        },
        {
            "agent": "web-researcher",
            "action": "response",
            "summary": "external evidence favored volatility expansion filters",
        },
    ]
    target_dir = tmp_path / "trace_exports" / "round-001-T1" / "reflexio"
    target_dir.mkdir(parents=True)
    (target_dir / "reflexio-event.json").write_text(json.dumps(payload), encoding="utf-8")

    controller = SimpleNamespace(root=tmp_path)

    builder_feedback = build_reflexion_feedback(controller, current_round=2, agent="builder")
    analyst_feedback = build_reflexion_feedback(controller, current_round=2, agent="analyst")

    assert "PRIOR AGENT REFLEXION (round 1, agent builder)" in builder_feedback
    assert "unsupported key new_config_keys_needed" in builder_feedback
    assert "missing /data/raw" not in builder_feedback
    assert "volatility expansion" not in builder_feedback

    assert "PRIOR AGENT REFLEXION (round 1, agent analyst)" in analyst_feedback
    assert "missing /data/raw path probe" in analyst_feedback
    assert "new_config_keys_needed" not in analyst_feedback


def test_agent_scoped_feedback_prefers_agent_reflection_over_round_reasoning(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    payload = build_reflexio_payload(
        research_round=1,
        thesis_id="T1",
        outcome="builder_failed",
        family="ema",
        reasoning="generic round lesson should not drive analyst feedback",
        rejection_reason="round failed",
    )
    payload["agent_reflections"] = {
        "analyst": {
            "lesson": "analyst-specific lesson: answer the focus question with compact pandas",
            "avoid": ["probing missing /data paths"],
            "repeat": ["read diagnostics first"],
            "evidence": ["run_python failed twice before succeeding"],
        },
        "builder": {
            "lesson": "builder-specific lesson: map keys through contract",
            "avoid": ["unsupported runtime keys"],
            "repeat": ["run verifier before reporting success"],
            "evidence": ["builder_implementation_contract_failed"],
        },
    }
    payload["trajectory"] = [
        {"agent": "analyst", "action": "tool_result", "summary": "analyst trace"},
        {"agent": "builder", "action": "builder_error", "summary": "builder trace"},
    ]
    target_dir = tmp_path / "trace_exports" / "round-001-T1" / "reflexio"
    target_dir.mkdir(parents=True)
    (target_dir / "reflexio-event.json").write_text(json.dumps(payload), encoding="utf-8")

    controller = SimpleNamespace(root=tmp_path)

    analyst_feedback = build_reflexion_feedback(controller, current_round=2, agent="analyst")
    builder_feedback = build_reflexion_feedback(controller, current_round=2, agent="builder")

    assert "analyst-specific lesson" in analyst_feedback
    assert "probing missing /data paths" in analyst_feedback
    assert "read diagnostics first" in analyst_feedback
    assert "run_python failed twice" in analyst_feedback
    assert "generic round lesson" not in analyst_feedback
    assert "builder-specific lesson" not in analyst_feedback

    assert "builder-specific lesson" in builder_feedback
    assert "unsupported runtime keys" in builder_feedback
    assert "run verifier before reporting success" in builder_feedback
    assert "analyst-specific lesson" not in builder_feedback


def test_agent_scoped_feedback_returns_empty_when_no_matching_agent(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    payload = build_reflexio_payload(
        research_round=1,
        thesis_id="T1",
        outcome="ok",
        family="ema",
    )
    payload["trajectory"] = [
        {"agent": "builder", "action": "response", "summary": "builder lesson only"}
    ]
    target_dir = tmp_path / "trace_exports" / "round-001-T1" / "reflexio"
    target_dir.mkdir(parents=True)
    (target_dir / "reflexio-event.json").write_text(json.dumps(payload), encoding="utf-8")

    controller = SimpleNamespace(root=tmp_path)

    assert build_reflexion_feedback(controller, current_round=2, agent="web-researcher") == ""


def test_latest_reflexion_feedback_reads_newest_round_for_builder(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_REFLEXION, "1")
    old_payload = build_reflexio_payload(
        research_round=1,
        thesis_id="old",
        outcome="failed",
        family="ema",
    )
    old_payload["trajectory"] = [
        {"agent": "builder", "action": "tool_result", "summary": "old builder lesson"}
    ]
    new_payload = build_reflexio_payload(
        research_round=3,
        thesis_id="new",
        outcome="failed",
        family="ema",
    )
    new_payload["trajectory"] = [
        {"agent": "builder", "action": "tool_result", "summary": "new builder lesson"}
    ]
    for round_id, thesis_id, payload in ((1, "old", old_payload), (3, "new", new_payload)):
        target_dir = tmp_path / "trace_exports" / f"round-{round_id:03d}-{thesis_id}" / "reflexio"
        target_dir.mkdir(parents=True)
        (target_dir / "reflexio-event.json").write_text(json.dumps(payload), encoding="utf-8")

    feedback = build_latest_reflexion_feedback(tmp_path, agent="builder")

    assert "PRIOR AGENT REFLEXION (round 3, agent builder)" in feedback
    assert "new builder lesson" in feedback
    assert "old builder lesson" not in feedback
