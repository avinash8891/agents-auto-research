from __future__ import annotations

from pathlib import Path


def job_runtime_root(root: Path, job: int) -> Path:
    """Return the artifact root for one autoresearch job."""
    if job < 1:
        raise ValueError(f"job id must be >= 1; got {job}")
    return root / "runtime" / "jobs" / f"job-{job}"


def job_runtime_dir(root: Path, job: int, dirname: str) -> Path:
    return job_runtime_root(root, job) / dirname


def job_runs_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "runs"


def job_run_artifact_dir(root: Path, job: int, git_commit: str, config_hash: str) -> Path:
    git_commit = str(git_commit or "").strip()
    config_hash = str(config_hash or "").strip()
    if not git_commit:
        raise ValueError("git commit is required for backtest run artifact path")
    if not config_hash:
        raise ValueError("config hash is required for backtest run artifact path")
    return job_runs_root(root, job) / git_commit / config_hash


def job_research_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "research"


def research_round_root(root: Path, job: int, round_number: int) -> Path:
    if round_number < 1:
        raise ValueError(f"research round number must be >= 1; got {round_number}")
    return job_research_root(root, job) / f"round-{round_number}"


def research_round_trace_exports_root(root: Path, job: int, round_number: int) -> Path:
    return research_round_root(root, job, round_number) / "trace_exports"


def job_builder_requests_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "builder-requests"


def job_run_queue_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "run-queue"
