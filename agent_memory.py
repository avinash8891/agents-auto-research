from __future__ import annotations

import subprocess

MEMPALACE_CMD = "/Users/avinashvankadaru/.local/bin/mempalace-mcp"
MEMPALACE_PALACE = "/Users/avinashvankadaru/.codex/mempalace/palace"


def _mempalace_search(query_text: str, wing: str = "autoresearch", n: int = 3) -> str:
    """Search mempalace via CLI subprocess. Returns formatted results."""
    try:
        result = subprocess.run(
            [
                "mempalace",
                "search",
                query_text,
                "--palace",
                MEMPALACE_PALACE,
                "--wing",
                wing,
                "-n",
                str(n),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout.strip()
        return output if output else "(no prior memory found)"
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return "(memory search unavailable)"


def _mempalace_write(wing: str, room: str, content: str) -> bool:
    """Write to mempalace via CLI subprocess."""
    try:
        subprocess.run(
            [
                "mempalace",
                "add",
                content,
                "--palace",
                MEMPALACE_PALACE,
                "--wing",
                wing,
                "--room",
                room,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False


def _mempalace_diary(agent_name: str, topic: str, entry: str) -> bool:
    """Write agent diary entry via CLI subprocess."""
    try:
        subprocess.run(
            [
                "mempalace",
                "diary",
                entry,
                "--palace",
                MEMPALACE_PALACE,
                "--agent",
                agent_name,
                "--topic",
                topic,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return False
