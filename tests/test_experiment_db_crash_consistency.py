"""Regression test: ExperimentDB is synced from JSONL on controller init
after a crash between write_entries and experiment_db.add."""

import json
from pathlib import Path

from autoresearch_loop import AutoresearchController
from strategy_family import load_family

REPO_ROOT = Path(__file__).parent.parent


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_controller_syncs_db_when_jsonl_ahead(tmp_path, monkeypatch):
    """Simulate crash: JSONL has an entry that ExperimentDB is missing."""
    family = load_family("ema")
    state_path = tmp_path / "ema_autoresearch.next.json"
    jsonl_path = tmp_path / "ema_autoresearch.jsonl"

    # Write JSONL with config header + one experiment entry (simulates post-crash state)
    _write_jsonl(
        jsonl_path,
        [
            {"type": "config", "bestDirection": "higher", "primaryMetric": "median_expectancy"},
            {
                "run": 1,
                "run_id": "test-run-id-001",
                "commit": "abc123",
                "metric": 0.05,
                "status": "keep",
                "description": "strict-native loop: ema_base",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "segment": 0,
                "confidence": None,
                "asi": {
                    "config": "configs/ema_base.yaml",
                    "thesis_id": "ema_base",
                    "trade_analysis": {},
                    "hypothesis": "ema crossover",
                },
            },
        ],
    )

    # DB file does NOT exist (or is empty) — simulates crash before experiment_db.add
    # (do not create it)

    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *a, **k: None)

    controller = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        ideas_md_path=tmp_path / "ema_autoresearch.ideas.md",
        family=family,
    )

    # After init, ExperimentDB must have the entry from JSONL
    all_records = controller.experiment_db.all()
    assert len(all_records) == 1, f"expected 1 record, got {len(all_records)}"
    assert all_records[0].experiment_id == "test-run-id-001"


def test_controller_no_duplicates_when_db_consistent(tmp_path, monkeypatch):
    """No duplicate entries when DB is already in sync with JSONL."""
    family = load_family("ema")
    state_path = tmp_path / "ema_autoresearch.next.json"
    jsonl_path = tmp_path / "ema_autoresearch.jsonl"

    _write_jsonl(
        jsonl_path,
        [
            {"type": "config", "bestDirection": "higher", "primaryMetric": "median_expectancy"},
            {
                "run": 1,
                "run_id": "test-run-id-002",
                "commit": "abc123",
                "metric": 0.05,
                "status": "keep",
                "description": "strict-native loop: ema_base",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "segment": 0,
                "confidence": None,
                "asi": {
                    "config": "configs/ema_base.yaml",
                    "thesis_id": "ema_base",
                    "trade_analysis": {},
                    "hypothesis": "",
                },
            },
        ],
    )

    monkeypatch.setattr("autoresearch_research.notify_discord", lambda *a, **k: None)

    # First init: syncs DB (1 entry)
    c1 = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        ideas_md_path=tmp_path / "ema_autoresearch.ideas.md",
        family=family,
    )
    assert len(c1.experiment_db.all()) == 1

    # Second init: must not duplicate
    c2 = AutoresearchController(
        root=tmp_path,
        state_path=state_path,
        jsonl_path=jsonl_path,
        current_md_path=tmp_path / "ema_autoresearch.current.md",
        ideas_md_path=tmp_path / "ema_autoresearch.ideas.md",
        family=family,
    )
    assert len(c2.experiment_db.all()) == 1, "second init must not create duplicate"
