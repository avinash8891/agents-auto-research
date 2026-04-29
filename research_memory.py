from __future__ import annotations

import json
from pathlib import Path

from research_infra import _ROOT

_PALACE_DIR = str(_ROOT / "palace")


def _palace_add(
    wing: str, room: str, content: str, added_by: str = "conductor"
) -> dict:
    """Add a drawer to the palace via palace.get_collection + ChromaDB upsert."""
    import hashlib
    from datetime import datetime

    try:
        from mempalace.palace import get_collection

        col = get_collection(_PALACE_DIR, create=True)
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
        return {"success": False, "error": str(exc)}


def _palace_search(
    query: str, wing: str | None = None, room: str | None = None, n_results: int = 10
) -> list[dict]:
    """Search the palace via mempalace.searcher.search_memories."""
    try:
        from mempalace.searcher import search_memories

        result = search_memories(
            query=query,
            palace_path=_PALACE_DIR,
            wing=wing,
            room=room,
            n_results=n_results,
        )
        return result.get("results", [])
    except Exception as exc:
        return [{"error": str(exc)}]


def _palace_status() -> dict:
    """Get palace overview via mempalace.layers.MemoryStack."""
    try:
        from mempalace.layers import MemoryStack

        stack = MemoryStack(palace_path=_PALACE_DIR)
        return stack.status()
    except Exception as exc:
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
        return (
            f"REJECTED: finding_type must be one of {VALID_TYPES}, got '{finding_type}'"
        )
    if status not in VALID_STATUSES:
        return f"REJECTED: status must be one of {VALID_STATUSES}, got '{status}''"
    if not evidence.strip():
        return "REJECTED: evidence cannot be empty — cite which round/experiment"
    if not scope.strip():
        return (
            "REJECTED: scope cannot be empty — specify what data period this applies to"
        )
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
    if result.get("success"):
        return f"SAVED: {finding_type}/{status} — {finding[:80]}"

    findings_log = _ROOT / "research_findings.jsonl"
    entry = {
        "finding": finding,
        "type": finding_type,
        "status": status,
        "evidence": evidence,
        "scope": scope,
        "expires_if": expires_if,
        "timestamp": __import__("time").time(),
    }
    with open(findings_log, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return f"SAVED (local): {finding_type}/{status} — {finding[:80]}"


def list_past_theses(root: Path) -> str:
    jsonl_files = sorted(root.glob("*_autoresearch.jsonl"))
    entries = []
    for jf in jsonl_files:
        for line in jf.read_text().splitlines():
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if e.get("type") != "research_round":
                continue
            entries.append(
                {
                    "thesis_id": e.get("thesis_id", "unknown"),
                    "outcome": e.get("outcome", "unknown"),
                    "mechanism_dimension": e.get("mechanism_dimension", ""),
                    "config_changes": e.get("config_changes", {}),
                    "hypothesis": e.get("hypothesis", "")[:150],
                    "rejection_reason": e.get("rejection_reason", "")[:100],
                    "round": e.get("round"),
                    "job": e.get("job"),
                }
            )
    if not entries:
        return "No previous theses found."
    return json.dumps(entries, indent=2)
