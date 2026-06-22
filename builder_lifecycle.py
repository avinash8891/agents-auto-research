"""Cohesive home for builder-lifecycle heartbeat transitions.

The autoresearch controller drives a builder through a small state machine:
``running`` while the compiler subprocess executes, then a terminal transition
to ``completed`` (config activated), ``manual_review``, ``builder_failed`` or
``research_retry_required`` when the builder finishes.

Each transition writes a specific subset of ``state["heartbeat"][...]`` keys.
Previously those keys were poked raw at five different call sites in
``autoresearch_orchestration.py``; this module gives every transition a named
method so callers invoke ``heartbeat.mark_running(...)`` instead of mutating
``heartbeat["builder_status"] = "running"`` by hand. The exact field names,
values and write ordering are preserved.
"""

from __future__ import annotations

from typing import Any

from persistence_utils import utc_now_iso8601 as iso8601_utc_now

# Heartbeat keys cleared at the start of a fresh builder run so a previous
# blocked transition does not leak stale operator-facing context forward.
_STALE_BLOCKED_KEYS = (
    "blocked_builder_status",
    "blocked_builder_result_status",
    "blocked_reason",
    "blocked_thesis",
)

# Builder runtime-result fields surfaced to the operator heartbeat. Mirrors the
# subset captured for the next-action/blocker payloads.
_BUILDER_RESULT_CONTEXT_KEYS = (
    "builder_task",
    "builder_phase",
    "builder_self_check",
    "builder_attempts",
    "implementation_verification_passed",
    "implementation_verification_failures",
    "error_code",
    "generated_config",
    "execution_root",
    "promotion_manifest",
    "promoted_files",
    "seeded_capability",
    "status",
    "validation_passed",
    "usage",
)


def builder_result_context(builder_result: dict[str, Any]) -> dict[str, Any]:
    """Project a builder result onto the operator-facing context subset."""
    context: dict[str, Any] = {}
    for key in _BUILDER_RESULT_CONTEXT_KEYS:
        if key in builder_result:
            context[key] = builder_result[key]
    return context


class BuilderHeartbeat:
    """Named transitions over ``state["heartbeat"]`` builder bookkeeping.

    Wraps the live ``state`` dict (it does not copy) and mutates the nested
    ``heartbeat`` map in place, matching the prior raw-key behavior exactly.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    @property
    def heartbeat(self) -> dict[str, Any]:
        return self._state.setdefault("heartbeat", {})

    def mark_running(self, thesis_id: str) -> None:
        """Builder subprocess started: clear stale blocked context, set running."""
        heartbeat = self.heartbeat
        for stale_key in _STALE_BLOCKED_KEYS:
            heartbeat.pop(stale_key, None)
        heartbeat["builder_status"] = "running"
        heartbeat["builder_thesis"] = thesis_id
        heartbeat["builder_started_at"] = iso8601_utc_now()

    def mark_finished(self, thesis_id: str, status: str) -> None:
        """Builder subprocess finished with the given terminal ``status``."""
        heartbeat = self.heartbeat
        heartbeat["builder_status"] = status
        heartbeat["builder_thesis"] = thesis_id
        heartbeat["builder_finished_at"] = iso8601_utc_now()

    def attach_result_context(self, builder_result: dict[str, Any]) -> None:
        """Surface the builder runtime-result context, if any fields are present."""
        context = builder_result_context(builder_result)
        if not context:
            return
        self.heartbeat["builder_result_context"] = context

    def mark_blocked(
        self,
        thesis_id: str,
        *,
        builder_status: str,
        raw_builder_status: str,
        reason: str,
        error_code: str | None = None,
    ) -> None:
        """Record the operator-facing blocked context for a builder failure.

        ``error_code`` is written only when present (the unknown-error
        manual-review path omits ``blocked_error_code``), preserving the prior
        per-branch field set.
        """
        heartbeat = self.heartbeat
        heartbeat["blocked_thesis"] = thesis_id
        heartbeat["blocked_builder_status"] = builder_status
        heartbeat["blocked_builder_result_status"] = raw_builder_status
        heartbeat["blocked_reason"] = reason
        if error_code is not None:
            heartbeat["blocked_error_code"] = error_code
