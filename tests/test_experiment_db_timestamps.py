"""Tests for the rule-J timestamp migration on ExperimentDB / BaselineCheckpoint.

Pre-this-PR: timestamps stored as int epoch milliseconds.
Post-this-PR: timestamps stored as ISO-8601 UTC strings, with backward
compatibility for legacy DB files that still contain int timestamps.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from config_hash import _config_hash
from experiment_db import (
    BaselineCheckpoint,
    BaselineTracker,
    build_config_hash,
    build_data_hash,
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
    db = ExperimentDB(tmp_path / "experiments.db")
    db.add(_make_record("exp1", timestamp="2026-04-29T12:00:00+00:00"))
    # Force re-load by clearing the in-memory cache.
    db._records = None
    out = db.all()
    assert len(out) == 1
    assert out[0].timestamp == "2026-04-29T12:00:00+00:00"


def test_disk_payload_is_iso_string(tmp_path: Path) -> None:
    """The on-disk sqlite row must contain a string, not an int, for new writes."""
    db_path = tmp_path / "experiments.db"
    db = ExperimentDB(db_path)
    db.add(_make_record("exp1", timestamp="2026-04-29T12:00:00+00:00"))
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT timestamp FROM experiments WHERE experiment_id = 'exp1'"
        ).fetchone()
    assert isinstance(row[0], str)
    assert row[0].endswith("+00:00")


def test_malformed_string_timestamp_does_not_crash_db_load(tmp_path: Path) -> None:
    db_path = tmp_path / "experiments.db"
    ExperimentDB(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM experiments")
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_experiment_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bad-string-ts",
                "t",
                "configs/ema_base.yaml",
                "{}",
                "abc1234",
                "d",
                "{}",
                "{}",
                0,
                "",
                "",
                "",
                "{}",
                0,
                "",
                "none",
                "",
                "",
                "unknown",
                "ema",
                "",
                "",
                0,
                "{}",
                "{}",
                "",
            ),
        )
        conn.commit()

    out = ExperimentDB(db_path).all()
    assert len(out) == 1
    assert out[0].timestamp == ""


def test_build_config_hash_matches_result_schema_hash_policy() -> None:
    config = {"b": 1, "a": 2}

    assert build_config_hash(config) == _config_hash(config)


def test_build_data_hash_uses_12_char_policy() -> None:
    config = {
        "symbols": ["SPY"],
        "data_dir": "data",
        "validation_start": "2024-01-01",
        "validation_end": "2024-01-31",
    }

    assert len(build_data_hash(config)) == 12


# ── Back-compat path: legacy int timestamps still load ──────────


def test_legacy_int_db_file_loads_and_coerces_to_iso(tmp_path: Path) -> None:
    """A sqlite row with int epoch-ms timestamps must still load as ISO in memory."""
    legacy_db = tmp_path / "experiments.db"
    db = ExperimentDB(legacy_db)
    with sqlite3.connect(legacy_db) as conn:
        conn.execute("DELETE FROM experiments")
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_experiment_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy1",
                "t",
                "configs/ema_base.yaml",
                "{}",
                "abc1234",
                "d",
                "{}",
                "{}",
                0,
                "",
                "",
                "",
                "{}",
                0,
                "",
                "none",
                "",
                "",
                1704067200000,
                "ema",
                "",
                "",
                0,
                "{}",
                "{}",
                "",
            ),
        )
        conn.commit()

    db = ExperimentDB(legacy_db)
    out = db.all()
    assert len(out) == 1
    # Coerced from int 1704067200000 → ISO string.
    assert out[0].timestamp == "2024-01-01T00:00:00+00:00"


def test_latest_orders_correctly_across_legacy_and_new(tmp_path: Path) -> None:
    """A DB containing one legacy int row and one new ISO row must order
    correctly when latest() is called."""
    db_path = tmp_path / "experiments.db"
    db = ExperimentDB(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM experiments")
        base = (
            "t",
            "x",
            "{}",
            "",
            "",
            "{}",
            "{}",
            0,
            "",
            "",
            "",
            "{}",
            0,
            "",
            "none",
            "",
            "",
            "",
            "",
            "",
            0,
            "{}",
            "{}",
            "",
        )
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_experiment_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy", *base[:17], 1700000000000, *base[17:]),
        )
        conn.execute(
            """
            INSERT INTO experiments (
                experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_experiment_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("new", *base[:17], "2024-01-01T00:00:00+00:00", *base[17:]),
        )
        conn.commit()

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


def test_baseline_checkpoint_record_leaves_no_tmp_artifacts(tmp_path: Path) -> None:
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

    assert not list(tmp_path.rglob("*.tmp"))
