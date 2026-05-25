"""Unit tests for improvement_halo_apply.

Covers flag gate, missing-binary degradation, the lockfile guard, and
the three eval-decision branches (keep, revert_recommended,
inconclusive_keep). Mocks the Claude Code subprocess and ``run_eval``.
Asserts that only read-only ``git rev-parse`` is invoked — no
destructive git.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import improvement_halo_apply
from autoresearch_constants import ENV_IMPROVEMENT_HALO_APPLY
from eval_metrics import SuiteSummary, summarize_eval


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_IMPROVEMENT_HALO_APPLY, raising=False)
    yield


def _make_report(tmp_path: Path) -> Path:
    reports = tmp_path / "improvement_reports" / "halo"
    reports.mkdir(parents=True)
    p = reports / "round-001.md"
    p.write_text("# halo report\n", encoding="utf-8")
    return p


def _plant_prior_eval(tmp_path: Path, mean: float, stdev: float) -> Path:
    out = tmp_path / "eval_results"
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "label": "prior",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "repeat": 1,
        "primary_metric_name": "compiled_rate",
        "primary_metric": {
            "mean": mean,
            "stdev": stdev,
            "min": mean - stdev,
            "max": mean + stdev,
        },
        "secondary_quality_p50_mean": None,
        "suites": [
            {
                "compiled_rate": mean,
                "quality_score_p50": 0.5,
                "n_tasks": 10,
                "n_compiled": int(round(mean * 10)),
            }
        ],
    }
    p = out / "prior-2026-01-01T000000p0000.json"
    p.write_text(json.dumps(payload))
    return p


def _stub_eval_runner(metric: float):
    def runner(*, label):
        n_tasks = 100
        return summarize_eval(
            label=label,
            timestamp="2026-05-04T00:00:00+00:00",
            suites=[SuiteSummary(metric, 0.5, n_tasks, int(round(metric * n_tasks)))],
        )

    return runner


# ── flag gate ────────────────────────────────────────────────────


def test_flag_off_returns_skip(tmp_path):
    report = _make_report(tmp_path)
    assert improvement_halo_apply.apply_halo_report(report, tmp_path) == {"status": "skip"}


# ── binary missing ───────────────────────────────────────────────


def test_missing_claude_binary_aborts(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: None)
    report = _make_report(tmp_path)
    decision = improvement_halo_apply.apply_halo_report(report, tmp_path)
    assert decision["status"] == "aborted"
    assert decision["reason"] == "missing_claude_binary"


# ── lockfile ─────────────────────────────────────────────────────


def test_lock_held_aborts(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    report = _make_report(tmp_path)
    # Pre-create the lock — second apply must abort.
    (report.parent / ".apply.lock").write_text("held")
    decision = improvement_halo_apply.apply_halo_report(report, tmp_path)
    assert decision["status"] == "aborted"
    assert decision["reason"] == "lock_held"
    # Lockfile preserved (we didn't own it).
    assert (report.parent / ".apply.lock").exists()


# ── decision branches ────────────────────────────────────────────


def test_kept_when_lift_above_one_stdev(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)
    _plant_prior_eval(tmp_path, mean=0.5, stdev=0.1)

    git_calls: list[tuple] = []
    cc_calls: list[tuple] = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            git_calls.append(tuple(cmd))
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        if cmd[0] == "claude":
            cc_calls.append(tuple(cmd))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess invocation: {cmd}")

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=_stub_eval_runner(0.8),  # +0.3 over prior, /0.1 stdev = 3 stdevs
        subprocess_run=fake_run,
    )
    assert decision["status"] == "keep"
    assert decision["pre_head"] == "abc123"
    # Exactly one read-only git invocation; one Claude invocation.
    assert len(git_calls) == 1
    assert git_calls[0] == ("git", "rev-parse", "HEAD")
    assert len(cc_calls) == 1
    # Decisions log appended.
    decisions = (report.parent / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(decisions) == 1
    row = json.loads(decisions[0])
    assert row["status"] == "keep"


def test_revert_recommended_when_drop_below_neg_one_stdev(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)
    _plant_prior_eval(tmp_path, mean=0.7, stdev=0.1)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc123\n", stderr="")
        if cmd[0] == "claude":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected: {cmd}")

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=_stub_eval_runner(0.4),  # -0.3, /0.1 = -3 stdevs
        subprocess_run=fake_run,
    )
    assert decision["status"] == "revert_recommended"
    assert decision["pre_head"] == "abc123"


def test_inconclusive_when_within_one_stdev(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)
    _plant_prior_eval(tmp_path, mean=0.5, stdev=0.5)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=_stub_eval_runner(0.55),  # +0.05 / 0.5 = 0.1 stdev
        subprocess_run=fake_run,
    )
    assert decision["status"] == "inconclusive_keep"


def test_inconclusive_when_no_prior_eval(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=_stub_eval_runner(0.6),
        subprocess_run=fake_run,
    )
    assert decision["status"] == "inconclusive_keep"
    assert decision["reason"] == "no_prior_eval"


# ── safety: never destructive git ────────────────────────────────


def test_no_destructive_git_invocation(tmp_path, monkeypatch):
    """Critical safety invariant — only read-only `git rev-parse` allowed."""
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)
    _plant_prior_eval(tmp_path, 0.5, 0.1)

    DESTRUCTIVE_GIT = {"checkout", "reset", "revert", "push", "commit", "rm", "branch"}
    seen: list[tuple] = []

    def fake_run(cmd, **kwargs):
        seen.append(tuple(cmd))
        if cmd[0] == "git":
            assert cmd[1] not in DESTRUCTIVE_GIT, f"destructive git command: {cmd}"
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=_stub_eval_runner(0.8),
        subprocess_run=fake_run,
    )
    git_invocations = [c for c in seen if c[0] == "git"]
    assert all(c[1] == "rev-parse" for c in git_invocations)


# ── claude subprocess error ──────────────────────────────────────


def test_claude_nonzero_exit_aborts(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
        return SimpleNamespace(returncode=2, stdout="", stderr="boom")

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=_stub_eval_runner(0.5),
        subprocess_run=fake_run,
    )
    assert decision["status"] == "aborted"
    assert decision["reason"] == "claude_nonzero_exit"
    # Lock released even on abort.
    assert not (report.parent / ".apply.lock").exists()


def test_eval_failure_aborts(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def crashing_eval(**kwargs):
        raise RuntimeError("eval blew up")

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=crashing_eval,
        subprocess_run=fake_run,
    )
    assert decision["status"] == "aborted"
    assert decision["reason"] == "eval_failed"
    assert decision["pre_head"] == "abc"


def test_primary_metric_name_mismatch_propagates_as_value_error(tmp_path, monkeypatch):
    """If the post-edit eval uses a different primary metric than the prior,
    ``compare_eval_results`` raises ValueError. Apply must not silently produce a
    keep/revert classification — that would be apples-to-oranges."""
    monkeypatch.setenv(ENV_IMPROVEMENT_HALO_APPLY, "1")
    monkeypatch.setattr(improvement_halo_apply.shutil, "which", lambda _b: "/usr/bin/claude")
    report = _make_report(tmp_path)
    _plant_prior_eval(tmp_path, mean=0.5, stdev=0.1)

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="abc\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def quality_metric_runner(*, label):
        return summarize_eval(
            label=label,
            timestamp="2026-05-04T00:00:00+00:00",
            suites=[SuiteSummary(0.5, 0.5, 10, 5)],
            primary_metric_name="quality_score_p50",
        )

    decision = improvement_halo_apply.apply_halo_report(
        report,
        tmp_path,
        eval_runner=quality_metric_runner,
        subprocess_run=fake_run,
    )
    assert decision["status"] == "aborted"
    assert decision["reason"] == "prior_compare_failed"


def test_claude_timeout_overridable_via_env(monkeypatch):
    from autoresearch_constants import ENV_CLAUDE_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_CLAUDE_TIMEOUT_SECONDS, "99")
    import importlib

    import improvement_halo_apply as m

    importlib.reload(m)
    assert m.CLAUDE_TIMEOUT_SECONDS == 99


def test_claude_timeout_invalid_env_falls_back_to_default(monkeypatch):
    from autoresearch_constants import ENV_CLAUDE_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_CLAUDE_TIMEOUT_SECONDS, "not-a-number")
    import importlib

    import improvement_halo_apply as m

    importlib.reload(m)
    assert m.CLAUDE_TIMEOUT_SECONDS == 1800


def test_claude_timeout_non_positive_env_falls_back_to_default(monkeypatch):
    from autoresearch_constants import ENV_CLAUDE_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_CLAUDE_TIMEOUT_SECONDS, "-1")
    import importlib

    import improvement_halo_apply as m

    importlib.reload(m)
    assert m.CLAUDE_TIMEOUT_SECONDS == 1800


def test_release_lock_does_not_remove_lock_recreated_by_another_owner(tmp_path):
    lock_path = tmp_path / ".apply.lock"

    assert improvement_halo_apply._acquire_lock(lock_path) is True
    original = lock_path.read_text(encoding="utf-8")
    lock_path.unlink()
    lock_path.write_text("other-owner", encoding="utf-8")

    improvement_halo_apply._release_lock(lock_path, original)

    assert lock_path.read_text(encoding="utf-8") == "other-owner"
