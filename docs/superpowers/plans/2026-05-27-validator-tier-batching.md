# Validator Tier Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fail-fast Stage 1 validation with tier-level validation: process first, behavioral critique second, mechanical batch third.

**Architecture:** `thesis_validator.py` keeps process validation as Tier 1, introduces `_run_behavioral_pass` for behavior and rethink-class signals, and collects mechanical validation failures into `BehaviorSignal` objects before raising either a single legacy code or `structural_mechanical_batch_failures`. `rejection_artifact.py` renders batched mechanical evidence as an explicit retry checklist.

**Tech Stack:** Python 3, pydantic `ResearchThesis`, existing `BehaviorSignal` policy layer, pytest.

---

### Task 1: Pin Tier Ordering and Mechanical Batching

**Files:**
- Create: `tests/test_validator_batching.py`
- Modify: `thesis_validator.py`

- [ ] **Step 1: Write failing tests**

Add tests for:
- multiple mechanical issues batch into `structural_mechanical_batch_failures`
- a single mechanical issue preserves its legacy rejection code
- behavioral signals fire before mechanical issues
- behavioral pass emits only the first signal
- config-key overlap and neighboring-threshold are behavioral signals
- mechanical validation runs when behavior is silent

- [ ] **Step 2: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_validator_batching.py -q
```

Expected: failures because the validator still runs structural before behavioral and has no mechanical batch collector.

- [ ] **Step 3: Implement minimal Tier 2 + Tier 3**

Add detector helpers for config overlap and neighboring threshold, `_run_behavioral_pass`, mechanical collection helpers, and route `validate_research_thesis` through process → behavior → mechanical.

- [ ] **Step 4: Run GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_validator_batching.py -q
```

Expected: all new tests pass.

### Task 2: Render Batched Mechanical Rejections

**Files:**
- Modify: `rejection_artifact.py`
- Test: existing or new rejection rendering test

- [ ] **Step 1: Write failing render test**

Add coverage proving `render_rejection_block` renders `evidence["failures"]` as a numbered “Fix all in one retry” list for `structural_mechanical_batch_failures`.

- [ ] **Step 2: Implement renderer branch**

When rendering a batched mechanical rejection, include the failure count and each sub-failure code/summary.

- [ ] **Step 3: Verify targeted tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_validator_batching.py tests/test_rejection_artifact.py -q
```

### Task 3: Regression Sweep and PR

**Files:**
- Verify only unless tests expose required fixture updates.

- [ ] **Step 1: Run validator and prompt checks**

Run:

```bash
.venv/bin/python -m pytest tests/test_validator_batching.py tests/test_validator_process_gate.py tests/test_validator_stages.py tests/test_validator_gate_coverage.py tests/test_thesis_validator.py tests/test_stage1_rules.py tests/test_stage1_rules_part2.py tests/test_behavior_signals.py tests/test_rejection_artifact.py -q
.venv/bin/python scripts/check_prompt_drift.py
```

- [ ] **Step 2: Grep order-sensitive fixtures**

Run:

```bash
rg -n "theme_keywords|config_validity_config_key_overlap_real|config_validity_neighboring_threshold|structural_mechanical_batch_failures" tests thesis_validator.py rejection_artifact.py
```

- [ ] **Step 3: Commit, push, PR**

Commit only this PR’s files, push `codex/validator-tier-batching`, and open a PR against `avinash8891/install-make-pages-interactive-skill`.
