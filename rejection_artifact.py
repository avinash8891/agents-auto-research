"""On-disk persistence of validator/compile rejections, scoped per round/thesis.

Path layout:
    runtime/jobs/job-N/research/round-M/theses/<thesis_id>/rejection.json

The conductor's per-round prompt and the rejection-pattern tools read these
artifacts on demand. Old rounds' rejections survive process restarts.
"""

from __future__ import annotations

import json
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


_CHALLENGE_REQUIRED_KEYS = (
    "challenged_round",
    "challenged_thesis_id",
    "challenged_rejection_code",
    "claim",
)


def challenge_artifact_path(root: Path, *, job: int, round_number: int, thesis_id: str) -> Path:
    """Path of challenge.json for one (job, round, thesis_id), next to rejection.json."""
    return (
        _research_round_thesis_root(root, job=job, round_number=round_number, thesis_id=thesis_id)
        / "challenge.json"
    )


def write_challenge(root: Path, *, job: int, payload: dict) -> Path:
    """Persist a conductor-authored validator_challenge payload.

    Doesn't change the validator's decision; logged for human review and for
    pattern analysis (e.g. "rule X has been challenged 12 times → revisit it").
    """
    missing = [k for k in _CHALLENGE_REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(f"validator_challenge payload missing required keys: {missing}")

    enriched = dict(payload)
    enriched.setdefault("created_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    target = challenge_artifact_path(
        root,
        job=job,
        round_number=int(payload["challenged_round"]),
        thesis_id=str(payload["challenged_thesis_id"]),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(enriched, indent=2))
    os.replace(tmp, target)
    return target


def compute_escalation_directive(root: Path, *, job: int) -> str:
    """Return a hard prompt directive when a rejection pattern recurs.

    Tiers:
    - 4+ theme_cluster_fixation in last 7 rounds → WARN: instruct agent to
      switch dimension; in-validator B1 already rejects, but the prompt-side
      directive surfaces the meta-pattern explicitly.
    - 6+ theme_cluster_fixation in last 10 rounds → HALT directive: surface
      to human review channel; the harness should stop the loop.
    """
    summary_7 = rejection_pattern_summary(root, job=job, window_rounds=7)
    summary_10 = rejection_pattern_summary(root, job=job, window_rounds=10)

    cluster_count_7 = next(
        (row["count"] for row in summary_7 if row["rejection_code"] == "thesis_quality_theme_cluster_fixation"),
        0,
    )
    cluster_count_10 = next(
        (row["count"] for row in summary_10 if row["rejection_code"] == "thesis_quality_theme_cluster_fixation"),
        0,
    )

    if cluster_count_10 >= 6:
        return (
            "HALT ESCALATION: thesis_quality_theme_cluster_fixation has fired"
            f"{cluster_count_10} times in the last 10 rounds. The agent is stuck "
            "in a single mechanism cluster. Loop should halt for human review."
        )
    if cluster_count_7 >= 4:
        return (
            "ESCALATION: thesis_quality_theme_cluster_fixation has fired"
            f"{cluster_count_7} times in the last 7 rounds. "
            "This round MUST propose from a different mechanism dimension; "
            "any thesis whose theme_keywords overlap the dominant cluster will "
            "be auto-rejected. Consult rejection_pattern_summary for details."
        )
    return ""


def render_rejection_block(root: Path, *, job: int, current_round: int) -> str:
    """Render the structured-rejection section of the per-round user prompt.

    Includes:
      - REJECTED THIS ROUND: one structured block per rejection in the current round.
      - RECENT VALIDATOR PATTERNS: grouped counts across the last 10 rounds with
        tool pointers for deeper inspection.

    Returns an empty string when there are no rejections anywhere for this job.
    """
    current_items = list_rejections(root, job=job, round_number=current_round)
    all_items = list_rejections(root, job=job)
    if not current_items and not all_items:
        return ""

    parts: list[str] = []

    if current_items:
        parts.append("REJECTED THIS ROUND")
        for item in current_items:
            evidence_str = (
                ", ".join(f"{k}={v}" for k, v in sorted(item.evidence.items()))
                if item.evidence
                else "(none)"
            )
            parts.append(f"  ─── {item.thesis_id} ───")
            parts.append(f"    stage: {item.stage}")
            parts.append(f"    rejection_code: {item.rejection_code}")
            if item.rule_violated:
                parts.append(f"    rule: {item.rule_violated}")
            parts.append(f"    evidence: {evidence_str}")
            if item.remediation_hint:
                parts.append(f"    hint: {item.remediation_hint}")

    summary = rejection_pattern_summary(root, job=job, window_rounds=10)
    if summary:
        if parts:
            parts.append("")
        parts.append("RECENT VALIDATOR PATTERNS (last 10 rounds)")
        for row in summary:
            parts.append(f"  {row['count']} × {row['rejection_code']}")
        parts.append("  Tools: list_rejections, get_rejection, rejection_pattern_summary")

    return "\n".join(parts)


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
