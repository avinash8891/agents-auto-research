# Spec B — Conductor OUTPUT Schema Refactor

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** `2026-05-28-preflight-recall-design.md` (unified long-form context)
**Depends on:** none
**Blocks:** none (but Spec A's expanded `previous_thesis` rendering auto-improves once this lands)
**Parallel with:** Spec A

---

## 1. Goal

Complete the migration the schema author started: stop asking the conductor for legacy free-form fields whose structured siblings already exist in `ResearchThesis` and were explicitly designed as the canonical machine contract.

Two specific refactors:

- **Refactor A:** `required_diagnostics: list[str]` (prose) → `required_diagnostic_specs: list[DiagnosticRequirementSpec]` (structured).
- **Refactor B:** `evidence: list[str]` (legacy) → `evidence_citations: list[EvidenceCitation]` (typed, with `source` enum).

The schema-author comments in `research_types.py` explicitly call these out:
- On `DiagnosticRequirementSpec`: *"`required_diagnostics` remains as human-facing prose/rationale. This structured form is the canonical machine contract."*
- On `evidence_citations`: *"Replaces the legacy `evidence: list[str]` for enforcement purposes."*

Both structured fields exist; the OUTPUT prompt was never updated to actually request them, so they sit empty. This spec finishes the migration and activates the aspirational `evidence_citations` source-coverage validator rule that the schema comment promises.

## 2. Non-goals

- Anything not related to these two field migrations.
- Wider OUTPUT-schema audit. (If other legacy fields exist, they're for a separate spec.)
- Validator rules unrelated to the two migrations (Spec A handles `prior_lever_outcomes` content + `underexplored_dimensions_considered` misclassification; Spec C handles dedup override).

## 3. Background

### 3.1 Refactor A — what's broken today

`research_prompts.py` OUTPUT block asks for `required_diagnostics: list[str]`. Real fixture values from `tests/`:

- `["Max_drawdown and pct_profitable_windows vs base"]` — descriptive prose
- `["volatility_quintile_pf_spread"]` — terse snake_case key
- `["regime_breakdown"]` — terse key

The helper `diagnostic_contracts.build_required_diagnostic_specs` normalizes each prose string into a `DiagnosticRequirementSpec`:
- For terse keys → clean spec with `key="regime_breakdown"`, default `surface="strategy_diagnostics"`.
- For prose → mangled key (whatever `normalize_diagnostic_requirement` produces) with the prose copied to `description`.

Downstream consumers (the compiler, the verifier) read `required_diagnostic_specs`. When the keys are mangled prose, downstream rules misfire.

### 3.2 Refactor B — what's broken today

`research_prompts.py` OUTPUT block asks for `evidence: list[str]` (legacy). The typed `evidence_citations` with `source ∈ {web_search, analyst, source_code, experiment_result, memory}` exists in the schema, has fully wired Pydantic validation, but is empty in real outputs because the prompt doesn't ask for it.

The schema comment says *"Validator requires at least one with source='web_search' AND one with source='analyst' (when applicable)"* — that rule is **aspirational, not enforced today** because the field is empty.

### 3.3 What's NOT broken

Other OUTPUT fields are already structured and used (`expected_effects`, `disqualifiers`, `prior_lever_outcomes`, `alternatives_considered`, `theme_keywords`, etc.). Not touching them.

## 4. Architecture

```
research_prompts.py              ← edited (OUTPUT block only)
  └─ Lines 117-144 OUTPUT instructions: two field-instruction changes
       - required_diagnostics → required_diagnostic_specs (structured)
       - evidence → evidence_citations (typed)

diagnostic_contracts.py          ← edited
  └─ build_required_diagnostic_specs: when caller passes structured input,
     use it as-is. Prose-derivation retained as legacy fallback for
     DB-loaded historical attempts.

thesis_validator.py              ← edited
  ├─ §6.1: migrate rules that read required_diagnostics (prose) to read
  │  required_diagnostic_specs
  └─ §6.2: new rule — evidence_citations source coverage, with
     cold-start waiver (no trades file → analyst waived; web_search
     never waived)

research_types.py                ← unchanged
  Schema already contains DiagnosticRequirementSpec, EvidenceCitation,
  and the typed fields. No additions; we're activating existing structure.
```

## 5. Components

### 5.1 Refactor A — `required_diagnostics` → `required_diagnostic_specs`

#### Prompt change (`research_prompts.py`)

Replace, in the OUTPUT instructions block:

```
required_diagnostics   non-builtin metrics this thesis needs
```

With:

```
required_diagnostic_specs  list of {key, surface, description, payload_fields?} entries.
                           key MUST be snake_case (either a registered diagnostic key
                           or a stable identifier you'll commit to). description carries
                           the rationale (free-form). surface ∈ {metrics,
                           strategy_diagnostics, experiment_evaluation, any}.
                           Example:
                           [{"key": "volatility_quintile_pf_spread",
                             "surface": "strategy_diagnostics",
                             "description": "PF by overnight-ATR quintile to
                              test the vol-regime hypothesis."}]
```

Leave the legacy `required_diagnostics: list[str]` field present in the schema but no longer requested by the prompt. Backward-compat: DB-loaded historical attempts still have it.

#### Helper change (`diagnostic_contracts.build_required_diagnostic_specs`)

```python
def build_required_diagnostic_specs(
    required_diagnostics: list[str],
    existing_specs: list[dict | DiagnosticRequirementSpec] | None = None,
) -> list[DiagnosticRequirementSpec]:
    # CHANGED: existing_specs is now the primary path.
    # When the caller passes structured input, we use it as-is.
    # Prose normalization remains as fallback for empty existing_specs
    # (legacy attempts loaded from older DBs).
    ...
```

Behaviour matrix:

| `existing_specs` | `required_diagnostics` | Result |
|---|---|---|
| Non-empty | (any) | Use `existing_specs` verbatim (deduplicated by `key`). |
| Empty | Non-empty | Fall back to prose normalization (legacy). |
| Empty | Empty | Empty list. |

### 5.2 Refactor B — `evidence` → `evidence_citations`

#### Prompt change (`research_prompts.py`)

Replace any mention of `evidence: list[str]` in the OUTPUT instructions with:

```
evidence_citations   list of {source, citation} entries.
                     source ∈ {web_search, analyst, source_code, experiment_result, memory}.
                     citation is a short verbatim quote, URL, or path to the supporting evidence.
                     Validator requires at least one entry with source='web_search'
                     AND at least one with source='analyst'. The analyst requirement
                     is waived when no trades file is available for the round.
                     Example:
                     [{"source": "web_search", "citation": "intraday volatility microstructure paper, Smith 2024"},
                      {"source": "analyst", "citation": "round-3 analyst: low-vol opens have weaker PF"}]
```

Drop the legacy `evidence: list[str]` field instruction. Schema field remains for backward-compat on DB reads.

### 5.3 Validator changes

#### 6.1 (Spec B §6.1) — Migrate diagnostic-spec consumers

Rides Refactor A. Any rule in `thesis_validator.py` that today reads `thesis.required_diagnostics` (prose) migrates to read `[spec.key for spec in thesis.required_diagnostic_specs]` (or the full spec when description matters).

- **Affected rules:** enumerated during writing-plans by grepping `required_diagnostics` in `thesis_validator.py`.
- **Behavior change for new outputs:** none — the structured field is the canonical source.
- **Behavior for DB-loaded legacy attempts:** the prose `required_diagnostics` field is mirrored into a synthetic `required_diagnostic_specs` view via `build_required_diagnostic_specs`'s fallback path, so rules see a consistent shape.
- **Rejection codes:** unchanged.

#### 6.2 (Spec B §6.2) — New rule: `evidence_citations` source coverage

Activates the aspirational rule from the schema comment.

- **Check:** when `evidence_citations` is non-empty, require ≥1 entry with `source="web_search"` AND ≥1 with `source="analyst"`.
- **Cold-start waiver:** `analyst` requirement waived when `latest_outcome` indicates no trades file available. Matches the existing `no_trades_instruction` path in `research_conductor.py:162–168`. `web_search` is never waived (even on cold start, external evidence is required).
- **Severity:** hard reject.
- **Rejection code:** `structural_evidence_citations_coverage_insufficient`.
- **Evidence in rejection:** `{present_sources: [...], required_sources: [...], missing_sources: [...], waiver_applied: bool}`.

**Empty `evidence_citations`:** until Spec B ships in production, agents may produce empty `evidence_citations`. The rule fires only when the field is non-empty during a transition window, then becomes mandatory once telemetry shows new conductor runs reliably populate it (target: ≥95% over 50 rounds).

## 6. Configuration

No new env vars. The validator's cold-start waiver derives from existing `latest_outcome["trades_file"]`-or-empty check.

## 7. Error handling

| Failure | Behavior |
|---|---|
| Conductor returns malformed `required_diagnostic_specs` (e.g. missing `key`) | Standard Pydantic validation error; same rejection path as any other malformed schema field. |
| Conductor returns malformed `evidence_citations` (bad `source` enum value) | Same — Pydantic validation. |
| Conductor returns empty `evidence_citations` during transition window | §6.2 rule skipped (until threshold met); structural rule fires only on non-empty. |
| DB-loaded attempt has prose `required_diagnostics` but empty `required_diagnostic_specs` | Helper falls back to prose normalization. Read-only path. |

## 8. Testing

### 8.1 Unit

- `build_required_diagnostic_specs`: structured input passes through unchanged; empty structured + non-empty prose falls back to normalization; both empty → empty.
- Prompt assembly: new wording present for both fields; legacy `required_diagnostics` and `evidence` instruction strings absent.

### 8.2 Integration

- Captured-fixture conductor run produces a `ResearchThesis` with non-empty `required_diagnostic_specs` whose entries pass Pydantic validation.
- Captured-fixture conductor run produces `evidence_citations` containing ≥1 `web_search` + ≥1 `analyst` entry.
- Validator: thesis with `evidence_citations` lacking `web_search` → hard reject `structural_evidence_citations_coverage_insufficient`.
- Validator: cold-start path (no trades file) → analyst requirement waived; web_search still required.
- Validator: existing diagnostic-spec-reading rules still fire on the same conditions after migrating to `required_diagnostic_specs` (no regression in rejection codes).
- DB-loaded historical attempt with prose `required_diagnostics`: validator sees the mirrored `required_diagnostic_specs` view.

## 9. Migration plan

One PR:

1. `diagnostic_contracts.build_required_diagnostic_specs` updated to prefer structured input.
2. `research_prompts.py` OUTPUT block edited: two field-instruction replacements.
3. `thesis_validator.py`: migrate existing rules + add §6.2 new rule.
4. Captured-fixture integration test covering both refactors.
5. End-to-end test + commit per CLAUDE.md.

No coordination needed with Spec A (independent files except `thesis_validator.py` — merge-conflict risk only there, mechanical to resolve).

## 10. Telemetry contract

- `EVIDENCE_CITATIONS_COVERAGE_OK` / `EVIDENCE_CITATIONS_COVERAGE_MISSING` counters per round.
- `REQUIRED_DIAGNOSTIC_SPECS_STRUCTURED` / `REQUIRED_DIAGNOSTIC_SPECS_FROM_PROSE_FALLBACK` to track migration completeness.

After 50 rounds with ≥95% `_STRUCTURED` rate → prose normalization can be deprecated in a follow-up.

## 11. Success criteria

- Fresh conductor run produces `required_diagnostic_specs` with snake_case keys and valid `surface` values; the legacy `required_diagnostics` field is empty.
- Fresh conductor run produces `evidence_citations` with at least one `web_search` and at least one `analyst` entry (when applicable).
- Validator rejects a non-cold-start thesis whose `evidence_citations` lacks `web_search` coverage with the correct code.
- Validator accepts a cold-start thesis whose `evidence_citations` has `web_search` but no `analyst` (waiver applied).
- Existing diagnostic-spec-reading validator rules produce the same rejection codes as before for the same scenarios.
- DB-loaded historical attempts (with prose `required_diagnostics`) are processed without errors.

## 12. Coupling notes

- **With Spec A:** Spec A's expanded `previous_thesis` block (§5.4 in Spec A) explicitly defers surfacing of `evidence_citations` and `required_diagnostic_specs` until Spec B lands. Once Spec B ships, Spec A's render path auto-picks-up the populated structured fields (the render code in Spec A handles "if populated, prefer structured; else legacy" automatically). Coupling lives only in the rendering branch — no order-of-deployment hazard.
- **Independent of Specs C and D.**
