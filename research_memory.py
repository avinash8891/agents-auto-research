from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from autoresearch_logging import get_logger
from research_paths import _ROOT

_PALACE_DIR = str(_ROOT / "palace")
log = get_logger(__name__)


def _resolve_palace_dir() -> str:
    """Pick an existing palace directory, or create a local fallback.

    The repo-local palace path is convenient for checked-in fixtures, but the VPS
    run usually keeps persistent palace state under the user's home directory.
    Prefer any existing configured palace before creating a new local directory.
    """
    configured = os.getenv("AUTORESEARCH_MEMPALACE_PALACE")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists() and not candidate.is_dir():
            raise RuntimeError(f"Configured palace path is not a directory: {candidate}")
        candidate.mkdir(parents=True, exist_ok=True)
        return str(candidate)

    repo_candidate = Path(_PALACE_DIR)
    if repo_candidate.exists() and not repo_candidate.is_dir():
        raise RuntimeError(f"Repo palace path is not a directory: {repo_candidate}")
    if repo_candidate.exists():
        return str(repo_candidate)

    home_candidate = Path.home() / ".codex/mempalace/palace"
    if home_candidate.exists() and not home_candidate.is_dir():
        raise RuntimeError(f"Home palace path is not a directory: {home_candidate}")
    if home_candidate.exists():
        return str(home_candidate)

    repo_candidate.mkdir(parents=True, exist_ok=True)
    return str(repo_candidate)


def _palace_add(wing: str, room: str, content: str, added_by: str = "conductor") -> dict:
    """Add a drawer to the palace via palace.get_collection + ChromaDB upsert."""
    import hashlib
    from datetime import datetime, timezone

    try:
        from mempalace.palace import get_collection

        col = get_collection(_resolve_palace_dir(), create=True)
        drawer_id = (
            f"drawer_{wing}_{room}_"
            f"{hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]}"
        )
        col.upsert(
            ids=[drawer_id],
            documents=[content],
            metadatas=[
                {
                    "wing": wing,
                    "room": room,
                    "source_file": "",
                    "chunk_index": 0,
                    "added_by": added_by,
                    "filed_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
        return {"success": True, "drawer_id": drawer_id}
    except Exception as exc:
        log.warning(
            "PALACE_ADD_FAILED wing=%s room=%s error=%s "
            "| hint=falling back to local research_findings.jsonl if saving a finding",
            wing,
            room,
            exc,
        )
        return {"success": False, "error": str(exc)}


def _palace_search(
    query: str, wing: str | None = None, room: str | None = None, n_results: int = 10
) -> list[dict]:
    """Search the palace via mempalace.searcher.search_memories."""
    try:
        from mempalace.searcher import search_memories

        result = search_memories(
            query=query,
            palace_path=_resolve_palace_dir(),
            wing=wing,
            room=room,
            n_results=n_results,
        )
        return result.get("results", [])
    except Exception as exc:
        log.warning(
            "PALACE_SEARCH_FAILED wing=%s room=%s error=%s "
            "| hint=memory search returned an error object to the conductor",
            wing,
            room,
            exc,
        )
        return [{"error": str(exc)}]


def _palace_status() -> dict:
    """Get palace overview via mempalace.layers.MemoryStack."""
    try:
        from mempalace.layers import MemoryStack

        stack = MemoryStack(palace_path=_resolve_palace_dir())
        return stack.status()
    except Exception as exc:
        log.warning(
            "PALACE_STATUS_FAILED error=%s "
            "| hint=memory status returned an error object to the conductor",
            exc,
        )
        return {"error": str(exc)}


def save_research_finding(
    finding: str,
    finding_type: str,
    status: str,
    evidence: str,
    scope: str,
    expires_if: str,
) -> str:
    VALID_TYPES = {
        "observation",
        "hypothesis",
        "validated_finding",
        "rejected_finding",
        "open_question",
        "implementation_note",
    }
    VALID_STATUSES = {"unvalidated", "validated", "rejected", "stale"}

    if finding_type not in VALID_TYPES:
        return f"REJECTED: finding_type must be one of {VALID_TYPES}, got '{finding_type}'"
    if status not in VALID_STATUSES:
        return f"REJECTED: status must be one of {VALID_STATUSES}, got '{status}''"
    if not evidence.strip():
        return "REJECTED: evidence cannot be empty — cite which round/experiment"
    if not scope.strip():
        return "REJECTED: scope cannot be empty — specify what data period this applies to"
    if not expires_if.strip():
        return "REJECTED: expires_if cannot be empty — what would invalidate this?"
    finding = _strip_duplicated_finding_metadata(finding)

    content = (
        f"TYPE:{finding_type} | STATUS:{status} | "
        f"EVIDENCE:{evidence} | SCOPE:{scope} | "
        f"EXPIRES_IF:{expires_if}\n"
        f"{finding}"
    )

    result = _palace_add(
        wing="research_findings",
        room=finding_type,
        content=content,
    )
    if not result.get("success"):
        findings_log = _ROOT / "research_findings.jsonl"
        entry = {
            "finding": finding,
            "type": finding_type,
            "status": status,
            "evidence": evidence,
            "scope": scope,
            "expires_if": expires_if,
            "timestamp": time.time(),
        }
        with open(findings_log, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return f"SAVED (local): {finding_type}/{status} — {finding[:80]}"
    return f"SAVED: {finding_type}/{status} — {finding[:80]}"


_FINDING_PREFIX_RE = re.compile(
    r"^\s*TYPE\s*:[^|\n]+"
    r"\s*\|\s*STATUS\s*:[^|\n]+"
    r"\s*\|\s*EVIDENCE\s*:[^|\n]+"
    r"\s*\|\s*SCOPE\s*:[^|\n]+"
    r"\s*\|\s*EXPIRES_IF\s*:[^\n]+"
    r"\n?",
    re.IGNORECASE,
)


def _strip_duplicated_finding_metadata(finding: str) -> str:
    cleaned = _FINDING_PREFIX_RE.sub("", finding or "", count=1).strip()
    return cleaned or (finding or "").strip()


def _local_research_findings(
    *,
    query: str,
    finding_type: str = "",
    n_results: int = 10,
) -> list[dict]:
    findings_log = _ROOT / "research_findings.jsonl"
    if not findings_log.exists():
        return []
    query_terms = [term for term in query.lower().split() if term]
    rows: list[dict] = []
    for line in findings_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        room = str(entry.get("type") or "")
        if finding_type and room != finding_type:
            continue
        finding = str(entry.get("finding") or "")
        haystack = " ".join(
            str(entry.get(key) or "")
            for key in ("finding", "type", "status", "evidence", "scope", "expires_if")
        ).lower()
        if query_terms and not any(term in haystack for term in query_terms):
            continue
        text = (
            f"TYPE:{room} | STATUS:{entry.get('status', '')} | "
            f"EVIDENCE:{entry.get('evidence', '')} | SCOPE:{entry.get('scope', '')} | "
            f"EXPIRES_IF:{entry.get('expires_if', '')}\n"
            f"{finding}"
        )
        rows.append(
            {
                "text": text,
                "room": room,
                "distance": "local",
                "source": "local_jsonl",
            }
        )
    return rows[-n_results:]


def search_research_findings(
    *,
    query: str,
    finding_type: str = "",
    n_results: int = 10,
) -> list[dict]:
    room = finding_type if finding_type else None
    palace_results = _palace_search(
        query=query,
        wing="research_findings",
        room=room,
        n_results=n_results,
    )
    if palace_results and not (len(palace_results) == 1 and "error" in palace_results[0]):
        return palace_results
    local_results = _local_research_findings(
        query=query,
        finding_type=finding_type,
        n_results=n_results,
    )
    if local_results:
        return local_results
    return palace_results


_PAST_THESES_DEFAULT_LIMIT = 25
_PAST_THESES_MAX_LIMIT = 50


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_pagination(offset: Any, limit: Any) -> tuple[int, int]:
    resolved_offset = max(0, _coerce_int(offset, 0))
    resolved_limit = _coerce_int(limit, _PAST_THESES_DEFAULT_LIMIT)
    resolved_limit = min(max(1, resolved_limit), _PAST_THESES_MAX_LIMIT)
    return resolved_offset, resolved_limit


def _short_text(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _learning_signal(entry: dict[str, Any]) -> str:
    status = entry.get("validator_status") or "unknown"
    dimension = entry.get("mechanism_dimension") or "unknown_dimension"
    return f"{status} thesis in {dimension}; inspect full details before building on it"


def _thesis_details(entry: dict[str, Any]) -> dict[str, Any]:
    details = entry.get("thesis_details")
    return details if isinstance(details, dict) else {}


def _index_entry(entry: dict[str, Any]) -> dict[str, Any]:
    config_changes = entry.get("config_changes")
    if not isinstance(config_changes, dict):
        config_changes = {}
    details = _thesis_details(entry)
    evidence = details.get("evidence")
    expected_effects = details.get("expected_effects")
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(expected_effects, list):
        expected_effects = []
    return {
        "thesis_id": entry.get("thesis_id", "unknown"),
        "round": entry.get("research_round_id"),
        "round_number": entry.get("round_number"),
        "job_id": entry.get("job_id"),
        "strategy_family": entry.get("strategy_family", ""),
        "outcome": entry.get("validator_status", "unknown"),
        "mechanism_dimension": entry.get("mechanism_dimension", ""),
        "dimension_novelty": details.get("dimension_novelty", ""),
        "new_dimension_name": details.get("new_dimension_name", ""),
        "requires_code_change": bool(details.get("requires_code_change")),
        "expected_effect_metrics": [
            effect.get("metric")
            for effect in expected_effects
            if isinstance(effect, dict) and effect.get("metric")
        ],
        "evidence_count": len(evidence),
        "selected_for_execution": bool(entry.get("selected_for_execution")),
        "config_change_keys": sorted(config_changes.keys()),
        "short_hypothesis": _short_text(entry.get("hypothesis", ""), 180),
        "short_validation_failure_reason": _short_text(
            entry.get("validation_failure_reason", ""), 160
        ),
        "learning_signal": _learning_signal(entry),
    }


def _attempt_detail(entry: dict[str, Any]) -> dict[str, Any]:
    details = _thesis_details(entry)
    return {
        "research_round_id": entry.get("research_round_id"),
        "attempt_number": entry.get("attempt_number"),
        "job_id": entry.get("job_id"),
        "round_number": entry.get("round_number"),
        "run_id": entry.get("run_id"),
        "hypothesis_id": entry.get("hypothesis_id"),
        "strategy_family": entry.get("strategy_family", ""),
        "config_changes": entry.get("config_changes", {}),
        "validator_status": entry.get("validator_status", ""),
        "round_outcome": entry.get("round_outcome"),
        "mechanism_dimension": entry.get("mechanism_dimension", ""),
        "hypothesis": entry.get("hypothesis", ""),
        "mechanism": entry.get("mechanism", ""),
        "validation_failure_reason": entry.get("validation_failure_reason", ""),
        "selected_for_execution": bool(entry.get("selected_for_execution")),
        "created_at_utc": entry.get("created_at_utc"),
        "round_usage": entry.get("round_usage", {}),
        "dimension_novelty": details.get("dimension_novelty", ""),
        "new_dimension_name": details.get("new_dimension_name", ""),
        "why_existing_dimensions_do_not_fit": details.get("why_existing_dimensions_do_not_fit", ""),
        "mechanism_family_definition": details.get("mechanism_family_definition", ""),
        "expected_reuse_across_future_theses": details.get(
            "expected_reuse_across_future_theses", ""
        ),
        "evidence": details.get("evidence", []),
        "expected_effects": details.get("expected_effects", []),
        "disqualifiers": details.get("disqualifiers", []),
        "why_not_overfit": details.get("why_not_overfit", ""),
        "requires_code_change": bool(details.get("requires_code_change")),
        "required_diagnostics": details.get("required_diagnostics", []),
    }


def _iter_thesis_attempts(
    root: Path, *, job_id: int | None = None, thesis_id: str | None = None
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for db_path in sorted(root.glob("*_backtest_runs.db")):
        from backtest_run_db import BacktestRunDB

        db = BacktestRunDB(db_path)
        entries.extend(db.list_research_thesis_attempts(job_id=job_id, thesis_id=thesis_id))
    # Per-DB rows arrive sorted, but concatenating across multiple family DBs
    # leaves the combined list ordered by filename, not by recency. Re-sort with
    # the same key BacktestRunDB.list_research_thesis_attempts uses so callers
    # like latest_thesis_details get the globally most recent attempt regardless
    # of which family DB it lives in.
    entries.sort(key=_thesis_attempt_sort_key, reverse=True)
    return entries


def _thesis_attempt_sort_key(entry: dict[str, Any]) -> tuple[int, int, str, int]:
    return (
        int(entry.get("job_id") or 0),
        int(entry.get("round_number") or 0),
        str(entry.get("created_at_utc") or ""),
        int(entry.get("attempt_number") or 0),
    )


def list_past_theses(
    root: Path,
    *,
    job_id: int | None = None,
    offset: int = 0,
    limit: int = _PAST_THESES_DEFAULT_LIMIT,
) -> str:
    offset, limit = _clamp_pagination(offset, limit)
    entries = _iter_thesis_attempts(root, job_id=job_id)
    total = len(entries)
    page = entries[offset : offset + limit]
    payload = {
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "job_id": job_id,
        "entries": [_index_entry(entry) for entry in page],
        "instructions": (
            "Call get_past_thesis(thesis_id) for full details before reusing, "
            "rejecting, or building on a related prior idea."
        ),
    }
    return json.dumps(payload, indent=2, default=str)


def get_past_thesis(root: Path, thesis_id: str, *, job_id: int | None = None) -> str:
    requested_id = str(thesis_id or "").strip()
    if not requested_id:
        return json.dumps(
            {"status": "error", "error": "thesis_id is required", "thesis_id": thesis_id},
            indent=2,
        )
    entries = _iter_thesis_attempts(root, job_id=job_id, thesis_id=requested_id)
    if not entries:
        return json.dumps(
            {
                "status": "not_found",
                "thesis_id": requested_id,
                "job_id": job_id,
                "attempts": [],
            },
            indent=2,
            default=str,
        )
    payload = {
        "status": "ok",
        "thesis_id": requested_id,
        "job_id": job_id,
        "attempts": [_attempt_detail(entry) for entry in entries],
    }
    return json.dumps(payload, indent=2, default=str)


def latest_thesis_details(
    root: Path,
    thesis_id: str,
    *,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Return proposal metadata from the most recent attempt for a thesis_id.

    Used by _resolve_conductor_inputs to inject what the previous round
    predicted into latest_outcome so the conductor can compare prediction
    vs. actual result.

    Pass ``job_id`` to scope the lookup to one job — without it, the same
    thesis_id used in multiple jobs returns the most-recent match across
    ALL jobs, which surfaces unrelated prior predictions.

    Returns {} for empty/whitespace ``thesis_id`` or when the thesis has
    no saved attempt records.
    """
    requested_id = str(thesis_id or "").strip()
    if not requested_id:
        return {}
    entries = _iter_thesis_attempts(root, job_id=job_id, thesis_id=requested_id)
    if not entries:
        return {}
    entry = entries[0]  # list_research_thesis_attempts orders DESC; first = most recent
    details = _thesis_details(entry)
    out: dict[str, Any] = {
        "hypothesis": entry.get("hypothesis", ""),
        "mechanism": entry.get("mechanism", ""),
        "config_changes": entry.get("config_changes") or {},
    }
    for key in (
        "expected_effects",
        "evidence",
        "disqualifiers",
        "evidence_strength",
        "closest_prior_theses_considered",
        "orthogonality_defense",
        "falsification_or_alternative",
        "why_not_overfit",
    ):
        val = details.get(key)
        if val is not None:
            out[key] = val
    return out


def _record_job_id(record: Any) -> int | None:
    try:
        return int(getattr(record, "job", 0))
    except (TypeError, ValueError):
        return None


def _metric_for_record(record: Any, metric_name: str) -> float | int | None:
    metrics = dict(getattr(record, "train_metrics", {}) or {})
    metrics.update(getattr(record, "validation_metrics", {}) or {})
    value = metrics.get(metric_name)
    if isinstance(value, (int, float)):
        return value
    return None


def _iter_round_records(root: Path, *, job_id: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for db_path in sorted(root.glob("*_backtest_runs.db")):
        from backtest_run_db import (
            INVALID_RESULT_VERDICTS,
            BacktestRunDB,
            is_metric_rankable_backtest_run,
        )

        db = BacktestRunDB(db_path)
        metric_name = db.primary_metric_name()
        direction = db.best_direction()
        for record in db.all():
            record_job = _record_job_id(record)
            if job_id is not None and record_job != job_id:
                continue
            records.append(
                {
                    "db_path": str(db_path),
                    "primary_metric_name": metric_name,
                    "best_direction": direction,
                    "record": record,
                    "metric": _metric_for_record(record, metric_name),
                    "job_id": record_job,
                    "metric_rankable": is_metric_rankable_backtest_run(record),
                    "invalid_result": getattr(record, "verdict_status", "")
                    in INVALID_RESULT_VERDICTS,
                }
            )
    return records


def _round_index_entry(item: dict[str, Any]) -> dict[str, Any]:
    record = item["record"]
    metrics = dict(getattr(record, "train_metrics", {}) or {})
    metrics.update(getattr(record, "validation_metrics", {}) or {})
    return {
        "run_id": getattr(record, "run_id", ""),
        "thesis_id": getattr(record, "thesis_id", ""),
        "research_round_id": getattr(record, "research_round_id", ""),
        "job_id": item.get("job_id"),
        "family": getattr(record, "family", ""),
        "metric_name": item.get("primary_metric_name"),
        "metric": item.get("metric"),
        "status": "keep" if getattr(record, "accepted", False) else "discard",
        "trade_count": getattr(record, "trade_count", 0),
        "profit_factor": metrics.get("profit_factor"),
        "max_drawdown": metrics.get("max_drawdown"),
        "pct_profitable_windows": metrics.get("pct_profitable_windows"),
        "config_path": getattr(record, "config_path", ""),
        "config_change_keys": sorted((getattr(record, "runtime_config", {}) or {}).keys()),
        "verdict_status": getattr(record, "verdict_status", ""),
        "verdict_summary": _short_text(getattr(record, "verdict_summary", ""), 180),
        "timestamp": getattr(record, "timestamp", ""),
        "hypothesis": _short_text(getattr(record, "hypothesis", ""), 300),
        "mechanism": _short_text(getattr(record, "mechanism", ""), 300),
    }


def _round_detail(item: dict[str, Any]) -> dict[str, Any]:
    record = item["record"]
    metrics = dict(getattr(record, "train_metrics", {}) or {})
    metrics.update(getattr(record, "validation_metrics", {}) or {})
    return {
        **_round_index_entry(item),
        "runtime_config": getattr(record, "runtime_config", {}) or {},
        "metrics": metrics,
        "train_metrics": getattr(record, "train_metrics", {}) or {},
        "validation_metrics": getattr(record, "validation_metrics", {}) or {},
        "trades_file": getattr(record, "trades_file", ""),
        "strategy_events_file": getattr(record, "strategy_events_file", ""),
        "diagnostics_file": getattr(record, "diagnostics_file", ""),
        "strategy_diagnostics": getattr(record, "strategy_diagnostics", {}) or {},
        "rejection_reason": getattr(record, "rejection_reason", ""),
        "hypothesis": getattr(record, "hypothesis", ""),
        "mechanism": getattr(record, "mechanism", ""),
        "code_commit": getattr(record, "code_commit", ""),
        "data_hash": getattr(record, "data_hash", ""),
        "usage": getattr(record, "usage", {}) or {},
    }


def _round_compact_detail(item: dict[str, Any]) -> dict[str, Any]:
    record = item["record"]
    runtime_config = getattr(record, "runtime_config", {}) or {}
    strategy_diagnostics = getattr(record, "strategy_diagnostics", {}) or {}
    diagnostics_summary = {
        key: strategy_diagnostics[key]
        for key in (
            "event_counts",
            "rejection_breakdown",
            "trade_analysis",
            "verdict",
        )
        if key in strategy_diagnostics
    }
    return {
        **_round_index_entry(item),
        "metrics": {
            **(getattr(record, "train_metrics", {}) or {}),
            **(getattr(record, "validation_metrics", {}) or {}),
        },
        "runtime_config_summary": {
            "key_count": len(runtime_config),
            "keys": sorted(runtime_config.keys()),
        },
        "artifact_paths": {
            "config_path": getattr(record, "config_path", ""),
            "trades_file": getattr(record, "trades_file", ""),
            "strategy_events_file": getattr(record, "strategy_events_file", ""),
            "diagnostics_file": getattr(record, "diagnostics_file", ""),
        },
        "diagnostics_summary": diagnostics_summary,
        "rejection_reason": getattr(record, "rejection_reason", ""),
        "hypothesis": _short_text(getattr(record, "hypothesis", ""), 600),
        "mechanism": _short_text(getattr(record, "mechanism", ""), 600),
        "code_commit": getattr(record, "code_commit", ""),
        "data_hash": getattr(record, "data_hash", ""),
        "usage_summary": (getattr(record, "usage", {}) or {}).get("total", {}),
    }


def _sort_round_records(items: list[dict[str, Any]], order: str) -> list[dict[str, Any]]:
    normalized = str(order or "latest").lower()
    if normalized == "best":
        return sorted(
            items,
            key=lambda item: (
                not bool(item.get("metric_rankable")),
                bool(item.get("invalid_result")),
                item.get("metric") is None,
                (
                    -(float(item.get("metric") or 0))
                    if item.get("best_direction") == "higher"
                    else float(item.get("metric") or 0)
                ),
            ),
        )
    if normalized == "worst":
        return sorted(
            items,
            key=lambda item: (
                not bool(item.get("metric_rankable")),
                bool(item.get("invalid_result")),
                item.get("metric") is None,
                (
                    float(item.get("metric") or 0)
                    if item.get("best_direction") == "higher"
                    else -(float(item.get("metric") or 0))
                ),
            ),
        )
    return sorted(
        items,
        key=lambda item: str(getattr(item["record"], "timestamp", "")),
        reverse=True,
    )


def list_round_results(
    root: Path,
    *,
    job_id: int | None = None,
    order: str = "latest",
    offset: int = 0,
    limit: int = 10,
) -> str:
    """Return a list of round results, ordered by recency / metric / etc.

    Each item is one round (= one backtest = one experiment).
    """
    offset, limit = _clamp_pagination(offset, limit)
    records = _sort_round_records(_iter_round_records(root, job_id=job_id), order)
    page = records[offset : offset + limit]
    payload = {
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(records),
        "job_id": job_id,
        "order": order,
        "entries": [_round_index_entry(item) for item in page],
        "instructions": (
            "Call get_round_result(research_round_id) before relying on full details."
        ),
    }
    return json.dumps(payload, indent=2, default=str)


def get_round_result(
    root: Path,
    *,
    research_round_id: str,
    detail: bool = False,
) -> str:
    """Return backtest result for a specific research round.

    ``research_round_id`` format: ``"job-{job}-round-{N}"`` (composite,
    unique per backtest). Raises ``KeyError`` if the round id doesn't
    resolve to a backtest record — callers must pass a valid id (no
    fallback resolution from thesis_id).
    """
    requested_id = str(research_round_id or "").strip()
    if not requested_id:
        raise KeyError("research_round_id is required")
    matches = [
        item
        for item in _iter_round_records(root)
        if getattr(item["record"], "research_round_id", "") == requested_id
    ]
    if not matches:
        raise KeyError(f"no round result for {requested_id!r}")
    sorted_matches = _sort_round_records(matches, "latest")
    chosen = sorted_matches[0]
    result = _round_detail(chosen) if detail else _round_compact_detail(chosen)
    payload = {
        "status": "ok",
        "research_round_id": requested_id,
        "detail": "full" if detail else "compact",
        "full_result_available": not detail,
        "result": result,
    }
    if not detail:
        payload["instructions"] = (
            "This is a compact result. Call "
            "get_round_result(research_round_id, detail=true) only when specific "
            "full fields are required."
        )
    return json.dumps(payload, indent=2, default=str)
