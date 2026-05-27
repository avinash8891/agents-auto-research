# Validator Contract Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract three multi-check contracts in `_validate_structural` into dedicated private helpers, matching the existing pattern used by `_validate_emergent_dimension` and `_validate_underexplored_dimensions`. Zero external behavior change — same rejection codes, same messages, same exception type, same fail-fast semantics within and across contracts. The win is internal: consistent organization, easier to evolve each contract independently.

**Architecture:** Three new private helper functions (`_validate_mechanism_dimension`, `_validate_thesis_specifies_change`, `_validate_expected_effects`) are extracted from inline raise sites in `_validate_structural`. Each owns one conceptual contract. `_validate_structural` becomes a thin orchestrator that calls helpers in dependency order. The raised exceptions, rejection codes, evidence dicts, and message strings are preserved verbatim.

**Tech Stack:** Python 3.13, no new dependencies, no new dataclasses.

**Why this is safe:** Each extraction moves existing code into a function — no logic changes. All 912 existing tests must pass without modification. The refactor is verifiable mechanically.

---

## Background — what we're aligning

The validator has a partial extraction pattern today:

| Contract | Today | After this PR |
|---|---|---|
| Emergent dimension | `_validate_emergent_dimension(thesis)` extracted helper | unchanged |
| Underexplored dimensions | `_validate_underexplored_dimensions(thesis, priors)` extracted helper | unchanged |
| Mechanism dimension | 3 scattered raises in `_validate_structural` + delegation to emergent | `_validate_mechanism_dimension(thesis, priors)` helper |
| Thesis specifies a change | 2 scattered raises in `_validate_structural` | `_validate_thesis_specifies_change(thesis)` helper |
| Expected effects well-formed | 1 raise + 1 loop in `_validate_structural` | `_validate_expected_effects(thesis)` helper |

After this PR, every multi-check contract has its own helper. Single-check contracts (thesis_id, hypothesis, mechanism, disqualifiers, falsification, dimension_novelty, causal_cluster, novel_connection) stay inline — extracting them would be over-engineering.

## File Structure

**Files modified:**
- `thesis_validator.py` — extract three helpers, slim `_validate_structural`

**Files NOT touched:**
- `behavior_signals.py` — unchanged
- All test files — must pass unchanged (proves zero behavior change)
- `research_types.py`, `research_prompts.py`, `rejection_artifact.py`, `research_conductor.py` — unchanged
- `scripts/check_prompt_drift.py` — unchanged

---

## Task 1: Extract _validate_mechanism_dimension

**Files:**
- Modify: `thesis_validator.py` (`_validate_structural` mechanism_dimension block + emergent delegation)

Current state in `_validate_structural` (verify exact line numbers before editing):

```python
    if not thesis.mechanism_dimension.strip():
        raise ThesisValidationError(
            "Missing mechanism_dimension. Every thesis must declare which "
            "dimension it explores: " + ", ".join(sorted(MECHANISM_DIMENSIONS)),
            rejection_code="structural_missing_mechanism_dimension",
        )
    known_dimensions = MECHANISM_DIMENSIONS | _known_emergent_dimension_names(prior_theses)
    if thesis.mechanism_dimension not in known_dimensions:
        raise ThesisValidationError(
            f"Invalid mechanism_dimension '{thesis.mechanism_dimension}'. "
            f"Must be one of: {sorted(known_dimensions)}",
            rejection_code="structural_mechanism_dimension_invalid",
            evidence={"mechanism_dimension": thesis.mechanism_dimension},
        )
    if thesis.mechanism_dimension == EMERGENT_MECHANISM_DIMENSION:
        _validate_emergent_dimension(thesis)
```

- [ ] **Step 1: Verify the exact source location**

Run `.venv/bin/grep -n "structural_missing_mechanism_dimension\|structural_mechanism_dimension_invalid\|_validate_emergent_dimension" thesis_validator.py` and read the lines around the matches to confirm the current shape matches the block above. If it differs, adapt the extraction accordingly.

- [ ] **Step 2: Add the helper**

Place the new helper IMMEDIATELY ABOVE `_validate_structural` so related contract helpers stay grouped (matches the existing placement of `_validate_emergent_dimension` and `_validate_underexplored_dimensions`).

```python
def _validate_mechanism_dimension(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]] | None,
) -> None:
    """Validate the mechanism_dimension contract.

    Three checks in dependency order:
      1. Field is non-empty.
      2. Value is a known dimension (core set OR a prior-emergent name).
      3. If "emergent", delegate to _validate_emergent_dimension for the
         conditional sub-contract (new_dimension_name + 3 emergent fields).

    Fail-fast within the contract: a missing-field failure does not check
    the value-validity rule, since the latter would fire a meaningless
    "'' is not a valid mechanism_dimension" rejection. Each gate's
    rejection_code is preserved from its pre-refactor identity.
    """
    if not thesis.mechanism_dimension.strip():
        raise ThesisValidationError(
            "Missing mechanism_dimension. Every thesis must declare which "
            "dimension it explores: " + ", ".join(sorted(MECHANISM_DIMENSIONS)),
            rejection_code="structural_missing_mechanism_dimension",
        )
    known_dimensions = MECHANISM_DIMENSIONS | _known_emergent_dimension_names(prior_theses)
    if thesis.mechanism_dimension not in known_dimensions:
        raise ThesisValidationError(
            f"Invalid mechanism_dimension '{thesis.mechanism_dimension}'. "
            f"Must be one of: {sorted(known_dimensions)}",
            rejection_code="structural_mechanism_dimension_invalid",
            evidence={"mechanism_dimension": thesis.mechanism_dimension},
        )
    if thesis.mechanism_dimension == EMERGENT_MECHANISM_DIMENSION:
        _validate_emergent_dimension(thesis)
```

- [ ] **Step 3: Replace the inline block in `_validate_structural`**

Replace the three-block sequence (missing → invalid → emergent delegation) with a single call:

```python
    _validate_mechanism_dimension(thesis, prior_theses)
```

- [ ] **Step 4: Verify all existing tests pass**

```
.venv/bin/python -m pytest tests/test_validator_gate_coverage.py tests/test_thesis_validator.py tests/test_validator_subsections.py tests/test_validator_stages.py tests/test_validator_challenge.py tests/test_stage1_rules.py tests/test_stage1_rules_part2.py tests/test_behavior_signals.py --tb=short
```
Expected: every test passes. No assertion failure is acceptable — this PR is supposed to be invisible externally.

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py
git commit -m "$(cat <<'EOF'
refactor(validator): extract _validate_mechanism_dimension helper

Groups the three mechanism_dimension checks (missing field, invalid
value, emergent path) into one private helper, matching the existing
pattern used by _validate_emergent_dimension and
_validate_underexplored_dimensions.

Zero behavior change: same rejection codes, same messages, same
fail-fast order within the contract. The win is internal — every
multi-check contract now has one owner function.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extract _validate_thesis_specifies_change

**Files:**
- Modify: `thesis_validator.py` (`_validate_structural` config_changes XOR + requested_primitives block)

Current state in `_validate_structural`:

```python
    if not thesis.config_changes and not thesis.requires_code_change:
        raise ThesisValidationError(
            "Thesis has neither config_changes nor requires_code_change=true",
            rejection_code="structural_missing_config_or_code_change",
        )
    if thesis.requires_code_change and not thesis.requested_primitives:
        raise ThesisValidationError(
            "requires_code_change theses must declare requested_primitives",
            rejection_code="structural_missing_requested_primitives",
        )
```

- [ ] **Step 1: Add the helper**

Place beside the other contract helpers (above `_validate_structural`):

```python
def _validate_thesis_specifies_change(thesis: ResearchThesis) -> None:
    """Validate that the thesis declares WHAT it changes.

    Two checks in dependency order:
      1. At least one of config_changes / requires_code_change is set.
      2. If requires_code_change=true, requested_primitives must be
         non-empty.

    Fail-fast within the contract: if neither change is specified, the
    requested_primitives check is meaningless. Each gate's rejection_code
    is preserved.
    """
    if not thesis.config_changes and not thesis.requires_code_change:
        raise ThesisValidationError(
            "Thesis has neither config_changes nor requires_code_change=true",
            rejection_code="structural_missing_config_or_code_change",
        )
    if thesis.requires_code_change and not thesis.requested_primitives:
        raise ThesisValidationError(
            "requires_code_change theses must declare requested_primitives",
            rejection_code="structural_missing_requested_primitives",
        )
```

- [ ] **Step 2: Replace the inline block**

```python
    _validate_thesis_specifies_change(thesis)
```

- [ ] **Step 3: Verify all existing tests pass**

```
.venv/bin/python -m pytest tests/test_validator_gate_coverage.py tests/test_thesis_validator.py tests/test_validator_subsections.py tests/test_validator_stages.py tests/test_validator_challenge.py tests/test_stage1_rules.py tests/test_stage1_rules_part2.py tests/test_behavior_signals.py --tb=short
```
Expected: every test passes.

- [ ] **Step 4: Commit**

```bash
git add thesis_validator.py
git commit -m "$(cat <<'EOF'
refactor(validator): extract _validate_thesis_specifies_change helper

Groups the config_changes-XOR-requires_code_change check and the
conditional requested_primitives check into one private helper. Both
gates own the same conceptual contract ("this thesis declares WHAT it
changes"); they should be in one function.

Zero behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Extract _validate_expected_effects

**Files:**
- Modify: `thesis_validator.py` (`_validate_structural` expected_effects presence + metric_unbacked loop)

Current state in `_validate_structural`:

```python
    if not thesis.expected_effects:
        raise ThesisValidationError(
            "Thesis has no expected_effects — cannot evaluate without predictions",
            rejection_code="structural_missing_expected_effects",
        )

    # ... falsification block in between ...

    for effect in thesis.expected_effects:
        if effect.metric not in BUILTIN_METRICS:
            if effect.metric not in thesis.required_diagnostics:
                raise ThesisValidationError(
                    f"Expected effect metric '{effect.metric}' is not a builtin metric "
                    f"and is not listed in required_diagnostics",
                    rejection_code="structural_expected_effect_metric_unbacked",
                    evidence={"metric": effect.metric},
                )
```

Note: in today's code, these two checks are separated by the falsification block and the disqualifiers block. Both checks still belong to one conceptual contract ("predictions are usable for evaluation"). Extracting them removes the visual separation.

- [ ] **Step 1: Add the helper**

```python
def _validate_expected_effects(thesis: ResearchThesis) -> None:
    """Validate the expected_effects contract.

    Two checks in dependency order:
      1. List is non-empty (predictions exist).
      2. Each effect's metric is either a builtin OR declared in
         required_diagnostics (predictions can be measured).

    Fail-fast within the contract: an empty list cannot have its metrics
    inspected. Each gate's rejection_code is preserved.
    """
    if not thesis.expected_effects:
        raise ThesisValidationError(
            "Thesis has no expected_effects — cannot evaluate without predictions",
            rejection_code="structural_missing_expected_effects",
        )
    for effect in thesis.expected_effects:
        if effect.metric in BUILTIN_METRICS:
            continue
        if effect.metric in thesis.required_diagnostics:
            continue
        raise ThesisValidationError(
            f"Expected effect metric '{effect.metric}' is not a builtin metric "
            f"and is not listed in required_diagnostics",
            rejection_code="structural_expected_effect_metric_unbacked",
            evidence={"metric": effect.metric},
        )
```

- [ ] **Step 2: Replace the two inline blocks in `_validate_structural`**

Remove the presence check at its current location and the for-loop at its current location. Replace BOTH with a single call to the new helper, placed where the presence check was (so the relative ordering with falsification + disqualifiers is preserved):

```python
    _validate_expected_effects(thesis)
```

The falsification check and the disqualifiers presence check stay in their current positions relative to the new helper call.

**IMPORTANT**: verify the current `_validate_structural` order before and after. The relative sequence with other checks must remain identical so that the FIRST gate to trip across the entire validator stays the same as before.

- [ ] **Step 3: Verify all existing tests pass**

```
.venv/bin/python -m pytest tests/test_validator_gate_coverage.py tests/test_thesis_validator.py tests/test_validator_subsections.py tests/test_validator_stages.py tests/test_validator_challenge.py tests/test_stage1_rules.py tests/test_stage1_rules_part2.py tests/test_behavior_signals.py --tb=short
```
Expected: every test passes.

- [ ] **Step 4: Commit**

```bash
git add thesis_validator.py
git commit -m "$(cat <<'EOF'
refactor(validator): extract _validate_expected_effects helper

Groups the expected_effects presence check and the per-effect
metric-backing check into one private helper. Both gates own the
contract "predictions are usable for evaluation"; combining them
removes the visual separation that previously had them split across
intervening checks (falsification, disqualifiers).

Zero behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Final verification

- [ ] **Step 1: Full test sweep**

```
.venv/bin/python -m pytest tests/ --ignore=tests/test_experiment_db_timestamps.py --ignore=tests/test_vps_runner_config.py -q 2>&1 | tail -5
```
Expected: `912 passed, 2 skipped` (no test count change — refactor adds no tests and removes no tests).

- [ ] **Step 2: Drift checker**

```
.venv/bin/python scripts/check_prompt_drift.py
```
Expected: `OK: no prompt drift detected.`

- [ ] **Step 3: Rejection code inventory**

```
.venv/bin/python -c "
import re
codes = sorted(set(re.findall(r'rejection_code\s*=\s*\"([a-z0-9_]+)\"', open('thesis_validator.py').read())))
print(f'Distinct rejection_code values: {len(codes)}')
"
```
Expected: 31 (unchanged from before this PR).

- [ ] **Step 4: Confirm `_validate_structural` is now a thin orchestrator**

Read `_validate_structural` end-to-end. Should be ~30 lines of mostly helper calls + simple presence checks. If it's still hundreds of lines with inline raises, something didn't get extracted.

---

## Out of scope (for follow-up PRs)

- Tier-1 batching (collecting multiple presence failures into one rejection) — separate PR
- Wrapping single-check contracts in helpers (over-engineering)
- Changing `_validate_config_validity` organization — not flagged in this audit; revisit if needed
- Changes to `behavior_signals.py` or the policy layer — already aligned

## Invariants this PR must preserve

1. Every existing test passes without modification
2. Rejection code count: 31 (unchanged)
3. Drift checker: OK
4. Same rejection_code for the same input (every code is preserved verbatim)
5. Same first-gate-wins order across the entire validator (fail-fast semantics intact)
6. No new imports, no new constants, no new exception types
