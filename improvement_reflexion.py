"""Reflexion (within-episode verbal RL): read the prior round's reflexio
export and return a prompt-injectable preamble for the next conductor
invocation via ``rejection_feedback``.

Default-off via ``AUTORESEARCH_IMPROVEMENT_REFLEXION``. Returns ``""``
when off, when no prior round exists, or when the export is unreadable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autoresearch_logging import get_logger
from improvement_flags import reflexion_enabled

log = get_logger(__name__)

# Glob shape matches what `_write_adapter_exports` writes:
#   trace_exports/round-{N:03d}-{thesis_id}/reflexio/reflexio-event.json
EXPORT_GLOB = "trace_exports/round-{round_str}-*/reflexio/reflexio-event.json"


def _format_preamble(payload: dict[str, Any], current_round: int) -> str:
    episode = payload.get("episode") or {}
    reflection = payload.get("reflection") or {}
    outcome = episode.get("outcome", "?")
    reasoning = (reflection.get("reasoning") or "").strip()
    rejection_reason = (reflection.get("rejection_reason") or "").strip()
    prev_round = current_round - 1
    body_lines = [
        f"PRIOR ROUND REFLEXION (round {prev_round}):",
        f"  outcome: {outcome}",
    ]
    if reasoning:
        body_lines.append(f"  you_reasoned: {reasoning}")
    if rejection_reason:
        body_lines.append(f"  why_it_failed: {rejection_reason}")
    body_lines.append("Avoid repeating this failure mode in this round.")
    return "\n".join(body_lines)


def build_reflexion_feedback(controller, current_round: int) -> str:
    """Return the prior-round reflexion preamble, or empty string.

    ``controller`` is duck-typed: only ``controller.root: Path`` is
    used. Tests can pass ``SimpleNamespace(root=tmp_path)``.
    """
    if not reflexion_enabled():
        return ""
    if current_round <= 1:
        return ""
    try:
        controller_root = Path(controller.root)
    except AttributeError:
        log.warning(
            "REFLEXION controller has no root attribute; skipping. "
            "Action: pass an AutoresearchController-shaped object."
        )
        return ""
    prev_round_str = f"{current_round - 1:03d}"
    pattern = EXPORT_GLOB.format(round_str=prev_round_str)
    matches = sorted(controller_root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        log.info(
            f"REFLEXION no prior reflexio export for round={current_round - 1} "
            f"under {controller_root}/{pattern}; returning empty feedback."
        )
        return ""
    chosen = matches[0]
    try:
        payload = json.loads(chosen.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error(
            f"REFLEXION failed to read {chosen}: {type(exc).__name__}: {exc}. "
            f"Action: inspect the file or delete it; returning empty feedback."
        )
        return ""
    if not isinstance(payload, dict):
        log.error(
            f"REFLEXION export at {chosen} is not a JSON object; ignoring. "
            f"Action: regenerate the export."
        )
        return ""
    return _format_preamble(payload, current_round)
