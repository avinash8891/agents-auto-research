"""Resume-state classification and transitions for the autoresearch controller.

Owns every piece of knowledge about which prior controller states are
resumable and how each resumes. The controller classifies once via
``resumable_state_type`` and dispatches via ``apply_resume_transition``;
it never inspects raw resume keys itself.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

_RECOVERABLE_BLOCKED_RESUME_KINDS = {"builder_failed", "command_failed", "metric_parse_failed"}
_RESEARCH_BLOCKER_KINDS = {"research_required", "research_retry_required"}


class ResumeType(str, Enum):
    """The recoverable prior-state shapes a ``--resume-current-job`` launch accepts."""

    INTERRUPTED_RESEARCH = "interrupted_research"
    MANUAL_REVIEW = "manual_review"
    HALTED_CODE_CHANGE = "halted_code_change"
    BUILDER_RUNNING = "builder_running"
    BLOCKED_RESEARCH = "blocked_research"
    FAILED_ROUND = "failed_round"


def _blocker_kinds(state: dict[str, Any]) -> set[str]:
    blockers = state.get("blockers")
    if not isinstance(blockers, list):
        return set()
    return {
        str(blocker.get("kind"))
        for blocker in blockers
        if isinstance(blocker, dict) and blocker.get("kind")
    }


def _is_interrupted_research_failure_state(state: dict[str, Any]) -> bool:
    return state.get("state") == "interrupted" and "research_failed" in _blocker_kinds(state)


def _is_manual_review_resume_state(state: dict[str, Any]) -> bool:
    return (
        state.get("state") == "blocked"
        and isinstance(state.get("next_action"), dict)
        and state["next_action"].get("type") == "manual_review"
        and (
            state.get("halted_thesis_id")
            or state.get("manual_review_theses")
            or state.get("halted_reason") == "requires_code_change"
        )
    )


def _is_halted_code_change_resume_state(state: dict[str, Any]) -> bool:
    return (
        state.get("state") == "halted"
        and state.get("halted_reason") == "requires_code_change"
        and bool(state.get("halted_thesis_id"))
    )


def _is_builder_running_resume_state(state: dict[str, Any]) -> bool:
    next_action = state.get("next_action")
    return (
        state.get("state") == "building"
        and state.get("halted_reason") == "requires_code_change"
        and bool(state.get("halted_thesis_id"))
        and isinstance(next_action, dict)
        and next_action.get("type") == "builder_running"
    )


def _is_blocked_research_required_resume_state(state: dict[str, Any]) -> bool:
    if state.get("state") != "blocked":
        return False
    next_action = state.get("next_action")
    if not isinstance(next_action, dict) or next_action.get("type") != "research":
        return False
    return bool(_RESEARCH_BLOCKER_KINDS & _blocker_kinds(state))


def _is_blocked_failed_round_resume_state(state: dict[str, Any]) -> bool:
    if state.get("state") != "blocked":
        return False
    next_action = state.get("next_action")
    if not isinstance(next_action, dict) or next_action.get("type") not in {
        "blocked",
        "builder_failed",
    }:
        return False
    reason = next_action.get("reason")
    return bool(_RECOVERABLE_BLOCKED_RESUME_KINDS & _blocker_kinds(state)) or (
        reason in _RECOVERABLE_BLOCKED_RESUME_KINDS
    )


# Priority order matches the historic normalize_controller_launch_state dispatch:
# interrupted-research wins, then the halted/manual group, then blocked-research,
# then the recoverable failed-round case. The shapes are distinguished by
# (state, next_action.type), so at most one detector matches a given state.
_DETECTORS: tuple[tuple[ResumeType, Any], ...] = (
    (ResumeType.INTERRUPTED_RESEARCH, _is_interrupted_research_failure_state),
    (ResumeType.MANUAL_REVIEW, _is_manual_review_resume_state),
    (ResumeType.HALTED_CODE_CHANGE, _is_halted_code_change_resume_state),
    (ResumeType.BUILDER_RUNNING, _is_builder_running_resume_state),
    (ResumeType.BLOCKED_RESEARCH, _is_blocked_research_required_resume_state),
    (ResumeType.FAILED_ROUND, _is_blocked_failed_round_resume_state),
)


def resumable_state_type(state: dict[str, Any]) -> ResumeType | None:
    """Classify a prior controller state, or return None if it is not resumable."""
    for resume_type, detector in _DETECTORS:
        if detector(state):
            return resume_type
    return None


def _dict_state_field(state: dict[str, Any], key: str) -> dict[str, Any]:
    value = state.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _record_state_history(state: dict[str, Any], *, key: str, value: dict[str, Any]) -> None:
    history = state.setdefault("history", {})
    if not isinstance(history, dict):
        history = {}
        state["history"] = history
    history[key] = value


def _blocker_resume_snapshot(prior_state: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "state": prior_state.get("state"),
        "next_action": prior_state.get("next_action"),
        "current_thesis": prior_state.get("current_thesis"),
    }
    if prior_state.get("halted_reason"):
        snapshot["halted_reason"] = prior_state.get("halted_reason")
    if prior_state.get("halted_thesis_id"):
        snapshot["halted_thesis_id"] = prior_state.get("halted_thesis_id")
    if prior_state.get("halted_thesis"):
        snapshot["halted_thesis"] = prior_state.get("halted_thesis")
    if prior_state.get("manual_review_theses"):
        snapshot["manual_review_theses"] = list(prior_state.get("manual_review_theses") or [])
    if prior_state.get("builder_failed_theses"):
        snapshot["builder_failed_theses"] = list(prior_state.get("builder_failed_theses") or [])
    return {k: v for k, v in snapshot.items() if v not in (None, [], {})}


def _resume_interrupted_research_state(prior_state: dict[str, Any], job: int) -> dict[str, Any]:
    failed_round = prior_state.get("research_round", 0)
    try:
        retry_from_round = max(int(failed_round) - 1, 0)
    except (TypeError, ValueError):
        retry_from_round = 0

    prior_detail = ""
    blockers = prior_state.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    for blocker in blockers:
        if isinstance(blocker, dict) and blocker.get("kind") == "research_failed":
            prior_detail = str(blocker.get("detail") or "")
            break

    state = dict(prior_state)
    state.update(
        {
            "state": "blocked",
            "job": job,
            "research_round": retry_from_round,
            "research_round_in_progress": failed_round,
            "blockers": [
                {
                    "kind": "research_required",
                    "detail": (
                        "Retrying interrupted research failure"
                        + (f": {prior_detail}" if prior_detail else ".")
                    ),
                }
            ],
            "next_action": {
                "type": "research",
                "reason": "resume_current_job_retry_interrupted_research",
            },
        }
    )
    state.pop("current_thesis", None)
    state.pop("thesis_statuses", None)
    state.pop("finished_reason", None)
    return state


def _running_resume_state(
    prior_state: dict[str, Any],
    job: int,
    *,
    preserve_resume_metadata: bool,
    resume_previous_blocker: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "state": "running",
        "job": job,
        "research_round": prior_state.get("research_round", 0),
        "job_usage": prior_state.get("job_usage"),
        "heartbeat": _dict_state_field(prior_state, "heartbeat"),
    }
    for key in ("current_best", "baseline_drift"):
        if key in prior_state:
            state[key] = prior_state[key]

    if preserve_resume_metadata:
        blocker_snapshot = _blocker_resume_snapshot(prior_state)
        if blocker_snapshot:
            _record_state_history(state, key="last_blocker", value=blocker_snapshot)
            state["resume_context"] = {
                "source": "resume_current_job",
                "blocker": blocker_snapshot,
            }

    if resume_previous_blocker:
        blocker_kinds = sorted(_blocker_kinds(prior_state))
        state["resume_previous_blocker"] = {
            "kind": blocker_kinds[0] if blocker_kinds else prior_state.get("state", "unknown"),
            "next_action": prior_state.get("next_action"),
            "current_thesis": prior_state.get("current_thesis"),
        }
    return state


def apply_resume_transition(
    resume_type: ResumeType, prior_state: dict[str, Any], job: int
) -> dict[str, Any]:
    """Build the resumed next-state for a classified prior state."""
    if resume_type is ResumeType.INTERRUPTED_RESEARCH:
        return _resume_interrupted_research_state(prior_state, job)
    if resume_type is ResumeType.BLOCKED_RESEARCH:
        state = dict(prior_state)
        state["job"] = job
        return state
    if resume_type is ResumeType.FAILED_ROUND:
        return _running_resume_state(
            prior_state,
            job,
            preserve_resume_metadata=True,
            resume_previous_blocker=True,
        )
    # MANUAL_REVIEW, HALTED_CODE_CHANGE, BUILDER_RUNNING all resume the same way.
    return _running_resume_state(prior_state, job, preserve_resume_metadata=True)
