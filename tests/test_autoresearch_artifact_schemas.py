from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoresearch_planning import should_terminate
from strategy_family import load_family


def test_round_artifact_writer_shape_is_terminal_reader_shape(tmp_path: Path) -> None:
    from autoresearch_artifact_schemas import (
        RoundArtifact,
        read_round_artifact,
        write_round_artifact,
    )

    round_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-1"
    artifact = RoundArtifact(
        job_id=1,
        round_number=1,
        strategy_family="ema",
        status="completed",
        outcome="research_exhausted",
        selected_thesis_id="",
        generated_configs=[],
        generated_config_path="",
        new_theses_generated=0,
        suggested_theses=[],
    )

    write_round_artifact(round_dir / "round.json", artifact)

    assert read_round_artifact(round_dir / "round.json") == artifact
    assert (
        should_terminate(
            tmp_path,
            load_family("ema"),
            tmp_path / "queue",
            round_dir.parent,
            [],
            job=1,
        )
        is False
    )


def test_round_artifact_reader_rejects_legacy_alias_payload(tmp_path: Path) -> None:
    from autoresearch_artifact_schemas import read_round_artifact

    path = tmp_path / "round.json"
    path.write_text(
        json.dumps(
            {
                "job": 2,
                "round_number": 4,
                "outcome": "compiled",
                "selected_thesis_id": "ema-thesis",
                "generated_config_path": "runtime/jobs/job-2/research/round-4/selected_config.json",
            }
        )
        + "\n"
    )

    with pytest.raises(ValidationError, match="job_id"):
        read_round_artifact(path)
