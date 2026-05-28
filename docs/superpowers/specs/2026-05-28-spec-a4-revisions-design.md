# Spec A4-revisions — §4 Field-Contract Refactor

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Amends:** Spec A4 (Conductor OUTPUT-Schema Instruction Overhaul) — `.context/attachments/T1Icrx/pasted_text_2026-05-28_20-51-16.txt`
**Depends on:** A4 (this spec rewrites parts of A4 §4 and §5; ships in the same PR or immediately after)
**Blocks:** none

---

## 0. Why this spec exists

A4 §4 documents 23 fields plus 5 proposed additions. Per the 2026-05-28 independent design audit, the field contracts are mostly sound but contain four classes of defects that will undermine A4's goal of "validator-compliant `ResearchThesis` JSON on the first attempt":

1. **Positional contracts** (e.g. `alternatives_considered[0]` is special) — LLMs comply unreliably with positional rules; named slots beat positions.
2. **Drift surfaces hardcoded in prose** (enums, accepted-marker lists, source taxonomies) — A4 §8 promises drift detection but the prose still contains the very strings that will drift.
3. **Cross-thesis consistency without state** — guidance like "use the same `theme_keyword` across theses touching the same lever" presumes the LLM sees prior state; the prompt doesn't always render it.
4. **Worked example fails its own rules** — A4 §5's example violates the §4.5 entry-[0] tiebreaker rule that A4 itself defines. The example is the LLM's primary pattern source; rule-failing examples teach the wrong contract.

This spec lands targeted refactors to fix all four classes, without re-opening A4's overall structure or §7's renderer design.

## 1. Goal

Make every §4 field contract independently teachable and machine-validatable:

- Every rule a field declares is enforceable by structural inspection (not prose regex).
- Every enum, marker list, and taxonomy lives in `research_types.py` as a named constant the §7 renderer pulls in.
- Every conditional requirement is gated on a value the LLM can read from the prompt, not infer.
- The worked example in A4 §5 passes every rule declared in §4.

## 2. Non-goals

- Re-litigating which fields survive A4a consolidation.
- Adding fields beyond what A4 §4.12 already proposes.
- Changing A4 §7's renderer architecture or §8's drift CI.
- Touching DOCTRINE prose beyond the cross-reference touch-points A4 §6 already requires.

## 3. Refactor 1 — Replace `alternatives_considered`'s positional contract

### 3.1 Problem

A4 §4.5 requires `alternatives_considered[0]` to be the "deepest near-equivalent alternative" with a `why_rejected` that contains a regex-matchable substring of an `evidence_citations` entry, a `disqualifiers` name, or a `MECHANISM_DIMENSIONS` value. Three failures:

- LLMs comply unreliably with "entry [0] is special" — they treat list slots as unordered.
- Substring regex against free-form prose is brittle: paraphrase breaks the match.
- A4 §5's worked example violates the rule: `"Too strict in low-vol regimes per fixture analysis."` matches none of the three accepted reference kinds.

### 3.2 Design

Replace the single `alternatives_considered: list[Alternative]` with two named fields:

```python
class TiebreakerRef(BaseModel):
    """Structured reference proving the rejected alternative was vetted, not handwaved."""
    kind: Literal["evidence_citation", "disqualifier", "mechanism_dimension"]
    value: str   # citation id, disqualifier name, or dimension name — looked up, not regex-matched

class DeepestAlternative(BaseModel):
    """The near-equivalent the proposer almost picked instead. Must cite a structured tiebreaker."""
    mechanism: str
    why_rejected: str = Field(min_length=40)
    tiebreaker: TiebreakerRef

class ResearchThesis(BaseModel):
    ...
    deepest_alternative: DeepestAlternative           # exactly one
    other_alternatives: list[Alternative]             # >=1 entry; same Alternative shape as today
```

Validator rules (post-refactor):

- `deepest_alternative` is required, non-null.
- `deepest_alternative.tiebreaker.value` must resolve:
  - `kind="evidence_citation"` → must equal the index or a generated stable id of an entry in `evidence_citations`.
  - `kind="disqualifier"` → must equal a `disqualifiers[i].name` value in this same thesis.
  - `kind="mechanism_dimension"` → must be a member of `MECHANISM_DIMENSIONS`.
- `len(other_alternatives) >= 1`; each `why_rejected` >= 40 chars.

Rejection codes:
- `structural_deepest_alternative_missing`
- `structural_deepest_alternative_tiebreaker_unresolved`
- `structural_other_alternatives_too_few`

### 3.3 Prompt §4 entries

Two entries replace the single `alternatives_considered` entry. The `Example:` slot for `deepest_alternative` MUST use a tiebreaker whose `value` is the literal name/dimension shown in the §5 worked example — guarantees structural pass.

### 3.4 Why this is better

- Named slot eliminates positional fragility.
- Structured tiebreaker eliminates regex-against-prose. Validation = dictionary lookup.
- `tiebreaker.kind` enum makes the LLM's choice of justification explicit and audit-able.
- Splitting the deepest alternative out gives §6 DOCTRINE a clean cross-reference target.

## 4. Refactor 2 — Promote enums and marker lists into `research_types.py`

### 4.1 Problem

A4 §4 currently embeds in prose:

- `PriorLeverOutcome.direction_then ∈ {tighten, loosen, extend, shorten, add, remove}` (§4.5).
- `PriorLeverOutcome.outcome ∈ {kept, killed, inconclusive}` (§4.5).
- `EvidenceCitation.source` narrowed to `{web_search, analyst}` in prose (§4.7).
- Overfit-marker enum `{trade_count_collapse, cross_symbol_divergence, regime_specific_overfit}` (§4.8).
- A4 §7's renderer can't introspect these — they will drift the moment the validator or schema disagrees with the prose.

### 4.2 Design

Each enum lives as a `Literal` (when used as a Pydantic field type) and as a paired module-level constant (for the §7 renderer and the validator to import):

```python
# research_types.py

PRIOR_LEVER_DIRECTIONS = (
    "tightened", "loosened", "extended", "shortened",
    "filtered_in", "filtered_out", "added", "removed",
)
PRIOR_LEVER_OUTCOMES = ("kept", "killed", "inconclusive")

EVIDENCE_SOURCES_FOR_DIVERSITY_GATE = ("web_search", "analyst")
# Note: EvidenceCitation.source enum is broader; this is the subset the
# DOCTRINE diversity rule counts toward.

OVERFIT_DISQUALIFIER_MARKERS = (
    "trade_count_collapse",
    "cross_symbol_divergence",
    "regime_specific_overfit",
    # extend here; renderer + validator pick up automatically.
)
```

The Pydantic field types reference the same tuples via `Literal[*PRIOR_LEVER_OUTCOMES]`-equivalent constructs (or duplicate the tuple values in the `Literal` and assert equality at import time — pick whichever the codebase already prefers).

### 4.3 Renderer change

§7's `scripts/render_output_schema.py` reads these constants and injects them into the rendered OUTPUT block, replacing the hardcoded prose lists. Adding/removing a marker = one constant edit; prompt and validator pick it up; §8 drift CI catches any miss.

### 4.4 Why this is better

Eliminates the drift A4 was built to prevent. The constants become the single source of truth for fields whose semantic vocabulary is open-ended (e.g. overfit markers will grow as new failure modes appear).

## 5. Refactor 3 — Gate conditional requirements on visible prompt state

### 5.1 Problem

Three §4 conditional requirements depend on context the LLM must infer:

- `novel_connection` required when cluster overlap with priors is "high" — but who tells the LLM the overlap is high?
- `underexplored_dimensions_considered` Producer guidance references a "FAMILY LANDSCAPE block above" — only works if that block is actually rendered.
- `theme_keywords` Producer guidance says "use the same token across theses touching the same lever" — only works if prior tokens are in the prompt.

If the upstream block isn't rendered, the LLM either hallucinates or omits — both reject downstream.

### 5.2 Design

The conductor system prompt MUST render, in a dedicated `## ROUND CONTEXT` block immediately above OUTPUT, three computed/looked-up signals the conditional rules depend on:

```
## ROUND CONTEXT (computed by conductor before LLM call)

cluster_overlap_with_priors: high | medium | low | none
  (high = >=50% theme_keyword overlap with any prior thesis in last 7 rounds)

dimensions_already_explored:
  - signal_quality (4 attempts; 1 kept)
  - regime_conditioning (1 attempt; killed)
  - ...

dimensions_unexplored:
  - portfolio_construction
  - alpha_decay
  - ...

theme_keywords_in_use (last 7 rounds, with attempt count):
  - stop_distance (5)
  - htf_gate (2)
  - ...
```

Each §4 conditional rule's `Required:` line then references the ROUND CONTEXT key by name:

- `novel_connection`: *"REQUIRED IF `cluster_overlap_with_priors == \"high\"` per ROUND CONTEXT."*
- `underexplored_dimensions_considered`: *"REQUIRED when ROUND CONTEXT lists any entries in `dimensions_unexplored`."*
- `theme_keywords` Producer guidance: *"Reuse a token from ROUND CONTEXT `theme_keywords_in_use` if your thesis touches the same lever; only invent a new token for a genuinely new lever."*

### 5.3 Renderer change

`research_prompts.py` (the assembler, not the §7 renderer) computes ROUND CONTEXT from the round's snapshot. Computation is already partially done for the cluster-fixation and whipsaw gates — this spec consolidates it into one block surfaced to the LLM, not just to the validator.

### 5.4 Why this is better

The LLM no longer has to infer round state. Validator rules and prompt instructions reference the same computed values, so a thesis that passes one passes the other. A4 §8's drift CI extends naturally: every ROUND CONTEXT key referenced in OUTPUT must exist in the conductor's context-builder.

## 6. Refactor 4 — Make the §5 worked example self-validating

### 6.1 Problem

A4 §11 requires the worked example to pass `validate_thesis_dict(...)`. That covers live validator rules but not rules A4 itself declares (e.g. the §4.5 tiebreaker rule). The current §5 example violates the tiebreaker rule.

### 6.2 Design

Move the worked example out of the spec markdown into `tests/fixtures/conductor_prompt_worked_example.json`. Add a test:

```python
def test_worked_example_satisfies_every_documented_rule():
    example = load_fixture("conductor_prompt_worked_example.json")
    # 1. Pydantic accepts:
    thesis = ResearchThesis.model_validate(example)
    # 2. Live validator accepts (no validator-level rejections):
    result = validate_thesis_dict(example, round_context=_fixture_round_context())
    assert result.accepted, result.rejection_code
    # 3. Every rule declared in the rendered OUTPUT prompt accepts:
    for rule_id, predicate in iter_prompt_declared_rules():
        assert predicate(example), f"worked example violates documented rule {rule_id}"
```

`iter_prompt_declared_rules()` reads the structured rule metadata that §7's renderer emits alongside the prompt (each field's `Validator rule:` slot becomes a callable predicate or a constant-table entry the test can iterate).

### 6.3 Update §5 example

After this spec lands, the example MUST use the post-refactor `deepest_alternative` + `other_alternatives` shape, with a structured `tiebreaker` that the test can resolve. Concretely, the existing thesis becomes:

```json
{
  ...,
  "evidence_citations": [
    {"source": "web_search", "citation": "Cont et al. on order-flow regime persistence", "id": "ec_1"},
    {"source": "analyst",    "citation": "round-3 analyst found 62% of stops occur in counter-HTF-trend setups", "id": "ec_2"}
  ],
  "deepest_alternative": {
    "mechanism": "ADX>30 entry filter",
    "why_rejected": "Too strict in low-vol regimes per round-3 analyst evidence — see ec_2 — would suppress signals where the HTF gate still admits them.",
    "tiebreaker": {"kind": "evidence_citation", "value": "ec_2"}
  },
  "other_alternatives": [
    {"mechanism": "session-time entry filter",
     "why_rejected": "Proxy for the regime problem rather than the structural fix."}
  ]
}
```

The `tiebreaker.value="ec_2"` resolves to `evidence_citations[1].id` — structural lookup, not regex.

### 6.4 Why this is better

The worked example becomes a test fixture, not a docstring. Spec edits that break the example fail CI immediately. The fixture becomes the LLM's canonical pattern AND the regression test for every rule the prompt declares.

## 7. Other §4 entry tweaks

Small fixes consolidated here so they ship in the same PR:

- **§4.1 `thesis_id`**: drop the "LLM emits today / system assigns post-A2" dual mode from the prompt. The §7 renderer omits this field from the OUTPUT section entirely; system-assignment is the only mode. Add to `_PROMPT_OMITTED_FIELDS`.
- **§4.1 `strategy_family`**: same treatment. Omitted from OUTPUT; documented in a separate `## SYSTEM-INJECTED FIELDS (do not emit)` appendix above OUTPUT so the LLM knows the key will appear in the final object but is not its responsibility.
- **§4.3 `thesis_role`**: tighten the empty-string escape. Either drop `""` from the Literal, or add an explicit `Required: Always — use \"\" only when none of the three roles apply AND state why in dimension_novelty.`
- **§4.6 `mechanism_family_definition`**: rewrite the Example to be an abstract family definition (not the thesis-specific instance), so future theses can pattern-match against it.
- **§4.7 `evidence_citations`**: add `citation: str = Field(min_length=20)` floor in the schema so "web_search: foo" can't pass.
- **§4.8 `expected_effects`**: collapse A4 §4.12's proposed `magnitude_min/max` into the existing `threshold: float | None` field by promoting `threshold` to a `magnitude_range: tuple[float, float] | None` shape, rather than adding two new fields alongside an existing one. (One concept, one field.)
- **§4.10 `required_diagnostic_specs`**: drop from OUTPUT until Spec B lands. Add to `_PROMPT_OMITTED_FIELDS` with a comment naming Spec B as the unblocker.
- **§4.10 `source_code_verification`**: add a process-tier gate — the conductor must have invoked the strategy-source-reading tool at least once during the attempt OR this field is structurally rejected with code `process_source_code_not_read`.

## 8. Migration plan

Single PR on top of A4 (or merged commits in A4's PR if not yet shipped):

1. **`research_types.py`**:
   - Add `TiebreakerRef`, `DeepestAlternative` models.
   - Add `deepest_alternative` and `other_alternatives` fields; mark old `alternatives_considered` deprecated (per A4 §2.1 no-backcompat policy: delete it in same commit).
   - Add `PRIOR_LEVER_DIRECTIONS`, `PRIOR_LEVER_OUTCOMES`, `EVIDENCE_SOURCES_FOR_DIVERSITY_GATE`, `OVERFIT_DISQUALIFIER_MARKERS` constants.
   - Collapse `threshold` → `magnitude_range` on `ExpectedEffect`.
   - Add `EvidenceCitation.citation` `min_length=20`.
   - Add `id` (stable identifier) to `EvidenceCitation` so tiebreaker references resolve.
2. **`thesis_validator.py`**:
   - Implement `structural_deepest_alternative_*` rejection codes.
   - Implement `process_source_code_not_read` gate (uses `ConductorResult.tools_called`).
   - Replace any hardcoded marker/enum prose-references with imports from the new constants.
3. **`research_prompts.py`** (or `autoresearch_research.py`, wherever the context is assembled):
   - Build the `## ROUND CONTEXT` block from existing snapshot data.
   - Add the `## SYSTEM-INJECTED FIELDS (do not emit)` appendix.
4. **`scripts/render_output_schema.py`** (A4 §7):
   - Read the new constants for enum/marker rendering.
   - Read field metadata (description + validator-rule dict) and emit per-field entries.
   - Render the post-refactor `deepest_alternative` + `other_alternatives` entries instead of `alternatives_considered`.
   - Omit fields in `_PROMPT_OMITTED_FIELDS` (now including `thesis_id`, `strategy_family`, `required_diagnostic_specs`).
5. **`tests/fixtures/conductor_prompt_worked_example.json`**: new file replacing A4 §5's inline JSON.
6. **`tests/test_conductor_prompt_v3.py`**:
   - Assert the fixture passes Pydantic + live validator + every prompt-declared rule (per §6.2).
   - Assert `## ROUND CONTEXT` block is present and contains all keys referenced by conditional `Required:` lines.
   - Assert no hardcoded enum/marker strings remain in the rendered prompt (they must come from constants).
7. **`scripts/check_prompt_drift.py`** (A4 §8):
   - Extend: every key in `## ROUND CONTEXT` referenced by OUTPUT must be produced by the conductor's context-builder.
   - Extend: every constant imported from `research_types.py` and rendered into the prompt must be a tuple/frozenset (not a list literal in prose).

## 9. Risk and rollback

**Risks:**

- **Schema change touches every downstream consumer of `alternatives_considered`.** Mitigation: per A4 §2.1, no backwards compatibility; one PR replaces the field everywhere. Compile-time errors will surface all sites.
- **`magnitude_range` collapse breaks code that reads `threshold`.** Mitigation: grep for `.threshold` on `ExpectedEffect` and migrate at the same time. Spec B-style telemetry that read `threshold` migrates with it.
- **ROUND CONTEXT computation pulls already-validated data into the prompt path; bugs there could distort LLM behavior.** Mitigation: ROUND CONTEXT is a pure projection of snapshot state already used by the validator; same source of truth, new render target. Unit-test the projection independently.

**Rollback:** revert the PR. A4 reverts to its hand-written §4 entries; the constants and `deepest_alternative` refactor are removed; the worked-example fixture goes back to being a docstring. Lose the structural rule enforceability until re-landed.

## 10. Success criteria

- `tests/test_conductor_prompt_v3.py` asserts the worked-example fixture passes (a) Pydantic, (b) `validate_thesis_dict`, and (c) every rule declared in the rendered OUTPUT prompt.
- `## ROUND CONTEXT` block is present in the rendered conductor prompt; every conditional `Required:` line references a key that exists in it.
- No hardcoded enum/marker string list appears in the rendered prompt — every such list comes from a `research_types.py` constant.
- `deepest_alternative` and `other_alternatives` fields replace `alternatives_considered` end-to-end (schema, validator, prompt, contract, evaluator).
- Post-deploy: conductor's per-attempt acceptance rate inherits A4's ≥50% target; A4-revisions adds the secondary metric "% of accepted theses whose `deepest_alternative.tiebreaker` resolves on first emit" (target ≥80%) as a measure of whether the structural-tiebreaker design is actually used as intended (vs the LLM picking a random `evidence_citation` to satisfy the gate).

## 11. Out of scope

- Adding fields beyond what A4 §4.12 already proposes.
- Re-litigating A4a's 12 dropped fields.
- DOCTRINE rewrites beyond updating the two cross-references for `alternatives_considered` → `deepest_alternative` / `other_alternatives`.
- Multi-model evaluation of the refactored prompt.
