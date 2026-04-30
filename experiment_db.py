"""Experiment result database — structured storage for autoresearch results.

Each experiment is a complete record with lineage, config, metrics, evidence
files, and verdict. The conductor queries this instead of loose jsonl blobs.

Storage: single JSON file (experiments_db.json) — append-friendly, human-readable.

Timestamp format (rule J): both ExperimentResult.timestamp and
BaselineCheckpoint.timestamp are ISO-8601 UTC strings (e.g.
"2026-04-29T12:00:00+00:00"). The dataclass fields default to the empty
string. Reads coerce legacy int-epoch-ms values (pre-rule-J files) on
load so existing experiments_db.json files keep working.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autoresearch_state import coerce_timestamp_to_epoch_ms, coerce_timestamp_to_iso8601_utc


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
    """Append-only experiment database backed by a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: list[ExperimentResult] | None = None

    def _load(self) -> list[ExperimentResult]:
        if self._records is not None:
            return self._records
        if not self.path.exists():
            self._records = []
            return self._records
        text = self.path.read_text().strip()
        if not text:
            self._records = []
            return self._records
        raw = json.loads(text)
        # Rule J back-compat: pre-migration DB files have int epoch-ms
        # timestamps; coerce to ISO-8601 UTC strings on load so the
        # in-memory contract is uniform.
        for row in raw:
            if "timestamp" in row:
                row["timestamp"] = coerce_timestamp_to_iso8601_utc(row["timestamp"]) or ""
        self._records = [ExperimentResult(**r) for r in raw]
        return self._records

    def _save(self) -> None:
        records = self._load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(r) for r in records], indent=2) + "\n")

    def add(self, result: ExperimentResult) -> None:
        """Add an experiment result. Deduplicates by experiment_id."""
        records = self._load()
        # Replace if same experiment_id exists (re-run)
        records = [r for r in records if r.experiment_id != result.experiment_id]
        records.append(result)
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
            except (TypeError, ValueError):
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


def backfill_from_jsonl(jsonl_path: Path, db: ExperimentDB) -> int:
    """Import existing jsonl entries into the experiment database.

    Returns number of records added.
    """
    if not jsonl_path.exists():
        return 0
    count = 0
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        if entry.get("type") == "config":
            continue
        asi = entry.get("asi") or {}
        ta = asi.get("trade_analysis", {})
        config = asi.get("config", "")
        commit = entry.get("commit", "unknown")
        metrics = entry.get("metrics", {})
        decision = entry.get("status", "discard")

        # Use run_id from the entry if present (matches _build_db_record's
        # fallback_experiment_id).  Fall back to a content hash only for
        # legacy entries that predate run_id so deduplication via
        # ExperimentDB.add still works on healthy databases.
        exp_id = (
            entry.get("run_id")
            or hashlib.sha256(
                f"{config}:{commit}:{entry.get('timestamp', 0)}".encode()
            ).hexdigest()[:16]
        )

        train_metrics: dict[str, Any] = dict(metrics)
        for k in (
            "trade_count",
            "profit_factor",
            "max_drawdown",
            "pct_profitable_windows",
            "avg_sharpe_across_windows",
            "win_rate",
            "median_expectancy",
        ):
            if ta.get(k) is not None:
                train_metrics[k] = ta[k]
        if "median_expectancy" not in train_metrics:
            train_metrics["median_expectancy"] = entry.get("metric")

        db.add(
            ExperimentResult(
                experiment_id=exp_id,
                thesis_id=asi.get("thesis_id") or (Path(config).stem if config else "unknown"),
                config_path=config,
                runtime_config={},  # not available in legacy entries
                code_commit=commit,
                data_hash="",
                train_metrics=train_metrics,
                validation_metrics={},
                trade_count=ta.get("trade_count", 0),
                trades_file="",
                strategy_events_file="",
                diagnostics_file="",
                strategy_diagnostics={},
                accepted=decision == "keep",
                rejection_reason=ta.get("why", "") if decision != "keep" else "",
                verdict_status="none",
                verdict_summary="",
                timestamp=coerce_timestamp_to_iso8601_utc(entry.get("timestamp", 0)) or "",
                family=entry.get("family", "unknown"),
                hypothesis=asi.get("hypothesis", ""),
                mechanism="",
            )
        )
        count += 1
    return count
