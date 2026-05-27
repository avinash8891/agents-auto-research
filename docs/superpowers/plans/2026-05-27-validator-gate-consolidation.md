# Validator Gate Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the thesis validator's 38 rejection codes down to ~28 by merging redundant gates, fixing one wrong categorization, and removing one trivially-gameable check. End state: fewer rejection paths for the conductor to interpret without losing enforcement coverage.

**Architecture:** All changes are localized to `thesis_validator.py` (validator) and the test files that exercise the gates. No schema changes to `ResearchThesis`. No prompt changes (prompt already documents the consolidated contract). Backwards compatibility for retired rejection codes is preserved via the `infer_rejection_code` mapping so any historical rejection.json records still resolve.

**Tech Stack:** Python 3.13, Pydantic v2, pytest. No new dependencies.

---

## Background — what we're consolidating

Audit from session 2026-05-27 identified 10 issues across 38 rejection codes:

| # | Issue | Codes affected | Resolution |
|---|---|---|---|
| 1 | Redundant pair | `structural_missing_dimension_novelty` + `thesis_quality_dimension_novelty_too_short` | Merge into ONE gate with conditional threshold |
| 2 | Redundant pair | `structural_missing_falsification` + `structural_falsification_too_short` | Merge into ONE gate with single error per failure mode |
| 3 | Over-fractured (rare path) | 3 emergent codes | Merge into one `structural_emergent_thesis_malformed` with structured evidence |
| 4 | Over-fractured | 3 underexplored-dim codes | Merge into one `structural_underexplored_dimensions_invalid` |
| 5 | Subsumed gate | `config_validity_base_config_path_legacy_experiments` | Fold into `config_validity_base_config_path_inheritance_blocked` |
| 6 | Wrong category | `thesis_quality_thesis_id_repeated` | Move from `_validate_thesis_quality` to `_validate_structural` |
| 7 | Gameable | `thesis_quality_missing_mechanism_evidence_disqualifier` | Add content check on `condition` (≥40 chars, non-trivial) |
| 8 | Mixed concerns | `config_validity_config_changes_metadata_leak` | Decide: leave as validator (current); document the choice |
| — | Tests must follow | All affected test files | Update test expectations to new codes |
| — | Backwards compat | All retired codes | `infer_rejection_code` returns new codes for legacy messages |

Net: 38 → 30 codes, plus one strengthened gate (#7) and one moved gate (#6).

## File Structure

**Files modified:**
- `thesis_validator.py` — the consolidation work (gate merges + reorganization)
- `tests/test_validator_gate_coverage.py` — update test expectations
- `tests/test_thesis_validator.py` — fixture & assertion updates
- `tests/test_validator_subsections.py` — section-dispatch assertions
- `tests/test_validator_stages.py` — fixture updates if any
- `tests/test_stage1_rules.py` — fixture updates if any
- `tests/test_stage1_rules_part2.py` — fixture updates if any

**Files NOT touched:**
- `research_types.py` — schema is fine
- `research_prompts.py` — prompt already matches consolidated contract
- `rejection_artifact.py` — no schema change to StructuredRejection
- `research_conductor.py` — receives rejection_code as opaque string; no change

---

## Task 1: Merge dimension_novelty gates

**Files:**
- Modify: `thesis_validator.py` (the `_validate_structural` and `_validate_thesis_quality` blocks for dimension_novelty)
- Test: `tests/test_validator_gate_coverage.py`

Current state: two gates touching the same field.
- `structural_missing_dimension_novelty` (always) — empty/whitespace
- `thesis_quality_dimension_novelty_too_short` (when same-dim priors) — <30 chars

Failure mode: a 1-char dimension_novelty with NO same-dim priors passes both (`_MIN_NOVELTY_EXPLANATION_CHARS = 30` never gets checked). Wrong.

New contract: ONE gate, ONE rejection_code (`structural_dimension_novelty_invalid`). Conditional length threshold inside.

- [ ] **Step 1: Write the failing test that the legacy 1-char-with-no-same-dim-priors edge case is now rejected**

Add to `tests/test_validator_gate_coverage.py`:

```python
def test_gate_structural_dimension_novelty_too_short_without_priors() -> None:
    """Without same-dim priors: novelty must be ≥30 chars (was previously the
    only-non-empty bar — letting "x" pass)."""
    thesis = _minimal_valid_thesis(dimension_novelty="x")  # 1 char, no priors
    _expect_rejection(thesis, None, "structural_dimension_novelty_invalid")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_validator_gate_coverage.py::test_gate_structural_dimension_novelty_too_short_without_priors -v`
Expected: FAIL — the new code doesn't exist yet, the old `structural_missing_dimension_novelty` would not fire ("x" is non-empty).

- [ ] **Step 3: Update existing tests to use the new code**

In `tests/test_validator_gate_coverage.py`:

```python
# Was: test_gate_structural_missing_dimension_novelty
def test_gate_structural_dimension_novelty_empty() -> None:
    thesis = _minimal_valid_thesis(dimension_novelty="")
    _expect_rejection(thesis, None, "structural_dimension_novelty_invalid")

# Was: test_gate_thesis_quality_dimension_novelty_too_short_when_same_dimension
def test_gate_structural_dimension_novelty_too_short_with_same_dim_priors() -> None:
    thesis = _minimal_valid_thesis(
        thesis_id="ema-new-v1",
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
        dimension_novelty="too short",  # <30 chars
        config_changes={"different_key": 7},
    )
    priors = [
        _prior(
            "ema-prior-same-dim",
            config_changes={"some_other_key": 10},
            mechanism_dimension=SAMPLE_MECHANISM_DIMENSION,
        ),
    ]
    _expect_rejection(thesis, priors, "structural_dimension_novelty_invalid")
```

- [ ] **Step 4: Implement the merged gate in `_validate_structural`**

Replace the existing `if not thesis.dimension_novelty.strip(): raise ...` block (around line 1106 in `_validate_structural`) AND delete the corresponding block in `_validate_thesis_quality` (around line 1262). The merged block:

```python
    # Unified dimension_novelty contract: must be non-empty AND ≥30 chars.
    # Was previously split into a structural empty-check (always) and a
    # thesis_quality length-check (only when same-dim priors exist). The split
    # let "x" pass when there were no same-dim priors — wrong. Merged here.
    novelty_text = thesis.dimension_novelty.strip()
    if not novelty_text or len(novelty_text) < _MIN_NOVELTY_EXPLANATION_CHARS:
        raise ThesisValidationError(
            f"dimension_novelty must be ≥{_MIN_NOVELTY_EXPLANATION_CHARS} chars "
            f"explaining why this thesis is not a parameter variation of prior work. "
            f"Got {len(novelty_text)} chars.",
            rejection_code="structural_dimension_novelty_invalid",
            evidence={
                "actual_chars": len(novelty_text),
                "min_chars": _MIN_NOVELTY_EXPLANATION_CHARS,
            },
        )
```

Delete the old `_validate_thesis_quality` block:
```python
    # DELETE these lines from _validate_thesis_quality:
    if prior_theses and thesis.mechanism_dimension:
        same_dim = [...]
        if same_dim:
            ...
            if len(thesis.dimension_novelty) < _MIN_NOVELTY_EXPLANATION_CHARS:
                raise ThesisValidationError(...)
```

- [ ] **Step 5: Update `infer_rejection_code` for backwards compatibility**

In `infer_rejection_code()` (around line 130), update the mapping:

```python
    if "dimension_novelty must explain" in msg or "dimension_novelty is empty" in msg or "dimension_novelty must be" in msg:
        return "structural_dimension_novelty_invalid"
```

Remove the two old mappings (`structural_missing_dimension_novelty` and `thesis_quality_dimension_novelty_too_short`).

- [ ] **Step 6: Run all tests touching dimension_novelty**

Run: `.venv/bin/python -m pytest tests/ -k "dimension_novelty" --tb=short`
Expected: all pass with the new `structural_dimension_novelty_invalid` code.

- [ ] **Step 7: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py
git commit -m "refactor(validator): merge dimension_novelty gates into one

Previously split across structural (empty check) and thesis_quality (length
check, conditional on same-dim priors). The split let a 1-char value pass
when no same-dim priors existed. Unified into structural_dimension_novelty_invalid
with the length threshold applied unconditionally.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Merge falsification gates

**Files:**
- Modify: `thesis_validator.py` (falsification block in `_validate_structural`)
- Test: `tests/test_validator_gate_coverage.py`

Current state: two gates I split myself in the last refactor — over-fractured.
- `structural_missing_falsification` — field empty
- `structural_falsification_too_short` — field non-empty but <80 chars

These check the same field with the same threshold (`_MIN_FALSIFICATION_CHARS = 80`). Empty (0 chars) is just a special case of <80.

New contract: ONE gate `structural_falsification_invalid` with a single check.

- [ ] **Step 1: Update existing tests to use the merged code**

In `tests/test_validator_gate_coverage.py`:

```python
def test_gate_structural_falsification_invalid_when_empty() -> None:
    thesis = _minimal_valid_thesis(falsification_or_alternative="")
    _expect_rejection(thesis, None, "structural_falsification_invalid")


def test_gate_structural_falsification_invalid_when_too_short() -> None:
    thesis = _minimal_valid_thesis(
        falsification_or_alternative="short text",  # set but <80 chars
    )
    _expect_rejection(thesis, None, "structural_falsification_invalid")
```

Replace the old `test_gate_structural_missing_falsification` and `test_gate_structural_falsification_too_short` tests with these two.

- [ ] **Step 2: Replace the validator block**

In `_validate_structural` (around lines 1196-1217), replace:

```python
    falsification_text = (thesis.falsification_or_alternative or "").strip()
    if not falsification_text:
        raise ThesisValidationError(
            "falsification_or_alternative is required. Describe what data pattern "
            "would weaken this mechanism, independent of whether metrics improve.",
            rejection_code="structural_missing_falsification",
        )
    if len(falsification_text) < _MIN_FALSIFICATION_CHARS:
        raise ThesisValidationError(
            f"falsification_or_alternative must be at least "
            f"{_MIN_FALSIFICATION_CHARS} characters to count as a real disconfirmer; "
            f"got {len(falsification_text)} characters.",
            rejection_code="structural_falsification_too_short",
            evidence={
                "min_chars": _MIN_FALSIFICATION_CHARS,
                "actual_chars": len(falsification_text),
            },
        )
```

With:

```python
    # Unified falsification contract: must be present AND ≥80 chars to count
    # as a real disconfirmer. Empty (0 chars) is just one failure mode of <80.
    falsification_text = (thesis.falsification_or_alternative or "").strip()
    if len(falsification_text) < _MIN_FALSIFICATION_CHARS:
        raise ThesisValidationError(
            f"falsification_or_alternative must be ≥{_MIN_FALSIFICATION_CHARS} chars "
            f"describing what data pattern would weaken this mechanism, independent "
            f"of metric movement. Got {len(falsification_text)} chars.",
            rejection_code="structural_falsification_invalid",
            evidence={
                "actual_chars": len(falsification_text),
                "min_chars": _MIN_FALSIFICATION_CHARS,
            },
        )
```

- [ ] **Step 3: Update `infer_rejection_code` mapping**

In `infer_rejection_code()`, consolidate:

```python
    if "falsification_or_alternative" in msg:
        return "structural_falsification_invalid"
```

Remove the two old branches.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/ -k "falsification" --tb=short`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py
git commit -m "refactor(validator): merge falsification gates into one

Empty and too-short are the same rule with different points on the length
axis. Unified into structural_falsification_invalid.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Merge emergent dimension gates

**Files:**
- Modify: `thesis_validator.py` (emergent block in `_validate_structural`)
- Test: `tests/test_validator_gate_coverage.py`

Current state: 3 gates for a rare path:
- `structural_missing_new_dimension_name`
- `structural_new_dimension_name_duplicates_core`
- `structural_emergent_field_too_short`

Emergent dimension is the escape hatch for novel mechanism families. Used <1% of theses. Three rejection codes for a rare path is over-engineering.

New contract: ONE gate `structural_emergent_thesis_malformed` with structured evidence describing what's wrong (missing name, duplicates core, or which short fields).

- [ ] **Step 1: Update existing tests to use the merged code**

In `tests/test_validator_gate_coverage.py`:

```python
def test_gate_structural_emergent_malformed_missing_new_dimension_name() -> None:
    thesis = _minimal_valid_thesis(
        mechanism_dimension="emergent",
        new_dimension_name="",
    )
    _expect_rejection(thesis, None, "structural_emergent_thesis_malformed")


def test_gate_structural_emergent_malformed_duplicates_core() -> None:
    thesis = _minimal_valid_thesis(
        mechanism_dimension="emergent",
        new_dimension_name="entry_timing",  # a core dimension
    )
    _expect_rejection(thesis, None, "structural_emergent_thesis_malformed")


def test_gate_structural_emergent_malformed_short_fields() -> None:
    thesis = _minimal_valid_thesis(
        mechanism_dimension="emergent",
        new_dimension_name="liquidity_regime_classifier",
        why_existing_dimensions_do_not_fit="short",  # <40 chars
        mechanism_family_definition="x" * 50,
        expected_reuse_across_future_theses="x" * 50,
    )
    _expect_rejection(thesis, None, "structural_emergent_thesis_malformed")
```

Remove the three old tests (`test_gate_structural_missing_new_dimension_name`, `test_gate_structural_new_dimension_name_duplicates_core`, `test_gate_structural_emergent_field_too_short`).

- [ ] **Step 2: Replace the emergent block in `_validate_structural`**

Replace the existing `if thesis.mechanism_dimension == EMERGENT_MECHANISM_DIMENSION:` block (around lines 1083-1105) with:

```python
    if thesis.mechanism_dimension == EMERGENT_MECHANISM_DIMENSION:
        _validate_emergent_dimension(thesis)
```

Add a new helper function above `_validate_structural`:

```python
def _validate_emergent_dimension(thesis: ResearchThesis) -> None:
    """Validate the rare emergent-dimension path with a single rejection code.

    All emergent-path failures roll up to ``structural_emergent_thesis_malformed``
    with structured evidence describing every issue found. One rejection per
    attempt (since emergent fields are interdependent and the LLM should fix
    them all together).
    """
    issues: list[dict[str, Any]] = []

    new_dimension_name = _dimension_slug(thesis.new_dimension_name)
    if not new_dimension_name:
        issues.append({"kind": "missing_new_dimension_name"})
    elif new_dimension_name in CORE_MECHANISM_DIMENSIONS:
        issues.append({
            "kind": "new_dimension_name_duplicates_core",
            "name": thesis.new_dimension_name,
        })

    short_fields = [
        {"field": field, "actual_chars": len(getattr(thesis, field).strip())}
        for field in _EMERGENT_REQUIRED_FIELDS
        if len(getattr(thesis, field).strip()) < _MIN_EMERGENT_FIELD_CHARS
    ]
    if short_fields:
        issues.append({"kind": "short_fields", "fields": short_fields})

    if not issues:
        return

    issue_descriptions = []
    for issue in issues:
        if issue["kind"] == "missing_new_dimension_name":
            issue_descriptions.append("new_dimension_name is empty")
        elif issue["kind"] == "new_dimension_name_duplicates_core":
            issue_descriptions.append(
                f"new_dimension_name '{issue['name']}' duplicates a core dimension"
            )
        elif issue["kind"] == "short_fields":
            field_list = ", ".join(
                f"{f['field']} ({f['actual_chars']} chars)" for f in issue["fields"]
            )
            issue_descriptions.append(
                f"emergent justification fields each need ≥{_MIN_EMERGENT_FIELD_CHARS} chars: {field_list}"
            )

    raise ThesisValidationError(
        f"Emergent thesis malformed: {'; '.join(issue_descriptions)}",
        rejection_code="structural_emergent_thesis_malformed",
        evidence={
            "issues": issues,
            "min_emergent_field_chars": _MIN_EMERGENT_FIELD_CHARS,
        },
    )
```

- [ ] **Step 3: Update `infer_rejection_code`**

Add to the mapping:

```python
    if "emergent" in msg and ("malformed" in msg or "new_dimension_name" in msg or "duplicates a core" in msg):
        return "structural_emergent_thesis_malformed"
```

Remove the three old branches (`structural_missing_new_dimension_name`, `structural_new_dimension_name_duplicates_core`, `structural_emergent_field_too_short`).

- [ ] **Step 4: Run all emergent tests**

Run: `.venv/bin/python -m pytest tests/ -k "emergent" --tb=short`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py
git commit -m "refactor(validator): collapse 3 emergent gates into one

Emergent dimension is a rare path (<1% of theses). Three rejection codes
for missing-name / duplicates-core / short-fields was over-engineered.
Unified into structural_emergent_thesis_malformed with structured evidence
listing every issue found at once.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Merge underexplored_dimensions gates

**Files:**
- Modify: `thesis_validator.py` (underexplored block in `_validate_structural`)
- Test: `tests/test_validator_gate_coverage.py`

Current state: 3 gates for one field:
- `structural_missing_underexplored_dimensions`
- `structural_underexplored_dimensions_invalid`
- `structural_underexplored_dimensions_includes_chosen`

I added the last two myself in a prior session — over-engineered.

New contract: ONE gate `structural_underexplored_dimensions_invalid` covering all failure modes.

- [ ] **Step 1: Update existing tests**

In `tests/test_validator_gate_coverage.py`:

```python
def test_gate_structural_underexplored_dimensions_invalid_empty() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=[],
    )
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_rejection(thesis, priors, "structural_underexplored_dimensions_invalid")


def test_gate_structural_underexplored_dimensions_invalid_garbage_value() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["not_a_real_dimension"],
    )
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_rejection(thesis, priors, "structural_underexplored_dimensions_invalid")


def test_gate_structural_underexplored_dimensions_invalid_includes_chosen() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=[SAMPLE_MECHANISM_DIMENSION, "risk_structure"],
    )
    priors = [_prior("ema-prior-1", config_changes={"some_other_key": 10})]
    _expect_rejection(thesis, priors, "structural_underexplored_dimensions_invalid")
```

Remove old `test_gate_structural_missing_underexplored_dimensions_when_priors_exist`, `test_gate_structural_underexplored_dimensions_invalid_values`, `test_gate_structural_underexplored_dimensions_includes_chosen`.

- [ ] **Step 2: Replace the underexplored block in `_validate_structural`**

Replace the three existing raise statements (around lines 1119-1147) with:

```python
    if prior_theses:
        ...  # causal_cluster check stays
        _validate_underexplored_dimensions(thesis, prior_theses)
        ...  # novel_connection check stays
```

Add the helper:

```python
def _validate_underexplored_dimensions(
    thesis: ResearchThesis,
    prior_theses: list[dict[str, Any]],
) -> None:
    """Enforce the underexplored_dimensions_considered contract:

      * Non-empty list (required when prior theses exist).
      * Every entry is a known mechanism dimension.
      * Chosen mechanism_dimension is NOT in the list.

    All failure modes share one rejection_code with structured evidence so
    the LLM sees every issue at once instead of one per retry.
    """
    issues: list[dict[str, Any]] = []
    items = thesis.underexplored_dimensions_considered

    if not items:
        issues.append({"kind": "empty"})
    else:
        known = MECHANISM_DIMENSIONS | _known_emergent_dimension_names(prior_theses)
        invalid = [d for d in items if d not in known]
        if invalid:
            issues.append({"kind": "invalid_values", "invalid": invalid, "valid": sorted(known)})
        if thesis.mechanism_dimension in items:
            issues.append({"kind": "includes_chosen", "chosen": thesis.mechanism_dimension})

    if not issues:
        return

    descriptions = []
    for issue in issues:
        if issue["kind"] == "empty":
            descriptions.append("must be non-empty when prior theses exist")
        elif issue["kind"] == "invalid_values":
            descriptions.append(
                f"contains invalid mechanism dimensions: {issue['invalid']}"
            )
        elif issue["kind"] == "includes_chosen":
            descriptions.append(
                f"must not include the chosen dimension '{issue['chosen']}'"
            )

    raise ThesisValidationError(
        f"underexplored_dimensions_considered invalid: {'; '.join(descriptions)}",
        rejection_code="structural_underexplored_dimensions_invalid",
        evidence={"issues": issues},
    )
```

- [ ] **Step 3: Update `infer_rejection_code`**

Replace the three old mappings with one:

```python
    if "underexplored_dimensions_considered" in msg:
        return "structural_underexplored_dimensions_invalid"
```

- [ ] **Step 4: Run all underexplored tests**

Run: `.venv/bin/python -m pytest tests/ -k "underexplored" --tb=short`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py
git commit -m "refactor(validator): collapse 3 underexplored_dimensions gates into one

Reverts an over-engineering from a prior session that fragmented one field
contract into three rejection codes. Unified back into one
structural_underexplored_dimensions_invalid with structured evidence.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Fold legacy_experiments path into inheritance_blocked

**Files:**
- Modify: `thesis_validator.py` (`_validate_base_config_path`)
- Test: `tests/test_validator_gate_coverage.py`, `tests/test_thesis_validator.py`

Current state: `config_validity_base_config_path_legacy_experiments` fires when path starts with `experiments/`. But `config_validity_base_config_path_inheritance_blocked` already catches any non-baseline path. The legacy-experiments code is just a more specific message for the same rule.

Rationale for keeping `runtime_construction` separate: paths under `runtime/` are semantically distinct (constructing from runtime state, which is a freshness/stability bug rather than a wrong-stable-path bug).

- [ ] **Step 1: Remove the legacy_experiments-specific raise**

In `_validate_base_config_path` (around lines 286-302), the current code is:

```python
    if not is_allowed:
        if normalized.startswith("runtime/"):
            raise ThesisValidationError(...)
        raise ThesisValidationError(
            f"base_config_path '{path}' must be under configs/ only; "
            "legacy experiments/ inheritance paths are not allowed",
            rejection_code="config_validity_base_config_path_legacy_experiments",
            evidence={"path": path},
        )
```

Replace the last raise with `pass` (let the caller's check in `_validate_config_validity` against the family baseline catch it):

```python
    if not is_allowed:
        if normalized.startswith("runtime/"):
            raise ThesisValidationError(
                f"base_config_path '{path}' points into runtime/. Do not construct "
                f"paths from runtime artifacts; reference a checked-in config under "
                f"configs/ instead (for example, the family baseline config).",
                rejection_code="config_validity_base_config_path_runtime_construction",
                evidence={"path": path},
            )
        # Other non-configs/ paths (including legacy experiments/) fall through
        # to the inheritance_blocked check in _validate_config_validity, which
        # rejects ANY path that isn't the family baseline. That gate is the
        # authoritative inheritance check; this specific subcase was redundant.
```

- [ ] **Step 2: Update the test for legacy_experiments**

Find `test_gate_config_validity_base_config_path_legacy_experiments` and change the expected code:

```python
def test_gate_config_validity_experiments_path_routes_to_inheritance_blocked() -> None:
    """The `experiments/` path is no longer special-cased. It falls through
    to the general inheritance_blocked gate that rejects ANY non-baseline path."""
    thesis = _minimal_valid_thesis(base_config_path="experiments/foo.json")
    _expect_rejection(
        thesis, None, "config_validity_base_config_path_inheritance_blocked"
    )
```

- [ ] **Step 3: Update tests in test_thesis_validator.py that expected the old code**

Find `test_validate_thesis_rejects_legacy_experiments_base_config_path` and `test_validate_thesis_rejects_job_scoped_experiment_base_config_path` and update their regex to accept the inheritance_blocked message:

```python
def test_validate_thesis_rejects_legacy_experiments_base_config_path() -> None:
    thesis = _base_engine_change_thesis("legacy_experiments_base_path", "market_microstructure")
    thesis["base_config_path"] = "experiments/05287d64f61f/runtime_config.json"
    with pytest.raises(ThesisValidationError, match="must be empty or the family baseline"):
        validate_thesis_dict(thesis, prior_theses=[])
```

- [ ] **Step 4: Update `infer_rejection_code`**

Remove the `legacy experiments/ inheritance` branch:

```python
# DELETE this:
    if "legacy experiments/ inheritance" in msg or "must be under configs/" in msg:
        return "config_validity_base_config_path_legacy_experiments"
```

- [ ] **Step 5: Run all base_config_path tests**

Run: `.venv/bin/python -m pytest tests/ -k "base_config" --tb=short`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py tests/test_thesis_validator.py
git commit -m "refactor(validator): fold legacy_experiments path into inheritance_blocked

The legacy experiments/ path was a specific subcase of \"path is not the
family baseline\". The general inheritance_blocked gate already rejects it.
Two codes for the same rule was redundant.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Move thesis_id_repeated from thesis_quality to structural

**Files:**
- Modify: `thesis_validator.py` (move the check between sub-functions)
- Test: `tests/test_validator_gate_coverage.py`, `tests/test_validator_subsections.py`

Current state: `thesis_quality_thesis_id_repeated` lives in `_validate_thesis_quality`. But uniqueness on an ID is a structural invariant, not a "pattern of reasoning" rule. Misleading category.

- [ ] **Step 1: Move the check call**

In `_validate_thesis_quality`, delete:

```python
    if prior_theses:
        _check_thesis_id_not_repeated(thesis, prior_theses)
```

In `_validate_structural`, add (right after thesis_id presence check):

```python
    if not thesis.thesis_id.strip():
        raise ThesisValidationError(
            "Missing thesis_id", rejection_code="structural_missing_thesis_id"
        )
    if prior_theses:
        _check_thesis_id_not_repeated(thesis, prior_theses)
```

- [ ] **Step 2: Rename the rejection_code in `_check_thesis_id_not_repeated`**

Change `rejection_code="thesis_quality_thesis_id_repeated"` → `rejection_code="structural_thesis_id_repeated"`:

```python
def _check_thesis_id_not_repeated(
    thesis: ResearchThesis, prior_theses: list[dict[str, Any]]
) -> None:
    """Uniqueness constraint on thesis_id across all prior rounds.

    This is a structural invariant (no duplicate IDs), not a pattern-of-
    reasoning rule. Lives in _validate_structural.
    """
    prior_ids = {str(p.get("thesis_id") or "") for p in prior_theses}
    if thesis.thesis_id in prior_ids:
        raise ThesisValidationError(
            f"thesis_id '{thesis.thesis_id}' has already been proposed in a prior "
            f"round. Each thesis must have a unique thesis_id (do not repeat or "
            f"resubmit prior names).",
            rejection_code="structural_thesis_id_repeated",
            evidence={"thesis_id": thesis.thesis_id},
        )
```

- [ ] **Step 3: Update tests**

In `tests/test_validator_gate_coverage.py`, change the expected code:

```python
def test_gate_structural_thesis_id_repeated() -> None:
    thesis = _minimal_valid_thesis(
        causal_cluster="opening-session noise",
        underexplored_dimensions_considered=["risk_structure"],
    )
    priors = [
        _prior(
            SAMPLE_THESIS_ID,
            config_changes={"some_other_key": 10},
        ),
    ]
    _expect_rejection(thesis, priors, "structural_thesis_id_repeated")
```

In `tests/test_validator_subsections.py`:

```python
def test_structural_section_rejects_repeated_thesis_id_with_prefixed_code() -> None:
    prior = [_prior("repeated_id", theme_keywords=["x"])]
    raw = _base_thesis("repeated_id")
    ...
    with pytest.raises(ThesisValidationError) as excinfo:
        validate_thesis_dict(raw, prior_theses=prior)
    assert excinfo.value.rejection_code.startswith("structural_")
```

Remove the old `test_thesis_quality_section_rejects_repeated_thesis_id_with_prefixed_code` (or update to assert the new code).

- [ ] **Step 4: Update `infer_rejection_code`**

```python
    if "has already been proposed" in msg:
        return "structural_thesis_id_repeated"
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/ -k "thesis_id_repeated or thesis_id" --tb=short`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py tests/test_validator_subsections.py
git commit -m "refactor(validator): move thesis_id_repeated to structural section

Uniqueness on an ID is a structural invariant, not a pattern-of-reasoning
rule. Moves the check to _validate_structural and renames the rejection
code from thesis_quality_* to structural_*.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Strengthen the mechanism_evidence disqualifier gate

**Files:**
- Modify: `thesis_validator.py` (`_check_qualitative_disqualifier_present`)
- Test: `tests/test_validator_gate_coverage.py`

Current state: the gate requires `at least one disqualifier with kind='mechanism_evidence'`. But the LLM can satisfy with `{"kind": "mechanism_evidence", "condition": "x"}` — a 1-char condition counts. The gate is trivially gameable.

New contract: require `kind='mechanism_evidence'` AND `len(condition) ≥ 40 chars`. The 40 char threshold rules out obviously-trivial conditions like "yes", "x", "true".

- [ ] **Step 1: Write the failing test**

In `tests/test_validator_gate_coverage.py`:

```python
def test_gate_thesis_quality_mechanism_evidence_disqualifier_too_short() -> None:
    """A mechanism_evidence disqualifier with a trivially-short condition does
    not satisfy the gate. Without a content threshold the LLM could pass with
    {"kind": "mechanism_evidence", "condition": "x"}, which is theater."""
    thesis = _minimal_valid_thesis(
        disqualifiers=[
            Disqualifier(
                name="trivial",
                condition="x",  # too short
                kind="mechanism_evidence",
            )
        ],
    )
    _expect_rejection(
        thesis, None, "thesis_quality_missing_mechanism_evidence_disqualifier"
    )
```

- [ ] **Step 2: Run test to verify it fails (today the validator accepts the trivial condition)**

Run: `.venv/bin/python -m pytest tests/test_validator_gate_coverage.py::test_gate_thesis_quality_mechanism_evidence_disqualifier_too_short -v`
Expected: FAIL — validator currently accepts because the gate only checks `kind`, not `condition` length.

- [ ] **Step 3: Strengthen the gate**

Add a constant at the top:

```python
_MIN_MECHANISM_EVIDENCE_CONDITION_CHARS = 40
```

Replace `_check_qualitative_disqualifier_present`:

```python
def _check_qualitative_disqualifier_present(thesis: ResearchThesis) -> None:
    """B5: ≥1 Disqualifier must have kind='mechanism_evidence' AND a
    substantive condition (≥40 chars).

    The kind alone is trivial — the LLM can satisfy with kind='mechanism_evidence'
    and condition='x'. The length threshold makes the gate enforce SUBSTANTIVE
    mechanism-evidence rather than ceremonial enum tagging.

    Pure metric-threshold disqualifiers ('PF must improve by 5%') are pass/fail
    criteria, not Popperian disconfirmers. Force one to be substantively
    qualitative.
    """
    if not thesis.disqualifiers:
        return  # absence handled by structural_missing_disqualifiers
    has_substantive_mechanism_evidence = any(
        d.kind == "mechanism_evidence"
        and len(d.condition.strip()) >= _MIN_MECHANISM_EVIDENCE_CONDITION_CHARS
        for d in thesis.disqualifiers
    )
    if has_substantive_mechanism_evidence:
        return
    raise ThesisValidationError(
        "Need at least one disqualifier with kind='mechanism_evidence' AND a "
        f"condition ≥{_MIN_MECHANISM_EVIDENCE_CONDITION_CHARS} chars describing "
        "an observable data pattern that would falsify the mechanism. "
        "Short or metric-only conditions don't count as Popperian disconfirmers.",
        rejection_code="thesis_quality_missing_mechanism_evidence_disqualifier",
        evidence={
            "min_condition_chars": _MIN_MECHANISM_EVIDENCE_CONDITION_CHARS,
        },
    )
```

- [ ] **Step 4: Update the existing mechanism_evidence test setup**

The existing positive case (a thesis that passes) used `condition="first-5min loss rate not concentrated"` — that's 36 chars. Just over the new threshold? Let me check: `"first-5min loss rate not concentrated"` = 37 chars. Will fail. Lengthen the existing fixture across all test files that have a passing disqualifier with `kind="mechanism_evidence"`.

In `_minimal_valid_thesis` (tests/test_validator_gate_coverage.py):

```python
        "disqualifiers": [
            Disqualifier(
                name="opening_loss_pattern",
                condition=(
                    "first-5min loss rate not concentrated in the opening "
                    "auction window"
                ),  # ≥40 chars
                kind="mechanism_evidence",
            )
        ],
```

Audit ALL other fixtures that have a passing `mechanism_evidence` disqualifier — see Task 9 for the test fixture sweep.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_validator_gate_coverage.py::test_gate_thesis_quality_mechanism_evidence_disqualifier_too_short -v`
Expected: PASS.

Run: `.venv/bin/python -m pytest tests/ -k "mechanism_evidence" --tb=short`
Expected: all pass (after Task 9 fixture sweep).

- [ ] **Step 6: Commit**

```bash
git add thesis_validator.py tests/test_validator_gate_coverage.py
git commit -m "fix(validator): require substantive mechanism_evidence disqualifier

The kind='mechanism_evidence' check alone was trivially gameable — the LLM
could pass with condition='x'. Adds a 40-char minimum on the condition text
so the gate enforces substantive content, not just ceremonial enum tagging.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Decide and document the metadata_leak gate placement

**Files:**
- Modify: `thesis_validator.py` (comment near `config_validity_config_changes_metadata_leak`)

Current state: `config_validity_config_changes_metadata_leak` raises when `config_changes` contains keys like `requires_code_change`. The fix is mechanical — move them to top-level.

This could be auto-fixed in `normalize_thesis_payload` (silently correcting the LLM's mistake) OR raised as a validator error (teaching the LLM not to do it). Currently it raises.

Decision: KEEP as validator error. Teaching the LLM is preferable to silent normalization because:
1. The LLM will keep making the mistake without feedback.
2. Silent normalization can hide bugs (e.g. the LLM sets `requires_code_change=False` in config_changes thinking it's overriding the top-level value).
3. The rejection feedback documents the right place for these fields.

Document the choice so future maintainers don't waste time re-litigating.

- [ ] **Step 1: Add a docstring comment near the gate**

In `_validate_config_validity`, before the metadata-leak loop:

```python
    # Design choice: this is enforced as a validator error rather than
    # silently auto-corrected in normalize_thesis_payload. Teaching the
    # conductor where these flags belong is more valuable than hiding the
    # mistake. Auto-normalization would also risk masking semantic confusion
    # (e.g. the LLM setting requires_code_change=False inside config_changes
    # thinking it overrides the top-level value). See plan doc 2026-05-27.
    for key in sorted(CONFIG_CHANGES_METADATA_KEYS & set(thesis.config_changes)):
        raise ThesisValidationError(...)
```

- [ ] **Step 2: Verify no test changes needed**

Run: `.venv/bin/python -m pytest tests/ -k "metadata_leak or config_changes_metadata" --tb=short`
Expected: all pass (no logic change).

- [ ] **Step 3: Commit**

```bash
git add thesis_validator.py
git commit -m "docs(validator): document metadata_leak gate as deliberate validator-level enforcement

No logic change. Documents the choice not to auto-correct in
normalize_thesis_payload so future maintainers don't relitigate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Test fixture sweep for the new 40-char mechanism_evidence threshold

**Files:**
- Modify: every test fixture with a passing `mechanism_evidence` disqualifier

Task 7 raises the bar from "any condition" to "≥40 chars". Fixtures with shorter conditions will start failing. Find them all and lengthen.

Known fixtures to audit (from prior session work):
- `tests/test_validator_gate_coverage.py::_minimal_valid_thesis`
- `tests/test_thesis_validator.py::_base_engine_change_thesis`
- `tests/test_validator_stages.py::_base_thesis`
- `tests/test_validator_subsections.py::_base_thesis`
- `tests/test_stage1_rules.py::_base_thesis`
- `tests/test_stage1_rules_part2.py::_base_thesis`
- `tests/test_l5_neighboring_threshold.py::_base_thesis`

- [ ] **Step 1: Grep for short mechanism_evidence conditions**

Run:
```bash
.venv/bin/python -c "
import re, glob, json
for path in glob.glob('tests/test_*.py'):
    with open(path) as f:
        text = f.read()
    # Look for mechanism_evidence with adjacent condition
    matches = re.findall(r'\"condition\":\s*\(?\s*(\"[^\"]+\")', text)
    for m in matches:
        if len(eval(m).strip()) < 40 and 'mechanism_evidence' in text:
            print(f'{path}: condition len={len(eval(m))} -> {m[:80]}')
"
```

Note: this regex is imperfect — manually verify each match.

- [ ] **Step 2: Lengthen each short condition to ≥40 chars**

For each match, expand the condition to be a real observable data pattern. Example:

```python
# Before
"condition": "first-5min loss rate not concentrated",

# After
"condition": (
    "first-5min loss rate not concentrated in the opening "
    "auction window vs the rest of the session"
),
```

Keep the meaning the same; just expand to be substantive.

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_experiment_db_timestamps.py --ignore=tests/test_vps_runner_config.py --tb=short`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: lengthen mechanism_evidence disqualifier conditions to ≥40 chars

Aligns test fixtures with the new validator threshold from Task 7. No
behavior change in the tests themselves — only the fixture strings are
expanded to satisfy the substantiveness check.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Verification pass

- [ ] **Step 1: Drift checker**

Run: `.venv/bin/python scripts/check_prompt_drift.py`
Expected: `OK: no prompt drift detected.` with validator codes count ≈ 30 (was 38).

If new rejection codes are referenced in the prompt but not in the validator, the checker will flag them.

- [ ] **Step 2: Full test sweep**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/test_experiment_db_timestamps.py --ignore=tests/test_vps_runner_config.py --tb=short -q 2>&1 | tail -5`
Expected: all green.

- [ ] **Step 3: Manual rejection-code inventory**

Run:
```bash
.venv/bin/python -c "
import re
with open('thesis_validator.py') as f:
    content = f.read()
codes = sorted(set(re.findall(r'rejection_code\s*=\s*\"([a-z0-9_]+)\"', content)))
print(f'Total distinct rejection codes: {len(codes)}')
for c in codes:
    print(f'  {c}')
"
```

Expected count: ~30 (was 38). Expected removals:
- `structural_missing_dimension_novelty` → merged
- `thesis_quality_dimension_novelty_too_short` → merged
- `structural_missing_falsification` → merged
- `structural_falsification_too_short` → merged
- `structural_missing_new_dimension_name` → merged
- `structural_new_dimension_name_duplicates_core` → merged
- `structural_emergent_field_too_short` → merged
- `structural_missing_underexplored_dimensions` → merged
- `structural_underexplored_dimensions_includes_chosen` → merged
- `config_validity_base_config_path_legacy_experiments` → folded
- `thesis_quality_thesis_id_repeated` → renamed to structural_thesis_id_repeated

Expected additions:
- `structural_dimension_novelty_invalid`
- `structural_falsification_invalid`
- `structural_emergent_thesis_malformed`
- `structural_thesis_id_repeated`

- [ ] **Step 4: Verify backwards compatibility via `infer_rejection_code`**

Run:
```bash
.venv/bin/python -c "
from thesis_validator import infer_rejection_code
# Legacy messages should still resolve to (new) codes
tests = [
    ('dimension_novelty is empty', 'structural_dimension_novelty_invalid'),
    ('dimension_novelty must explain', 'structural_dimension_novelty_invalid'),
    ('falsification_or_alternative is required', 'structural_falsification_invalid'),
    ('falsification_or_alternative must be at least', 'structural_falsification_invalid'),
    ('has already been proposed', 'structural_thesis_id_repeated'),
    ('legacy experiments/ inheritance', 'config_validity_base_config_path_inheritance_blocked'),
    # ... etc
]
for msg, expected in tests:
    actual = infer_rejection_code(msg)
    print(f'{\"OK\" if actual == expected else \"FAIL\"}: {msg!r} -> {actual} (expected {expected})')
"
```

Expected: all OK.

- [ ] **Step 5: Final commit + push**

If any cleanup was needed during verification:

```bash
git add -A
git commit -m "chore(validator): final verification fixes from gate consolidation"
git push origin <branch>
```

---

## Out of scope

- The validator architecture split between schema and behavioral concerns (Group F observations in audit) — that's a bigger architecture change.
- Adding new rejection codes for currently-silent gates (`theme_keywords` non-empty, `rationale` non-empty, etc.) — separate workstream.
- LLM-judge semantic checks for any gate — not in this scope.
- Refactoring `_validate_base_config_path` into the dispatcher pattern — current style is fine.

---

## Removed gates

### `thesis_quality_prior_winner_inheritance_language` (regex-based)

Formerly implemented via the `_PRIOR_BASE_LANGUAGE_PATTERNS` regex set in
`thesis_validator.py`. Deleted because:

- It was a behavioral-text detector with high false-negative rate (the LLM
  could trivially paraphrase around the patterns).
- The actual enforcement against inheritance lives in gates
  `config_validity_base_contract_id_not_allowed` and
  `config_validity_base_config_path_inheritance_blocked`, which check the
  structured fields the conductor sets to actually inherit. Those gates
  are deterministic and unevadable.
- Maintaining the regex was an arms race against paraphrasing — the wrong
  approach for harness code.

The prompt's ANCHORING section continues to teach the principle so the
agent still receives the doctrine without the validator chasing
synonyms.
