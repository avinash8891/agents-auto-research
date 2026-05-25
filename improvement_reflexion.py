"""Reflexion (within-episode verbal RL): read the prior round's reflexio
export and return a prompt-injectable preamble for the next conductor
invocation via ``rejection_feedback``.

Default-off via ``AUTORESEARCH_IMPROVEMENT_REFLEXION``. Returns ``""``
when off, when no prior round exists, or when the export is unreadable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autoresearch_logging import get_logger
from autoresearch_runtime_paths import research_round_trace_exports_root
from improvement_flags import reflexion_enabled
from reflexio_agent_reflections import build_agent_reflections

log = get_logger(__name__)

# Glob shape matches what `_write_adapter_exports` writes under the current
# round root:
#   runtime/jobs/job-N/research/round-N/trace_exports/round-{N:03d}-{thesis_id}/reflexio/reflexio-event.json
EXPORT_GLOB = "round-{round_str}-*/reflexio/reflexio-event.json"
LATEST_EXPORT_GLOB = "round-*-*/reflexio/reflexio-event.json"


AGENT_ALIASES: dict[str, set[str]] = {
    "analyst": {"analyst", "codex-diagnostic-analyst"},
    "builder": {"builder", "compiler-builder", "codex-builder"},
    "conductor": {"conductor", "research-conductor"},
    "web-researcher": {"web-researcher", "web_researcher", "research-agent"},
}


def _normalized_agent(agent: str) -> str:
    return agent.strip().lower().replace("_", "-")


def _agent_matches(value: str, wanted: str) -> bool:
    normalized_wanted = _normalized_agent(wanted)
    aliases = AGENT_ALIASES.get(normalized_wanted, {normalized_wanted})
    return _normalized_agent(value) in aliases


def _trajectory_for_agent(trajectory: list[Any], agent: str) -> list[dict[str, Any]]:
    scoped: list[dict[str, Any]] = []
    for item in trajectory:
        if not isinstance(item, dict):
            continue
        if _agent_matches(str(item.get("agent") or ""), agent):
            scoped.append(item)
    return scoped


def _format_preamble(
    payload: dict[str, Any],
    current_round: int | None,
    *,
    agent: str | None = None,
    source_round: int | None = None,
) -> str:
    episode = payload.get("episode") or {}
    reflection = payload.get("reflection") or {}
    agent_reflections = (
        payload.get("agent_reflections")
        if isinstance(payload.get("agent_reflections"), dict)
        else {}
    )
    trajectory = payload.get("trajectory") if isinstance(payload.get("trajectory"), list) else []
    if not agent_reflections and trajectory:
        agent_reflections = build_agent_reflections(
            [item for item in trajectory if isinstance(item, dict)]
        )
    if agent is not None:
        trajectory = _trajectory_for_agent(trajectory, agent)
        if not trajectory:
            return ""
    outcome = episode.get("research_outcome") or episode.get("outcome", "?")
    reasoning = (reflection.get("reasoning") or "").strip()
    rejection_reason = (reflection.get("rejection_reason") or "").strip()
    prev_round = source_round if source_round is not None else int(current_round or 1) - 1
    title = (
        f"PRIOR AGENT REFLEXION (round {prev_round}, agent {agent})"
        if agent is not None
        else f"PRIOR ROUND REFLEXION (round {prev_round})"
    )
    body_lines = [f"{title}:", f"  research_outcome: {outcome}"]
    agent_reflection = _agent_reflection_for(agent_reflections, agent) if agent else None
    if agent_reflection:
        _append_agent_reflection_lines(body_lines, agent_reflection)
    elif reasoning and agent is None:
        body_lines.append(f"  you_reasoned: {reasoning}")
    elif agent is not None:
        log.warning(
            "REFLEXION missing agent_reflections for agent=%s despite matching trajectory; "
            "using trajectory only. Action: inspect Reflexio export generation.",
            agent,
        )
    if rejection_reason:
        body_lines.append(f"  why_it_failed: {rejection_reason}")
    if trajectory:
        body_lines.append("  prior_trajectory:")
        for item in trajectory[-8:]:
            if not isinstance(item, dict):
                continue
            action = item.get("action", "?")
            summary = str(item.get("summary") or item.get("content") or "").strip()
            if summary:
                body_lines.append(f"    - {action}: {summary[:240]}")
    body_lines.append("Avoid repeating this failure mode in this round.")
    return "\n".join(body_lines)


def _agent_reflection_for(
    agent_reflections: dict[str, Any], agent: str | None
) -> dict[str, Any] | None:
    if not agent:
        return None
    for key, value in agent_reflections.items():
        if _agent_matches(str(key), agent) and isinstance(value, dict):
            return value
    return None


def _append_agent_reflection_lines(body_lines: list[str], reflection: dict[str, Any]) -> None:
    lesson = str(reflection.get("lesson") or "").strip()
    if lesson:
        body_lines.append(f"  agent_lesson: {lesson}")
    for label, key in (
        ("avoid", "avoid"),
        ("repeat", "repeat"),
        ("evidence", "evidence"),
        ("error_evidence", "error_evidence"),
    ):
        values = reflection.get(key)
        if not isinstance(values, list):
            continue
        clean_values = [str(value).strip() for value in values if str(value).strip()]
        if not clean_values:
            continue
        body_lines.append(f"  {label}:")
        for value in clean_values[:5]:
            body_lines.append(f"    - {value[:240]}")


def build_reflexion_feedback(controller, current_round: int, *, agent: str | None = None) -> str:
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
    raw_job = getattr(controller, "job", None)
    try:
        controller_job = int(raw_job) if raw_job is not None else None
    except (TypeError, ValueError):
        controller_job = None
    export_root = (
        research_round_trace_exports_root(controller_root, controller_job, current_round - 1)
        if controller_job is not None
        else _current_job_trace_exports_root(controller, controller_root)
    )
    prev_round_str = f"{current_round - 1:03d}"
    pattern = EXPORT_GLOB.format(round_str=prev_round_str)
    matches = sorted(export_root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not matches:
        log.info(
            f"REFLEXION no prior reflexio export for round={current_round - 1} "
            f"under {export_root}/{pattern}; returning empty feedback."
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
    return _format_preamble(payload, current_round, agent=agent)


def build_latest_reflexion_feedback(root: str | Path, *, agent: str | None = None) -> str:
    """Return the newest available reflexion preamble for long-running side agents.

    Builder runs outside the conductor call and often only has repo root plus
    thesis_id. It still needs Reflexion memory, so this reads the latest
    reflexio export by round number and scopes it to the requested agent.
    """
    if not reflexion_enabled():
        return ""
    controller_root = Path(root)
    matches = sorted(
        (
            path
            for job_path in (controller_root / "runtime" / "jobs").glob("job-*")
            for round_path in (job_path / "research").glob("round-*")
            for path in (round_path / "trace_exports").glob(LATEST_EXPORT_GLOB)
        ),
        key=lambda p: (_round_from_export_path(p), p.stat().st_mtime),
        reverse=True,
    )
    if not matches:
        return ""
    chosen = matches[0]
    try:
        payload = json.loads(chosen.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error(
            f"REFLEXION failed to read latest export {chosen}: {type(exc).__name__}: {exc}. "
            f"Action: inspect the file or delete it; returning empty feedback."
        )
        return ""
    if not isinstance(payload, dict):
        return ""
    return _format_preamble(
        payload,
        current_round=None,
        agent=agent,
        source_round=_round_from_export_path(chosen),
    )


def _round_from_export_path(path: Path) -> int:
    match = re.search(r"round-(\d+)-", path.as_posix())
    return int(match.group(1)) if match else -1


def _current_job_trace_exports_root(controller: Any, controller_root: Path) -> Path:
    job_runtime = getattr(controller, "job_runtime_root", None)
    if job_runtime is not None:
        current_round = getattr(controller, "research_round", None)
        if current_round is not None:
            return Path(job_runtime) / "research" / f"round-{int(current_round)}" / "trace_exports"
    raw_job = getattr(controller, "job", None)
    current_round = getattr(controller, "research_round", None)
    try:
        job = int(raw_job) if raw_job is not None else None
    except (TypeError, ValueError):
        job = None
    if job is None:
        if current_round is not None:
            candidates = sorted(
                (
                    path / "research" / f"round-{int(current_round)}" / "trace_exports"
                    for path in (controller_root / "runtime" / "jobs").glob("job-*")
                    if (
                        path / "research" / f"round-{int(current_round)}" / "trace_exports"
                    ).exists()
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                return candidates[0]
        raise ValueError("job id is required for reflexion trace discovery")
    round_number = int(current_round) if current_round is not None else 1
    return research_round_trace_exports_root(controller_root, job, round_number)
