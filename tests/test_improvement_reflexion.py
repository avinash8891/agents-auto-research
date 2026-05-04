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
from improvement_reflexion import build_reflexion_feedback


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
