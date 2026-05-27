"""Tests for rendering structured rejections into the per-round user prompt."""

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


def test_render_rejection_block_returns_empty_string_when_no_rejections(tmp_path: Path) -> None:
    from rejection_artifact import render_rejection_block

    out = render_rejection_block(tmp_path, job=7, current_round=2)
    assert out == ""


def test_render_rejection_block_includes_current_round_rejections(tmp_path: Path) -> None:
    from rejection_artifact import render_rejection_block

    _seed(
        tmp_path,
        job=7,
        entries=[
            {
                "round": 2,
                "thesis_id": "ema-rsi-v1",
                "code": "thesis_quality_theme_cluster_fixation",
                "rule": "4 of last 7 theses share keywords",
                "hint": "propose from a different mechanism dimension",
                "evidence": {"shared_keywords": ["opening", "stop_distance"]},
            },
            {
                "round": 2,
                "thesis_id": "ema-rsi-v2",
                "code": "config_validity_config_key_overlap_real",
                "rule": "Jaccard 100%",
                "hint": "this lever was tested in round 1",
            },
        ],
    )

    out = render_rejection_block(tmp_path, job=7, current_round=2)

    assert "REJECTED THIS ROUND" in out
    assert "ema-rsi-v1" in out
    assert "ema-rsi-v2" in out
    assert "thesis_quality_theme_cluster_fixation" in out
    assert "config_validity_config_key_overlap_real" in out


def test_render_rejection_block_expands_mechanical_batch_failures(tmp_path: Path) -> None:
    from rejection_artifact import render_rejection_block

    _seed(
        tmp_path,
        job=7,
        entries=[
            {
                "round": 2,
                "thesis_id": "ema-batched-v1",
                "code": "structural_mechanical_batch_failures",
                "rule": "Multiple mechanical issues: ['structural_missing_thesis_id']",
                "evidence": {
                    "count": 2,
                    "failures": [
                        {
                            "code": "structural_missing_thesis_id",
                            "summary": "Missing thesis_id",
                            "evidence": {},
                        },
                        {
                            "code": "structural_missing_hypothesis",
                            "summary": "Missing hypothesis",
                            "evidence": {},
                        },
                    ],
                },
            }
        ],
    )

    out = render_rejection_block(tmp_path, job=7, current_round=2)

    assert "rejection_code: structural_mechanical_batch_failures (2 failures)" in out
    assert "Fix all in one retry:" in out
    assert "[1] structural_missing_thesis_id: Missing thesis_id" in out
    assert "[2] structural_missing_hypothesis: Missing hypothesis" in out


def test_render_rejection_block_includes_pattern_summary_when_history_exists(
    tmp_path: Path,
) -> None:
    from rejection_artifact import render_rejection_block

    _seed(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "a", "code": "thesis_quality_theme_cluster_fixation"},
            {"round": 2, "thesis_id": "b", "code": "thesis_quality_theme_cluster_fixation"},
            {"round": 3, "thesis_id": "c", "code": "config_validity_config_key_overlap_real"},
        ],
    )

    out = render_rejection_block(tmp_path, job=7, current_round=3)

    assert "RECENT VALIDATOR PATTERNS" in out
    assert "thesis_quality_theme_cluster_fixation" in out
    assert "2" in out  # count of theme_cluster_fixation
    # Tool pointers help the agent dig deeper:
    assert "list_rejections" in out
    assert "rejection_pattern_summary" in out


def test_render_rejection_block_only_lists_current_round_in_rejected_section(
    tmp_path: Path,
) -> None:
    from rejection_artifact import render_rejection_block

    _seed(
        tmp_path,
        job=7,
        entries=[
            {
                "round": 1,
                "thesis_id": "old-thesis",
                "code": "config_validity_config_key_overlap_real",
            },
            {
                "round": 3,
                "thesis_id": "current-thesis",
                "code": "thesis_quality_theme_cluster_fixation",
            },
        ],
    )

    out = render_rejection_block(tmp_path, job=7, current_round=3)

    # Current round detail block has only round-3 thesis
    rejected_section = out.split("RECENT VALIDATOR PATTERNS")[0]
    assert "current-thesis" in rejected_section
    assert "old-thesis" not in rejected_section
