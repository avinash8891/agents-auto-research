from __future__ import annotations

import os
import subprocess
from pathlib import Path

from autoresearch_logging import get_logger

MEMPALACE_CMD = "mempalace"
MEMPALACE_PALACE = os.getenv(
    "AUTORESEARCH_MEMPALACE_PALACE",
    str(Path.home() / ".codex/mempalace/palace"),
)
log = get_logger(__name__)


def _run_mempalace(
    args: list[str], timeout_seconds: int = 15
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        log.warning("mempalace command timed out")
        return None
    except FileNotFoundError:
        log.warning("mempalace CLI is not installed or not on PATH")
        return None
    except OSError as exc:
        log.warning("mempalace CLI invocation failed: %s", exc)
        return None


def _mempalace_search(query_text: str, wing: str = "autoresearch", n: int = 3) -> str:
    """Search mempalace via CLI subprocess. Returns formatted results."""
    result = _run_mempalace(
        [
            MEMPALACE_CMD,
            "search",
            query_text,
            "--palace",
            MEMPALACE_PALACE,
            "--wing",
            wing,
            "-n",
            str(n),
        ]
    )
    if result is None:
        return "(memory search unavailable)"
    if result.returncode != 0:
        log.warning("mempalace search failed rc=%s", result.returncode)
        return "(memory search unavailable)"
    output = result.stdout.strip()
    return output if output else "(no prior memory found)"


def _mempalace_write(wing: str, room: str, content: str) -> bool:
    """Write to mempalace via CLI subprocess."""
    result = _run_mempalace(
        [
            MEMPALACE_CMD,
            "add",
            content,
            "--palace",
            MEMPALACE_PALACE,
            "--wing",
            wing,
            "--room",
            room,
        ]
    )
    if result is None:
        return False
    if result.returncode != 0:
        log.warning("mempalace write failed rc=%s", result.returncode)
        return False
    return True


def _mempalace_diary(agent_name: str, topic: str, entry: str) -> bool:
    """Write agent diary entry via CLI subprocess."""
    result = _run_mempalace(
        [
            MEMPALACE_CMD,
            "diary",
            entry,
            "--palace",
            MEMPALACE_PALACE,
            "--agent",
            agent_name,
            "--topic",
            topic,
        ]
    )
    if result is None:
        return False
    if result.returncode != 0:
        log.warning("mempalace diary failed rc=%s", result.returncode)
        return False
    return True
