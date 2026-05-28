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
    """Structured reference proving the rejected alternative was vetted, not handwaved.

    The LLM does NOT emit citation ids. It references evidence by 1-indexed position
    (citation_1, citation_2, ...) printed in the rendered prompt by the conductor.
    """
    kind: Literal["evidence_citation", "disqualifier", "mechanism_dimension"]
    value: str

class DeepestAlternative(BaseModel):
    """The near-equivalent the proposer almost picked instead. Must cite a structured tiebreaker."""
    mechanism: str
    why_rejected: str = Field(min_length=40)
    tiebreaker: TiebreakerRef

class Alternative(BaseModel):
    """Other rejected alternatives. lighter_tiebreaker is optional — populating it
    signals deeper vetting and is graded by reviewers, not the validator."""
    mechanism: str
    why_rejected: str = Field(min_length=40)
    lighter_tiebreaker: TiebreakerRef | None = None

class ResearchThesis(BaseModel):
    ...
    deepest_alternative: DeepestAlternative           # exactly one
    other_alternatives: list[Alternative]             # >=1 entry
```

**Tiebreaker id strategy — server-assigned, not LLM-emitted.** The conductor's
output assembler renders `evidence_citations` with auto-assigned positional ids
(`citation_1`, `citation_2`, ...) in the prompt above OUTPUT (so the LLM can
reference them) and accepts the same ids back in `tiebreaker.value`. The LLM
emits `evidence_citations` as an ordered list; positions become ids. No LLM
bookkeeping. Validator resolves `value="citation_2"` → `evidence_citations[1]`.

Validator rules (post-refactor):

- `deepest_alternative` is required, non-null.
- `deepest_alternative.tiebreaker.value` must resolve:
  - `kind="evidence_citation"` → must equal `citation_N` where `1 <= N <= len(evidence_citations)`.
  - `kind="disqualifier"` → must equal a `disqualifiers[i].name` value in this same thesis.
  - `kind="mechanism_dimension"` → must be a member of `MECHANISM_DIMENSIONS`.
- `len(other_alternatives) >= 1`; each `why_rejected` >= 40 chars.
- `other_alternatives[i].lighter_tiebreaker`, when present, must resolve by the same rules.

Rejection codes:
- `structural_deepest_alternative_missing`
- `structural_deepest_alternative_tiebreaker_unresolved`
- `structural_other_alternatives_too_few`
- `structural_lighter_tiebreaker_unresolved` (when populated but invalid)

### 3.5 Category ordering — fields referenced must render before fields referencing them

`deepest_alternative.tiebreaker` references entries in `evidence_citations`,
`disqualifiers`, or `MECHANISM_DIMENSIONS`. The LLM emits left-to-right; if §4
renders `deepest_alternative` before its targets, the LLM commits to a tiebreaker
name without knowing what targets it will populate.

The §7 renderer MUST emit §4 categories in this order:

1. Identity (4.1) — system-injected; omitted from LLM-facing OUTPUT
2. Core description (4.2) — `hypothesis`, `mechanism`
3. Positioning + classification (4.3) — `mechanism_dimension`, `theme_keywords`, `thesis_role`
4. Novelty justification (4.4) — `dimension_novelty`, `novel_connection`, `underexplored_dimensions_considered`
5. Evidence (4.7, **moved up**) — `evidence_citations`
6. Predictions + falsification (4.8, **moved up**) — `expected_effects`, `disqualifiers`
7. Alternatives (4.5, **moved down**) — `deepest_alternative`, `other_alternatives`, `prior_lever_outcomes`
8. Emergent-dimension contract (4.6)
9. Config + engine (4.9)
10. Diagnostics + code grounding (4.10)
11. Optional escape hatch (4.11)

Validator rule: `scripts/check_prompt_drift.py` asserts referenced-field
categories render before referencing-field categories.

### 3.3 Prompt §4 entries — drafted

Two entries replace the single `alternatives_considered` entry. Drafts:

```
- deepest_alternative
    Type:        DeepestAlternative
    Format:      typed object — see Inner shape
    Inner shape: DeepestAlternative = {
                     mechanism:    str,
                     why_rejected: str (>=40 chars),
                     tiebreaker:   TiebreakerRef = {
                         kind:  Literal["evidence_citation",
                                        "disqualifier",
                                        "mechanism_dimension"],
                         value: str   # citation_N | disqualifier name | dimension name
                     }
                 }
    Source set:  Free; tiebreaker.value constrained by lookup
    Token cap:   ~80 words total
    Required:    Always
    Meaning:     The single near-equivalent mechanism you almost picked. The
                 tiebreaker is a structured reference to the specific evidence,
                 disqualifier, or dimension that made you pick the current
                 hypothesis instead.
    Producer guidance: Pick the alternative that, if you reversed the decision,
                       would produce a roughly equally strong thesis. The
                       tiebreaker must reference a target you have already
                       committed to in this thesis: a citation by id (citation_1,
                       citation_2, ...) shown in ROUND CONTEXT under
                       `evidence_citations_available_ids`, OR a disqualifier
                       by its `name`, OR a `mechanism_dimension` from the enum.
                       The why_rejected prose can paraphrase, but the tiebreaker
                       must resolve by exact match.
    Validator rule:    tiebreaker.value resolves by exact match against the
                       referenced target (positional id, disqualifier name,
                       or dimension enum). Rejection codes:
                         structural_deepest_alternative_missing
                         structural_deepest_alternative_tiebreaker_unresolved
    Example:     {"mechanism": "ADX>30 entry filter",
                  "why_rejected": "Too strict in low-vol regimes per round-3
                                   analyst evidence (citation_2) — would
                                   suppress signals where the HTF gate still
                                   admits them.",
                  "tiebreaker": {"kind": "evidence_citation",
                                 "value": "citation_2"}}
```

```
- other_alternatives
    Type:        list[Alternative]
    Format:      typed list — see Inner shape
    Inner shape: Alternative = {
                     mechanism:         str,
                     why_rejected:      str (>=40 chars),
                     lighter_tiebreaker: TiebreakerRef | None
                 }
    Source set:  Free
    Token cap:   >=1 entry; <=4 entries
    Required:    Always (>=1 entry)
    Meaning:     Other rejected alternatives. lighter_tiebreaker is optional;
                 populating it signals deeper vetting and helps reviewers grade
                 the proposal but is not gated by the validator (except for
                 resolution when present).
    Producer guidance: Each entry is a DIFFERENT mechanism, not a parameter
                       variant. why_rejected must be substantively distinct
                       from deepest_alternative.why_rejected. Use
                       lighter_tiebreaker when you have a structural anchor;
                       leave null when the rejection is prose-only.
    Validator rule:    >=1 entry; each why_rejected >=40 chars. When
                       lighter_tiebreaker is non-null, it resolves by the same
                       rules as deepest_alternative.tiebreaker. Rejection codes:
                         structural_other_alternatives_too_few
                         structural_lighter_tiebreaker_unresolved
    Example:     [
                   {"mechanism": "session-time entry filter",
                    "why_rejected": "Proxy for the regime problem rather
                                     than the structural fix; cannot
                                     distinguish high-vol from low-vol
                                     opens within the same session.",
                    "lighter_tiebreaker": null}
                 ]
```

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

Each enum lives as a module-level constant. Pydantic `Literal` types cannot
unpack tuples, so the canonical pattern is: declare the `Literal` and the
constant separately, assert equality at import time, validator and renderer
import the constant.

```python
# research_types.py

PRIOR_LEVER_OUTCOMES = ("kept", "killed", "inconclusive")
# Paired Literal — assertion below guards drift.
_PriorLeverOutcomeLiteral = Literal["kept", "killed", "inconclusive"]
assert set(typing.get_args(_PriorLeverOutcomeLiteral)) == set(PRIOR_LEVER_OUTCOMES)

# PriorLeverOutcome.direction_then stays a free `str`, with a guidance list of
# common verbs in the rendered prompt. Reason: levers come in shapes the closed
# enum cannot anticipate ("swapped_for", "throttled_by_regime", "conditioned_on_X").
# Closing the enum would force a schema migration each time a new lever shape
# appears. The validator rule is "non-empty + past-tense verb form recommended";
# the rendered prompt shows PRIOR_LEVER_DIRECTION_HINTS as guidance, not gate.
PRIOR_LEVER_DIRECTION_HINTS = (
    "tightened", "loosened", "extended", "shortened",
    "filtered_in", "filtered_out", "added", "removed",
)

# Full EvidenceCitation.source enum — every value the schema accepts.
EVIDENCE_SOURCES = (
    "web_search", "analyst", "source_code", "experiment_result", "memory",
)
# Subset counted by the DOCTRINE diversity gate. Rendered as a distinct
# "Diversity gate counts:" line in the prompt, separate from the full enum
# rendering, so the LLM doesn't conclude the gate-subset IS the enum.
EVIDENCE_SOURCES_FOR_DIVERSITY_GATE = ("web_search", "analyst")

# Open-ended marker list. The validator accepts either a structural name match
# OR a keyword match against OVERFIT_KEYWORD_HINTS in the `condition` text.
# Two paths: structured (preferred) or prose (escape hatch for newly-discovered
# failure modes the LLM identifies before the enum is updated).
OVERFIT_DISQUALIFIER_MARKERS = (
    "trade_count_collapse",
    "cross_symbol_divergence",
    "regime_specific_overfit",
)
OVERFIT_KEYWORD_HINTS = (
    "overfit", "overfitting", "lookahead", "selection_bias",
    "data_snooping", "regime_specific", "symbol_specific",
)
```

Validator rule for the overfit gate becomes:
- ≥1 entry in `disqualifiers` where `name in OVERFIT_DISQUALIFIER_MARKERS`
  OR `any(hint in condition.lower() for hint in OVERFIT_KEYWORD_HINTS)`.

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

The conductor system prompt MUST render, in a dedicated `## ROUND CONTEXT` block immediately above OUTPUT, computed signals the conditional rules depend on:

```
## ROUND CONTEXT (computed by conductor before LLM call)

Treat values below as ground truth. Reference them literally in conditional
fields below; do not paraphrase entries or invent counts not shown here.

family_cluster_density: high | medium | low | none
  (high = the family has >=3 prior theses sharing >=2 theme_keywords each
   in the last 7 rounds; signals you must work harder on novelty)

dimensions_already_explored: (capped at 12; tail summarized)
  - signal_quality (4 attempts; 1 kept)
  - regime_conditioning (1 attempt; killed)
  - ...
  (and 0 more)

dimensions_unexplored: (capped at 12)
  - portfolio_construction
  - alpha_decay
  - ...

emergent_dimensions_in_use: (capped at 8)
  - session_microstructure (introduced job-9-round-2)
  - liquidity_asymmetry (introduced job-11-round-1)

theme_keywords_in_use: (top 12 by attempt count; tail summarized)
  - stop_distance (5)
  - htf_gate (2)
  - ...
  (and 38 more)

evidence_citations_available_ids: citation_1, citation_2, ...
  (these are the ids you may reference in tiebreaker.value; populated
   once you have committed your evidence_citations list)
```

Each §4 conditional rule's `Required:` line then references the ROUND CONTEXT key by name:

- `novel_connection`: *"REQUIRED IF `family_cluster_density == \"high\"` per ROUND CONTEXT AND your `theme_keywords` overlap any prior thesis's keywords (post-emit gate; the validator computes per-thesis overlap once you have committed your keywords)."*
- `underexplored_dimensions_considered`: *"REQUIRED when ROUND CONTEXT lists any entries in `dimensions_unexplored`. Pick from that list; cite only dimensions shown there."*
- `theme_keywords` Producer guidance: *"Reuse a token from ROUND CONTEXT `theme_keywords_in_use` if your thesis touches the same lever; only invent a new token for a genuinely new lever."*
- `new_dimension_name` (emergent path): *"Must not duplicate any entry in `emergent_dimensions_in_use` or any value in `MECHANISM_DIMENSIONS`."*

**`novel_connection` trigger split into two parts.** `family_cluster_density`
is a pre-emit signal that warns the LLM. The hard validator rule fires
post-emit, when the LLM's own `theme_keywords` exist and per-thesis overlap
is computable. This separates the "warn the LLM" job from the "validate the
output" job — the LLM has visible state to react to; the validator has
ground truth to gate on.

**Size caps are hard.** Each list is capped at the values shown; the renderer
sorts by attempt count (or recency, for `emergent_dimensions_in_use`) and
emits a `(and N more)` tail line so the LLM knows the view is truncated.

### 5.3 Renderer change

`research_prompts.py` (the assembler, not the §7 renderer) computes ROUND CONTEXT from the round's snapshot. Computation is already partially done for the cluster-fixation and whipsaw gates — this spec consolidates it into one block surfaced to the LLM, not just to the validator.

### 5.4 Why this is better

The LLM no longer has to infer round state. Validator rules and prompt instructions reference the same computed values, so a thesis that passes one passes the other. A4 §8's drift CI extends naturally: every ROUND CONTEXT key referenced in OUTPUT must exist in the conductor's context-builder.

## 6. Refactor 4 — Make the §5 worked example self-validating

### 6.1 Problem

A4 §11 requires the worked example to pass `validate_thesis_dict(...)`. That covers live validator rules but not rules A4 itself declares (e.g. the §4.5 tiebreaker rule). The current §5 example violates the tiebreaker rule.

### 6.2 Design

Move the worked example out of the spec markdown into
`tests/fixtures/conductor_prompt_worked_example.json`. Alongside it, a
**negative-example directory** `tests/fixtures/conductor_prompt_rejections/`
holds one fixture per rejection code, each minimally violating one rule.

**Structured rule metadata is part of A4-revisions, not a future TBD.**
The §7 renderer emits, alongside `prompts/conductor_output_section.md`, a
sidecar file `prompts/conductor_output_rules.json` with the shape:

```json
{
  "schema_version": "<hash>",
  "rules": [
    {
      "rule_id": "structural_deepest_alternative_tiebreaker_unresolved",
      "field": "deepest_alternative.tiebreaker",
      "predicate_kind": "tiebreaker_resolves",
      "predicate_args": {
        "ref_field": "deepest_alternative.tiebreaker",
        "lookup_tables": ["evidence_citations", "disqualifiers", "MECHANISM_DIMENSIONS"]
      },
      "rejection_code": "structural_deepest_alternative_tiebreaker_unresolved"
    },
    {
      "rule_id": "structural_other_alternatives_too_few",
      "field": "other_alternatives",
      "predicate_kind": "list_min_length",
      "predicate_args": {"min": 1},
      "rejection_code": "structural_other_alternatives_too_few"
    }
  ]
}
```

A small library `prompt_rules.py` exposes `iter_prompt_declared_rules() →
Iterable[(rule_id, predicate_callable)]` by mapping `predicate_kind` values to
predicate functions. The validator imports the same mapping. The test asserts
the positive fixture passes every predicate and each negative fixture trips
exactly its one named rule:

```python
def test_worked_example_satisfies_every_documented_rule():
    example = load_fixture("conductor_prompt_worked_example.json")
    ResearchThesis.model_validate(example)
    result = validate_thesis_dict(example, round_context=_fixture_round_context())
    assert result.accepted, result.rejection_code
    for rule_id, predicate in iter_prompt_declared_rules():
        assert predicate(example), f"worked example violates documented rule {rule_id}"

def test_each_negative_fixture_trips_exactly_its_named_rule():
    for fixture_path in glob("tests/fixtures/conductor_prompt_rejections/*.json"):
        fixture = json.load(open(fixture_path))
        expected_rule_id = fixture["__expected_rejection_code__"]
        result = validate_thesis_dict(fixture, round_context=_fixture_round_context())
        assert not result.accepted
        assert result.rejection_code == expected_rule_id, (
            f"{fixture_path}: expected {expected_rule_id}, got {result.rejection_code}"
        )
```

This makes the prompt-declared rule set machine-readable (the renderer
consumes it for `Validator rule:` slot text), test-iterable (the suite uses
it directly), and impossible to drift from validator behavior (one source).

### 6.3 Update §5 example

After this spec lands, the example MUST use the post-refactor
`deepest_alternative` + `other_alternatives` shape, with a structured
`tiebreaker` the test can resolve. The LLM emits citations as an ordered
list; the validator maps position `i` → id `citation_{i+1}`. No `id` field
in `EvidenceCitation`.

```json
{
  ...,
  "evidence_citations": [
    {"source": "web_search", "citation": "Cont et al. on order-flow regime persistence"},
    {"source": "analyst",    "citation": "round-3 analyst found 62% of stops occur in counter-HTF-trend setups"}
  ],
  "deepest_alternative": {
    "mechanism": "ADX>30 entry filter",
    "why_rejected": "Too strict in low-vol regimes per round-3 analyst evidence (citation_2) — would suppress signals where the HTF gate still admits them, costing trade frequency without addressing the wick-only stop-out mechanism.",
    "tiebreaker": {"kind": "evidence_citation", "value": "citation_2"}
  },
  "other_alternatives": [
    {"mechanism": "session-time entry filter",
     "why_rejected": "Proxy for the regime problem rather than the structural fix; cannot distinguish high-vol from low-vol opens within the same session.",
     "lighter_tiebreaker": null}
  ]
}
```

The `tiebreaker.value="citation_2"` resolves to `evidence_citations[1]` by
positional convention — structural lookup, no LLM-emitted ids.

### 6.4 Why this is better

The worked example becomes a test fixture, not a docstring. Spec edits that break the example fail CI immediately. The fixture becomes the LLM's canonical pattern AND the regression test for every rule the prompt declares.

## 7. Other §4 entry tweaks

Small fixes consolidated here so they ship in the same PR:

- **§4.1 `thesis_id`**: drop the "LLM emits today / system assigns post-A2" dual mode from the prompt. The §7 renderer omits this field from the OUTPUT section entirely; system-assignment is the only mode. Add to `_PROMPT_OMITTED_FIELDS`.
- **§4.1 `strategy_family`**: same treatment. Omitted from OUTPUT; documented in a separate `## SYSTEM-INJECTED FIELDS (do not emit)` appendix above OUTPUT so the LLM knows the key will appear in the final object but is not its responsibility.
- **§4.3 `thesis_role`**: **drop `""` from the Literal.** Forcing a non-empty choice is the simpler enforcement; the "use `""` only when…" escape introduces a soft path the LLM defaults to under uncertainty. Validator rejects empty with `structural_thesis_role_required`.
- **§4.6 `mechanism_family_definition`**: rewrite the Example to be an abstract family definition (not the thesis-specific instance), so future theses can pattern-match against it. *"Theses in this dimension address asymmetric liquidity or volatility behavior in a specific session window (open, close, lunch). Distinct from market_microstructure (order-flow level) and regime_conditioning (multi-session)."*
- **§4.7 `evidence_citations`**: add `citation: str = Field(min_length=30)` floor — long enough to require a real reference, short enough to accommodate DOI/short-citation formats. "web_search: foo" rejected; "Cont et al. (2023) Journal of Finance" passes.
- **§4.8 `expected_effects` — clean redesign, not partial collapse.** A4's `threshold` field was doing two jobs (sometimes lower bound, sometimes acceptance floor) and `unit` was orphaned. Replace `{metric, direction, threshold, unit, rationale}` with `{metric, direction, magnitude_range, unit, rationale}` where:
  - `direction: Literal["increase","decrease","increase_or_same","decrease_or_same","not_worse_than"]` (existing enum preserved).
  - `magnitude_range: tuple[float, float] | None` — bounds of expected movement. Required when `direction in {"increase","decrease"}`; optional otherwise.
  - `unit: str | None` — "pct", "ratio", "trades", "bps", etc. Required when `magnitude_range` is set.
  - `rationale: str | None` — same as today.
  `threshold` is deleted; any existing reader migrates to `magnitude_range[0]` (lower bound) or `magnitude_range[1]` (upper bound) depending on intent.
- **§4.10 `required_diagnostic_specs`**: drop from OUTPUT until Spec B lands. Add to `_PROMPT_OMITTED_FIELDS` with a comment naming Spec B as the unblocker.
- **§4.10 `source_code_verification`**: process-tier gate validates the cited path was actually read. The conductor's trace records `tools_called` and the file paths each `read_file` invocation touched. Validator parses `source_code_verification` to extract `<repo path>` and asserts that path appears in the trace's read-paths set. Rejection codes:
  - `process_source_code_not_read` — no source-reading tool invoked at all.
  - `process_source_code_path_not_read` — tool invoked but the cited path wasn't among the reads.

## 8. Migration plan

Single PR on top of A4 (or merged commits in A4's PR if not yet shipped):

1. **`research_types.py`**:
   - Add `TiebreakerRef`, `DeepestAlternative` models; extend `Alternative` with `lighter_tiebreaker: TiebreakerRef | None`.
   - Add `deepest_alternative` and `other_alternatives` fields to `ResearchThesis`; delete `alternatives_considered` (no backcompat per A4 §2.1).
   - Add constants: `PRIOR_LEVER_OUTCOMES`, `PRIOR_LEVER_DIRECTION_HINTS`, `EVIDENCE_SOURCES`, `EVIDENCE_SOURCES_FOR_DIVERSITY_GATE`, `OVERFIT_DISQUALIFIER_MARKERS`, `OVERFIT_KEYWORD_HINTS`. Import-time assertions pair each `Literal` with its constant.
   - Redesign `ExpectedEffect`: replace `threshold` with `magnitude_range: tuple[float,float] | None`; keep `unit`; require `unit` when `magnitude_range` is set; require `magnitude_range` when `direction in {"increase","decrease"}`.
   - Add `EvidenceCitation.citation` `min_length=30`. No `id` field — positional convention.
   - Drop `""` from `thesis_role` Literal.
2. **`thesis_validator.py`**:
   - Implement `structural_deepest_alternative_*` and `structural_lighter_tiebreaker_unresolved` rejection codes.
   - Implement `process_source_code_not_read` and `process_source_code_path_not_read` gates (use `ConductorResult.tools_called` and read-path trace).
   - Implement the overfit-disqualifier gate as `name in OVERFIT_DISQUALIFIER_MARKERS OR any(hint in condition.lower() for hint in OVERFIT_KEYWORD_HINTS)`.
   - Implement `structural_thesis_role_required` for empty `thesis_role`.
   - Replace all hardcoded marker/enum prose-references with imports from the new constants.
   - Resolve tiebreaker references against `evidence_citations` by positional id (`citation_{i+1}`), `disqualifiers[i].name`, or `MECHANISM_DIMENSIONS`.
3. **`research_prompts.py`** (or wherever the system prompt is assembled):
   - Build the `## ROUND CONTEXT` block with size caps and tail summaries per §5.2.
   - Add the `## SYSTEM-INJECTED FIELDS (do not emit)` appendix listing `thesis_id`, `strategy_family`.
   - Render `evidence_citations_available_ids` only AFTER `evidence_citations` is rendered as a required field (so the LLM knows the ids will be assigned).
4. **`scripts/render_output_schema.py`** (A4 §7):
   - Read the new constants for enum/marker rendering. Render `EVIDENCE_SOURCES` (full) and `EVIDENCE_SOURCES_FOR_DIVERSITY_GATE` (subset) as **two distinct lines**.
   - Emit per-field entries in the **§3.5 category order** (Evidence and Disqualifiers before Alternatives).
   - Render `deepest_alternative` + `other_alternatives` entries instead of `alternatives_considered`.
   - Omit fields in `_PROMPT_OMITTED_FIELDS` (now including `thesis_id`, `strategy_family`, `required_diagnostic_specs`).
   - **Also emit** `prompts/conductor_output_rules.json` sidecar per §6.2.
5. **`prompt_rules.py`** (new): exposes `iter_prompt_declared_rules()` mapping `predicate_kind` strings to callables; imported by both validator and test suite.
6. **`tests/fixtures/conductor_prompt_worked_example.json`**: new file replacing A4 §5's inline JSON.
7. **`tests/fixtures/conductor_prompt_rejections/`** (new dir): one fixture per rejection code, each with an `__expected_rejection_code__` marker.
8. **`tests/test_conductor_prompt_v3.py`**:
   - Positive: fixture passes Pydantic + live validator + every prompt-declared rule.
   - Negative: each rejection fixture trips exactly its named rule.
   - `## ROUND CONTEXT` block present and contains all keys referenced by conditional `Required:` lines.
   - Category ordering: every field referenced by `tiebreaker.value` candidates renders before `deepest_alternative` / `other_alternatives` in the rendered prompt.
   - No hardcoded enum/marker strings remain in the rendered prompt (they must come from constants).
9. **`scripts/check_prompt_drift.py`** (A4 §8):
   - Every key in `## ROUND CONTEXT` referenced by OUTPUT must be produced by the conductor's context-builder.
   - Every constant imported from `research_types.py` and rendered into the prompt must be a tuple/frozenset.
   - Category ordering: referenced-field categories render before referencing-field categories.
   - Rules sidecar `conductor_output_rules.json` is up to date relative to validator code.

## 9. Risk and rollback

**Risks:**

- **Schema change touches every downstream consumer of `alternatives_considered`.** Mitigation: per A4 §2.1, no backwards compatibility; one PR replaces the field everywhere. Compile-time errors will surface all sites.
- **`magnitude_range` collapse breaks code that reads `threshold`.** Mitigation: grep for `.threshold` on `ExpectedEffect` and migrate at the same time. Spec B-style telemetry that read `threshold` migrates with it.
- **ROUND CONTEXT computation pulls already-validated data into the prompt path; bugs there could distort LLM behavior.** Mitigation: ROUND CONTEXT is a pure projection of snapshot state already used by the validator; same source of truth, new render target. Unit-test the projection independently.

**Rollback:** revert the PR. A4 reverts to its hand-written §4 entries; the constants and `deepest_alternative` refactor are removed; the worked-example fixture goes back to being a docstring. Lose the structural rule enforceability until re-landed.

## 10. Success criteria

- `tests/test_conductor_prompt_v3.py` asserts the worked-example fixture passes (a) Pydantic, (b) `validate_thesis_dict`, and (c) every rule declared in the rendered OUTPUT prompt.
- Each negative fixture in `tests/fixtures/conductor_prompt_rejections/` trips exactly its named rejection code.
- `prompts/conductor_output_rules.json` exists, is regenerated by the renderer, and is imported by both the validator and the test suite — single source for rule predicates.
- `## ROUND CONTEXT` block is present in the rendered conductor prompt; every conditional `Required:` line references a key that exists in it; size caps enforced.
- §3.5 category ordering verified: `deepest_alternative` and `other_alternatives` render after `evidence_citations`, `disqualifiers`, and `MECHANISM_DIMENSIONS` rendering.
- No hardcoded enum/marker string list appears in the rendered prompt — every such list comes from a `research_types.py` constant.
- `EvidenceCitation` carries no LLM-emitted `id` field; tiebreaker references use positional `citation_N` ids assigned by the validator.
- `ExpectedEffect` schema is `{metric, direction, magnitude_range, unit, rationale}`; `threshold` removed; consumers migrated.
- `deepest_alternative` and `other_alternatives` fields replace `alternatives_considered` end-to-end (schema, validator, prompt, contract, evaluator).
- `source_code_verification` gate validates the cited path against the conductor's read-paths trace, not just any-tool-was-called.
- Post-deploy: conductor's per-attempt acceptance rate inherits A4's ≥50% target. Secondary metrics:
  - ≥80% of accepted theses have `deepest_alternative.tiebreaker` resolving on first emit (measures whether structural tiebreaker is used as intended).
  - ≥30% of accepted theses populate at least one `other_alternatives[i].lighter_tiebreaker` (measures whether optional depth-vetting catches on).
  - 0 rejections with code `process_source_code_path_not_read` after 4 weeks (measures whether the path-check gate is too strict; non-zero would prompt a guidance revision, not a gate removal).

## 11. Out of scope

- Adding fields beyond what A4 §4.12 already proposes.
- Re-litigating A4a's 12 dropped fields.
- DOCTRINE rewrites beyond updating the two cross-references for `alternatives_considered` → `deepest_alternative` / `other_alternatives`.
- Multi-model evaluation of the refactored prompt.
