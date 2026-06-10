"""Tests for the rule-J timestamp migration on BacktestRunDB / BaselineCheckpoint.

Pre-this-PR: timestamps stored as int epoch milliseconds.
Post-this-PR: timestamps stored as ISO-8601 UTC strings, with backward
compatibility for legacy DB files that still contain int timestamps.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from backtest_run_db import (
    BacktestRunDB,
    BacktestRunRecord,
    BaselineCheckpoint,
    BaselineTracker,
    build_config_hash,
    build_data_hash,
)
from config_hash import _config_hash
from persistence_utils import write_text_atomic


def _make_record(run_id: str, *, timestamp: str = "") -> BacktestRunRecord:
    return BacktestRunRecord(
        run_id=run_id,
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
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.add(_make_record("exp1", timestamp="2026-04-29T12:00:00+00:00"))
    # Force re-load by clearing the in-memory cache.
    db._records = None
    out = db.all()
    assert len(out) == 1
    assert out[0].timestamp == "2026-04-29T12:00:00+00:00"


def test_disk_payload_is_iso_string(tmp_path: Path) -> None:
    """The on-disk sqlite row must contain a string, not an int, for new writes."""
    db_path = tmp_path / "backtest_runs.db"
    db = BacktestRunDB(db_path)
    db.add(_make_record("exp1", timestamp="2026-04-29T12:00:00+00:00"))
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT timestamp FROM backtest_runs WHERE run_id = 'exp1'").fetchone()
    assert isinstance(row[0], str)
    assert row[0].endswith("+00:00")


def test_malformed_string_timestamp_does_not_crash_db_load(tmp_path: Path) -> None:
    db_path = tmp_path / "backtest_runs.db"
    BacktestRunDB(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM backtest_runs")
        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_backtest_run_id,
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

    out = BacktestRunDB(db_path).all()
    assert len(out) == 1
    assert out[0].timestamp == ""


def test_malformed_json_fields_do_not_crash_db_load(tmp_path: Path) -> None:
    db_path = tmp_path / "backtest_runs.db"
    BacktestRunDB(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM backtest_runs")
        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_backtest_run_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bad-json",
                "t",
                "configs/ema_base.yaml",
                "{not-json",
                "abc1234",
                "d",
                "{still-bad",
                "[]",
                0,
                "",
                "",
                "",
                "oops",
                0,
                "",
                "none",
                "",
                "",
                "2026-01-01T00:00:00+00:00",
                "ema",
                "",
                "",
                0,
                "not-json",
                "[]",
                "",
            ),
        )
        conn.commit()

    out = BacktestRunDB(db_path).all()
    assert len(out) == 1
    assert out[0].runtime_config == {}
    assert out[0].strategy_diagnostics == {}
    assert out[0].usage == {}
    assert getattr(out[0], "_asi_export", {}) == {}


def test_build_config_hash_matches_result_schema_hash_policy() -> None:
    config = {"b": 1, "a": 2}

    assert build_config_hash(config) == _config_hash(config)


def test_build_data_hash_uses_12_char_policy() -> None:
    config = {
        "symbols": ["SPY"],
        "data_universe": "nasdaq8",
        "validation_start": "2024-01-01",
        "validation_end": "2024-01-31",
    }

    assert len(build_data_hash(config)) == 12


# ── Back-compat path: legacy int timestamps still load ──────────


def test_legacy_int_db_file_loads_and_coerces_to_iso(tmp_path: Path) -> None:
    """A sqlite row with int epoch-ms timestamps must still load as ISO in memory."""
    legacy_db = tmp_path / "backtest_runs.db"
    db = BacktestRunDB(legacy_db)
    with sqlite3.connect(legacy_db) as conn:
        conn.execute("DELETE FROM backtest_runs")
        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_backtest_run_id,
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

    db = BacktestRunDB(legacy_db)
    out = db.all()
    assert len(out) == 1
    # Coerced from int 1704067200000 → ISO string.
    assert out[0].timestamp == "2024-01-01T00:00:00+00:00"
    assert out[0].created_at_utc == "2024-01-01T00:00:00+00:00"


def test_legacy_timestamp_backfills_created_at_utc_column(tmp_path: Path) -> None:
    db_path = tmp_path / "backtest_runs.db"
    BacktestRunDB(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM backtest_runs")
        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_backtest_run_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-created-at",
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
                "",
            ),
        )
        conn.commit()

    BacktestRunDB(db_path)

    with sqlite3.connect(db_path) as conn:
        created_at_utc = conn.execute(
            "SELECT created_at_utc FROM backtest_runs WHERE run_id = 'legacy-created-at'"
        ).fetchone()[0]

    assert created_at_utc == "2024-01-01T00:00:00+00:00"


def test_latest_orders_correctly_across_legacy_and_new(tmp_path: Path) -> None:
    """A DB containing one legacy int row and one new ISO row must order
    correctly when latest() is called."""
    db_path = tmp_path / "backtest_runs.db"
    db = BacktestRunDB(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM backtest_runs")
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
            INSERT INTO backtest_runs (
                run_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_backtest_run_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy", *base[:17], 1700000000000, *base[17:]),
        )
        conn.execute(
            """
            INSERT INTO backtest_runs (
                run_id, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file, strategy_diagnostics_json,
                accepted, rejection_reason, verdict_status, verdict_summary, parent_backtest_run_id,
                timestamp, family, hypothesis, mechanism, job, usage_json, asi_json, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("new", *base[:17], "2024-01-01T00:00:00+00:00", *base[17:]),
        )
        conn.commit()

    db = BacktestRunDB(db_path)
    latest_records = db.latest(2)
    assert latest_records[0].run_id == "new"
    assert latest_records[1].run_id == "legacy"


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


def test_baseline_checkpoint_records_strategy_family_in_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "ema_backtest_runs.db"
    tracker = BaselineTracker(
        tmp_path / "baseline.json",
        db_path=db_path,
        strategy_family="ema",
    )

    tracker.record(
        BaselineCheckpoint(
            code_commit="abc1234",
            data_hash="d",
            config_hash="c",
            metrics={"profit_factor": 1.4},
            timestamp="2026-04-29T12:00:00+00:00",
            round_number=3,
        )
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT strategy_family, code_commit, metrics_json, created_at_utc, round_number
            FROM baseline_checkpoints
            """).fetchone()

    assert row["strategy_family"] == "ema"
    assert row["code_commit"] == "abc1234"
    assert json.loads(row["metrics_json"]) == {"profit_factor": 1.4}
    assert row["created_at_utc"] == "2026-04-29T12:00:00+00:00"
    assert row["round_number"] == 3


def test_baseline_checkpoint_raises_when_configured_sqlite_write_fails(
    tmp_path: Path,
) -> None:
    db_dir = tmp_path / "not-a-db"
    db_dir.mkdir()
    tracker = BaselineTracker(
        tmp_path / "baseline.json",
        db_path=db_dir,
        strategy_family="ema",
    )

    with pytest.raises(sqlite3.Error):
        tracker.record(
            BaselineCheckpoint(
                code_commit="abc1234",
                data_hash="d",
                config_hash="c",
                metrics={"profit_factor": 1.4},
                timestamp="2026-04-29T12:00:00+00:00",
            )
        )

    assert not (tmp_path / "baseline.json").exists()


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


def test_baseline_checkpoint_serializes_non_finite_metrics_as_json_strings(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    tracker = BaselineTracker(path)
    tracker.record(
        BaselineCheckpoint(
            code_commit="abc1234",
            data_hash="d",
            config_hash="c",
            metrics={"profit_factor": float("inf")},
            timestamp="2026-04-29T12:00:00+00:00",
        )
    )

    raw = path.read_text()
    assert '"Infinity"' in raw
    parsed = json.loads(raw)
    assert parsed[0]["metrics"]["profit_factor"] == "Infinity"


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


def test_baseline_checkpoint_record_preserves_existing_file_mode(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("[]\n")
    path.chmod(0o640)

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

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_write_text_atomic_ignores_race_when_target_disappears_before_stat(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "artifact.txt"
    path.write_text("original")

    real_stat = Path.stat
    real_exists = Path.exists
    calls = {"exists": 0, "stat": 0}

    def fake_exists(self: Path, *args: object, **kwargs: object) -> bool:
        if self == path:
            calls["exists"] += 1
            return True
        return real_exists(self, *args, **kwargs)

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == path:
            calls["stat"] += 1
            if calls["stat"] == 1:
                raise FileNotFoundError
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "stat", fake_stat)

    write_text_atomic(path, "updated")

    assert path.read_text() == "updated"
