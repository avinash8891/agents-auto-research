from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from artifact_io import timestamp_now
from persistence_utils import write_text_atomic


def write_research_artifact(
    research_dir: Path,
    request: dict[str, Any],
    parsed: dict[str, Any],
    raw_output: str,
    *,
    research_mode: str = "grounded",
    external_research_attempted: bool = True,
    external_research_attempts: int = 1,
    fallback_reason: str | None = None,
    job: int | None = None,
) -> Path:
    """Write completed research artifact to disk."""
    research_dir.mkdir(parents=True, exist_ok=True)
    request_id = request.get("request_id", f"research-{int(time.time())}")

    artifact = {
        "request_id": request_id,
        "status": "completed",
        "timestamp": timestamp_now(),
        "research_mode": research_mode,
        "job_id": job if job is not None else request.get("job_id"),
        "external_research_attempted": external_research_attempted,
        "external_research_attempts": external_research_attempts,
        "findings": parsed.get("findings", []),
        "suggested_theses": parsed.get("suggested_theses", []),
        "sources": parsed.get("sources", []),
    }
    if fallback_reason:
        artifact["fallback_reason"] = fallback_reason

    artifact_path = research_dir / f"{request_id}-findings.json"
    write_text_atomic(artifact_path, json.dumps(artifact, indent=2) + "\n")

    raw_path = research_dir / f"{request_id}-raw.txt"
    write_text_atomic(raw_path, raw_output)

    return artifact_path


def mark_request_completed(request_path: Path) -> None:
    """Mark a research request as completed."""
    payload = json.loads(request_path.read_text())
    payload["status"] = "completed"
    payload["completed_at"] = timestamp_now()
    write_text_atomic(request_path, json.dumps(payload, indent=2) + "\n")
