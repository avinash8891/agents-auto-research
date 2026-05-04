"""Unit tests for improvement_ratchet.

Critical invariants:
  - Flag-off is a no-op returning ``"skip"``.
  - The cold-start oracle (no eval baseline) keeps iff outcome is in
    {compiled, stopped}.
  - The benchmark oracle classifies into keep / revert_recommended /
    inconclusive_keep depending on delta vs. prior stdev.
  - **No git invocation, ever.** Asserted by replacing
    ``subprocess.run`` with a sentinel that fails the test if called.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import improvement_ratchet
from autoresearch_constants import ENV_IMPROVEMENT_RATCHET


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_IMPROVEMENT_RATCHET, raising=False)
    yield


@pytest.fixture(autouse=True)
def _ban_subprocess(monkeypatch):
    """Critical safety: any subprocess invocation fails the test."""

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            f"improvement_ratchet must NEVER call subprocess.run; got args={args!r}"
        )

    monkeypatch.setattr(subprocess, "run", _forbidden)
    yield


def _eval_payload(*, label, mean, stdev):
    return {
        "label": label,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "repeat": 3,
        "primary_metric_name": "compiled_rate",
        "primary_metric": {"mean": mean, "stdev": stdev, "min": mean - stdev, "max": mean + stdev},
        "secondary_quality_p50_mean": None,
        "suites": [],
    }


def _write_eval(path: Path, *, label, mean, stdev):
    path.write_text(json.dumps(_eval_payload(label=label, mean=mean, stdev=stdev)))


# ── flag gate ────────────────────────────────────────────────────


def test_flag_off_returns_skip(tmp_path):
    controller = SimpleNamespace(root=tmp_path)
    assert improvement_ratchet.record_round_decision(controller, 1, "compiled", None) == "skip"
    # No file written.
    assert not (tmp_path / improvement_ratchet.DECISIONS_DIRNAME).exists()


# ── cold-start oracle ────────────────────────────────────────────


@pytest.mark.parametrize("outcome", ["compiled", "stopped"])
def test_cold_start_keeps_compiled_or_stopped(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 5, outcome, None)
    assert decision == "keep"
    rows = (tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["round"] == 5
    assert row["outcome"] == outcome
    assert row["decision"] == "keep"


@pytest.mark.parametrize("outcome", ["rejected", "conductor_error", "needs_code"])
def test_cold_start_revert_for_failure_outcomes(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 1, outcome, None)
    assert decision == "revert_recommended"


# ── benchmark oracle ─────────────────────────────────────────────


def test_benchmark_keep_when_lift_above_one_stdev(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    prior_path = eval_dir / "prior.json"
    current_path = eval_dir / "current.json"
    _write_eval(prior_path, label="prior", mean=0.5, stdev=0.1)
    _write_eval(current_path, label="current", mean=0.8, stdev=0.1)
    # Touch prior to be older.
    import os
    import time

    now = time.time()
    os.utime(prior_path, (now - 100, now - 100))
    os.utime(current_path, (now, now))

    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 7, "compiled", current_path)
    assert decision == "keep"
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert row["delta_vs_prior"] == pytest.approx(0.3, abs=1e-9)
    assert row["delta_in_stdevs"] == pytest.approx(3.0, abs=1e-9)


def test_benchmark_revert_when_drop_below_neg_one_stdev(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    prior_path = eval_dir / "prior.json"
    current_path = eval_dir / "current.json"
    _write_eval(prior_path, label="prior", mean=0.7, stdev=0.1)
    _write_eval(current_path, label="current", mean=0.4, stdev=0.1)
    import os
    import time

    now = time.time()
    os.utime(prior_path, (now - 100, now - 100))
    os.utime(current_path, (now, now))
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 1, "compiled", current_path)
    assert decision == "revert_recommended"


def test_benchmark_inconclusive_within_one_stdev(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    prior_path = eval_dir / "prior.json"
    current_path = eval_dir / "current.json"
    _write_eval(prior_path, label="prior", mean=0.5, stdev=0.5)
    _write_eval(current_path, label="current", mean=0.55, stdev=0.5)
    import os
    import time

    now = time.time()
    os.utime(prior_path, (now - 100, now - 100))
    os.utime(current_path, (now, now))
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 1, "compiled", current_path)
    assert decision == "inconclusive_keep"


def test_benchmark_no_prior_eval_returns_inconclusive(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    current_path = eval_dir / "current.json"
    _write_eval(current_path, label="current", mean=0.5, stdev=0.1)
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 1, "compiled", current_path)
    assert decision == "inconclusive_keep"


def test_benchmark_zero_stdev_baseline_returns_inconclusive(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    prior_path = eval_dir / "prior.json"
    current_path = eval_dir / "current.json"
    _write_eval(prior_path, label="prior", mean=0.5, stdev=0.0)
    _write_eval(current_path, label="current", mean=0.7, stdev=0.0)
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 1, "compiled", current_path)
    assert decision == "inconclusive_keep"


def test_benchmark_metric_name_mismatch_returns_inconclusive(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    prior = eval_dir / "prior.json"
    current = eval_dir / "current.json"
    payload = _eval_payload(label="prior", mean=0.5, stdev=0.1)
    payload["primary_metric_name"] = "quality_score_p50"
    prior.write_text(json.dumps(payload))
    _write_eval(current, label="current", mean=0.7, stdev=0.1)
    controller = SimpleNamespace(root=tmp_path)
    decision = improvement_ratchet.record_round_decision(controller, 1, "compiled", current)
    assert decision == "inconclusive_keep"


# ── decisions log shape ──────────────────────────────────────────


def test_decisions_log_records_required_fields(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    improvement_ratchet.record_round_decision(controller, 12, "compiled", None)
    rows = (tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().splitlines()
    row = json.loads(rows[0])
    for key in ("round", "outcome", "decision", "rationale", "ts"):
        assert key in row, f"missing key {key}"


def test_appends_rather_than_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    improvement_ratchet.record_round_decision(controller, 1, "compiled", None)
    improvement_ratchet.record_round_decision(controller, 2, "rejected", None)
    rows = (tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().splitlines()
    assert len(rows) == 2


# ── verdict coupling: conservative-min override ──────────────────


def test_apply_revert_overrides_ratchet_keep(tmp_path, monkeypatch):
    """Conservative-min: HALO-apply revert beats Ratchet keep."""
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    apply_decision = {"status": "revert_recommended", "reason": "lift_below_neg_one_stdev"}
    decision = improvement_ratchet.record_round_decision(
        controller, 3, "compiled", None, apply_decision=apply_decision
    )
    assert decision == "revert_recommended"
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert row["decision"] == "revert_recommended"
    assert row["ratchet_verdict_raw"] == "keep"
    assert row["halo_apply_verdict"] == "revert_recommended"
    assert row["halo_apply_status"] == "revert_recommended"


def test_ratchet_revert_holds_when_apply_keeps(tmp_path, monkeypatch):
    """Conservative-min: Ratchet revert is preserved when apply keeps."""
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    apply_decision = {"status": "keep"}
    decision = improvement_ratchet.record_round_decision(
        controller, 3, "rejected", None, apply_decision=apply_decision
    )
    assert decision == "revert_recommended"
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert row["ratchet_verdict_raw"] == "revert_recommended"
    assert row["halo_apply_verdict"] == "keep"


def test_apply_aborted_collapses_to_inconclusive(tmp_path, monkeypatch):
    """Aborted apply (eval gap) downgrades a Ratchet keep to inconclusive."""
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    apply_decision = {"status": "aborted", "reason": "eval_failed"}
    decision = improvement_ratchet.record_round_decision(
        controller, 3, "compiled", None, apply_decision=apply_decision
    )
    assert decision == "inconclusive_keep"
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert row["ratchet_verdict_raw"] == "keep"
    assert row["halo_apply_verdict"] == "inconclusive_keep"
    assert "eval_failed" in row["rationale"]


def test_apply_skip_no_opinion(tmp_path, monkeypatch):
    """HALO-apply skipped: Ratchet verdict survives unchanged."""
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    apply_decision = {"status": "skip"}
    decision = improvement_ratchet.record_round_decision(
        controller, 3, "compiled", None, apply_decision=apply_decision
    )
    assert decision == "keep"
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert row["halo_apply_verdict"] is None


def test_both_keep_concurs(tmp_path, monkeypatch):
    """Both signals keep: final verdict keeps, rationale notes concurrence."""
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    apply_decision = {"status": "keep"}
    decision = improvement_ratchet.record_round_decision(
        controller, 3, "compiled", None, apply_decision=apply_decision
    )
    assert decision == "keep"
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert "concurs" in row["rationale"]


def test_apply_decision_none_records_null_audit(tmp_path, monkeypatch):
    """Default-off HALO-apply path: audit fields present but null."""
    monkeypatch.setenv(ENV_IMPROVEMENT_RATCHET, "1")
    controller = SimpleNamespace(root=tmp_path)
    improvement_ratchet.record_round_decision(controller, 4, "compiled", None)
    row = json.loads((tmp_path / "improvement_reports/ratchet/decisions.jsonl").read_text().strip())
    assert row["halo_apply_status"] is None
    assert row["halo_apply_reason"] is None
    assert row["halo_apply_verdict"] is None
    assert row["ratchet_verdict_raw"] == "keep"


def test_safe_stat_mtime_returns_zero_on_deleted_file(tmp_path):
    """safe_stat_mtime must return 0.0 rather than raise on a missing file."""
    from persistence_utils import safe_stat_mtime

    ghost = tmp_path / "ghost.json"
    # File never existed — stat() would raise FileNotFoundError without the guard
    assert safe_stat_mtime(ghost) == 0.0
