from __future__ import annotations

import subprocess

MEMPALACE_CMD = "mempalace"
MEMPALACE_PALACE = "/Users/avinashvankadaru/.codex/mempalace/palace"


def _run_mempalace(args: list[str], timeout_seconds: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("mempalace command timed out") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("mempalace CLI is not installed or not on PATH") from exc
    except OSError as exc:
        raise RuntimeError(f"mempalace CLI invocation failed: {exc}") from exc


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
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"mempalace search failed rc={result.returncode}: {stderr}")
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
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"mempalace write failed rc={result.returncode}: {stderr}")
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
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"mempalace diary failed rc={result.returncode}: {stderr}")
    return True
