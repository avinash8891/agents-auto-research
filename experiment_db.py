"""Experiment result database — canonical sqlite-backed storage."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoresearch_state import coerce_timestamp_to_epoch_ms, coerce_timestamp_to_iso8601_utc

log = logging.getLogger(__name__)


def _iso8601_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ExperimentResult:
    """One complete experiment record."""

    experiment_id: str
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

    parent_experiment_id: str = ""
    # ISO-8601 UTC string. Legacy DB files with int epoch-ms timestamps
    # are coerced to ISO on load (see ExperimentDB._load).
    timestamp: str = ""
    family: str = ""
    hypothesis: str = ""
    mechanism: str = ""
    job: int = 0
    usage: dict[str, Any] = field(default_factory=dict)


class ExperimentDB:
    """Canonical experiment database backed by sqlite3."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: list[ExperimentResult] | None = None
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
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
                    parent_experiment_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    family TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    mechanism TEXT NOT NULL,
                    job INTEGER NOT NULL,
                    usage_json TEXT NOT NULL,
                    asi_json TEXT NOT NULL DEFAULT '{}',
                    description TEXT NOT NULL DEFAULT ''
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
                    research_round_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    thesis_id TEXT NOT NULL,
                    strategy_family TEXT NOT NULL,
                    config_changes_json TEXT NOT NULL,
                    validator_status TEXT NOT NULL,
                    mechanism_dimension TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    mechanism TEXT NOT NULL,
                    rejection_reason TEXT NOT NULL,
                    selected_for_execution INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY (research_round_id, attempt_number)
                )
                """)
            conn.commit()

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
        return self.session_meta().get("metricName", "median_expectancy")

    def best_direction(self) -> str:
        return self.session_meta().get("bestDirection", "higher")

    def evaluate_metric(self, metric: float) -> str:
        direction = self.best_direction()
        records = self.all()
        if not records:
            return "keep"
        kept = [r for r in records if r.accepted]
        compare_against = None
        if kept:
            compare_against = kept[0].validation_metrics.get(self.primary_metric_name(), 0.0)
            for record in kept[1:]:
                candidate = record.validation_metrics.get(self.primary_metric_name(), 0.0)
                if direction == "higher" and candidate > compare_against:
                    compare_against = candidate
                if direction == "lower" and candidate < compare_against:
                    compare_against = candidate
        else:
            compare_against = records[0].validation_metrics.get(self.primary_metric_name(), 0.0)
        improved = metric > compare_against if direction == "higher" else metric < compare_against
        return "keep" if improved else "discard"

    def add_from_sqlite_fields(
        self,
        *,
        experiment_id: str,
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
        run_id: str,
        primary_metric_name: str,
        primary_metric_value: float,
    ) -> None:
        merged_metrics = dict(metrics)
        merged_metrics.setdefault(primary_metric_name, primary_metric_value)
        if trade_analysis:
            for key, value in trade_analysis.items():
                if key not in merged_metrics and isinstance(value, (int, float)):
                    merged_metrics[key] = value
        self.add(
            ExperimentResult(
                experiment_id=experiment_id,
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
                timestamp=_iso8601_utc_now(),
                family=family,
                job=job_id,
            )
        )

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
        from trace_logger import current_hypothesis_id, get_run_id

        state = read_state(state_path)
        resolved_hypothesis_id = hypothesis_id or current_hypothesis_id() or thesis_id
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_rounds (
                    research_round_id, job_id, round_number, run_id, hypothesis_id,
                    selected_thesis_id, outcome, created_at_utc, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"job-{state.get('job', 0)}-round-{round_number}",
                    state.get("job"),
                    round_number,
                    get_run_id(),
                    resolved_hypothesis_id,
                    thesis_id,
                    outcome,
                    _iso8601_utc_now(),
                    json.dumps(usage or {}),
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
        return [
            {
                "research_round_id": row["research_round_id"],
                "job_id": row["job_id"],
                "round_number": row["round_number"],
                "run_id": row["run_id"],
                "hypothesis_id": row["hypothesis_id"],
                "selected_thesis_id": row["selected_thesis_id"],
                "outcome": row["outcome"],
                "created_at_utc": row["created_at_utc"],
                "usage_json": json.loads(row["usage_json"]),
            }
            for row in rows
        ]

    def list_research_thesis_attempts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT research_round_id, attempt_number, thesis_id, strategy_family,
                       config_changes_json, validator_status, mechanism_dimension,
                       hypothesis, mechanism, rejection_reason, selected_for_execution,
                       created_at_utc
                FROM research_thesis_attempts ORDER BY research_round_id, attempt_number
                """).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                config_changes = json.loads(row["config_changes_json"])
            except Exception:
                config_changes = None
            record = {
                "research_round_id": row["research_round_id"],
                "attempt_number": row["attempt_number"],
                "thesis_id": row["thesis_id"],
                "strategy_family": row["strategy_family"],
                "config_changes": config_changes,
                "validator_status": row["validator_status"],
                "mechanism_dimension": row["mechanism_dimension"],
                "hypothesis": row["hypothesis"],
                "mechanism": row["mechanism"],
                "rejection_reason": row["rejection_reason"],
                "selected_for_execution": row["selected_for_execution"],
                "created_at_utc": row["created_at_utc"],
            }
            if not record["thesis_id"] or not isinstance(config_changes, dict):
                result.append({"_invalid": True, **record})
                continue
            result.append(record)
        return result

    def add_research_thesis_attempt(self, row: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_thesis_attempts (
                    research_round_id, attempt_number, thesis_id, strategy_family,
                    config_changes_json, validator_status, mechanism_dimension,
                    hypothesis, mechanism, rejection_reason, selected_for_execution,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["research_round_id"],
                    row["attempt_number"],
                    row["thesis_id"],
                    row.get("strategy_family", ""),
                    json.dumps(row.get("config_changes", {})),
                    row.get("validator_status", ""),
                    row.get("mechanism_dimension", ""),
                    row.get("hypothesis", ""),
                    row.get("mechanism", ""),
                    row.get("rejection_reason", ""),
                    int(row.get("selected_for_execution", 0)),
                    row.get("created_at_utc", _iso8601_utc_now()),
                ),
            )
            conn.commit()

    def seed_research_thesis_attempts_rows(self, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM research_thesis_attempts")
            for row in rows:
                if not isinstance(row, dict) or "thesis_id" not in row:
                    conn.execute(
                        """
                        INSERT INTO research_thesis_attempts (
                            research_round_id, attempt_number, thesis_id, strategy_family,
                            config_changes_json, validator_status, mechanism_dimension,
                            hypothesis, mechanism, rejection_reason, selected_for_execution,
                            created_at_utc
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row.get("research_round_id", "") if isinstance(row, dict) else "",
                            row.get("attempt_number", 0) if isinstance(row, dict) else 0,
                            "",
                            row.get("strategy_family", "") if isinstance(row, dict) else "",
                            json.dumps(row),
                            row.get("validator_status", "") if isinstance(row, dict) else "",
                            row.get("mechanism_dimension", "") if isinstance(row, dict) else "",
                            row.get("hypothesis", "") if isinstance(row, dict) else "",
                            row.get("mechanism", "") if isinstance(row, dict) else "",
                            row.get("rejection_reason", "") if isinstance(row, dict) else "",
                            0,
                            (
                                row.get("created_at_utc", _iso8601_utc_now())
                                if isinstance(row, dict)
                                else _iso8601_utc_now()
                            ),
                        ),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO research_thesis_attempts (
                        research_round_id, attempt_number, thesis_id, strategy_family,
                        config_changes_json, validator_status, mechanism_dimension,
                        hypothesis, mechanism, rejection_reason, selected_for_execution,
                        created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.get("research_round_id", ""),
                        row.get("attempt_number", 0),
                        row.get("thesis_id", ""),
                        row.get("strategy_family", ""),
                        json.dumps(row.get("config_changes", {})),
                        row.get("validator_status", ""),
                        row.get("mechanism_dimension", ""),
                        row.get("hypothesis", ""),
                        row.get("mechanism", ""),
                        row.get("rejection_reason", ""),
                        int(row.get("selected_for_execution", 0)),
                        row.get("created_at_utc", _iso8601_utc_now()),
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
        entries.extend(_record_to_entry(record, idx + 1) for idx, record in enumerate(self.all()))
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
        from autoresearch_state import ExperimentRecord

        results: list[ExperimentRecord] = []
        for record in self.all():
            metric = record.validation_metrics.get("median_expectancy")
            if metric is None:
                metric = record.train_metrics.get("median_expectancy", 0.0)
            results.append(
                ExperimentRecord(
                    config=record.config_path,
                    metric=float(metric),
                    status="keep" if record.accepted else "discard",
                    description=f"strict-native loop: {Path(record.config_path).stem}",
                    timestamp=record.timestamp or "1970-01-01T00:00:00+00:00",
                    asi={"config": record.config_path, "thesis_id": record.thesis_id},
                )
            )
        return results

    def _load(self) -> list[ExperimentResult]:
        if self._records is not None:
            return self._records
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                       data_hash, train_metrics_json, validation_metrics_json, trade_count,
                       trades_file, strategy_events_file, diagnostics_file,
                       strategy_diagnostics_json, accepted, rejection_reason, verdict_status,
                       verdict_summary, parent_experiment_id, timestamp, family, hypothesis,
                       mechanism, job, usage_json, asi_json, description
                FROM experiments
                """).fetchall()
        self._records = []
        for row in rows:
            record = ExperimentResult(
                experiment_id=row["experiment_id"],
                thesis_id=row["thesis_id"],
                config_path=row["config_path"],
                runtime_config=json.loads(row["runtime_config_json"]),
                code_commit=row["code_commit"],
                data_hash=row["data_hash"],
                train_metrics=json.loads(row["train_metrics_json"]),
                validation_metrics=json.loads(row["validation_metrics_json"]),
                trade_count=row["trade_count"],
                trades_file=row["trades_file"],
                strategy_events_file=row["strategy_events_file"],
                diagnostics_file=row["diagnostics_file"],
                strategy_diagnostics=json.loads(row["strategy_diagnostics_json"]),
                accepted=bool(row["accepted"]),
                rejection_reason=row["rejection_reason"],
                verdict_status=row["verdict_status"],
                verdict_summary=row["verdict_summary"],
                parent_experiment_id=row["parent_experiment_id"],
                timestamp=coerce_timestamp_to_iso8601_utc(
                    row["timestamp"]
                    if not isinstance(row["timestamp"], str) or "T" in row["timestamp"]
                    else int(row["timestamp"])
                )
                or "",
                family=row["family"],
                hypothesis=row["hypothesis"],
                mechanism=row["mechanism"],
                job=row["job"],
                usage=json.loads(row["usage_json"]),
            )
            setattr(record, "_asi_export", json.loads(row["asi_json"]))
            setattr(record, "_description_export", row["description"])
            self._records.append(record)
        return self._records

    def _save(self) -> None:
        records = self._load()
        with self._connect() as conn:
            conn.execute("DELETE FROM experiments")
            for r in records:
                conn.execute(
                    """
                    INSERT INTO experiments (
                        experiment_id, thesis_id, config_path, runtime_config_json, code_commit,
                        data_hash, train_metrics_json, validation_metrics_json, trade_count,
                        trades_file, strategy_events_file, diagnostics_file,
                        strategy_diagnostics_json, accepted, rejection_reason, verdict_status,
                        verdict_summary, parent_experiment_id, timestamp, family, hypothesis,
                        mechanism, job, usage_json, asi_json, description
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        r.experiment_id,
                        r.thesis_id,
                        r.config_path,
                        json.dumps(r.runtime_config),
                        r.code_commit,
                        r.data_hash,
                        json.dumps(r.train_metrics),
                        json.dumps(r.validation_metrics),
                        r.trade_count,
                        r.trades_file,
                        r.strategy_events_file,
                        r.diagnostics_file,
                        json.dumps(r.strategy_diagnostics),
                        int(r.accepted),
                        r.rejection_reason,
                        r.verdict_status,
                        r.verdict_summary,
                        r.parent_experiment_id,
                        r.timestamp,
                        r.family,
                        r.hypothesis,
                        r.mechanism,
                        r.job,
                        json.dumps(r.usage),
                        json.dumps(getattr(r, "_asi_export", {})),
                        getattr(r, "_description_export", ""),
                    ),
                )
            conn.commit()

    def add(self, result: ExperimentResult) -> None:
        """Add an experiment result. Deduplicates by experiment_id."""
        records = self._load()
        export_asi = getattr(result, "_asi_export", None)
        export_description = getattr(result, "_description_export", None)
        # Replace if same experiment_id exists (re-run)
        records = [r for r in records if r.experiment_id != result.experiment_id]
        records.append(result)
        if export_asi is not None:
            setattr(records[-1], "_asi_export", export_asi)
        if export_description is not None:
            setattr(records[-1], "_description_export", export_description)
        self._records = records
        self._save()

    def get(self, experiment_id: str) -> ExperimentResult | None:
        for r in self._load():
            if r.experiment_id == experiment_id:
                return r
        return None

    def get_by_thesis(self, thesis_id: str) -> list[ExperimentResult]:
        return [r for r in self._load() if r.thesis_id == thesis_id]

    def all(self) -> list[ExperimentResult]:
        return list(self._load())

    def latest(self, n: int = 1) -> list[ExperimentResult]:
        records = self._load()
        # Sort by epoch-ms equivalent so a mixed-format DB (legacy int
        # rows that have not been re-saved yet) still orders correctly.
        return sorted(
            records, key=lambda r: coerce_timestamp_to_epoch_ms(r.timestamp), reverse=True
        )[:n]

    def accepted_experiments(self) -> list[ExperimentResult]:
        return [r for r in self._load() if r.accepted]

    def best_by_metric(self, metric: str) -> ExperimentResult | None:
        records = self._load()
        best = None
        for r in records:
            val = r.train_metrics.get(metric) or r.validation_metrics.get(metric)
            if val is None:
                continue
            if best is None:
                best = r
                continue
            best_val = best.train_metrics.get(metric) or best.validation_metrics.get(metric)
            if val > best_val:
                best = r
        return best

    def format_for_conductor(self) -> str:
        """Format all results as a structured table for the conductor."""
        records = self._load()
        if not records:
            return "No experiments in database yet."
        lines: list[str] = []
        for r in records:
            m = r.train_metrics
            parts = [
                f"metric={m.get('median_expectancy', '?')}",
                f"status={'accepted' if r.accepted else 'rejected'}",
            ]
            if m.get("trade_count"):
                parts.append(f"trades={m['trade_count']}")
            if m.get("profit_factor"):
                parts.append(f"PF={m['profit_factor']}")
            if m.get("max_drawdown"):
                parts.append(f"maxDD={m['max_drawdown']}")
            if m.get("avg_sharpe_across_windows"):
                parts.append(f"sharpe={m['avg_sharpe_across_windows']}")
            if r.verdict_status and r.verdict_status != "none":
                parts.append(f"verdict={r.verdict_status}")
            if r.rejection_reason:
                parts.append(f"why={r.rejection_reason[:80]}")
            sd = r.strategy_diagnostics
            if sd and sd.get("rejection_breakdown"):
                rb = sd["rejection_breakdown"]
                top = sorted(rb.items(), key=lambda x: x[1], reverse=True)[:2]
                parts.append(f"top_rejections={dict(top)}")
            lines.append(f"  - {r.thesis_id} ({r.experiment_id[:8]}): {' | '.join(parts)}")
            if r.parent_experiment_id:
                lines.append(f"    parent: {r.parent_experiment_id[:8]}")
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

    def __init__(self, path: Path) -> None:
        self.path = path
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
        raw = json.loads(text)
        # Rule J back-compat: pre-migration checkpoint files have int
        # epoch-ms timestamps; coerce to ISO-8601 UTC on load.
        for row in raw:
            if "timestamp" in row:
                row["timestamp"] = coerce_timestamp_to_iso8601_utc(row["timestamp"]) or ""
        self._checkpoints = [BaselineCheckpoint(**c) for c in raw]
        return self._checkpoints

    def _save(self) -> None:
        checkpoints = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(c) for c in checkpoints], indent=2) + "\n")

    def record(self, checkpoint: BaselineCheckpoint) -> None:
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
    blob = json.dumps(config, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def build_data_hash(config: dict[str, Any]) -> str:
    """Deterministic hash of data-affecting config fields."""
    data_keys = ["data_dir", "symbols", "validation_start", "validation_end"]
    data_fields = {k: config.get(k) for k in data_keys if config.get(k) is not None}
    blob = json.dumps(data_fields, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _entry_to_record(entry: dict[str, Any]) -> ExperimentResult | None:
    if entry.get("type") in ("config", "research_round"):
        return None
    asi = entry.get("asi") or {}
    metrics = dict(entry.get("metrics") or {})
    primary_metric_name = metrics.get("primary_metric_name", "median_expectancy")
    if primary_metric_name not in metrics and entry.get("metric") is not None:
        metrics[primary_metric_name] = entry.get("metric")
    trade_analysis = asi.get("trade_analysis") or {}
    for k, v in trade_analysis.items():
        if k not in metrics and isinstance(v, (int, float)):
            metrics[k] = v
    record = ExperimentResult(
        experiment_id=entry.get("run_id", ""),
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
    )
    setattr(record, "_asi_export", asi)
    setattr(record, "_description_export", entry.get("description", ""))
    return record


def _record_to_entry(record: ExperimentResult, run: int) -> dict[str, Any]:
    primary_metric_name = "median_expectancy"
    primary_metric_value = (
        record.validation_metrics.get(primary_metric_name)
        or record.train_metrics.get(primary_metric_name)
        or 0.0
    )
    asi = getattr(record, "_asi_export", None) or {
        "config": record.config_path,
        "thesis_id": record.thesis_id,
        "trade_analysis": {},
    }
    return {
        "run": run,
        "job": record.job,
        "run_id": record.experiment_id,
        "commit": record.code_commit,
        "metric": primary_metric_value,
        "metrics": dict(record.validation_metrics or record.train_metrics),
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
