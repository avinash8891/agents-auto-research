# Controller Resume-State Machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the controller's six scattered `_is_*_resume_state()` predicates and the `normalize_controller_launch_state` if-chain into one deep `autoresearch_resume` module exposing `resumable_state_type()` and `apply_resume_transition()`.

**Architecture:** Extract a new module `autoresearch_resume.py` that owns all resume-state *shape* knowledge — blocker-kind sets, the six detection predicates, the transition builders, and the dispatch. `autoresearch_controller.normalize_controller_launch_state` shrinks to: classify once → validate → compute job → dispatch. The six existing characterization tests in `tests/test_autoresearch_controller_characterization.py` are the behavior-preservation oracle and MUST pass unchanged after Task 3.

**Tech Stack:** Python 3, `enum.Enum`, pytest. No new dependencies.

## Implementation Scope (per user directive 2026-06-18)

Implement the **first 8 candidates** from `architecture-review-20260618-213349.html` — the eight `Strong` ones — and **exclude** the two `Worth exploring` candidates (#9 research_memory presentation split, #10 RejectionCodec).

| # | Candidate | Deepened module | Primary files | Status |
|---|-----------|-----------------|---------------|--------|
| 1 | Controller resume state machine | `autoresearch_resume.ResumeType` / `resumable_state_type` / `apply_resume_transition` | controller, orchestration, planning | **DONE** `abfdbdc` (resume-classification slice; Tasks 1–3 below). Builder-lifecycle + planning state-building halves remain follow-ons. |
| 2 | Backtest verdict decision | `experiment_decision.decide_verdict` (DecisionFactory) | experiment, experiment_evaluator, backtest_run_db | **DONE** `801b472`. `BacktestRunDB.find_duplicate` move deferred (separate locality win). |
| 3 | Thesis validation pipeline | `research_retry.RetryBudget` (retry/budget seam) | research | **DONE** `312a12f`. Full ValidationPipeline (conductor+validate+dispatch) is a larger follow-on; the per-stage budget leak — the report's named pain — is fixed. |
| 4 | Causal verdict lifecycle | `causal_harvest.resolve_registered_verdict` (VerdictPipeline) | causal_harvest, experiment | **DONE** `9aa6635`. |
| 5 | Operationalize thesis contract | `ThesisContract` | compiler_operationalize | **SKIPPED — premise false (rule L).** `operationalize_thesis`/`finalize_thesis_config_changes` have zero callers in live code or tests (only re-exported in `compiler_pipeline.__all__`); real entry point `compile_research_thesis` never operationalizes. Deepening dead code = unused abstraction. Dead-code removal deferred to a separate user-approved decision (it's a public `__all__` export). |
| 6 | Token accumulation seam | public `accumulate_usage`/`record_failed_call`/`record_unmetered_call` | agent_token_usage, agent_sdk_token_usage | **DONE** `74a66a5` (private→public promotion, rule B). Typed `AccumulationRequest` deferred until a second adapter exists. |
| 7 | trace_sdk engine | `TraceEngine` + exporter registry | trace_sdk | **DEFERRED to dedicated staged plan.** Verified: trace state is already encapsulated in `TraceRuntimeState` (methods + contexts + exporter); the only loose globals `_PROVIDER`/`_INITIALIZED` are init-internals used in ~5 lines of one file. Exporter registry is speculative (one exporter exists today). Full TraceEngine rewrite (15+ `trace_*()` → methods) is repo-wide blast radius on load-bearing observability for low marginal value — not safe in a rapid sweep; needs its own plan with the full trace suite as oracle. |
| 8 | Builder workspace lifecycle | `BuilderWorkspace` value object | compiler_builder | **PARTIAL** `183d5d5` (path-layout value object + named segment constants). The high-value sequencing extraction (initialize/sync/record_promotion out of the ~500-line `build_missing_primitives`) is deferred to a dedicated staged plan — high blast radius, single-function caller for the path helpers. |

**Sequencing constraint:** candidates #2 and #4 both modify `autoresearch_experiment.py`, and #6/#7 both touch agent-infra — these cannot run as parallel worktrees without conflict. Implement sequentially, one deliverable per commit (CLAUDE.md rule E), stopping at each candidate boundary (CLAUDE.md rule 4). Candidates #2–#8 each get their own detailed TDD plan (the report deferred interface design) before implementation.

## Deferred TODOs

Work intentionally left out of the 2026-06-18 implementation sweep (each was scoped down to a safe, behavior-preserving slice or skipped with a verified reason). Each item below needs its own detailed TDD plan before implementation.

- [ ] **#7 — TraceEngine + exporter registry** (`trace_sdk.py`). Fold `_PROVIDER`/`_INITIALIZED` into the existing `TraceRuntimeState`; add a swappable-exporter seam (test-capture + Halo) so tests stop poking module globals. **High blast radius** (15+ `trace_*()` functions imported repo-wide) on load-bearing observability — staged plan with the full trace suite as oracle. Only build the exporter registry once a second exporter actually exists (today only `JsonLineTraceExporter`).
- [ ] **#8 sequencing half — `BuilderWorkspace` lifecycle** (`compiler_builder.py`). Extract `initialize()` / `sync_changes()` / `record_promotion()` out of the ~500-line `build_missing_primitives` onto the `BuilderWorkspace` value object already shipped in `183d5d5`. High-risk extraction from one giant function — needs strong builder-test coverage as oracle.
- [ ] **#1 builder-lifecycle + planning halves.** Consolidate the `_mark_builder_*` / `_activate_builder_config` mutations in `autoresearch_orchestration.py`, and `build_research_failure_state` / `plan_next_action` in `autoresearch_planning.py`, behind the `autoresearch_resume` state-machine seam (`abfdbdc` did only the resume-classification half).
- [ ] **#2 — `BacktestRunDB.find_duplicate`.** Move `_find_duplicate_artifact_output` (+ `_sha256_file`) from `autoresearch_experiment.py` onto `BacktestRunDB` so duplicate detection lives with the records it scans (`801b472` did the `decide_verdict` half; the factory already takes `duplicate` as input, so this slots in cleanly).
- [ ] **#3 — full ValidationPipeline.** Extract the conductor→validate→screen→compile→dispatch sequence (not just the `RetryBudget` shipped in `312a12f`) out of `execute_research_sdk` into a pipeline object exposing `.attempt(conductor_result, prior_theses)`.
- [ ] **#6 — typed `AccumulationRequest`.** When a second (non-OpenAI-SDK) token adapter appears, introduce the normalized `AccumulationRequest` dataclass so adapters build one shape feeding `accumulate_usage` (`74a66a5` promoted the seam to public; the dataclass is speculative until then).
- [ ] **#5 — dead-code decision** (`compiler_operationalize.py`). `operationalize_thesis` / `finalize_thesis_config_changes` / `_run_operationalization_agent` / `_build_operationalization_prompt` have **zero callers** (verified) but are a `compiler_pipeline.__all__` public export. Decide with the maintainer: delete (and drop from `__all__`) or keep as intentional API. Do **not** build the `ThesisContract` abstraction — it would deepen dead code.

## Global Constraints

- **Behavior preservation is the whole point.** The six existing tests in `tests/test_autoresearch_controller_characterization.py` (`test_launch_state_*`) must pass unchanged after Task 3. Do not edit them.
- **No toy names in tests.** Reuse the exact production fixtures from the characterization file: job ids `5`–`8`, thesis ids `"ema-resume"` / `"ema-command"` / `"interrupted"`, state strings `"running"`/`"blocked"`/`"halted"`/`"building"`/`"interrupted"`, `halted_reason="requires_code_change"`, blocker kinds `"research_required"` / `"command_failed"` / `"research_failed"`.
- **Scope is the resume-classification seam only.** Builder-lifecycle mutations in `autoresearch_orchestration.py` and planning's state-building in `autoresearch_planning.py` are OUT of scope — they become follow-on plans. Do not touch them.
- **Constants:** introduce the `ResumeType` enum (the report named it). Do NOT introduce a broad `ControllerState` enum for the raw `"running"`/`"blocked"` strings — that touches the whole controller and is a separate cleanup. Keep the raw state strings inside the moved code exactly as they are (behavior-preserving move).
- **Style:** black + isort + ruff. Run `pre-commit run --files <changed>` before each commit. Use `logging`, never `print`.
- **Grep before deleting.** Before removing any moved function from `autoresearch_controller.py`, grep the whole repo for its name — "unused in this file" ≠ "unused globally."

---

## File Structure

- **Create `autoresearch_resume.py`** — the deep resume module. Owns: `ResumeType` enum; `_RESEARCH_BLOCKER_KINDS` / `_RECOVERABLE_BLOCKED_RESUME_KINDS`; `_blocker_kinds`; the six private detectors; public `resumable_state_type`; the transition builders (`_dict_state_field`, `_record_state_history`, `_blocker_resume_snapshot`, `_resume_interrupted_research_state`, `_running_resume_state`); public `apply_resume_transition`.
- **Modify `autoresearch_controller.py`** — delete the moved code; rewrite `normalize_controller_launch_state` to call the new module; update `validate_controller_state_invariants` and `_validate_current_executable_state` to use the new module.
- **Create `tests/test_autoresearch_resume.py`** — unit tests for `resumable_state_type` and `apply_resume_transition` against the new module's public interface.
- **Unchanged oracle:** `tests/test_autoresearch_controller_characterization.py` — must keep passing.

---

## Task 1: ResumeType enum + `resumable_state_type` classifier

Introduce the new module with the enum and the single-pass classifier that replaces the six predicates. The controller is untouched this task — the new module is proven in isolation.

**Files:**
- Create: `autoresearch_resume.py`
- Test: `tests/test_autoresearch_resume.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `class ResumeType(str, Enum)` with members `INTERRUPTED_RESEARCH`, `MANUAL_REVIEW`, `HALTED_CODE_CHANGE`, `BUILDER_RUNNING`, `BLOCKED_RESEARCH`, `FAILED_ROUND`.
  - `resumable_state_type(state: dict[str, Any]) -> ResumeType | None` — returns the first matching type in priority order `INTERRUPTED_RESEARCH → MANUAL_REVIEW → HALTED_CODE_CHANGE → BUILDER_RUNNING → BLOCKED_RESEARCH → FAILED_ROUND`, else `None`.
  - `_blocker_kinds(state: dict[str, Any]) -> set[str]` (module-private, reused in Task 2 + by controller in Task 3).

- [ ] **Step 1: Write the failing test**

Create `tests/test_autoresearch_resume.py`:

```python
from __future__ import annotations

import pytest

from autoresearch_resume import ResumeType, resumable_state_type


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_autoresearch_resume.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoresearch_resume'`

- [ ] **Step 3: Write minimal implementation**

Create `autoresearch_resume.py`:

```python
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
# then the recoverable failed-round case.
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_autoresearch_resume.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files autoresearch_resume.py tests/test_autoresearch_resume.py
git add autoresearch_resume.py tests/test_autoresearch_resume.py
git commit -m "feat: add ResumeType classifier for controller resume states"
```

---

## Task 2: Transition builders + `apply_resume_transition`

Move the transition-building helpers into `autoresearch_resume.py` and add the dispatch that turns a `(ResumeType, prior_state, job)` into the next state. Controller still untouched.

**Files:**
- Modify: `autoresearch_resume.py` (append helpers + `apply_resume_transition`)
- Test: `tests/test_autoresearch_resume.py` (append cases)

**Interfaces:**
- Consumes: `ResumeType`, `resumable_state_type`, `_blocker_kinds` from Task 1.
- Produces: `apply_resume_transition(resume_type: ResumeType, prior_state: dict[str, Any], job: int) -> dict[str, Any]` — returns the resumed next-state dict. Dispatch:
  - `INTERRUPTED_RESEARCH` → rewind-to-failed-round state (sets `state="blocked"`, `research_round = max(failed-1,0)`, `research_round_in_progress`, a `research_required` blocker, `next_action.reason="resume_current_job_retry_interrupted_research"`; drops `current_thesis`/`thesis_statuses`/`finished_reason`).
  - `BLOCKED_RESEARCH` → `{**prior_state, "job": job}`.
  - `FAILED_ROUND` → running-resume state with `preserve_resume_metadata=True, resume_previous_blocker=True`.
  - `MANUAL_REVIEW` / `HALTED_CODE_CHANGE` / `BUILDER_RUNNING` → running-resume state with `preserve_resume_metadata=True`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_autoresearch_resume.py`:

```python
from autoresearch_resume import apply_resume_transition


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_autoresearch_resume.py -v -k apply`
Expected: FAIL — `ImportError: cannot import name 'apply_resume_transition'`

- [ ] **Step 3: Write minimal implementation**

Append to `autoresearch_resume.py`:

```python
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


def _resume_interrupted_research_state(
    prior_state: dict[str, Any], job: int
) -> dict[str, Any]:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_autoresearch_resume.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Lint and commit**

```bash
pre-commit run --files autoresearch_resume.py tests/test_autoresearch_resume.py
git add autoresearch_resume.py tests/test_autoresearch_resume.py
git commit -m "feat: add apply_resume_transition dispatch to autoresearch_resume"
```

---

## Task 3: Rewire the controller onto the new seam and delete the dead code

Point `normalize_controller_launch_state` at the new module, update the two other callers, and delete the now-duplicated predicates and builders from `autoresearch_controller.py`. The existing characterization tests are the oracle — they must pass unchanged.

**Files:**
- Modify: `autoresearch_controller.py` (imports; `normalize_controller_launch_state`; `validate_controller_state_invariants`; `_validate_current_executable_state`; delete lines ~147–230 and ~233–342 that moved to the new module — keep `_fresh_launch_state`, `_next_fresh_job_for_launch`, `_state_coerce_job_to_int`)
- Test (oracle, do not edit): `tests/test_autoresearch_controller_characterization.py`

**Interfaces:**
- Consumes: `ResumeType`, `resumable_state_type`, `apply_resume_transition`, `_blocker_kinds` from `autoresearch_resume`.
- Produces: `normalize_controller_launch_state(prior_state, *, resume_current_job, fresh_job=None) -> tuple[dict[str, Any], int]` — unchanged signature and behavior.

- [ ] **Step 1: Confirm the oracle is green before touching controller**

Run: `pytest tests/test_autoresearch_controller_characterization.py -k "launch_state or executable or invariants" -v`
Expected: PASS (the six `test_launch_state_*` plus invariants/executable tests). This is the baseline the rewire must preserve.

- [ ] **Step 2: Grep every caller of the symbols being moved/deleted**

Run:
```bash
grep -rn "_is_manual_review_resume_state\|_is_halted_code_change_resume_state\|_is_builder_running_resume_state\|_is_interrupted_research_failure_state\|_is_blocked_research_required_resume_state\|_is_blocked_failed_round_resume_state\|_resume_interrupted_research_state\|_running_resume_state\|_blocker_resume_snapshot\|_record_state_history\|_blocker_kinds\|_RESEARCH_BLOCKER_KINDS\|_RECOVERABLE_BLOCKED_RESUME_KINDS\|_dict_state_field" --include='*.py' .
```
Expected: hits only in `autoresearch_controller.py` and `autoresearch_resume.py` (+ the new test). If any OTHER module imports these private names, STOP — that module must be updated in this task too. Note them before proceeding.

- [ ] **Step 3: Add the import to `autoresearch_controller.py`**

Near the existing top-level imports (after the `from autoresearch_state import ...` line at line 70), add:

```python
from autoresearch_resume import (
    ResumeType,
    apply_resume_transition,
    resumable_state_type,
)
from autoresearch_resume import _blocker_kinds
```

- [ ] **Step 4: Rewrite `normalize_controller_launch_state`**

Replace the whole function body (currently lines ~368–433) with the classify-validate-dispatch form:

```python
def normalize_controller_launch_state(
    prior_state: dict[str, Any],
    *,
    resume_current_job: bool,
    fresh_job: int | None = None,
) -> tuple[dict[str, Any], int]:
    job = _state_coerce_job_to_int(prior_state.get("job"))
    resume_type = resumable_state_type(prior_state)

    if resume_current_job:
        if resume_type is None:
            raise ValueError(
                "--resume-current-job requires a recoverable halted code-change, "
                "builder-running, manual-review, blocked research-required, "
                "blocked command/metric failure, or interrupted research-failure state; "
                f"found state={prior_state.get('state')}"
            )
        if job < 1:
            job = 1
        return apply_resume_transition(resume_type, prior_state, job), job

    # Fresh jobs start from the next job number so new launches stay
    # distinguishable from earlier runs in traces and backtest rows, and are
    # isolated from any prior resume/manual-review state.
    job = fresh_job if fresh_job is not None else job + 1
    if job < 1:
        job = 1
    return _fresh_launch_state(job), job
```

- [ ] **Step 5: Update `validate_controller_state_invariants` and `_validate_current_executable_state`**

In `validate_controller_state_invariants` (line ~162) the call to `_blocker_kinds(state)` now resolves to the imported one — no edit needed beyond the import. Verify it reads:

```python
def validate_controller_state_invariants(state: dict[str, Any]) -> None:
    """Reject contradictory controller states before they reach disk."""
    if state.get("state") == "running" and _blocker_kinds(state):
        raise ValueError("running controller state cannot carry blockers")
```

In `_validate_current_executable_state` (line ~436), replace the `_is_blocked_research_required_resume_state(prior_state)` call with the classifier:

```python
def _validate_current_executable_state(prior_state: dict[str, Any]) -> int:
    job = _state_coerce_job_to_int(prior_state.get("job"))
    executable_blocked = resumable_state_type(prior_state) is ResumeType.BLOCKED_RESEARCH
    if (
        prior_state.get("state") not in {"running", "blocked"}
        or (prior_state.get("state") == "blocked" and not executable_blocked)
        or job < 1
    ):
        raise ValueError(
            "--run-current-state requires an already prepared executable state with a valid job id; "
            f"found state={prior_state.get('state')}"
        )
    return job
```

- [ ] **Step 6: Delete the moved code from `autoresearch_controller.py`**

Delete these definitions now living in `autoresearch_resume.py` (git preserves history — remove, don't comment out):
- `_RECOVERABLE_BLOCKED_RESUME_KINDS`, `_RESEARCH_BLOCKER_KINDS` (lines ~147–148)
- `_blocker_kinds` (lines ~151–159) — now imported
- `_dict_state_field` (lines ~168–170)
- `_is_manual_review_resume_state`, `_is_halted_code_change_resume_state`, `_is_builder_running_resume_state`, `_is_interrupted_research_failure_state`, `_is_blocked_research_required_resume_state`, `_is_blocked_failed_round_resume_state` (lines ~173–230)
- `_resume_interrupted_research_state` (lines ~233–274)
- `_running_resume_state` (lines ~277–315)
- `_record_state_history` (lines ~318–323)
- `_blocker_resume_snapshot` (lines ~326–342)

Keep: `validate_controller_state_invariants`, `_fresh_launch_state`, `_next_fresh_job_for_launch`, `_state_coerce_job_to_int` import, `normalize_controller_launch_state`, `_validate_current_executable_state`.

- [ ] **Step 7: Run the oracle + the new module tests together**

Run: `pytest tests/test_autoresearch_resume.py tests/test_autoresearch_controller_characterization.py -v`
Expected: PASS — all new tests AND all six `test_launch_state_*` tests pass unchanged. If any `test_launch_state_*` fails, the rewire changed behavior — fix the controller/new-module code, not the test.

- [ ] **Step 8: Verify nothing else imports the deleted names**

Run: `python -c "import autoresearch_controller"` and re-run the Step 2 grep.
Expected: clean import; grep hits only `autoresearch_resume.py` and the new test.

- [ ] **Step 9: Lint and commit**

```bash
pre-commit run --files autoresearch_controller.py
git add autoresearch_controller.py
git commit -m "refactor: route controller resume normalization through autoresearch_resume seam"
```

---

## CI Verification (after Task 3)

Per repo policy the full suite runs in GitHub Actions, not locally.

```bash
git push origin HEAD
gh run watch --exit-status
```
Report the run URL and the final test summary.

---

## Self-Review

**1. Spec coverage (vs report candidate #1, predicate half):**
- Six `_is_*_resume_state()` predicates collapsed → `resumable_state_type` (Task 1). ✓
- `ResumeType` enum introduced (report named it). ✓
- `normalize_controller_launch_state` if-chain → classify+dispatch (Task 3). ✓
- Scattered builders given one home → `apply_resume_transition` + helpers in new module (Task 2). ✓
- Builder-lifecycle mutations (orchestration.py) and planning state-building: explicitly OUT of scope, flagged as follow-on plans. ✓ (Report's full candidate; this plan is the resume-classification slice.)

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Every code step shows complete code. ✓

**3. Type consistency:** `ResumeType` members, `resumable_state_type(state) -> ResumeType | None`, `apply_resume_transition(resume_type, prior_state, job) -> dict` used identically in Tasks 1–3 and both test files. `_blocker_kinds` signature unchanged across move. ✓

**Follow-on plans (not in scope here):**
- *Builder-lifecycle state machine* — consolidate `_mark_builder_*` / `_activate_builder_config` in `autoresearch_orchestration.py` (report candidate #1, builder half).
- *Planning state-building* — `build_research_failure_state` / `plan_next_action` in `autoresearch_planning.py`.
