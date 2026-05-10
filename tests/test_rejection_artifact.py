"""Tests for rejection.json on-disk artifact + writer helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_types import StructuredRejection


def _sample_rejection(
    round_no: int = 3, thesis_id: str = "ema-rsi-filter-v1"
) -> StructuredRejection:
    return StructuredRejection(
        rejected_at="2026-05-09T13:45:22Z",
        round=round_no,
        thesis_id=thesis_id,
        stage="stage_1",
        rejection_code="thesis_quality_theme_cluster_fixation",
        rule_violated="4 of last 7 theses share keywords",
        evidence={"shared_keywords": ["stop_distance", "opening"]},
        remediation_hint="propose from a different mechanism dimension",
        validator_version="2.3.0",
    )


def test_rejection_artifact_path_is_under_research_round_theses(tmp_path: Path) -> None:
    from rejection_artifact import rejection_artifact_path

    p = rejection_artifact_path(tmp_path, job=7, round_number=3, thesis_id="ema-rsi-filter-v1")

    expected = (
        tmp_path
        / "runtime"
        / "jobs"
        / "job-7"
        / "research"
        / "round-3"
        / "theses"
        / "ema-rsi-filter-v1"
        / "rejection.json"
    )
    assert p == expected


def test_write_rejection_creates_parent_dirs_and_writes_valid_json(tmp_path: Path) -> None:
    from rejection_artifact import write_rejection

    rejection = _sample_rejection()
    path = write_rejection(tmp_path, job=7, rejection=rejection)

    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["thesis_id"] == "ema-rsi-filter-v1"
    assert payload["rejection_code"] == "thesis_quality_theme_cluster_fixation"
    assert payload["evidence"]["shared_keywords"] == ["stop_distance", "opening"]


def test_write_rejection_is_atomic(tmp_path: Path) -> None:
    """Writer must not leave a partial file on disk if interrupted.

    We can't easily simulate interruption, but we can verify the writer uses
    a temp-file-then-rename pattern by checking no `*.tmp` file lingers after
    a successful write.
    """
    from rejection_artifact import write_rejection

    write_rejection(tmp_path, job=7, rejection=_sample_rejection())

    parent = (
        tmp_path
        / "runtime"
        / "jobs"
        / "job-7"
        / "research"
        / "round-3"
        / "theses"
        / "ema-rsi-filter-v1"
    )
    leftovers = list(parent.glob("*.tmp"))
    assert leftovers == [], f"writer left temp files: {leftovers}"


def test_write_rejection_idempotent_on_retry(tmp_path: Path) -> None:
    """Calling write_rejection twice with the same key overwrites cleanly."""
    from rejection_artifact import write_rejection

    rejection_a = _sample_rejection()
    write_rejection(tmp_path, job=7, rejection=rejection_a)

    rejection_b = _sample_rejection()
    rejection_b = rejection_b.model_copy(update={"rule_violated": "updated reason"})
    path = write_rejection(tmp_path, job=7, rejection=rejection_b)

    payload = json.loads(path.read_text())
    assert payload["rule_violated"] == "updated reason"


def test_write_rejection_round_must_be_non_negative(tmp_path: Path) -> None:
    from rejection_artifact import write_rejection

    bad = _sample_rejection().model_copy(update={"round": -1})
    with pytest.raises(ValueError):
        write_rejection(tmp_path, job=7, rejection=bad)


def test_build_rejection_from_validation_error_uses_explicit_code(tmp_path: Path) -> None:
    """When ThesisValidationError is raised with an explicit rejection_code,
    that code is used verbatim."""
    from rejection_artifact import build_rejection_from_validation_error
    from thesis_validator import ThesisValidationError

    exc = ThesisValidationError(
        "config-key Jaccard 100% with 'X'",
        rejection_code="config_validity_config_key_overlap_real",
        evidence={"shared_keys": ["foo"]},
        remediation_hint="propose different keys",
    )

    rejection = build_rejection_from_validation_error(
        exc, round_number=3, thesis_id="ema-rsi-v1", stage="stage_1"
    )

    assert rejection.rejection_code == "config_validity_config_key_overlap_real"
    assert rejection.evidence == {"shared_keys": ["foo"]}
    assert rejection.remediation_hint == "propose different keys"
    assert rejection.thesis_id == "ema-rsi-v1"
    assert rejection.round == 3
    assert rejection.stage == "stage_1"
    assert rejection.rule_violated  # rule_violated populated from message


def test_build_rejection_from_validation_error_falls_back_to_inferred_code() -> None:
    """Legacy raises without explicit code use infer_rejection_code on the message."""
    from rejection_artifact import build_rejection_from_validation_error
    from thesis_validator import ThesisValidationError

    exc = ThesisValidationError(
        "Config-key overlap 100% with prior thesis 'X' (shared keys: [...]).",
    )

    rejection = build_rejection_from_validation_error(
        exc, round_number=2, thesis_id="ema-foo", stage="stage_1"
    )

    assert rejection.rejection_code == "config_validity_config_key_overlap_real"


def _seed_rejections(root: Path, *, job: int, entries: list[dict]) -> None:
    """Helper: create rejection.json files for a list of {round, thesis_id, code} entries."""
    from rejection_artifact import write_rejection

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


def test_list_rejections_returns_all_rejections_in_job(tmp_path: Path) -> None:
    from rejection_artifact import list_rejections

    _seed_rejections(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "ema-a", "code": "thesis_quality_theme_cluster_fixation"},
            {"round": 2, "thesis_id": "ema-b", "code": "config_validity_config_key_overlap_real"},
            {"round": 2, "thesis_id": "ema-c", "code": "thesis_quality_theme_cluster_fixation"},
        ],
    )

    items = list_rejections(tmp_path, job=7)

    assert len(items) == 3
    codes = {it.rejection_code for it in items}
    assert codes == {
        "thesis_quality_theme_cluster_fixation",
        "config_validity_config_key_overlap_real",
    }


def test_list_rejections_filters_by_round(tmp_path: Path) -> None:
    from rejection_artifact import list_rejections

    _seed_rejections(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "ema-a", "code": "config_validity_config_key_overlap_real"},
            {"round": 2, "thesis_id": "ema-b", "code": "thesis_quality_theme_cluster_fixation"},
        ],
    )

    items = list_rejections(tmp_path, job=7, round_number=2)
    assert len(items) == 1
    assert items[0].thesis_id == "ema-b"


def test_list_rejections_filters_by_code(tmp_path: Path) -> None:
    from rejection_artifact import list_rejections

    _seed_rejections(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "ema-a", "code": "config_validity_config_key_overlap_real"},
            {"round": 2, "thesis_id": "ema-b", "code": "thesis_quality_theme_cluster_fixation"},
            {"round": 3, "thesis_id": "ema-c", "code": "thesis_quality_theme_cluster_fixation"},
        ],
    )

    items = list_rejections(tmp_path, job=7, rejection_code="thesis_quality_theme_cluster_fixation")
    assert len(items) == 2
    assert all(it.rejection_code == "thesis_quality_theme_cluster_fixation" for it in items)


def test_list_rejections_returns_empty_when_no_rejections(tmp_path: Path) -> None:
    from rejection_artifact import list_rejections

    items = list_rejections(tmp_path, job=7)
    assert items == []


def test_get_rejection_returns_specific_thesis_rejection(tmp_path: Path) -> None:
    from rejection_artifact import get_rejection

    _seed_rejections(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "ema-foo", "code": "config_validity_config_key_overlap_real"},
        ],
    )

    obj = get_rejection(tmp_path, job=7, round_number=1, thesis_id="ema-foo")
    assert obj is not None
    assert obj.rejection_code == "config_validity_config_key_overlap_real"


def test_get_rejection_returns_none_when_missing(tmp_path: Path) -> None:
    from rejection_artifact import get_rejection

    obj = get_rejection(tmp_path, job=7, round_number=1, thesis_id="nonexistent")
    assert obj is None


def test_rejection_pattern_summary_groups_and_counts(tmp_path: Path) -> None:
    from rejection_artifact import rejection_pattern_summary

    _seed_rejections(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "a", "code": "thesis_quality_theme_cluster_fixation"},
            {"round": 2, "thesis_id": "b", "code": "thesis_quality_theme_cluster_fixation"},
            {"round": 2, "thesis_id": "c", "code": "config_validity_config_key_overlap_real"},
            {"round": 3, "thesis_id": "d", "code": "thesis_quality_theme_cluster_fixation"},
        ],
    )

    summary = rejection_pattern_summary(tmp_path, job=7, window_rounds=10)

    counts = {row["rejection_code"]: row["count"] for row in summary}
    assert counts["thesis_quality_theme_cluster_fixation"] == 3
    assert counts["config_validity_config_key_overlap_real"] == 1


def test_rejection_pattern_summary_respects_window(tmp_path: Path) -> None:
    """Only rejections in the last `window_rounds` rounds are counted."""
    from rejection_artifact import rejection_pattern_summary

    _seed_rejections(
        tmp_path,
        job=7,
        entries=[
            {"round": 1, "thesis_id": "old1", "code": "code_old"},
            {"round": 5, "thesis_id": "old2", "code": "code_old"},
            {"round": 10, "thesis_id": "recent1", "code": "code_recent"},
            {"round": 11, "thesis_id": "recent2", "code": "code_recent"},
        ],
    )

    summary = rejection_pattern_summary(tmp_path, job=7, window_rounds=3)

    codes = {row["rejection_code"] for row in summary}
    assert "code_recent" in codes
    assert "code_old" not in codes


def test_persist_rejection_writes_to_round_thesis_folder(tmp_path: Path) -> None:
    """End-to-end: ValidationError → StructuredRejection → rejection.json on disk."""
    from rejection_artifact import persist_rejection
    from thesis_validator import ThesisValidationError

    exc = ThesisValidationError(
        "Theme-cluster fixation: 4/7 share keywords",
        rejection_code="thesis_quality_theme_cluster_fixation",
        evidence={"shared_keywords": ["opening", "stop_distance"]},
    )

    path = persist_rejection(
        tmp_path,
        job=7,
        round_number=3,
        thesis_id="opening_x_stop_filter",
        stage="stage_1",
        exc=exc,
    )

    assert path.name == "rejection.json"
    payload = json.loads(path.read_text())
    assert payload["rejection_code"] == "thesis_quality_theme_cluster_fixation"
    assert payload["thesis_id"] == "opening_x_stop_filter"
    assert payload["round"] == 3
