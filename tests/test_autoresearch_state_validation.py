"""Tests for the JSONL boundary validation + quarantine path.

Project rule 5: validate at every layer boundary, source-specific model.
Project rule I: malformed rows go to a quarantine file plus a log line;
the read continues so one bad row does not kill the loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoresearch_state import (
    JsonlConfigEntry,
    JsonlExperimentEntry,
    JsonlResearchRoundEntry,
    read_entries,
    write_entries,
)

# ── round-trip: valid rows still pass through cleanly ───────────


def test_valid_config_row_passes_validation_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = [{"type": "config", "metricName": "median_expectancy", "bestDirection": "higher"}]
    write_entries(path, rows)
    assert read_entries(path) == rows


def test_valid_research_round_row_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = [
        {
            "type": "research_round",
            "outcome": "compiled",
            "round": 3,
            "thesis_id": "trailing_stop",
            "config_changes": {"trailing_stop": 0.5},
        }
    ]
    write_entries(path, rows)
    assert read_entries(path) == rows


def test_valid_experiment_row_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = [
        {
            "run": 1,
            "metric": 1.42,
            "status": "keep",
            "asi": {"config": "configs/ema_base.yaml"},
        }
    ]
    write_entries(path, rows)
    assert read_entries(path) == rows


def test_no_default_field_injection_on_read(tmp_path: Path) -> None:
    """Reading a config row without bestDirection must NOT inject the
    default value — validation pure, not mutating."""
    path = tmp_path / "log.jsonl"
    rows = [{"type": "config", "metricName": "median_expectancy"}]
    write_entries(path, rows)
    out = read_entries(path)
    assert out == rows
    assert "bestDirection" not in out[0]


# ── quarantine path ─────────────────────────────────────────────


def test_malformed_json_row_is_quarantined_and_skipped(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"run": 1, "metric": 1.0, "status": "keep"}\n'
        "{this is not valid json\n"
        '{"run": 2, "metric": 2.0, "status": "discard"}\n'
    )
    rows = read_entries(path)
    # The middle row should have been dropped.
    assert len(rows) == 2
    assert rows[0]["run"] == 1
    assert rows[1]["run"] == 2
    # And the malformed line should have been quarantined.
    quarantine = path.with_suffix(path.suffix + ".quarantine.jsonl")
    assert quarantine.exists()
    captured = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
    assert len(captured) == 1
    assert captured[0]["reason"].startswith("json_decode_error:")
    assert "this is not valid json" in captured[0]["raw"]


def test_non_object_row_is_quarantined(tmp_path: Path) -> None:
    """A JSON value that isn't an object (a list, a number, a string)
    should fail validation and quarantine."""
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"run": 1, "metric": 1.0, "status": "keep"}\n' "[1, 2, 3]\n" '"just a string"\n' "42\n"
    )
    rows = read_entries(path)
    assert len(rows) == 1
    assert rows[0]["run"] == 1
    quarantine = path.with_suffix(path.suffix + ".quarantine.jsonl")
    captured = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
    reasons = [c["reason"] for c in captured]
    assert all(r.startswith("not_a_json_object:") for r in reasons)
    assert len(reasons) == 3  # list, string, int


def test_schema_violation_quarantines_and_continues(tmp_path: Path) -> None:
    """An object that doesn't match its source-specific schema (e.g. an
    experiment row with `status` that isn't 'keep' or 'discard') must
    quarantine, not pass through, not kill the loop."""
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"run": 1, "metric": 1.0, "status": "keep"}\n'
        # metric must be a float; "abc" will fail Pydantic coercion.
        '{"run": 2, "metric": "abc", "status": "keep"}\n'
        # status must be Literal["keep", "discard"]; "maybe" is invalid.
        '{"run": 3, "metric": 1.0, "status": "maybe"}\n'
        '{"run": 4, "metric": 4.0, "status": "discard"}\n'
    )
    rows = read_entries(path)
    runs = [r["run"] for r in rows]
    assert runs == [1, 4]
    quarantine = path.with_suffix(path.suffix + ".quarantine.jsonl")
    captured = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
    assert len(captured) == 2
    assert all(c["reason"].startswith("schema_validation_failed:") for c in captured)


def test_blank_lines_are_neither_quarantined_nor_returned(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"run": 1, "metric": 1.0, "status": "keep"}\n'
        "\n"
        "   \n"
        '{"run": 2, "metric": 2.0, "status": "discard"}\n'
    )
    rows = read_entries(path)
    assert len(rows) == 2
    quarantine = path.with_suffix(path.suffix + ".quarantine.jsonl")
    assert not quarantine.exists()


def test_quarantine_file_is_appended_across_reads(tmp_path: Path) -> None:
    """Two read passes on a file with a malformed row must both quarantine
    that row (the live loop reads many times per run)."""
    path = tmp_path / "log.jsonl"
    path.write_text("{still bad json\n")
    read_entries(path)
    read_entries(path)
    quarantine = path.with_suffix(path.suffix + ".quarantine.jsonl")
    captured = [json.loads(line) for line in quarantine.read_text().splitlines() if line]
    assert len(captured) == 2


# ── direct schema tests (exercise the pydantic models) ──────────


def test_jsonl_config_entry_accepts_required_minimum() -> None:
    JsonlConfigEntry.model_validate({"type": "config"})


def test_jsonl_research_round_requires_outcome() -> None:
    import pytest

    with pytest.raises(Exception):
        JsonlResearchRoundEntry.model_validate({"type": "research_round"})


def test_jsonl_experiment_rejects_unknown_status() -> None:
    import pytest

    with pytest.raises(Exception):
        JsonlExperimentEntry.model_validate({"metric": 1.0, "status": "weird"})


def test_jsonl_experiment_allows_extra_fields() -> None:
    """ConfigDict(extra='allow') means asi/run/timestamp etc. don't have
    to be declared on the model — the row is validated for required keys
    only."""
    JsonlExperimentEntry.model_validate(
        {
            "metric": 1.0,
            "status": "keep",
            "run": 17,
            "asi": {"config": "x.yaml"},
            "timestamp": 1700000000,
            "novel_future_field": "ok",
        }
    )
