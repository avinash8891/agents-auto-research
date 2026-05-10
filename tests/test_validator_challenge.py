"""Tests for validator_challenge persistence (challenge.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_challenge_artifact_path_lives_next_to_rejection(tmp_path: Path) -> None:
    from rejection_artifact import challenge_artifact_path, rejection_artifact_path

    rj = rejection_artifact_path(tmp_path, job=7, round_number=3, thesis_id="ema-rsi-v1")
    ch = challenge_artifact_path(tmp_path, job=7, round_number=3, thesis_id="ema-rsi-v1")
    assert ch.parent == rj.parent
    assert ch.name == "challenge.json"


def test_write_challenge_persists_payload(tmp_path: Path) -> None:
    from rejection_artifact import write_challenge

    payload = {
        "challenged_round": 3,
        "challenged_thesis_id": "ema-rsi-v1",
        "challenged_rejection_code": "config_key_overlap_real",
        "claim": "the shared key is a sentinel, not a real config key",
        "evidence": {"shared_keys": ["requires_engine_change"]},
    }
    path = write_challenge(tmp_path, job=7, payload=payload)

    assert path.name == "challenge.json"
    on_disk = json.loads(path.read_text())
    assert on_disk["challenged_thesis_id"] == "ema-rsi-v1"
    assert on_disk["challenged_rejection_code"] == "config_key_overlap_real"
    assert "created_at" in on_disk  # writer adds timestamp


def test_write_challenge_atomic(tmp_path: Path) -> None:
    """Writer uses temp-file-then-rename; no .tmp file leftovers."""
    from rejection_artifact import write_challenge

    write_challenge(
        tmp_path,
        job=7,
        payload={
            "challenged_round": 1,
            "challenged_thesis_id": "ema-foo",
            "challenged_rejection_code": "x",
            "claim": "y",
            "evidence": {},
        },
    )

    parent = tmp_path / "runtime" / "jobs" / "job-7" / "research" / "round-1" / "theses" / "ema-foo"
    leftovers = list(parent.glob("*.tmp"))
    assert leftovers == []


def test_write_challenge_requires_core_fields(tmp_path: Path) -> None:
    from rejection_artifact import write_challenge

    with pytest.raises(ValueError):
        write_challenge(
            tmp_path,
            job=7,
            payload={"claim": "missing required keys"},
        )
