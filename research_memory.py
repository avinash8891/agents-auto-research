from __future__ import annotations

import json
import os
import time
from pathlib import Path

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
    from datetime import datetime

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
                    "filed_at": datetime.now().isoformat(),
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


def list_past_theses(root: Path) -> str:
    entries = []
    for db_path in sorted(root.glob("*_experiments.db")):
        from experiment_db import ExperimentDB

        db = ExperimentDB(db_path)
        for e in db.list_research_thesis_attempts():
            entries.append(
                {
                    "thesis_id": e.get("thesis_id", "unknown"),
                    "outcome": e.get("validator_status", "unknown"),
                    "mechanism_dimension": e.get("mechanism_dimension", ""),
                    "config_changes": e.get("config_changes", {}),
                    "hypothesis": e.get("hypothesis", "")[:150],
                    "rejection_reason": e.get("rejection_reason", "")[:100],
                    "round": e.get("research_round_id"),
                }
            )
    if not entries:
        return "No previous theses found."
    return json.dumps(entries, indent=2)
