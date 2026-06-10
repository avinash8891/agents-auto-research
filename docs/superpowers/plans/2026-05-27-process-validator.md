# Process Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move per-thesis required-tool enforcement into `thesis_validator.py` as a process tier and remove the conductor's per-tool L6/L7 gates.

**Architecture:** `thesis_validator.py` gets a new `_validate_process(thesis, tools_called)` helper that runs before structural validation. `research_conductor.py` keeps tracking `tools_called_this_round`, but stops enforcing inline ordering and passes that set into `validate_thesis_dict`. Legacy rejection messages continue to map to the new process code through `infer_rejection_code`.

**Tech Stack:** Python 3, pydantic `ResearchThesis`, pytest, existing `ThesisValidationError` structured evidence.

---

### Task 1: Process Gate Tests

**Files:**
- Create: `tests/test_validator_process_gate.py`
- Modify: `tests/test_l6_l7_tool_order_gates.py`

- [ ] **Step 1: Add failing validator process-gate tests**

Create `tests/test_validator_process_gate.py` with a local valid thesis fixture and five tests:

```python
from __future__ import annotations

import pytest

from thesis_validator import ThesisValidationError, validate_thesis_dict


def _valid_thesis() -> dict:
    return {
        "thesis_id": "ema-process-gate-v1",
        "strategy_family": "ema",
        "hypothesis": "Skipping opening auction noise improves entry quality.",
        "mechanism": "The first minutes have thin liquidity and noisy fills.",
        "mechanism_dimension": "entry_timing",
        "dimension_novelty": "Tests session timing instead of threshold tuning.",
        "config_changes": {"opening_skip_minutes": 5},
        "expected_effects": [{"metric": "profit_factor", "direction": "increase"}],
        "disqualifiers": [
            {
                "name": "opening_noise_not_concentrated",
                "condition": "Opening-window losses are not concentrated in the first five minutes.",
                "kind": "mechanism_evidence",
            }
        ],
        "falsification_or_alternative": (
            "If first-five-minute losses are not worse than later-session losses, "
            "the auction-noise mechanism is false."
        ),
    }


def test_process_gate_passes_when_all_required_tools_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_experiment_results", "web_search"},
    )
    assert thesis.thesis_id == "ema-process-gate-v1"


def test_process_gate_rejects_when_web_search_not_called() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(_valid_thesis(), tools_called={"list_experiment_results"})
    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == ["web_search"]


def test_process_gate_rejects_when_experiment_results_not_called() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(_valid_thesis(), tools_called={"web_search"})
    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == ["list_experiment_results"]


def test_process_gate_batches_multiple_missing_tools() -> None:
    with pytest.raises(ThesisValidationError) as exc_info:
        validate_thesis_dict(_valid_thesis(), tools_called=set())
    assert exc_info.value.rejection_code == "process_required_tools_not_called"
    assert exc_info.value.evidence["missing_tools"] == [
        "list_experiment_results",
        "web_search",
    ]


def test_process_gate_succeeds_with_extra_tools_called() -> None:
    thesis = validate_thesis_dict(
        _valid_thesis(),
        tools_called={"list_experiment_results", "web_search", "analyze_trades"},
    )
    assert thesis.config_changes == {"opening_skip_minutes": 5}
```

- [ ] **Step 2: Replace retired L6/L7 helper tests**

Replace `tests/test_l6_l7_tool_order_gates.py` with tests proving the old conductor helpers are gone and legacy messages map to the new process code:

```python
from __future__ import annotations

import research_conductor
from thesis_validator import infer_rejection_code


def test_conductor_no_longer_exports_web_search_order_gate() -> None:
    assert not hasattr(research_conductor, "_check_web_search_called_first")


def test_conductor_no_longer_exports_experiment_results_gate() -> None:
    assert not hasattr(research_conductor, "_check_experiment_results_consulted")


def test_legacy_l6_message_maps_to_process_required_tools_code() -> None:
    assert (
        infer_rejection_code(
            "ERROR: HARD GATE — call web_search at least once before analyze_trades."
        )
        == "process_required_tools_not_called"
    )


def test_legacy_l7_message_maps_to_process_required_tools_code() -> None:
    assert (
        infer_rejection_code(
            "ERROR: HARD GATE — call list_experiment_results at least once before proposing a thesis."
        )
        == "process_required_tools_not_called"
    )
```

- [ ] **Step 3: Run red tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_validator_process_gate.py tests/test_l6_l7_tool_order_gates.py -q
```

Expected: failures because `tools_called` is not accepted by `validate_thesis_dict`, `_validate_process` does not exist, and the conductor still exports the old helpers.

### Task 2: Validator Process Tier

**Files:**
- Modify: `thesis_validator.py`

- [ ] **Step 1: Add `_validate_process` above structural helper definitions**

Add:

```python
def _validate_process(thesis: ResearchThesis, tools_called: set[str]) -> None:
    required_tools = {
        "list_experiment_results": "must understand prior experiment outcomes",
        "web_search": "must be grounded in external evidence",
    }
    missing = [tool for tool in required_tools if tool not in tools_called]
    if not missing:
        return
    raise ThesisValidationError(
        f"Process gate failed: required tools not called: {missing}",
        rejection_code="process_required_tools_not_called",
        evidence={"missing_tools": missing, "tools_called": sorted(tools_called)},
    )
```

- [ ] **Step 2: Thread `tools_called` through validator entry points**

Update signatures:

```python
def validate_research_thesis(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None = None,
    *,
    tools_called: set[str] | None = None,
) -> ResearchThesis:
```

and:

```python
def validate_thesis_dict(
    raw: dict,
    prior_theses: list[dict[str, Any]] | None = None,
    *,
    tools_called: set[str] | None = None,
) -> ResearchThesis:
```

Call `_validate_process(thesis, tools_called or set())` before `_collect_mechanical_failures(...)`. Pass `tools_called=tools_called` from `validate_thesis_dict` to `validate_research_thesis`.

- [ ] **Step 3: Update legacy inference mapping**

In `infer_rejection_code`, before config/structural mappings, add a branch for both retired messages and the new process message:

```python
if (
    "process gate failed" in msg
    or "call web_search at least once before analyze_trades" in msg
    or "call list_experiment_results at least once before proposing a thesis" in msg
):
    return "process_required_tools_not_called"
```

- [ ] **Step 4: Run targeted validator tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_validator_process_gate.py tests/test_validator_stages.py tests/test_validator_gate_coverage.py -q
```

Expected: process-gate tests pass; existing validator tests still pass because omitted `tools_called` defaults to an empty set and process gate is explicit only when conductor passes a set.

### Task 3: Conductor Wiring and L6/L7 Removal

**Files:**
- Modify: `research_conductor.py`

- [ ] **Step 1: Remove old helper definitions**

Delete `_check_web_search_called_first` and `_check_experiment_results_consulted`.

- [ ] **Step 2: Make `analyze_trades` dispatch unconditionally when trades exist**

Replace the old gate branch:

```python
gate_error = _check_web_search_called_first(tools_called_this_round)
if gate_error is not None:
    output = gate_error
elif not trades_file:
```

with:

```python
if not trades_file:
```

Keep `tools_called_this_round.add("analyze_trades")` in the successful trades path.

- [ ] **Step 3: Remove the L7 parsed-output block**

Delete the block that calls `_check_experiment_results_consulted(...)` and returns `experiment_results_not_consulted`. Leave the `should_stop` branch as the first parsed-output branch.

- [ ] **Step 4: Pass tools into validator**

Change:

```python
validate_thesis_dict(candidate)
```

to:

```python
validate_thesis_dict(candidate, tools_called=tools_called_this_round)
```

- [ ] **Step 5: Run conductor-focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_l6_l7_tool_order_gates.py tests/test_research_conductor_characterization.py -q
```

Expected: tests pass after updating any monkeypatched `validate_thesis_dict` lambdas that need to accept `**kwargs`.

### Task 4: Independent Verification and Commit

**Files:**
- Verify only unless a targeted test fixture needs a signature update.

- [ ] **Step 1: Grep/AST checks**

Run:

```bash
rg -n "_check_web_search_called_first|_check_experiment_results_consulted|validate_thesis_dict\\(|validate_research_thesis\\(" research_conductor.py thesis_validator.py tests
```

Expected: no old helper definitions or calls remain; conductor validation call passes `tools_called=tools_called_this_round`; tests may contain legacy names only in retired-helper absence tests.

- [ ] **Step 2: Run prompt drift checker**

Run:

```bash
.venv/bin/python scripts/check_prompt_drift.py
```

Expected: exit 0.

- [ ] **Step 3: Run full local pytest sweep only if explicitly allowed**

Project AGENTS says full suite verification defaults to GitHub Actions. Run targeted tests locally, then commit and push. If CI access fails, report the exact blocker.

- [ ] **Step 4: Commit and push**

Use HEREDOC style:

```bash
git add thesis_validator.py research_conductor.py tests/test_validator_process_gate.py tests/test_l6_l7_tool_order_gates.py docs/superpowers/plans/2026-05-27-process-validator.md
git commit -F - <<'COMMIT'
fix: move thesis process gates into validator

Wrong assumption: web_search had to precede analyze_trades, but those tools are
independent evidence sources. The real requirement is per-thesis presence:
before producing a thesis, the conductor must have consulted experiment history
and external evidence.

This removes the L6 ordering gate and the L7 conductor-local presence gate, then
adds a validator process tier that checks required tools once per thesis. Theses
that call analyze_trades before web_search can now pass if web_search was called
before thesis validation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
COMMIT
git push origin HEAD
```

- [ ] **Step 5: Watch CI**

Run:

```bash
gh run watch --exit-status
```

Expected: report the run URL and final status. If no workflow starts or auth is missing, report that instead of claiming CI passed.

