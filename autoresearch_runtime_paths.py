from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Single definition lives in autoresearch_paths; re-exported here so existing
# `from autoresearch_runtime_paths import resolve_runtime_root` importers keep working.
from autoresearch_paths import resolve_runtime_root


@dataclass(frozen=True)
class AutoresearchRuntimeContext:
    """Canonical path authority for one autoresearch strategy-family run."""

    code_root: Path
    runtime_root: Path
    family_name: str
    state_path: Path
    current_md_path: Path
    jobs_root: Path
    backtest_db_path: Path
    baseline_checkpoints_path: Path

    @classmethod
    def for_family(
        cls,
        *,
        code_root: Path,
        runtime_root: Path,
        family_name: str,
        state_path: Path | None = None,
        current_md_path: Path | None = None,
        jobs_root: Path | None = None,
    ) -> "AutoresearchRuntimeContext":
        code_root = code_root.resolve()
        runtime_root = runtime_root.resolve()
        prefix = "" if family_name == "orb" else f"{family_name}_"
        resolved_state_path = cls._resolve_runtime_path(
            state_path or runtime_root / f"{prefix}autoresearch.next.json",
            runtime_root=runtime_root,
        )
        resolved_current_md_path = cls._resolve_runtime_path(
            current_md_path or runtime_root / f"{prefix}autoresearch.current.md",
            runtime_root=runtime_root,
        )
        resolved_jobs_root = cls._resolve_runtime_path(
            jobs_root or runtime_root / "runtime" / "jobs",
            runtime_root=runtime_root,
        )
        return cls(
            code_root=code_root,
            runtime_root=runtime_root,
            family_name=family_name,
            state_path=resolved_state_path,
            current_md_path=resolved_current_md_path,
            jobs_root=resolved_jobs_root,
            backtest_db_path=runtime_root / f"{family_name}_backtest_runs.db",
            baseline_checkpoints_path=runtime_root / f"{family_name}_baseline_checkpoints.json",
        )

    @staticmethod
    def _resolve_runtime_path(path: Path, *, runtime_root: Path) -> Path:
        return path.resolve() if path.is_absolute() else (runtime_root / path).resolve()

    def job_runtime_root(self, job: int) -> Path:
        if job < 1:
            raise ValueError(f"job id must be >= 1; got {job}")
        return self.jobs_root / f"job-{job}"

    def research_dir(self, job: int) -> Path:
        return self.job_runtime_root(job) / "research"

    def builder_requests_dir(self, job: int) -> Path:
        return self.job_runtime_root(job) / "builder-requests"


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


def iter_family_backtest_db_paths(root: Path, *, family: str | None = None) -> list[Path]:
    """Return backtest DB paths using the canonical runtime-root policy."""
    root = root.resolve()
    runtime_root = resolve_runtime_root(root)
    roots = [runtime_root]
    if root == runtime_root:
        roots.append(root)
    pattern = f"{family}_backtest_runs.db" if family else "*_backtest_runs.db"
    seen: set[Path] = set()
    db_paths: list[Path] = []
    for candidate_root in roots:
        for db_path in sorted(candidate_root.glob(pattern)):
            resolved = db_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            db_paths.append(db_path)
    return db_paths
