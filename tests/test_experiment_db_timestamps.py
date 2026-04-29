"""Tests for the rule-J timestamp migration on ExperimentDB / BaselineCheckpoint.

Pre-this-PR: timestamps stored as int epoch milliseconds.
Post-this-PR: timestamps stored as ISO-8601 UTC strings, with backward
compatibility for legacy DB files that still contain int timestamps.
"""

from __future__ import annotations

import json
from pathlib import Path

from experiment_db import (
    BaselineCheckpoint,
    BaselineTracker,
    ExperimentDB,
    ExperimentResult,
)


def _make_record(experiment_id: str, *, timestamp: str = "") -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_id,
        thesis_id="t",
        config_path="configs/ema_base.yaml",
        runtime_config={},
        code_commit="abc1234",
        data_hash="d",
        train_metrics={},
        validation_metrics={},
        trade_count=0,
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=False,
        rejection_reason="",
        verdict_status="none",
        verdict_summary="",
        timestamp=timestamp,
    )


# ── Forward path: new DB writes ISO strings ─────────────────────


def test_new_record_round_trip_preserves_iso_timestamp(tmp_path: Path) -> None:
    db = ExperimentDB(tmp_path / "experiments_db.json")
    db.add(_make_record("exp1", timestamp="2026-04-29T12:00:00+00:00"))
    # Force re-load by clearing the in-memory cache.
    db._records = None
    out = db.all()
    assert len(out) == 1
    assert out[0].timestamp == "2026-04-29T12:00:00+00:00"


def test_disk_payload_is_iso_string(tmp_path: Path) -> None:
    """The on-disk JSON must contain a string, not an int, for new writes."""
    db_path = tmp_path / "experiments_db.json"
    db = ExperimentDB(db_path)
    db.add(_make_record("exp1", timestamp="2026-04-29T12:00:00+00:00"))
    raw = json.loads(db_path.read_text())
    assert isinstance(raw[0]["timestamp"], str)
    assert raw[0]["timestamp"].endswith("+00:00")


# ── Back-compat path: legacy int timestamps still load ──────────


def test_legacy_int_db_file_loads_and_coerces_to_iso(tmp_path: Path) -> None:
    """A pre-rule-J DB file with int epoch-ms timestamps must still load.
    The in-memory record has the timestamp coerced to ISO-8601 UTC."""
    legacy_db = tmp_path / "experiments_db.json"
    legacy_record = {
        "experiment_id": "legacy1",
        "thesis_id": "t",
        "config_path": "configs/ema_base.yaml",
        "runtime_config": {},
        "code_commit": "abc1234",
        "data_hash": "d",
        "train_metrics": {},
        "validation_metrics": {},
        "trade_count": 0,
        "trades_file": "",
        "strategy_events_file": "",
        "diagnostics_file": "",
        "strategy_diagnostics": {},
        "accepted": False,
        "rejection_reason": "",
        "verdict_status": "none",
        "verdict_summary": "",
        "timestamp": 1704067200000,  # 2024-01-01T00:00:00 UTC
        "family": "ema",
    }
    legacy_db.write_text(json.dumps([legacy_record]) + "\n")

    db = ExperimentDB(legacy_db)
    out = db.all()
    assert len(out) == 1
    # Coerced from int 1704067200000 → ISO string.
    assert out[0].timestamp == "2024-01-01T00:00:00+00:00"


def test_latest_orders_correctly_across_legacy_and_new(tmp_path: Path) -> None:
    """A DB containing one legacy int row and one new ISO row must order
    correctly when latest() is called."""
    db_path = tmp_path / "experiments_db.json"
    legacy = {
        "experiment_id": "legacy",
        "thesis_id": "t",
        "config_path": "x",
        "runtime_config": {},
        "code_commit": "",
        "data_hash": "",
        "train_metrics": {},
        "validation_metrics": {},
        "trade_count": 0,
        "trades_file": "",
        "strategy_events_file": "",
        "diagnostics_file": "",
        "strategy_diagnostics": {},
        "accepted": False,
        "rejection_reason": "",
        "verdict_status": "none",
        "verdict_summary": "",
        "timestamp": 1700000000000,  # Nov 14 2023
    }
    new = dict(legacy)
    new["experiment_id"] = "new"
    new["timestamp"] = "2024-01-01T00:00:00+00:00"  # 1704067200000 ms — later
    db_path.write_text(json.dumps([legacy, new]) + "\n")

    db = ExperimentDB(db_path)
    latest_records = db.latest(2)
    assert latest_records[0].experiment_id == "new"
    assert latest_records[1].experiment_id == "legacy"


# ── BaselineCheckpoint mirrors the same migration ───────────────


def test_baseline_checkpoint_new_record_round_trip(tmp_path: Path) -> None:
    tracker = BaselineTracker(tmp_path / "baseline.json")
    cp = BaselineCheckpoint(
        code_commit="abc1234",
        data_hash="d",
        config_hash="c",
        metrics={"profit_factor": 1.4},
        timestamp="2026-04-29T12:00:00+00:00",
    )
    tracker.record(cp)
    tracker._checkpoints = None  # force re-load
    out = tracker.latest()
    assert out is not None
    assert out.timestamp == "2026-04-29T12:00:00+00:00"


def test_baseline_checkpoint_legacy_int_loads(tmp_path: Path) -> None:
    legacy_path = tmp_path / "baseline.json"
    legacy = [
        {
            "code_commit": "abc1234",
            "data_hash": "d",
            "config_hash": "c",
            "metrics": {"profit_factor": 1.4},
            "timestamp": 1704067200000,
            "round_number": 0,
        }
    ]
    legacy_path.write_text(json.dumps(legacy) + "\n")
    tracker = BaselineTracker(legacy_path)
    out = tracker.latest()
    assert out is not None
    assert out.timestamp == "2024-01-01T00:00:00+00:00"


def test_baseline_checkpoint_disk_payload_is_iso_string(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    tracker = BaselineTracker(path)
    tracker.record(
        BaselineCheckpoint(
            code_commit="abc1234",
            data_hash="d",
            config_hash="c",
            metrics={},
            timestamp="2026-04-29T12:00:00+00:00",
        )
    )
    raw = json.loads(path.read_text())
    assert isinstance(raw[0]["timestamp"], str)
    assert raw[0]["timestamp"].endswith("+00:00")
