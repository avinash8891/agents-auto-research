from __future__ import annotations

from autoresearch_resume import ResumeType, apply_resume_transition, resumable_state_type


def test_interrupted_research_failure_classifies_as_interrupted_research() -> None:
    state = {
        "state": "interrupted",
        "job": 6,
        "research_round": 4,
        "current_thesis": {"thesis_id": "interrupted"},
        "blockers": [{"kind": "research_failed", "detail": "agent process exited"}],
    }
    assert resumable_state_type(state) is ResumeType.INTERRUPTED_RESEARCH


def test_blocked_manual_review_classifies_as_manual_review() -> None:
    state = {
        "state": "blocked",
        "job": 5,
        "halted_reason": "requires_code_change",
        "halted_thesis_id": "ema-resume",
        "manual_review_theses": [{"thesis_id": "ema-resume"}],
        "next_action": {"type": "manual_review"},
    }
    assert resumable_state_type(state) is ResumeType.MANUAL_REVIEW


def test_halted_requires_code_change_classifies_as_halted_code_change() -> None:
    state = {
        "state": "halted",
        "job": 5,
        "halted_reason": "requires_code_change",
        "halted_thesis_id": "ema-resume",
    }
    assert resumable_state_type(state) is ResumeType.HALTED_CODE_CHANGE


def test_building_classifies_as_builder_running() -> None:
    state = {
        "state": "building",
        "job": 5,
        "halted_reason": "requires_code_change",
        "halted_thesis_id": "ema-resume",
        "next_action": {"type": "builder_running"},
    }
    assert resumable_state_type(state) is ResumeType.BUILDER_RUNNING


def test_blocked_research_required_classifies_as_blocked_research() -> None:
    state = {
        "state": "blocked",
        "job": 8,
        "next_action": {"type": "research", "reason": "needs_next_thesis"},
        "blockers": [{"kind": "research_required"}],
    }
    assert resumable_state_type(state) is ResumeType.BLOCKED_RESEARCH


def test_blocked_command_failed_classifies_as_failed_round() -> None:
    state = {
        "state": "blocked",
        "job": 7,
        "next_action": {"type": "blocked", "reason": "command_failed"},
        "blockers": [{"kind": "command_failed", "detail": "exit 7"}],
    }
    assert resumable_state_type(state) is ResumeType.FAILED_ROUND


def test_finished_state_is_not_resumable() -> None:
    assert resumable_state_type({"state": "finished", "job": 3, "research_round": 1}) is None


def test_apply_interrupted_research_rewinds_to_failed_round() -> None:
    prior = {
        "state": "interrupted",
        "job": 6,
        "research_round": 4,
        "current_thesis": {"thesis_id": "interrupted"},
        "blockers": [{"kind": "research_failed", "detail": "agent process exited"}],
    }
    state = apply_resume_transition(ResumeType.INTERRUPTED_RESEARCH, prior, job=6)

    assert state["state"] == "blocked"
    assert state["research_round"] == 3
    assert state["research_round_in_progress"] == 4
    assert state["next_action"]["reason"] == "resume_current_job_retry_interrupted_research"
    assert state["blockers"][0]["kind"] == "research_required"
    assert "current_thesis" not in state


def test_apply_blocked_research_copies_prior_and_sets_job() -> None:
    prior = {
        "state": "blocked",
        "job": 8,
        "research_round": 1,
        "next_action": {"type": "research", "reason": "needs_next_thesis"},
        "blockers": [{"kind": "research_required"}],
    }
    state = apply_resume_transition(ResumeType.BLOCKED_RESEARCH, prior, job=8)

    assert state == {**prior, "job": 8}


def test_apply_failed_round_restores_running_with_previous_blocker() -> None:
    prior = {
        "state": "blocked",
        "job": 7,
        "research_round": 2,
        "current_thesis": {"thesis_id": "ema-command"},
        "next_action": {"type": "blocked", "reason": "command_failed"},
        "blockers": [{"kind": "command_failed", "detail": "exit 7"}],
    }
    state = apply_resume_transition(ResumeType.FAILED_ROUND, prior, job=7)

    assert state["state"] == "running"
    assert state["resume_previous_blocker"]["kind"] == "command_failed"
    assert state["resume_context"]["blocker"]["current_thesis"] == {"thesis_id": "ema-command"}


def test_apply_manual_review_restores_running_with_blocker_history() -> None:
    prior = {
        "state": "blocked",
        "job": 5,
        "research_round": 3,
        "halted_reason": "requires_code_change",
        "halted_thesis_id": "ema-resume",
        "halted_thesis": {"thesis_id": "ema-resume"},
        "manual_review_theses": [{"thesis_id": "ema-resume"}],
        "next_action": {"type": "manual_review"},
        "heartbeat": {"last_result": "blocked"},
    }
    state = apply_resume_transition(ResumeType.MANUAL_REVIEW, prior, job=5)

    assert state["state"] == "running"
    assert state["research_round"] == 3
    assert state["resume_context"]["source"] == "resume_current_job"
    assert state["history"]["last_blocker"]["halted_thesis_id"] == "ema-resume"
    assert state["heartbeat"] == {"last_result": "blocked"}
