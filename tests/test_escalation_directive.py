"""Tests for escalation directive: hard prompt instructions when patterns recur."""

from __future__ import annotations

from pathlib import Path

from rejection_artifact import write_rejection
from research_types import StructuredRejection


def _seed(root: Path, *, job: int, entries: list[dict]) -> None:
    for entry in entries:
        rej = StructuredRejection(
            rejected_at="2026-05-09T13:45:22Z",
            round=entry["round"],
            thesis_id=entry["thesis_id"],
            stage=entry.get("stage", "stage_1"),
            rejection_code=entry["code"],
            rule_violated=entry.get("rule", ""),
            evidence=entry.get("evidence", {}),
            remediation_hint=entry.get("hint", ""),
        )
        write_rejection(root, job=job, rejection=rej)


def test_no_escalation_when_no_pattern(tmp_path: Path) -> None:
    from rejection_artifact import compute_escalation_directive

    out = compute_escalation_directive(tmp_path, job=7)
    assert out == ""


def test_no_escalation_below_threshold(tmp_path: Path) -> None:
    from rejection_artifact import compute_escalation_directive

    _seed(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": f"t{i}", "code": "thesis_quality_theme_cluster_fixation"}
            for i in range(3)  # only 3
        ],
    )

    out = compute_escalation_directive(tmp_path, job=7)
    assert out == ""


def test_warn_escalation_when_4_or_more_cluster_fixations_in_last_7_rounds(tmp_path: Path) -> None:
    from rejection_artifact import compute_escalation_directive

    _seed(
        tmp_path,
        job=7,
        entries=[
            {"round": r, "thesis_id": f"t{r}", "code": "thesis_quality_theme_cluster_fixation"}
            for r in range(1, 5)  # rounds 1..4
        ],
    )

    out = compute_escalation_directive(tmp_path, job=7)
    assert "ESCALATION" in out
    assert "thesis_quality_theme_cluster_fixation" in out
    assert "different mechanism dimension" in out.lower()


def test_halt_escalation_when_6_or_more_cluster_fixations_in_last_10(tmp_path: Path) -> None:
    from rejection_artifact import compute_escalation_directive

    _seed(
        tmp_path,
        job=7,
        entries=[
            {"round": r, "thesis_id": f"t{r}", "code": "thesis_quality_theme_cluster_fixation"}
            for r in range(1, 7)  # rounds 1..6
        ],
    )

    out = compute_escalation_directive(tmp_path, job=7)
    assert "HALT" in out or "halt" in out.lower()
