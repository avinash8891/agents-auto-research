from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from autoresearch_runtime_paths import research_round_id
from backtest_run_db import BacktestRunDB, BacktestRunRecord


def _record(*, round_number: int, job: int, baseline: bool = False) -> BacktestRunRecord:
    backtest_dir = (
        f"runtime/jobs/job-{job}/research/round-0-baseline/backtest"
        if baseline
        else f"runtime/jobs/job-{job}/research/round-{round_number}/backtest"
    )
    return BacktestRunRecord(
        run_id=f"exp-{job}-{round_number}",
        thesis_id="baseline" if baseline else f"thesis-{round_number}",
        config_path=(
            "configs/ema_base.yaml"
            if baseline
            else f"runtime/jobs/job-{job}/research/round-{round_number}/selected_config.json"
        ),
        runtime_config={},
        code_commit="abc1234",
        data_hash="data",
        train_metrics={},
        validation_metrics={"profit_factor": 1.5},
        trade_count=10,
        trades_file=f"{backtest_dir}/trades.csv",
        strategy_events_file=f"{backtest_dir}/strategy_events.parquet",
        diagnostics_file=f"{backtest_dir}/diagnostics.json",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="good",
        parent_backtest_run_id="",
        timestamp="2026-05-09T00:00:00+00:00",
        family="ema",
        hypothesis="h",
        mechanism="m",
        job=job,
        backtest_run_id=f"job-{job}-round-{round_number}-backtest",
        research_round_id=f"job-{job}-round-{round_number}",
        research_round_number=round_number,
        is_baseline=baseline,
    )


def test_sqlite_schema_keeps_backtest_runs_table_with_round_columns(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    with sqlite3.connect(db.path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)")}

    assert "research_round_id" in columns
    assert "research_round_number" in columns
    assert "backtest_run_id" in columns
    assert "created_at_utc" in columns
    assert "primary_metric_name" in columns
    assert "primary_metric_value" in columns
    assert "metrics_json" in columns
    assert "trade_analysis_json" in columns


def test_add_populates_canonical_metric_columns_for_direct_sqlite_reads(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    record = _record(round_number=1, job=1)
    record.trade_analysis = {"trade_count": 10, "avg_win": 1.25}
    db.add(record)

    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT created_at_utc, primary_metric_name, primary_metric_value,
                   metrics_json, trade_analysis_json
            FROM backtest_runs
            WHERE run_id = ?
            """,
            (record.run_id,),
        ).fetchone()

    assert row["created_at_utc"] == "2026-05-09T00:00:00+00:00"
    assert row["primary_metric_name"] == "profit_factor"
    assert row["primary_metric_value"] == 1.5
    assert json.loads(row["metrics_json"])["profit_factor"] == 1.5
    assert json.loads(row["trade_analysis_json"]) == {"trade_count": 10, "avg_win": 1.25}


def test_read_results_reports_round_scoped_artifact_dir(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(_record(round_number=1, job=1))

    result = db.read_results()[0]

    assert result.asi["artifact_dir"] == "runtime/jobs/job-1/research/round-1/backtest"
    assert result.asi["research_round_id"] == "job-1-round-1"
    assert result.asi["backtest_run_id"] == "job-1-round-1-backtest"


def test_read_results_preserves_stored_asi_json_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "backtest_runs.db"
    db = BacktestRunDB(db_path)
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    record = _record(round_number=1, job=1)
    setattr(
        record,
        "_asi_export",
        {
            "baseline_rerun_for_commit": "abc1234",
            "insights": ["retry baseline after deploy"],
            "config_changes": {"ema_length": 13},
            "next_thesis_suggestion": {"hypothesis": "try a slower EMA"},
        },
    )
    db.add(record)

    result = BacktestRunDB(db_path).read_results()[0]

    assert result.asi["baseline_rerun_for_commit"] == "abc1234"
    assert result.asi["insights"] == ["retry baseline after deploy"]
    assert result.asi["config_changes"] == {"ema_length": 13}
    assert result.asi["next_thesis_suggestion"] == {"hypothesis": "try a slower EMA"}


def test_export_entries_use_backtest_run_type(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    db.add(_record(round_number=0, job=3, baseline=True))

    entries = [entry for entry in db.export_entries() if entry.get("type") != "config"]

    assert entries[0]["type"] == "backtest_run"
    assert entries[0]["is_baseline"] is True
    assert entries[0]["research_round_number"] == 0


def test_list_research_rounds_keeps_round_zero_baseline(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")
    state_path = tmp_path / "state.json"
    state_path.write_text('{"job": 3}\n')
    db.log_research_round(
        state_path,
        round_number=0,
        thesis_id="baseline",
        hypothesis_id="baseline",
        outcome="baseline_backtest_completed",
    )

    rows = db.list_research_rounds()

    assert rows[0]["round_number"] == 0
    assert rows[0]["selected_thesis_id"] == "baseline"


def test_research_thesis_attempt_schema_has_attempt_id_and_required_indexes(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")

    with sqlite3.connect(db.path) as conn:
        attempt_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(research_thesis_attempts)")
        }
        attempt_index_names = {
            row[1] for row in conn.execute("PRAGMA index_list(research_thesis_attempts)")
        }
        round_index_names = {row[1] for row in conn.execute("PRAGMA index_list(research_rounds)")}

    assert "thesis_attempt_id" in attempt_columns
    assert "mechanism_dimension" not in attempt_columns
    assert "idx_research_rounds_job_round" in round_index_names
    assert "idx_research_rounds_outcome" in round_index_names
    assert "idx_research_thesis_attempts_round_attempt" in attempt_index_names
    assert "idx_research_thesis_attempts_validator_status" in attempt_index_names


def test_research_round_retry_attempts_remain_first_class_rows(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            """
            INSERT INTO research_rounds (
                research_round_id, job_id, round_number, run_id, hypothesis_id,
                selected_thesis_id, outcome, created_at_utc, usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-17-round-2",
                17,
                2,
                "run-17",
                "ema-tight-entry",
                "ema-tight-entry",
                "compiled",
                "2026-05-28T00:00:00+00:00",
                "{}",
            ),
        )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-17-round-2",
            "attempt_number": 1,
            "thesis_id": "ema-duplicate",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 8},
            "validator_status": "rejected_attempt_1",
            "validation_failure_reason": "duplicate mechanism",
            "selected_for_execution": 0,
            "created_at_utc": "2026-05-28T00:00:01+00:00",
        }
    )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-17-round-2",
            "attempt_number": 2,
            "thesis_id": "ema-metadata-leak",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 13},
            "validator_status": "rejected_attempt_2",
            "validation_failure_reason": "config_changes contains thesis metadata key",
            "selected_for_execution": 0,
            "created_at_utc": "2026-05-28T00:00:02+00:00",
        }
    )
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-17-round-2",
            "attempt_number": 3,
            "thesis_id": "ema-tight-entry",
            "strategy_family": "ema",
            "config_changes": {"ema_length": 21},
            "validator_status": "compiled",
            "hypothesis": "tighten entry after weak crosses",
            "mechanism": "avoid low-conviction crosses",
            "selected_for_execution": 1,
            "created_at_utc": "2026-05-28T00:00:03+00:00",
        }
    )

    attempts = db.list_research_thesis_attempts(job_id=17)
    by_attempt = {attempt["attempt_number"]: attempt for attempt in attempts}

    assert sorted(by_attempt) == [1, 2, 3]
    assert by_attempt[1]["thesis_attempt_id"] == "job-17-round-2-attempt-1"
    assert by_attempt[1]["validator_status"] == "rejected_attempt_1"
    assert by_attempt[1]["selected_for_execution"] == 0
    assert by_attempt[2]["thesis_attempt_id"] == "job-17-round-2-attempt-2"
    assert by_attempt[2]["validator_status"] == "rejected_attempt_2"
    assert by_attempt[2]["validation_failure_reason"] == (
        "config_changes contains thesis metadata key"
    )
    assert by_attempt[2]["selected_for_execution"] == 0
    assert by_attempt[3]["thesis_attempt_id"] == "job-17-round-2-attempt-3"
    assert by_attempt[3]["validator_status"] == "compiled"
    assert by_attempt[3]["selected_for_execution"] == 1
    assert by_attempt[3]["hypothesis"] == "tighten entry after weak crosses"
    assert by_attempt[3]["mechanism"] == "avoid low-conviction crosses"
    assert by_attempt[3]["created_at_utc"] == "2026-05-28T00:00:03+00:00"


def test_get_by_research_round_id_returns_single_record(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    db.add(_record(round_number=5, job=1))
    db.add(_record(round_number=7, job=1))

    target_round_id = research_round_id(1, 5)
    record = db.get_by_research_round_id(target_round_id)

    assert record is not None
    assert record.research_round_id == target_round_id
    assert record.run_id == "exp-1-5"


def test_get_by_research_round_id_returns_none_for_unknown(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    db.add(_record(round_number=5, job=1))

    assert db.get_by_research_round_id(research_round_id(1, 99)) is None


def test_add_from_sqlite_fields_persists_research_round_id(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    round_id = research_round_id(2, 4)
    db.add_from_sqlite_fields(
        run_id="exp-2-4",
        thesis_id="ema_breakout_v1",
        config_path="runtime/jobs/job-2/research/round-4/selected_config.json",
        runtime_config={"ema_length": 21},
        code_commit="abc1234",
        data_hash="data",
        metrics={"profit_factor": 1.65},
        trade_analysis={"trade_count": 42},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="accepted",
        verdict_summary="accepted",
        family="ema",
        job_id=2,
        primary_metric_name="profit_factor",
        primary_metric_value=1.65,
        research_round_id=round_id,
        research_round_number=4,
        is_baseline=False,
    )

    record = db.get_by_research_round_id(round_id)

    assert record is not None
    assert record.research_round_id == round_id
    assert record.research_round_number == 4
    assert record.is_baseline is False
    assert record.thesis_id == "ema_breakout_v1"


def test_add_from_sqlite_fields_persists_canonical_metric_columns(
    tmp_path: Path,
) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    round_id = research_round_id(2, 4)
    db.add_from_sqlite_fields(
        run_id="exp-2-4",
        thesis_id="ema_breakout_v1",
        config_path="runtime/jobs/job-2/research/round-4/selected_config.json",
        runtime_config={"ema_length": 21},
        code_commit="abc1234",
        data_hash="data",
        metrics={"sharpe": 0.4},
        trade_analysis={"trade_count": 42, "avg_loss": -0.7},
        strategy_diagnostics={},
        decision_status="keep",
        verdict_status="accepted",
        verdict_summary="accepted",
        family="ema",
        job_id=2,
        primary_metric_name="profit_factor",
        primary_metric_value=1.65,
        research_round_id=round_id,
        research_round_number=4,
        is_baseline=False,
    )

    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT primary_metric_name, primary_metric_value, metrics_json, trade_analysis_json
            FROM backtest_runs
            WHERE run_id = 'exp-2-4'
            """).fetchone()

    metrics = json.loads(row["metrics_json"])
    assert row["primary_metric_name"] == "profit_factor"
    assert row["primary_metric_value"] == 1.65
    assert metrics["profit_factor"] == 1.65
    assert metrics["sharpe"] == 0.4
    assert json.loads(row["trade_analysis_json"]) == {"trade_count": 42, "avg_loss": -0.7}


def test_add_from_sqlite_fields_rejects_empty_research_round_id(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    with pytest.raises(ValueError, match="research_round_id is required"):
        db.add_from_sqlite_fields(
            run_id="exp-no-rrid",
            thesis_id="ema_breakout_v1",
            config_path="runtime/jobs/job-2/research/round-4/selected_config.json",
            runtime_config={"ema_length": 21},
            code_commit="abc1234",
            data_hash="data",
            metrics={"profit_factor": 1.65},
            trade_analysis={},
            strategy_diagnostics={},
            decision_status="keep",
            verdict_status="accepted",
            verdict_summary="accepted",
            family="ema",
            job_id=2,
            primary_metric_name="profit_factor",
            primary_metric_value=1.65,
            research_round_id="",
            research_round_number=4,
        )


def test_add_from_sqlite_fields_rejects_negative_round_number(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    with pytest.raises(ValueError, match="research_round_number must be >= 0"):
        db.add_from_sqlite_fields(
            run_id="exp-bad-round",
            thesis_id="ema_breakout_v1",
            config_path="runtime/jobs/job-2/research/round-4/selected_config.json",
            runtime_config={"ema_length": 21},
            code_commit="abc1234",
            data_hash="data",
            metrics={"profit_factor": 1.65},
            trade_analysis={},
            strategy_diagnostics={},
            decision_status="keep",
            verdict_status="accepted",
            verdict_summary="accepted",
            family="ema",
            job_id=2,
            primary_metric_name="profit_factor",
            primary_metric_value=1.65,
            research_round_id="job-2-round-x",
            research_round_number=-1,
        )


def test_add_from_sqlite_fields_rejects_mismatched_research_round_id(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    with pytest.raises(ValueError, match="research_round_id mismatch"):
        db.add_from_sqlite_fields(
            run_id="exp-mismatch",
            thesis_id="ema_breakout_v1",
            config_path="runtime/jobs/job-2/research/round-4/selected_config.json",
            runtime_config={"ema_length": 21},
            code_commit="abc1234",
            data_hash="data",
            metrics={"profit_factor": 1.65},
            trade_analysis={},
            strategy_diagnostics={},
            decision_status="keep",
            verdict_status="accepted",
            verdict_summary="accepted",
            family="ema",
            job_id=2,
            primary_metric_name="profit_factor",
            primary_metric_value=1.65,
            research_round_id="job-99-round-99",
            research_round_number=4,
        )


def test_best_by_metric_ignores_malformed_metric_values(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    first = _record(round_number=1, job=1)
    first.validation_metrics["profit_factor"] = "not-a-number"
    second = _record(round_number=2, job=1)
    second.validation_metrics["profit_factor"] = 1.75

    db.add(first)
    db.add(second)

    best = db.best_by_metric("profit_factor")

    assert best is not None
    assert best.run_id == second.run_id


def test_best_by_metric_respects_lower_direction(tmp_path: Path) -> None:
    """U1: direction='lower' must pick the smallest metric value."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="max_drawdown", direction="lower")

    worse = _record(round_number=1, job=1)
    worse.validation_metrics["max_drawdown"] = 0.30
    better = _record(round_number=2, job=1)
    better.validation_metrics["max_drawdown"] = 0.10

    db.add(worse)
    db.add(better)

    best = db.best_by_metric("max_drawdown")
    assert best is not None
    assert best.run_id == better.run_id


def test_backtest_runs_has_canonical_columns_and_indexes(tmp_path: Path) -> None:
    """F2/U4: backtest_runs must have doc-01 canonical columns + 6 indexes."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    conn = sqlite3.connect(tmp_path / "backtest_runs.db")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()}
    for col in (
        "decision_status",
        "created_at_utc",
        "strategy_family",
        "job_id",
        "primary_metric_name",
        "primary_metric_value",
        "metrics_json",
        "trade_analysis_json",
        "trace_run_id",
    ):
        assert col in columns, f"missing canonical column: {col}"

    indexes = {
        row[1]
        for row in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='backtest_runs'"
        ).fetchall()
    }
    for idx in (
        "idx_backtest_runs_thesis_id",
        "idx_backtest_runs_strategy_family_created_at",
        "idx_backtest_runs_job_id",
        "idx_backtest_runs_code_commit",
        "idx_backtest_runs_decision_status",
        "idx_backtest_runs_primary_metric_value",
    ):
        assert idx in indexes, f"missing required index: {idx}"
    conn.close()


def test_backtest_runs_decision_status_backfill(tmp_path: Path) -> None:
    """F2: accepted=1 -> decision_status='keep', accepted=0 -> 'discard'."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    accepted_rec = _record(round_number=1, job=1)
    accepted_rec.accepted = True
    rejected_rec = _record(round_number=2, job=1)
    rejected_rec.accepted = False

    db.add(accepted_rec)
    db.add(rejected_rec)

    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute(
        "SELECT run_id, decision_status FROM backtest_runs ORDER BY run_id"
    ).fetchall()
    conn.close()

    status_by_id = {r[0]: r[1] for r in rows}
    assert status_by_id[accepted_rec.run_id] == "keep"
    assert status_by_id[rejected_rec.run_id] == "discard"


def test_best_by_metric_skips_corrupt_lower_direction(tmp_path: Path, caplog) -> None:
    """F9: corrupt metric must be skipped, not coerced to 0.0 and win lower-is-better."""
    import logging

    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="max_drawdown", direction="lower")

    corrupt = _record(round_number=1, job=1)
    corrupt.validation_metrics["max_drawdown"] = "not-a-number"
    good = _record(round_number=2, job=1)
    good.validation_metrics["max_drawdown"] = 2.5

    db.add(corrupt)
    db.add(good)

    with caplog.at_level(logging.WARNING):
        best = db.best_by_metric("max_drawdown")

    assert best is not None
    assert best.run_id == good.run_id
    assert "not-a-number" in caplog.text


def test_baseline_checkpoint_persists_to_sqlite(tmp_path: Path) -> None:
    """F1/U3: BaselineTracker.record() must write to baseline_checkpoints table."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    from backtest_run_db import BaselineCheckpoint, BaselineTracker

    tracker = BaselineTracker(
        tmp_path / "ema_baseline_checkpoints.json",
        db_path=db.path,
    )

    checkpoint = BaselineCheckpoint(
        code_commit="abc123",
        data_hash="def456",
        config_hash="ghi789",
        metrics={"profit_factor": 1.5, "max_drawdown": 0.12},
        timestamp="2026-06-10T12:00:00+00:00",
        round_number=3,
    )
    tracker.record(checkpoint)

    import sqlite3

    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute(
        "SELECT checkpoint_id, strategy_family, code_commit, round_number FROM baseline_checkpoints"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0]  # checkpoint_id is non-empty
    assert rows[0][1] == "ema"  # strategy_family from session
    assert rows[0][2] == "abc123"
    assert rows[0][3] == 3


def test_reload_picks_up_external_sqlite_write(tmp_path: Path) -> None:
    """U2: reload() must invalidate the cache so external writes are visible."""
    db_path = tmp_path / "backtest_runs.db"
    db1 = BacktestRunDB(db_path)
    db1.init_session(name="ema", metric_name="profit_factor", direction="higher")

    rec = _record(round_number=1, job=1)
    db1.add(rec)
    assert db1.count() == 1

    # External write via a second connection (simulates VPS run)
    db2 = BacktestRunDB(db_path)
    rec2 = _record(round_number=2, job=1)
    db2.add(rec2)

    # db1 still sees stale cache
    assert db1.count() == 1

    # After reload, sees the external write
    db1.reload()
    assert db1.count() == 2


def test_ensure_round_started_creates_in_progress_row(tmp_path: Path) -> None:
    """F15: a research_rounds row should exist after ensure_round_started."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    db.ensure_round_started(
        research_round_id="job-1-round-1",
        job_id=1,
        round_number=1,
        run_id="run-abc",
    )

    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute(
        "SELECT outcome FROM research_rounds WHERE research_round_id = 'job-1-round-1'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "in_progress"


def test_ensure_round_started_is_idempotent(tmp_path: Path) -> None:
    """ensure_round_started must not overwrite an existing round row."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    db.ensure_round_started(
        research_round_id="job-1-round-1",
        job_id=1,
        round_number=1,
        run_id="run-abc",
    )
    # Calling again should not error or duplicate
    db.ensure_round_started(
        research_round_id="job-1-round-1",
        job_id=1,
        round_number=1,
        run_id="run-abc",
    )

    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute(
        "SELECT outcome FROM research_rounds WHERE research_round_id = 'job-1-round-1'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
