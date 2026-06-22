from __future__ import annotations

from typing import Any

import builder_lifecycle
from builder_lifecycle import BuilderHeartbeat, builder_result_context


def test_mark_running_clears_stale_blocked_context_and_sets_running() -> None:
    state: dict[str, Any] = {
        "heartbeat": {
            "blocked_builder_status": "builder_failed",
            "blocked_builder_result_status": "error",
            "blocked_reason": "Builder failed for prior-thesis: builder_timeout",
            "blocked_thesis": "prior-thesis",
            "blocked_error_code": "builder_timeout",
            "last_completed_thesis": "configs/ema_base.yaml",
        }
    }

    BuilderHeartbeat(state).mark_running("ema-round-2-longer-ema")

    heartbeat = state["heartbeat"]
    # Stale blocked-* keys cleared so prior failure context does not leak forward.
    assert "blocked_builder_status" not in heartbeat
    assert "blocked_builder_result_status" not in heartbeat
    assert "blocked_reason" not in heartbeat
    assert "blocked_thesis" not in heartbeat
    # blocked_error_code is intentionally NOT in the stale set (matches prior behavior).
    assert heartbeat["blocked_error_code"] == "builder_timeout"
    # Unrelated heartbeat keys are preserved.
    assert heartbeat["last_completed_thesis"] == "configs/ema_base.yaml"
    assert heartbeat["builder_status"] == "running"
    assert heartbeat["builder_thesis"] == "ema-round-2-longer-ema"
    assert "builder_started_at" in heartbeat
    assert "builder_finished_at" not in heartbeat


def test_mark_finished_sets_status_thesis_and_finished_at() -> None:
    state: dict[str, Any] = {"state": "building"}

    BuilderHeartbeat(state).mark_finished("ema-round-2-longer-ema", "completed")

    heartbeat = state["heartbeat"]
    assert heartbeat["builder_status"] == "completed"
    assert heartbeat["builder_thesis"] == "ema-round-2-longer-ema"
    assert "builder_finished_at" in heartbeat
    assert "builder_started_at" not in heartbeat


def test_builder_result_context_projects_known_fields_only() -> None:
    builder_result = {
        "status": "error",
        "error_code": "builder_config_validation_failed",
        "validation_passed": False,
        "usage": {"total_tokens": 4096},
        "promoted_files": ["compiler_output.py"],
        "reason": "config out of scope",  # not a context key
        "stack": "traceback...",  # not a context key
    }

    context = builder_result_context(builder_result)

    assert context == {
        "status": "error",
        "error_code": "builder_config_validation_failed",
        "validation_passed": False,
        "usage": {"total_tokens": 4096},
        "promoted_files": ["compiler_output.py"],
    }


def test_attach_result_context_skips_empty_context() -> None:
    state: dict[str, Any] = {"heartbeat": {"builder_status": "completed"}}

    BuilderHeartbeat(state).attach_result_context({"reason": "no surfaced fields"})

    assert "builder_result_context" not in state["heartbeat"]


def test_attach_result_context_surfaces_usage() -> None:
    state: dict[str, Any] = {}

    BuilderHeartbeat(state).attach_result_context(
        {"status": "error", "usage": {"total_tokens": 42}}
    )

    assert state["heartbeat"]["builder_result_context"] == {
        "status": "error",
        "usage": {"total_tokens": 42},
    }


def test_mark_blocked_writes_error_code_for_deterministic_failure() -> None:
    state: dict[str, Any] = {}

    BuilderHeartbeat(state).mark_blocked(
        "timeout-thesis",
        builder_status="builder_failed",
        raw_builder_status="error",
        reason="Builder failed for timeout-thesis: builder_timeout",
        error_code="builder_timeout",
    )

    heartbeat = state["heartbeat"]
    assert heartbeat["blocked_thesis"] == "timeout-thesis"
    assert heartbeat["blocked_builder_status"] == "builder_failed"
    assert heartbeat["blocked_builder_result_status"] == "error"
    assert heartbeat["blocked_reason"] == "Builder failed for timeout-thesis: builder_timeout"
    assert heartbeat["blocked_error_code"] == "builder_timeout"


def test_mark_blocked_omits_error_code_for_unknown_manual_review() -> None:
    state: dict[str, Any] = {}

    BuilderHeartbeat(state).mark_blocked(
        "manual-thesis",
        builder_status="manual_review",
        raw_builder_status="error",
        reason="Builder failed for manual-thesis; operator review required.",
    )

    heartbeat = state["heartbeat"]
    assert heartbeat["blocked_thesis"] == "manual-thesis"
    assert heartbeat["blocked_builder_status"] == "manual_review"
    assert heartbeat["blocked_builder_result_status"] == "error"
    assert "blocked_error_code" not in heartbeat


def test_builder_result_context_matches_orchestration_helper() -> None:
    # The seam helper must produce the exact same projection the orchestration
    # blocker/next-action payloads rely on, since both read the same fields.
    import autoresearch_orchestration as orch

    builder_result = {
        "status": "completed",
        "validation_passed": True,
        "generated_config": "runtime/jobs/job-6/research/round-2/selected_config.json",
        "execution_root": "/tmp/exec",
        "builder_task": "compile-runtime-config",
        "untracked": "ignored",
    }

    assert builder_result_context(builder_result) == orch._builder_result_context(builder_result)


def test_heartbeat_property_initializes_missing_map() -> None:
    state: dict[str, Any] = {}
    hb = BuilderHeartbeat(state)

    assert hb.heartbeat == {}
    assert state["heartbeat"] is hb.heartbeat


def test_stale_blocked_keys_constant_excludes_error_code() -> None:
    # Guards the subtle invariant that mark_running does NOT clear
    # blocked_error_code, matching the prior _mark_builder_running behavior.
    assert "blocked_error_code" not in builder_lifecycle._STALE_BLOCKED_KEYS
