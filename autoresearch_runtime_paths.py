from __future__ import annotations

from pathlib import Path


def job_runtime_root(root: Path, job: int) -> Path:
    """Return the artifact root for one autoresearch job."""
    return root / "runtime" / "jobs" / f"job-{job}"


def job_runtime_dir(root: Path, job: int, dirname: str) -> Path:
    return job_runtime_root(root, job) / dirname


def job_trace_exports_root(root: Path, job: int) -> Path:
    return job_runtime_root(root, job) / "trace_exports"
