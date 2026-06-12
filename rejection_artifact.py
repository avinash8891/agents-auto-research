"""On-disk persistence of validator/compile rejections, scoped per round/thesis.

Path layout:
    runtime/jobs/job-N/research/round-M/theses/<thesis_id>/rejection.json

The conductor's per-round prompt and the rejection-pattern tools read these
artifacts on demand. Old rounds' rejections survive process restarts.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from autoresearch_runtime_paths import research_round_root
from research_types import StructuredRejection

# Avoid a hard import cycle (thesis_validator imports research_types). The
# validator's exception class is referenced via TYPE_CHECKING; runtime guards
# fall back to duck-typing on the message attribute.
RejectionStage = Literal["stage_1", "stage_2", "compile"]


def _research_round_thesis_root(root: Path, *, job: int, round_number: int, thesis_id: str) -> Path:
    """Return `runtime/jobs/job-N/research/round-M/theses/<thesis_id>/`."""
    if not thesis_id:
        raise ValueError("thesis_id must be non-empty")
    return research_round_root(root, job, round_number) / "theses" / thesis_id


def rejection_artifact_path(root: Path, *, job: int, round_number: int, thesis_id: str) -> Path:
    """Return the rejection.json path for the given (job, round, thesis_id)."""
    return (
        _research_round_thesis_root(root, job=job, round_number=round_number, thesis_id=thesis_id)
        / "rejection.json"
    )


def write_rejection(root: Path, *, job: int, rejection: StructuredRejection) -> Path:
    """Persist a StructuredRejection atomically; return the final file path.

    Uses temp-file-then-rename for atomicity. Idempotent: calling twice with
    the same (round, thesis_id) overwrites in place.
    """
    target = rejection_artifact_path(
        root,
        job=job,
        round_number=rejection.round,
        thesis_id=rejection.thesis_id,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(rejection.model_dump_json(indent=2))
    os.replace(tmp, target)
    return target


def build_rejection_from_validation_error(
    exc: Exception,
    *,
    round_number: int,
    thesis_id: str,
    stage: RejectionStage,
    validator_version: str = "",
) -> StructuredRejection:
    """Construct a StructuredRejection from a ThesisValidationError.

    Uses explicit `rejection_code` / `evidence` / `remediation_hint` attributes
    when present on the exception; falls back to `infer_rejection_code` for
    legacy raises that only carry a message.
    """
    from thesis_validator import infer_rejection_code  # local import: avoid cycle

    message = str(exc)
    code = getattr(exc, "rejection_code", "") or infer_rejection_code(message)
    evidence = dict(getattr(exc, "evidence", {}) or {})
    remediation_hint = getattr(exc, "remediation_hint", "") or ""

    return StructuredRejection(
        rejected_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        round=round_number,
        thesis_id=thesis_id,
        stage=stage,
        rejection_code=code,
        rule_violated=message,
        evidence=evidence,
        remediation_hint=remediation_hint,
        validator_version=validator_version,
    )


def list_rejections(
    root: Path,
    *,
    job: int,
    round_number: int | None = None,
    rejection_code: str | None = None,
    limit: int | None = None,
) -> list[StructuredRejection]:
    """Scan all rejection.json artifacts under a job and return them as objects.

    Filters: optional round_number (single round), optional rejection_code.
    Optional limit caps the result count (most-recent-round-first ordering).
    """
    from autoresearch_runtime_paths import job_research_root

    research_root = job_research_root(root, job)
    if not research_root.exists():
        return []

    out: list[StructuredRejection] = []
    for round_dir in sorted(research_root.iterdir(), key=_round_sort_key, reverse=True):
        if not round_dir.is_dir():
            continue
        round_no = _round_number_from_dir_name(round_dir.name)
        if round_no is None:
            continue
        if round_number is not None and round_no != round_number:
            continue
        theses_dir = round_dir / "theses"
        if not theses_dir.exists():
            continue
        for thesis_dir in sorted(theses_dir.iterdir()):
            rejection_file = thesis_dir / "rejection.json"
            if not rejection_file.exists():
                continue
            try:
                obj = StructuredRejection.model_validate_json(rejection_file.read_text())
            except Exception:  # noqa: BLE001
                continue
            if rejection_code is not None and obj.rejection_code != rejection_code:
                continue
            out.append(obj)
            if limit is not None and len(out) >= limit:
                return out
    return out


def get_rejection(
    root: Path, *, job: int, round_number: int, thesis_id: str
) -> StructuredRejection | None:
    """Return one rejection record, or None if not found."""
    path = rejection_artifact_path(root, job=job, round_number=round_number, thesis_id=thesis_id)
    if not path.exists():
        return None
    try:
        return StructuredRejection.model_validate_json(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def rejection_pattern_summary(root: Path, *, job: int, window_rounds: int = 10) -> list[dict]:
    """Group recent rejections by rejection_code and return counts.

    `window_rounds` defines how many of the most-recent round numbers are
    included. Each result entry is `{rejection_code, count, example_thesis_ids}`,
    sorted by count descending.
    """
    items = list_rejections(root, job=job)
    if not items:
        return []

    # Window is a sliding range: include rounds in [latest - window_rounds + 1, latest].
    latest_round = max(it.round for it in items)
    cutoff_min = latest_round - max(window_rounds, 1) + 1

    grouped: dict[str, list[StructuredRejection]] = {}
    for it in items:
        if it.round >= cutoff_min:
            grouped.setdefault(it.rejection_code, []).append(it)

    summary = [
        {
            "rejection_code": code,
            "count": len(group),
            "example_thesis_ids": [r.thesis_id for r in group[:3]],
        }
        for code, group in grouped.items()
    ]
    summary.sort(key=lambda row: row["count"], reverse=True)
    return summary


def _round_number_from_dir_name(name: str) -> int | None:
    """Parse `round-0-baseline` and `round-N` directory names."""
    if name == "round-0-baseline":
        return 0
    if name.startswith("round-"):
        suffix = name.removeprefix("round-")
        try:
            return int(suffix)
        except ValueError:
            return None
    return None


def _round_sort_key(path: Path) -> int:
    n = _round_number_from_dir_name(path.name)
    return n if n is not None else -1


def persist_rejection(
    root: Path,
    *,
    job: int,
    round_number: int,
    thesis_id: str,
    stage: RejectionStage,
    exc: Exception,
    validator_version: str = "",
) -> Path:
    """Build a StructuredRejection from `exc` and write it to disk."""
    rejection = build_rejection_from_validation_error(
        exc,
        round_number=round_number,
        thesis_id=thesis_id,
        stage=stage,
        validator_version=validator_version,
    )
    return write_rejection(root, job=job, rejection=rejection)
