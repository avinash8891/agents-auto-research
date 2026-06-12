"""Backtest-run database — canonical sqlite-backed storage."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autoresearch_logging import get_logger
from autoresearch_runtime_paths import research_round_id as _build_research_round_id
from autoresearch_runtime_paths import research_round_id_or_empty
from autoresearch_state import coerce_timestamp_to_epoch_ms, coerce_timestamp_to_iso8601_utc
from config_hash import _config_hash
from persistence_utils import (
    json_dumps_strict,
    json_loads_metric_sentinels,
)
from persistence_utils import utc_now_iso8601 as _iso8601_utc_now
from persistence_utils import (
    write_text_atomic,
)

log = get_logger(__name__)


INVALID_RESULT_VERDICTS = frozenset(
    {
        "invalid_duplicate_result",
        "invalid_noop_config",
    }
)

BACKTEST_RUNS_TABLE = "backtest_runs"


def research_thesis_attempt_id(research_round_id: str, attempt_number: int) -> str:
    return f"{research_round_id}-attempt-{int(attempt_number)}"


def _coerce_metric_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric_value_for_record(record: BacktestRunRecord, metric: str) -> Any:
    validation_value = record.validation_metrics.get(metric)
    if validation_value is not None:
        return validation_value
    return record.train_metrics.get(metric)


def _record_job_as_int(record: BacktestRunRecord) -> int | None:
    try:
        return int(getattr(record, "job", 0))
    except (TypeError, ValueError):
        return None


def is_metric_rankable_backtest_run(record: BacktestRunRecord) -> bool:
    """Return whether a backtest run can be used as a best/worst metric candidate."""
    return bool(record.accepted) and record.verdict_status not in INVALID_RESULT_VERDICTS


def _artifact_dir_from_files(paths: tuple[str, str, str]) -> str:
    for path in paths:
        if not path:
            continue
        parent = Path(path).parent
        if str(parent) != ".":
            return str(parent)
    return ""


def _load_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_metric_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json_loads_metric_sentinels(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_created_at_utc(created_at_utc: Any, timestamp: Any) -> str:
    return (
        coerce_timestamp_to_iso8601_utc(created_at_utc)
        or coerce_timestamp_to_iso8601_utc(timestamp)
        or (
            coerce_timestamp_to_iso8601_utc(int(timestamp))
            if isinstance(timestamp, str) and timestamp.isdigit()
            else ""
        )
    )


def _canonical_metrics_for_record(
    record: BacktestRunRecord, primary_metric_name: str
) -> dict[str, Any]:
    metrics = dict(record.train_metrics)
    metrics.update(record.validation_metrics)
    metrics.update(record.metrics)
    primary_metric_value = record.primary_metric_value
    if primary_metric_value is None:
        primary_metric_value = _metric_value_for_record(record, primary_metric_name)
    if primary_metric_name and primary_metric_value is not None:
        metrics.setdefault(primary_metric_name, primary_metric_value)
    return metrics


def _canonical_trade_analysis_for_record(record: BacktestRunRecord) -> dict[str, Any]:
    if record.trade_analysis:
        return dict(record.trade_analysis)
    asi = getattr(record, "_asi_export", None)
    if isinstance(asi, dict):
        trade_analysis = asi.get("trade_analysis")
        if isinstance(trade_analysis, dict):
            return dict(trade_analysis)
    return {}


def _canonical_primary_metric_value(record: BacktestRunRecord, metric_name: str) -> float | None:
    value = record.primary_metric_value
    if value is None:
        value = _metric_value_for_record(record, metric_name)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _strategy_family_from_path(path: Path | None) -> str:
    if path is None:
        return ""
    suffix = "_backtest_runs"
    stem = path.stem
    if stem.endswith(suffix):
        return stem[: -len(suffix)]
    return ""


@dataclass
class BacktestRunRecord:
    """One complete backtest-run record."""

    run_id: str
    thesis_id: str
    config_path: str
    runtime_config: dict[str, Any]

    code_commit: str
    data_hash: str

    train_metrics: dict[str, Any]
    validation_metrics: dict[str, Any]

    trade_count: int
    trades_file: str
    strategy_events_file: str
    diagnostics_file: str
    strategy_diagnostics: dict[str, Any]

    accepted: bool
    rejection_reason: str
    verdict_status: str  # accepted, rejected, inconclusive, none
    verdict_summary: str

    parent_backtest_run_id: str = ""
    # ISO-8601 UTC string. Legacy DB files with int epoch-ms timestamps
    # are coerced to ISO on load (see BacktestRunDB._load).
    timestamp: str = ""
    family: str = ""
    hypothesis: str = ""
    mechanism: str = ""
    job: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    backtest_run_id: str = ""
    research_round_id: str = ""
    research_round_number: int = -1
    is_baseline: bool = False
    created_at_utc: str = ""
    primary_metric_name: str = ""
    primary_metric_value: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    trade_analysis: dict[str, Any] = field(default_factory=dict)


class BacktestRunDB:
    """Canonical backtest-run database backed by sqlite3."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: list[BacktestRunRecord] | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    name TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    best_direction TEXT NOT NULL
                )
                """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {BACKTEST_RUNS_TABLE} (
                    run_id TEXT PRIMARY KEY,
                    backtest_run_id TEXT NOT NULL DEFAULT '',
                    research_round_id TEXT NOT NULL DEFAULT '',
                    research_round_number INTEGER NOT NULL DEFAULT -1,
                    is_baseline INTEGER NOT NULL DEFAULT 0,
                    thesis_id TEXT NOT NULL,
                    config_path TEXT NOT NULL,
                    runtime_config_json TEXT NOT NULL,
                    code_commit TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    train_metrics_json TEXT NOT NULL,
                    validation_metrics_json TEXT NOT NULL,
                    trade_count INTEGER NOT NULL,
                    trades_file TEXT NOT NULL,
                    strategy_events_file TEXT NOT NULL,
                    diagnostics_file TEXT NOT NULL,
                    strategy_diagnostics_json TEXT NOT NULL,
                    accepted INTEGER NOT NULL,
                    rejection_reason TEXT NOT NULL,
                    verdict_status TEXT NOT NULL,
                    verdict_summary TEXT NOT NULL,
                    parent_backtest_run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    family TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    mechanism TEXT NOT NULL,
                    job INTEGER NOT NULL,
                    usage_json TEXT NOT NULL,
                    asi_json TEXT NOT NULL DEFAULT '{{}}',
                    description TEXT NOT NULL DEFAULT '',
                    created_at_utc TEXT NOT NULL DEFAULT '',
                    primary_metric_name TEXT NOT NULL DEFAULT '',
                    primary_metric_value REAL,
                    metrics_json TEXT NOT NULL DEFAULT '{{}}',
                    trade_analysis_json TEXT NOT NULL DEFAULT '{{}}'
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_rounds (
                    research_round_id TEXT PRIMARY KEY,
                    job_id INTEGER NOT NULL,
                    round_number INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    hypothesis_id TEXT,
                    selected_thesis_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    usage_json TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_thesis_attempts (
                    thesis_attempt_id TEXT PRIMARY KEY,
                    research_round_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    thesis_id TEXT NOT NULL,
                    strategy_family TEXT NOT NULL,
                    config_changes_json TEXT NOT NULL,
                    validator_status TEXT NOT NULL,
                    mechanism_dimension TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    mechanism TEXT NOT NULL,
                    thesis_details_json TEXT NOT NULL DEFAULT '{}',
                    validation_failure_reason TEXT NOT NULL,
                    selected_for_execution INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    UNIQUE (research_round_id, attempt_number)
                )
                """)
            from screening import _ensure_screenings_table

            _ensure_screenings_table(conn)
            self._ensure_column(
                conn,
                "research_thesis_attempts",
                "thesis_attempt_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                conn,
                "research_thesis_attempts",
                "thesis_details_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column_renamed(
                conn, "research_thesis_attempts", "rejection_reason", "validation_failure_reason"
            )
            self._backfill_research_thesis_attempt_ids(conn)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_research_rounds_job_round
                ON research_rounds (job_id, round_number)
                """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_research_rounds_outcome
                ON research_rounds (outcome)
                """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_thesis_attempts_attempt_id
                ON research_thesis_attempts (thesis_attempt_id)
                """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_research_thesis_attempts_round_attempt
                ON research_thesis_attempts (research_round_id, attempt_number)
                """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_research_thesis_attempts_validator_status
                ON research_thesis_attempts (validator_status)
                """)
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "backtest_run_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "research_round_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn,
                BACKTEST_RUNS_TABLE,
                "research_round_number",
                "INTEGER NOT NULL DEFAULT -1",
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "is_baseline", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "decision_status", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "created_at_utc", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "strategy_family", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(conn, BACKTEST_RUNS_TABLE, "job_id", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "primary_metric_name", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(conn, BACKTEST_RUNS_TABLE, "primary_metric_value", "REAL")
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "metrics_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "trade_analysis_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "trace_run_id", "TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET decision_status = CASE WHEN accepted = 1 THEN 'keep' ELSE 'discard' END
                WHERE decision_status = ''
            """)
            self._backfill_backtest_run_created_at_utc(conn)
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET strategy_family = family
                WHERE strategy_family = ''
            """)
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET job_id = job
                WHERE job_id = 0
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_thesis_id
                ON {BACKTEST_RUNS_TABLE} (thesis_id)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_family_created_at
                ON {BACKTEST_RUNS_TABLE} (strategy_family, created_at_utc)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_job_id
                ON {BACKTEST_RUNS_TABLE} (job_id)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_code_commit
                ON {BACKTEST_RUNS_TABLE} (code_commit)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_decision_status
                ON {BACKTEST_RUNS_TABLE} (decision_status)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_primary_metric_value
                ON {BACKTEST_RUNS_TABLE} (primary_metric_value)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS baseline_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    strategy_family TEXT NOT NULL,
                    code_commit TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    round_number INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_baseline_checkpoints_strategy_family_created_at
                ON baseline_checkpoints (strategy_family, created_at_utc)
                """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_baseline_checkpoints_code_commit
                ON baseline_checkpoints (code_commit)
                """)
            conn.commit()

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _backfill_backtest_run_created_at_utc(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(f"""
            SELECT rowid, timestamp, created_at_utc
            FROM {BACKTEST_RUNS_TABLE}
            WHERE created_at_utc IS NULL OR created_at_utc = ''
            """).fetchall()
        for row in rows:
            created_at_utc = _coerce_created_at_utc(row["created_at_utc"], row["timestamp"])
            if not created_at_utc:
                continue
            conn.execute(
                f"UPDATE {BACKTEST_RUNS_TABLE} SET created_at_utc = ? WHERE rowid = ?",
                (created_at_utc, row["rowid"]),
            )

    def _backfill_research_thesis_attempt_ids(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("""
            SELECT rowid, research_round_id, attempt_number, thesis_attempt_id
            FROM research_thesis_attempts
            WHERE thesis_attempt_id = ''
            """).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE research_thesis_attempts
                SET thesis_attempt_id = ?
                WHERE rowid = ?
                """,
                (
                    research_thesis_attempt_id(
                        row["research_round_id"], int(row["attempt_number"])
                    ),
                    row["rowid"],
                ),
            )

    def _ensure_column_renamed(
        self, conn: sqlite3.Connection, table: str, old: str, new: str
    ) -> None:
        """Rename a column at init time. Raise on failure.

        Swallowing this previously allowed a half-migrated schema to slip
        through init: subsequent INSERT/SELECT statements would then fail
        at runtime with `no such column`, producing failures far from the
        actual cause. Failing loudly keeps the error localized to startup.
        """
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if old not in columns or new in columns:
            return
        try:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to rename column {table}.{old} to {new}; schema migration is incomplete"
            ) from exc

    def session_meta(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, metric_name, best_direction FROM session_meta WHERE id = 1"
            ).fetchone()
        if row is None:
            return {}
        return {
            "name": row["name"],
            "metricName": row["metric_name"],
            "bestDirection": row["best_direction"],
        }

    def init_session(self, *, name: str, metric_name: str, direction: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM session_meta WHERE id = 1")
            conn.execute(
                """
                INSERT INTO session_meta (id, name, metric_name, best_direction)
                VALUES (1, ?, ?, ?)
                """,
                (name, metric_name, direction),
            )
            conn.commit()

    def primary_metric_name(self) -> str:
        return self.session_meta().get("metricName", "profit_factor")

    def _primary_metric_name_from_conn(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT metric_name FROM session_meta WHERE id = 1").fetchone()
        if row and row["metric_name"]:
            return row["metric_name"]
        return "profit_factor"

    def best_direction(self) -> str:
        return self.session_meta().get("bestDirection", "higher")

    def evaluate_metric(self, metric: float, *, job_id: int | None = None) -> str:
        direction = self.best_direction()
        records = self.all()
        if job_id is not None:
            records = [record for record in records if _record_job_as_int(record) == job_id]
        if not records:
            return "keep"
        kept = [r for r in records if r.accepted]
        compare_against = None
        if kept:
            compare_against = _coerce_metric_float(
                _metric_value_for_record(kept[0], self.primary_metric_name())
            )
            for record in kept[1:]:
                candidate = _coerce_metric_float(
                    _metric_value_for_record(record, self.primary_metric_name())
                )
                if direction == "higher" and candidate > compare_against:
                    compare_against = candidate
                if direction == "lower" and candidate < compare_against:
                    compare_against = candidate
        else:
            compare_against = _coerce_metric_float(
                _metric_value_for_record(records[0], self.primary_metric_name())
            )
        improved = metric > compare_against if direction == "higher" else metric < compare_against
        return "keep" if improved else "discard"

    def add_from_sqlite_fields(
        self,
        *,
        run_id: str,
        thesis_id: str,
        config_path: str,
        runtime_config: dict[str, Any],
        code_commit: str,
        data_hash: str,
        metrics: dict[str, Any],
        trade_analysis: dict[str, Any],
        strategy_diagnostics: dict[str, Any],
        decision_status: str,
        verdict_status: str,
        verdict_summary: str,
        family: str,
        job_id: int,
        primary_metric_name: str,
        primary_metric_value: float,
        research_round_id: str,
        research_round_number: int,
        is_baseline: bool = False,
    ) -> None:
        if not research_round_id:
            raise ValueError(
                "research_round_id is required — empty value would write a row "
                "unfindable via get_by_research_round_id; callers must derive "
                "it via autoresearch_runtime_paths.research_round_id(job, round)"
            )
        if research_round_number < 0:
            raise ValueError(f"research_round_number must be >= 0; got {research_round_number}")
        expected_research_round_id = _build_research_round_id(job_id, research_round_number)
        if research_round_id != expected_research_round_id:
            raise ValueError(
                f"research_round_id mismatch: got {research_round_id!r}, "
                f"expected {expected_research_round_id!r} from (job_id={job_id}, "
                f"research_round_number={research_round_number})"
            )
        merged_metrics = dict(metrics)
        merged_metrics.setdefault(primary_metric_name, primary_metric_value)
        if trade_analysis:
            for key, value in trade_analysis.items():
                if key not in merged_metrics and isinstance(value, (int, float)):
                    merged_metrics[key] = value
        timestamp = _iso8601_utc_now()
        self.add(
            BacktestRunRecord(
                run_id=run_id,
                thesis_id=thesis_id,
                config_path=config_path,
                runtime_config=runtime_config,
                code_commit=code_commit,
                data_hash=data_hash,
                train_metrics={},
                validation_metrics=merged_metrics,
                trade_count=int(merged_metrics.get("trade_count", 0) or 0),
                trades_file="",
                strategy_events_file="",
                diagnostics_file="",
                strategy_diagnostics=strategy_diagnostics,
                accepted=decision_status == "keep",
                rejection_reason="",
                verdict_status=verdict_status,
                verdict_summary=verdict_summary,
                timestamp=timestamp,
                family=family,
                job=job_id,
                research_round_id=research_round_id,
                research_round_number=research_round_number,
                is_baseline=is_baseline,
                created_at_utc=timestamp,
                primary_metric_name=primary_metric_name,
                primary_metric_value=primary_metric_value,
                metrics=merged_metrics,
                trade_analysis=trade_analysis,
            )
        )

    def _write_record(self, conn: sqlite3.Connection, record: BacktestRunRecord) -> None:
        primary_metric_name = record.primary_metric_name or self._primary_metric_name_from_conn(
            conn
        )
        primary_metric_value = _canonical_primary_metric_value(record, primary_metric_name)
        metrics = _canonical_metrics_for_record(record, primary_metric_name)
        trade_analysis = _canonical_trade_analysis_for_record(record)
        created_at_utc = _coerce_created_at_utc(record.created_at_utc, record.timestamp)
        timestamp = coerce_timestamp_to_iso8601_utc(record.timestamp) or created_at_utc
        conn.execute(
            """
            INSERT OR REPLACE INTO backtest_runs (
                run_id, backtest_run_id, research_round_id, research_round_number,
                is_baseline, thesis_id, config_path, runtime_config_json, code_commit,
                data_hash, train_metrics_json, validation_metrics_json, trade_count,
                trades_file, strategy_events_file, diagnostics_file,
                strategy_diagnostics_json, accepted, rejection_reason, verdict_status,
                verdict_summary, parent_backtest_run_id, timestamp, family, hypothesis,
                mechanism, job, usage_json, asi_json, description,
                decision_status, created_at_utc, strategy_family, job_id,
                primary_metric_name, primary_metric_value, metrics_json,
                trade_analysis_json, trace_run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.backtest_run_id,
                record.research_round_id,
                record.research_round_number,
                int(record.is_baseline),
                record.thesis_id,
                record.config_path,
                json_dumps_strict(record.runtime_config),
                record.code_commit,
                record.data_hash,
                json_dumps_strict(record.train_metrics),
                json_dumps_strict(record.validation_metrics),
                record.trade_count,
                record.trades_file,
                record.strategy_events_file,
                record.diagnostics_file,
                json_dumps_strict(record.strategy_diagnostics),
                int(record.accepted),
                record.rejection_reason,
                record.verdict_status,
                record.verdict_summary,
                record.parent_backtest_run_id,
                timestamp,
                record.family,
                record.hypothesis,
                record.mechanism,
                record.job,
                json_dumps_strict(record.usage),
                json_dumps_strict(getattr(record, "_asi_export", {})),
                getattr(record, "_description_export", ""),
                "keep" if record.accepted else "discard",
                created_at_utc,
                record.family,
                record.job,
                primary_metric_name,
                primary_metric_value,
                json_dumps_strict(metrics),
                json_dumps_strict(trade_analysis),
                "",
            ),
        )

    def ensure_round_started(
        self,
        *,
        research_round_id: str,
        job_id: int,
        round_number: int,
        run_id: str,
    ) -> None:
        """Write a provisional round row if one does not already exist."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM research_rounds WHERE research_round_id = ?",
                (research_round_id,),
            ).fetchone()
            if existing:
                return
            conn.execute(
                """INSERT INTO research_rounds
                   (research_round_id, job_id, round_number, run_id,
                    selected_thesis_id, outcome, created_at_utc, usage_json)
                   VALUES (?, ?, ?, ?, '', 'in_progress', ?, '{}')""",
                (research_round_id, job_id, round_number, run_id, _iso8601_utc_now()),
            )
            conn.commit()

    def log_research_round(
        self,
        state_path: Path,
        *,
        round_number: int,
        thesis_id: str,
        hypothesis_id: str = "",
        outcome: str,
        usage: dict[str, Any] | None = None,
    ) -> None:
        from autoresearch_state import read_state
        from trace_sdk import current_hypothesis_id, get_run_id

        state = read_state(state_path)
        raw_job = state.get("job")
        try:
            job_id = int(raw_job)
        except (TypeError, ValueError):
            job_id = 0
        resolved_hypothesis_id = hypothesis_id or current_hypothesis_id() or thesis_id
        round_id = research_round_id_or_empty(job_id, round_number)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_rounds (
                    research_round_id, job_id, round_number, run_id, hypothesis_id,
                    selected_thesis_id, outcome, created_at_utc, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    round_id,
                    job_id,
                    round_number,
                    get_run_id(),
                    resolved_hypothesis_id,
                    thesis_id,
                    outcome,
                    _iso8601_utc_now(),
                    json_dumps_strict(usage or {}),
                ),
            )
            conn.commit()

    def list_research_rounds(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT research_round_id, job_id, round_number, run_id, hypothesis_id,
                       selected_thesis_id, outcome, created_at_utc, usage_json
                FROM research_rounds ORDER BY rowid
                """).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                usage_json = json.loads(row["usage_json"])
                invalid_usage_json = False
            except Exception:
                usage_json = {}
                invalid_usage_json = True
            payload = {
                "research_round_id": row["research_round_id"],
                "job_id": row["job_id"],
                "round_number": row["round_number"],
                "run_id": row["run_id"],
                "hypothesis_id": row["hypothesis_id"],
                "selected_thesis_id": row["selected_thesis_id"],
                "outcome": row["outcome"],
                "created_at_utc": row["created_at_utc"],
                "usage_json": usage_json,
            }
            if invalid_usage_json:
                payload["invalid_usage_json"] = True
            result.append(payload)
        return result

    def list_research_thesis_attempts(
        self, *, job_id: int | None = None, thesis_id: str | None = None
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if job_id is not None:
            where.append("r.job_id = ?")
            params.append(job_id)
        if thesis_id:
            where.append("a.thesis_id = ?")
            params.append(thesis_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.thesis_attempt_id, a.research_round_id, a.attempt_number, a.thesis_id,
                       a.strategy_family, a.config_changes_json, a.validator_status,
                       a.mechanism_dimension, a.hypothesis, a.mechanism,
                       a.thesis_details_json, a.validation_failure_reason, a.selected_for_execution,
                       a.created_at_utc, r.job_id, r.round_number,
                       r.outcome AS round_outcome, r.run_id, r.hypothesis_id,
                       r.usage_json AS round_usage_json
                FROM research_thesis_attempts a
                LEFT JOIN research_rounds r
                  ON r.research_round_id = a.research_round_id
                {where_sql}
                ORDER BY COALESCE(r.job_id, 0) DESC,
                         COALESCE(r.round_number, 0) DESC,
                         a.created_at_utc DESC,
                         a.attempt_number DESC
                """,
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                config_changes = json.loads(row["config_changes_json"])
            except Exception:
                config_changes = None
            try:
                round_usage = json.loads(row["round_usage_json"] or "{}")
            except Exception:
                round_usage = {}
            try:
                thesis_details = json.loads(row["thesis_details_json"] or "{}")
            except Exception:
                thesis_details = {}
            record = {
                "thesis_attempt_id": row["thesis_attempt_id"],
                "research_round_id": row["research_round_id"],
                "attempt_number": row["attempt_number"],
                "thesis_id": row["thesis_id"],
                "strategy_family": row["strategy_family"],
                "config_changes": config_changes,
                "validator_status": row["validator_status"],
                "mechanism_dimension": row["mechanism_dimension"],
                "hypothesis": row["hypothesis"],
                "mechanism": row["mechanism"],
                "thesis_details": thesis_details if isinstance(thesis_details, dict) else {},
                "validation_failure_reason": row["validation_failure_reason"],
                "selected_for_execution": row["selected_for_execution"],
                "created_at_utc": row["created_at_utc"],
                "job_id": row["job_id"],
                "round_number": row["round_number"],
                "round_outcome": row["round_outcome"],
                "run_id": row["run_id"],
                "hypothesis_id": row["hypothesis_id"],
                "round_usage": round_usage,
            }
            if not record["thesis_id"] or not isinstance(config_changes, dict):
                result.append({"_invalid": True, **record})
                continue
            result.append(record)
        return result

    def next_research_thesis_attempt_number(self, research_round_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(attempt_number), 0) + 1 AS next_attempt_number
                FROM research_thesis_attempts
                WHERE research_round_id = ?
                """,
                (research_round_id,),
            ).fetchone()
        return int(row["next_attempt_number"])

    def add_research_thesis_attempt(self, row: dict[str, Any]) -> None:
        research_round_id = row["research_round_id"]
        attempt_number = int(row["attempt_number"])
        thesis_id = row.get("thesis_id") or research_thesis_attempt_id(
            research_round_id, attempt_number
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_thesis_attempts (
                    thesis_attempt_id, research_round_id, attempt_number, thesis_id, strategy_family,
                    config_changes_json, validator_status, mechanism_dimension,
                    hypothesis, mechanism, thesis_details_json, validation_failure_reason, selected_for_execution,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get(
                        "thesis_attempt_id",
                        research_thesis_attempt_id(research_round_id, attempt_number),
                    ),
                    research_round_id,
                    attempt_number,
                    thesis_id,
                    row.get("strategy_family", ""),
                    json_dumps_strict(row.get("config_changes", {})),
                    row.get("validator_status", ""),
                    row.get("mechanism_dimension", ""),
                    row.get("hypothesis", ""),
                    row.get("mechanism", ""),
                    json_dumps_strict(row.get("thesis_details", {})),
                    row.get("validation_failure_reason", ""),
                    int(row.get("selected_for_execution", 0)),
                    row.get("created_at_utc", _iso8601_utc_now()),
                ),
            )
            conn.commit()

    def seed_research_thesis_attempts_rows(self, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM research_thesis_attempts")
            for row in rows:
                invalid = not isinstance(row, dict) or "thesis_id" not in row
                r = row if isinstance(row, dict) else {}
                thesis_id = "" if invalid else r.get("thesis_id", "")
                research_round_id = r.get("research_round_id", "")
                attempt_number = int(r.get("attempt_number", 0))
                config_changes = (
                    json_dumps_strict(row)
                    if invalid
                    else json_dumps_strict(r.get("config_changes", {}))
                )
                selected = 0 if invalid else int(r.get("selected_for_execution", 0))
                conn.execute(
                    """
                    INSERT INTO research_thesis_attempts (
                        thesis_attempt_id, research_round_id, attempt_number, thesis_id, strategy_family,
                        config_changes_json, validator_status, mechanism_dimension,
                        hypothesis, mechanism, thesis_details_json, validation_failure_reason, selected_for_execution,
                        created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.get(
                            "thesis_attempt_id",
                            research_thesis_attempt_id(research_round_id, attempt_number),
                        ),
                        research_round_id,
                        attempt_number,
                        thesis_id,
                        r.get("strategy_family", ""),
                        config_changes,
                        r.get("validator_status", ""),
                        r.get("mechanism_dimension", ""),
                        r.get("hypothesis", ""),
                        r.get("mechanism", ""),
                        json_dumps_strict(r.get("thesis_details", {})),
                        r.get("validation_failure_reason", ""),
                        selected,
                        r.get("created_at_utc", _iso8601_utc_now()),
                    ),
                )
            conn.commit()

    def export_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        meta = self.session_meta()
        if meta:
            entries.append(
                {
                    "type": "config",
                    "name": meta.get("name"),
                    "metricName": meta.get("metricName"),
                    "bestDirection": meta.get("bestDirection"),
                }
            )
        entries.extend(_research_round_to_entry(row) for row in self.list_research_rounds())
        primary_metric_name = self.primary_metric_name()
        entries.extend(
            _record_to_entry(record, idx + 1, primary_metric_name)
            for idx, record in enumerate(self.all())
        )
        return entries

    def import_entries(self, entries: list[dict[str, Any]]) -> None:
        records = []
        for entry in entries:
            record = _entry_to_record(entry)
            if record is not None:
                records.append(record)
        self._records = records
        self._save()

    def read_results(self) -> list[Any]:
        from autoresearch_state import BacktestResultRecord

        primary_metric_name = self.primary_metric_name()
        results: list[BacktestResultRecord] = []
        for record in self.all():
            metric = record.validation_metrics.get(primary_metric_name)
            if metric is None:
                metric = record.train_metrics.get(primary_metric_name, 0.0)
            artifact_dir = _artifact_dir_from_files(
                (
                    record.trades_file,
                    record.strategy_events_file,
                    record.diagnostics_file,
                )
            )
            results.append(
                BacktestResultRecord(
                    config=record.config_path,
                    metric=_coerce_metric_float(metric),
                    status="keep" if record.accepted else "discard",
                    description=f"strict-native loop: {Path(record.config_path).stem}",
                    timestamp=record.timestamp or "1970-01-01T00:00:00+00:00",
                    asi={
                        **dict(getattr(record, "_asi_export", {}) or {}),
                        "config": record.config_path,
                        "thesis_id": record.thesis_id,
                        "research_round_id": record.research_round_id,
                        "research_round_number": record.research_round_number,
                        "backtest_run_id": record.backtest_run_id or record.run_id,
                        "artifact_dir": artifact_dir,
                        "trade_analysis": {
                            **dict(record.validation_metrics),
                            "verdict": {
                                "status": record.verdict_status,
                                "summary": record.verdict_summary,
                            },
                        },
                        "trades_file": record.trades_file,
                        "strategy_events_file": record.strategy_events_file,
                        "diagnostics_file": record.diagnostics_file,
                        "strategy_diagnostics": record.strategy_diagnostics,
                    },
                    job=record.job,
                )
            )
        return results

    def reload(self) -> None:
        """Invalidate the in-memory cache so the next call re-reads from SQLite."""
        self._records = None

    def _load(self) -> list[BacktestRunRecord]:
        if self._records is not None:
            return self._records
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT run_id, backtest_run_id, research_round_id, research_round_number,
                       is_baseline, thesis_id, config_path, runtime_config_json, code_commit,
                       data_hash, train_metrics_json, validation_metrics_json, trade_count,
                       trades_file, strategy_events_file, diagnostics_file,
                       strategy_diagnostics_json, accepted, rejection_reason, verdict_status,
                       verdict_summary, parent_backtest_run_id, timestamp, family, hypothesis,
                       mechanism, job, usage_json, asi_json, description, created_at_utc,
                       primary_metric_name, primary_metric_value, metrics_json,
                       trade_analysis_json
                FROM backtest_runs
                """).fetchall()
        self._records = []
        for row in rows:
            record = BacktestRunRecord(
                run_id=row["run_id"],
                thesis_id=row["thesis_id"],
                config_path=row["config_path"],
                runtime_config=_load_json_object(row["runtime_config_json"]),
                code_commit=row["code_commit"],
                data_hash=row["data_hash"],
                train_metrics=_load_metric_json(row["train_metrics_json"]),
                validation_metrics=_load_metric_json(row["validation_metrics_json"]),
                trade_count=row["trade_count"],
                trades_file=row["trades_file"],
                strategy_events_file=row["strategy_events_file"],
                diagnostics_file=row["diagnostics_file"],
                strategy_diagnostics=_load_json_object(row["strategy_diagnostics_json"]),
                accepted=bool(row["accepted"]),
                rejection_reason=row["rejection_reason"],
                verdict_status=row["verdict_status"],
                verdict_summary=row["verdict_summary"],
                parent_backtest_run_id=row["parent_backtest_run_id"],
                timestamp=coerce_timestamp_to_iso8601_utc(row["timestamp"])
                or (
                    coerce_timestamp_to_iso8601_utc(int(row["timestamp"]))
                    if isinstance(row["timestamp"], str) and row["timestamp"].isdigit()
                    else ""
                ),
                family=row["family"],
                hypothesis=row["hypothesis"],
                mechanism=row["mechanism"],
                job=row["job"],
                usage=_load_json_object(row["usage_json"]),
                backtest_run_id=row["backtest_run_id"],
                research_round_id=row["research_round_id"],
                research_round_number=row["research_round_number"],
                is_baseline=bool(row["is_baseline"]),
                created_at_utc=_coerce_created_at_utc(row["created_at_utc"], row["timestamp"]),
                primary_metric_name=row["primary_metric_name"],
                primary_metric_value=row["primary_metric_value"],
                metrics=_load_metric_json(row["metrics_json"]),
                trade_analysis=_load_json_object(row["trade_analysis_json"]),
            )
            setattr(record, "_asi_export", _load_json_object(row["asi_json"]))
            setattr(record, "_description_export", row["description"])
            self._records.append(record)
        return self._records

    def _save(self) -> None:
        records = self._load()
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {BACKTEST_RUNS_TABLE}")
            for r in records:
                self._write_record(conn, r)
            conn.commit()

    def add(self, result: BacktestRunRecord) -> None:
        """Add a backtest-run result. Deduplicates by run_id."""
        records = self._load()
        export_asi = getattr(result, "_asi_export", None)
        export_description = getattr(result, "_description_export", None)
        # Replace if same run_id exists (re-run)
        records = [r for r in records if r.run_id != result.run_id]
        records.append(result)
        if export_asi is not None:
            setattr(records[-1], "_asi_export", export_asi)
        if export_description is not None:
            setattr(records[-1], "_description_export", export_description)
        self._records = records
        with self._connect() as conn:
            self._write_record(conn, records[-1])
            conn.commit()

    def get(self, run_id: str) -> BacktestRunRecord | None:
        for r in self._load():
            if r.run_id == run_id:
                return r
        return None

    def get_by_research_round_id(self, research_round_id: str) -> BacktestRunRecord | None:
        if not research_round_id:
            return None
        for r in self._load():
            if r.research_round_id == research_round_id:
                return r
        return None

    def all(self) -> list[BacktestRunRecord]:
        return list(self._load())

    def latest(self, n: int = 1) -> list[BacktestRunRecord]:
        records = self._load()
        # Sort by epoch-ms equivalent so a mixed-format DB (legacy int
        # rows that have not been re-saved yet) still orders correctly.
        return sorted(
            records, key=lambda r: coerce_timestamp_to_epoch_ms(r.timestamp), reverse=True
        )[:n]

    def max_job_id(self) -> int:
        with self._connect() as conn:
            experiment_row = conn.execute(
                f"SELECT COALESCE(MAX(job), 0) FROM {BACKTEST_RUNS_TABLE}"
            ).fetchone()
            research_row = conn.execute(
                "SELECT COALESCE(MAX(job_id), 0) FROM research_rounds"
            ).fetchone()
        experiment_job = int(experiment_row[0] or 0) if experiment_row else 0
        research_job = int(research_row[0] or 0) if research_row else 0
        return max(experiment_job, research_job)

    def accepted_backtest_runs(self) -> list[BacktestRunRecord]:
        return [r for r in self._load() if r.accepted]

    def best_by_metric(self, metric: str) -> BacktestRunRecord | None:
        records = [r for r in self._load() if is_metric_rankable_backtest_run(r)]
        direction = self.best_direction()
        best = None
        best_candidate = 0.0
        for r in records:
            val = r.validation_metrics.get(metric)
            if val is None:
                val = r.train_metrics.get(metric)
            if val is None:
                continue
            try:
                candidate = float(val)
            except (TypeError, ValueError):
                log.warning(
                    "best_by_metric: skipping run %s — metric %r value %r is not numeric",
                    r.run_id,
                    metric,
                    val,
                )
                continue
            if best is None:
                best = r
                best_candidate = candidate
                continue
            if direction == "higher" and candidate > best_candidate:
                best = r
                best_candidate = candidate
            elif direction != "higher" and candidate < best_candidate:
                best = r
                best_candidate = candidate
        return best

    def format_for_conductor(self) -> str:
        """Format all results as a structured table for the conductor."""
        records = self._load()
        if not records:
            return "No backtest runs in database yet."
        primary_metric_name = self.primary_metric_name()
        lines: list[str] = []
        for r in records:
            m = {**r.train_metrics, **r.validation_metrics}
            metric_value = _metric_value_for_record(r, primary_metric_name)
            parts = [
                f"metric={metric_value if metric_value is not None else '?'}",
                f"status={'accepted' if r.accepted else 'rejected'}",
            ]
            if m.get("trade_count") is not None:
                parts.append(f"trades={m['trade_count']}")
            if m.get("profit_factor") is not None:
                parts.append(f"PF={m['profit_factor']}")
            if m.get("max_drawdown") is not None:
                parts.append(f"maxDD={m['max_drawdown']}")
            if r.verdict_status and r.verdict_status != "none":
                parts.append(f"verdict={r.verdict_status}")
            if r.rejection_reason:
                parts.append(f"why={r.rejection_reason[:80]}")
            sd = r.strategy_diagnostics
            if sd and sd.get("rejection_breakdown"):
                rb = sd["rejection_breakdown"]
                top = sorted(rb.items(), key=lambda x: x[1], reverse=True)[:2]
                parts.append(f"top_rejections={dict(top)}")
            lines.append(f"  - {r.thesis_id} ({r.run_id[:8]}): {' | '.join(parts)}")
            if r.parent_backtest_run_id:
                lines.append(f"    parent: {r.parent_backtest_run_id[:8]}")
        return "\n".join(lines)

    def count(self) -> int:
        return len(self._load())


# ---------------------------------------------------------------------------
# Baseline checkpoint — detect environment drift
# ---------------------------------------------------------------------------


@dataclass
class BaselineCheckpoint:
    """Snapshot of baseline metrics at a point in time."""

    code_commit: str
    data_hash: str
    config_hash: str
    metrics: dict[str, Any]
    # ISO-8601 UTC string. Legacy checkpoint files with int epoch-ms
    # timestamps are coerced to ISO on load (see BaselineTracker._load).
    timestamp: str = ""
    round_number: int = 0


class BaselineTracker:
    """Track baseline metrics across rounds to detect environment drift."""

    def __init__(
        self, path: Path, *, db_path: Path | None = None, strategy_family: str = ""
    ) -> None:
        self.path = path
        self.db_path = db_path
        self.strategy_family = strategy_family
        self._checkpoints: list[BaselineCheckpoint] | None = None

    def _load(self) -> list[BaselineCheckpoint]:
        if self._checkpoints is not None:
            return self._checkpoints
        if not self.path.exists():
            self._checkpoints = []
            return self._checkpoints
        text = self.path.read_text().strip()
        if not text:
            self._checkpoints = []
            return self._checkpoints
        raw = json_loads_metric_sentinels(text)
        # Rule J back-compat: pre-migration checkpoint files have int
        # epoch-ms timestamps; coerce to ISO-8601 UTC on load.
        for row in raw:
            if "metrics" in row and isinstance(row["metrics"], dict):
                row["metrics"] = json_loads_metric_sentinels(json.dumps(row["metrics"]))
            if "timestamp" in row:
                row["timestamp"] = coerce_timestamp_to_iso8601_utc(row["timestamp"]) or ""
        self._checkpoints = [BaselineCheckpoint(**c) for c in raw]
        return self._checkpoints

    def _save(self) -> None:
        checkpoints = self._load()
        write_text_atomic(self.path, json_dumps_strict([asdict(c) for c in checkpoints]) + "\n")

    def _checkpoint_id(self, checkpoint: BaselineCheckpoint) -> str:
        family = self.strategy_family or _strategy_family_from_path(self.db_path)
        return _config_hash(
            {
                "family": family,
                "code_commit": checkpoint.code_commit,
                "data_hash": checkpoint.data_hash,
                "config_hash": checkpoint.config_hash,
                "timestamp": checkpoint.timestamp,
                "round_number": checkpoint.round_number,
            }
        )

    def _record_sqlite(self, checkpoint: BaselineCheckpoint) -> None:
        if self.db_path is None:
            return
        family = self.strategy_family or _strategy_family_from_path(self.db_path)
        db = BacktestRunDB(self.db_path)
        if not family:
            family = db.session_meta().get("name", "")
        if not family:
            raise ValueError("BaselineTracker requires strategy_family when db_path is configured")
        created_at_utc = coerce_timestamp_to_iso8601_utc(checkpoint.timestamp) or _iso8601_utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO baseline_checkpoints (
                    checkpoint_id, strategy_family, code_commit, data_hash, config_hash,
                    metrics_json, created_at_utc, round_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._checkpoint_id(checkpoint),
                    family,
                    checkpoint.code_commit,
                    checkpoint.data_hash,
                    checkpoint.config_hash,
                    json_dumps_strict(checkpoint.metrics),
                    created_at_utc,
                    checkpoint.round_number,
                ),
            )
            conn.commit()

    def record(self, checkpoint: BaselineCheckpoint) -> None:
        self._record_sqlite(checkpoint)
        checkpoints = self._load()
        checkpoints.append(checkpoint)
        self._checkpoints = checkpoints
        self._save()

    def latest(self) -> BaselineCheckpoint | None:
        checkpoints = self._load()
        return checkpoints[-1] if checkpoints else None

    def check_drift(
        self, current: BaselineCheckpoint, tolerance_pct: float = 5.0
    ) -> dict[str, Any]:
        """Compare current baseline against the last checkpoint.

        Returns {"drifted": bool, "details": [...]} with per-metric drift info.
        """
        prev = self.latest()
        if prev is None:
            return {"drifted": False, "details": [], "reason": "first_checkpoint"}

        details: list[dict[str, Any]] = []
        drifted = False

        # Check code/data/config changes
        if current.code_commit != prev.code_commit:
            details.append(
                {
                    "field": "code_commit",
                    "previous": prev.code_commit,
                    "current": current.code_commit,
                    "severity": "info",
                }
            )
        if current.data_hash != prev.data_hash:
            details.append(
                {
                    "field": "data_hash",
                    "previous": prev.data_hash,
                    "current": current.data_hash,
                    "severity": "critical",
                }
            )
            drifted = True
        if current.config_hash != prev.config_hash:
            details.append(
                {
                    "field": "config_hash",
                    "previous": prev.config_hash,
                    "current": current.config_hash,
                    "severity": "critical",
                }
            )
            drifted = True

        # Check metric drift
        for metric, cur_val in current.metrics.items():
            prev_val = prev.metrics.get(metric)
            if prev_val is None or cur_val is None:
                continue
            try:
                prev_val = float(prev_val)
                cur_val = float(cur_val)
            except (TypeError, ValueError) as exc:
                drifted = True
                details.append(
                    {
                        "field": metric,
                        "previous": prev_val,
                        "current": cur_val,
                        "drifted": True,
                        "severity": "critical",
                        "error": f"metric_not_numeric: {exc}",
                    }
                )
                log.error(
                    "BASELINE_DRIFT_METRIC_INVALID metric=%s previous=%r current=%r error=%s "
                    "| hint=baseline drift cannot compare this metric; fix the stored metric type",
                    metric,
                    prev_val,
                    cur_val,
                    exc,
                )
                continue
            if prev_val == 0:
                pct_change = 100.0 if cur_val != 0 else 0.0
            else:
                pct_change = abs(cur_val - prev_val) / abs(prev_val) * 100
            metric_drifted = pct_change > tolerance_pct
            if metric_drifted:
                drifted = True
            details.append(
                {
                    "field": metric,
                    "previous": prev_val,
                    "current": cur_val,
                    "pct_change": round(pct_change, 2),
                    "drifted": metric_drifted,
                    "severity": "critical" if metric_drifted else "ok",
                }
            )

        return {"drifted": drifted, "details": details}

    def all_checkpoints(self) -> list[BaselineCheckpoint]:
        return list(self._load())


def build_config_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of the full runtime config."""
    return _config_hash(config)


def build_data_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of data-affecting config fields."""
    data_keys = [
        "data_universe",
        "data_provenance",
        "symbols",
        "validation_start",
        "validation_end",
    ]
    data_fields = {k: config.get(k) for k in data_keys if config.get(k) is not None}
    return _config_hash(data_fields)


def _entry_to_record(entry: dict[str, Any]) -> BacktestRunRecord | None:
    if entry.get("type") in ("config", "research_round"):
        return None
    asi = entry.get("asi") or {}
    metrics = json_loads_metric_sentinels(json.dumps(entry.get("metrics") or {}))
    primary_metric_name = entry.get("primary_metric_name") or metrics.get(
        "primary_metric_name", "profit_factor"
    )
    if primary_metric_name not in metrics and entry.get("metric") is not None:
        metrics[primary_metric_name] = entry.get("metric")
    trade_analysis = asi.get("trade_analysis") or {}
    for k, v in trade_analysis.items():
        if k not in metrics and isinstance(v, (int, float)):
            metrics[k] = v
    primary_metric_value = entry.get("metric")
    if primary_metric_value is None:
        primary_metric_value = metrics.get(primary_metric_name)
    record = BacktestRunRecord(
        run_id=entry.get("run_id", ""),
        thesis_id=asi.get("thesis_id") or Path(asi.get("config", "")).stem,
        config_path=asi.get("config", ""),
        runtime_config={},
        code_commit=entry.get("commit", ""),
        data_hash="",
        train_metrics={},
        validation_metrics=metrics,
        trade_count=int(metrics.get("trade_count", 0) or 0),
        trades_file=metrics.get("trades_file", ""),
        strategy_events_file=metrics.get("strategy_events_file", ""),
        diagnostics_file=metrics.get("diagnostics_file", ""),
        strategy_diagnostics=metrics.get("strategy_diagnostics", {}),
        accepted=entry.get("status") == "keep",
        rejection_reason="",
        verdict_status=metrics.get("verdict_status", "none"),
        verdict_summary=metrics.get("verdict_summary", ""),
        timestamp=coerce_timestamp_to_iso8601_utc(entry.get("timestamp")) or _iso8601_utc_now(),
        family=entry.get("family", ""),
        hypothesis=entry.get("hypothesis", ""),
        mechanism=entry.get("mechanism", ""),
        job=entry.get("job", 0),
        usage=entry.get("usage", {}),
        backtest_run_id=entry.get("backtest_run_id") or entry.get("run_id", ""),
        research_round_id=entry.get("research_round_id", ""),
        research_round_number=int(entry.get("research_round_number", -1) or -1),
        is_baseline=bool(entry.get("is_baseline", False)),
        created_at_utc=coerce_timestamp_to_iso8601_utc(entry.get("created_at_utc"))
        or coerce_timestamp_to_iso8601_utc(entry.get("timestamp"))
        or _iso8601_utc_now(),
        primary_metric_name=str(primary_metric_name),
        primary_metric_value=_coerce_metric_float(primary_metric_value),
        metrics=metrics,
        trade_analysis=trade_analysis if isinstance(trade_analysis, dict) else {},
    )
    setattr(record, "_asi_export", asi)
    setattr(record, "_description_export", entry.get("description", ""))
    return record


def _record_to_entry(
    record: BacktestRunRecord, run: int, primary_metric_name: str
) -> dict[str, Any]:
    primary_metric_name = record.primary_metric_name or primary_metric_name
    primary_metric_value = _canonical_primary_metric_value(record, primary_metric_name)
    metrics = _canonical_metrics_for_record(record, primary_metric_name)
    asi = getattr(record, "_asi_export", None) or {
        "config": record.config_path,
        "thesis_id": record.thesis_id,
        "trade_analysis": _canonical_trade_analysis_for_record(record),
    }
    if isinstance(asi, dict):
        asi.setdefault("trade_analysis", _canonical_trade_analysis_for_record(record))
    return {
        "type": "backtest_run",
        "run": run,
        "job": record.job,
        "run_id": record.run_id,
        "backtest_run_id": record.backtest_run_id or record.run_id,
        "research_round_id": record.research_round_id,
        "research_round_number": record.research_round_number,
        "is_baseline": record.is_baseline,
        "commit": record.code_commit,
        "metric": _coerce_metric_float(primary_metric_value),
        "primary_metric_name": primary_metric_name,
        "metrics": metrics,
        "created_at_utc": record.created_at_utc or record.timestamp,
        "status": "keep" if record.accepted else "discard",
        "description": getattr(
            record, "_description_export", f"strict-native loop: {Path(record.config_path).stem}"
        ),
        "timestamp": record.timestamp,
        "hypothesis": record.hypothesis,
        "mechanism": record.mechanism,
        "asi": asi,
    }


def _research_round_to_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "research_round",
        "job": row.get("job_id"),
        "round": row.get("round_number"),
        "run_id": row.get("run_id"),
        "hypothesis_id": row.get("hypothesis_id"),
        "thesis_id": row.get("selected_thesis_id"),
        "outcome": row.get("outcome"),
        "usage": row.get("usage_json", {}),
        "timestamp": row.get("created_at_utc"),
    }
