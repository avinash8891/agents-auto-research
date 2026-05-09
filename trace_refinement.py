from __future__ import annotations

from itertools import count
from typing import Any

from trace_sdk import record_event


class RefinementRecorder:
    def __init__(self) -> None:
        self._session_ids = count(1)

    def start_session(
        self,
        *,
        summary: str,
        objective: str,
        initial_context: dict[str, Any] | None = None,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        session_id = f"refinement-{next(self._session_ids):04d}"
        payload = {
            "session_id": session_id,
            "objective": objective,
            "initial_context": dict(initial_context or {}),
        }
        record_event(
            source_module="trace_refinement",
            category="refinement",
            action="session_start",
            summary=summary,
            payload=payload,
            artifact_paths=artifact_paths,
        )
        return payload

    def record_iteration(
        self,
        *,
        session_id: str,
        iteration: int,
        generate: dict[str, Any] | None = None,
        critique: dict[str, Any] | None = None,
        revise: dict[str, Any] | None = None,
        evaluate: dict[str, Any] | None = None,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "session_id": session_id,
            "iteration": iteration,
            "generate": dict(generate or {}),
            "critique": dict(critique or {}),
            "revise": dict(revise or {}),
            "evaluate": dict(evaluate or {}),
        }
        record_event(
            source_module="trace_refinement",
            category="refinement",
            action="iteration",
            summary=f"{session_id} iteration {iteration}",
            payload=payload,
            artifact_paths=artifact_paths,
        )
        return payload

    def finish_session(
        self,
        *,
        session_id: str,
        stopping_reason: str,
        final_outcome: str,
        artifact_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "session_id": session_id,
            "stopping_reason": stopping_reason,
            "final_outcome": final_outcome,
        }
        record_event(
            source_module="trace_refinement",
            category="refinement",
            action="session_finish",
            summary=f"{session_id} finished",
            payload=payload,
            artifact_paths=artifact_paths,
        )
        return payload
