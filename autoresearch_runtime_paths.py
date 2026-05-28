from __future__ import annotations

from pathlib import Path
from typing import Any


def job_runtime_root(root: Path, job: int) -> Path:
    """Return the artifact root for one autoresearch job."""
    if job < 1:
        raise ValueError(f"job id must be >= 1; got {job}")
    return root / "runtime" / "jobs" / f"job-{job}"


def job_runtime_dir(root: Path, job: int, dirname: str) -> Path:
    return job_runtime_root(root, job) / dirname


def job_research_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "research"


def research_round_id(job: int, round_number: int) -> str:
    """Canonical id for a research round (= one backtest = one experiment).

    Format: "job-{job}-round-{round_number}". Use everywhere a round id
    is constructed — the literal string is duplicated across modules today.
    """
    if job < 1:
        raise ValueError(f"job id must be >= 1; got {job}")
    if round_number < 0:
        raise ValueError(f"round number must be >= 0; got {round_number}")
    return f"job-{job}-round-{round_number}"


def research_round_id_or_empty(job: int | Any, round_number: int | Any) -> str:
    """Best-effort round id; returns "" when inputs cannot produce a valid id.

    Use ONLY at boundaries where state may be partially populated
    (controller bootstrap, sqlite int-coercion failures). New code should
    prefer research_round_id(...) and surface bad inputs as ValueError.
    """
    try:
        j = int(job)
        r = int(round_number)
    except (TypeError, ValueError):
        return ""
    if j < 1 or r < 0:
        return ""
    return research_round_id(j, r)


def research_round_root(root: Path, job: int, round_number: int) -> Path:
    if round_number < 0:
        raise ValueError(f"research round number must be >= 0; got {round_number}")
    if round_number == 0:
        return job_research_root(root, job) / "round-0-baseline"
    return job_research_root(root, job) / f"round-{round_number}"


def research_round_backtest_root(root: Path, job: int, round_number: int) -> Path:
    return research_round_root(root, job, round_number) / "backtest"


def research_round_attempts_root(root: Path, job: int, round_number: int) -> Path:
    return research_round_root(root, job, round_number) / "attempts"


def research_round_attempt_root(
    root: Path, job: int, round_number: int, attempt_number: int
) -> Path:
    if attempt_number < 1:
        raise ValueError(f"attempt number must be >= 1; got {attempt_number}")
    return research_round_attempts_root(root, job, round_number) / f"attempt-{attempt_number}"


def research_round_trace_exports_root(root: Path, job: int, round_number: int) -> Path:
    return research_round_root(root, job, round_number) / "trace_exports"


def job_builder_requests_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "builder-requests"
