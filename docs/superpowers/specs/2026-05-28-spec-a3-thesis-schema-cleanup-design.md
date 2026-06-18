# Spec A3 — `ResearchThesis` Schema Cleanup (Drop Weak / Redundant Fields)

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** Spec A §5.0.6 (master field inventory); Spec A §5.6 (proposer-reasoning render)
**Depends on:** Spec A's §5.0.6 inventory must land first — A3 acts on the same field decisions
**Blocks:** none
**Parallel with:** Spec A1 (terminology), Spec A2 (id provenance), Spec B / C / D — A3 only touches the schema's *content*, not its identifiers, not its terminology, not its evidence-typing migration

---

## 1. Goal

**Drop 5 fields from the `ResearchThesis` schema** that don't serve any of the three reasoning goals the schema is supposed to serve, and **tighten the gate on 2 kept fields** whose current gates are too weak to enforce their purpose.

The three goals:

1. Let the conductor **compare the prior round's outcome against the prior round's reasoning** (so it can detect when a prediction failed).
2. **Force the conductor to be creative** (resist the urge to tweak parameters; demand structural choices).
3. **Improve thesis quality via validation gates** (rules that catch bad reasoning at the schema level, not after the fact).

A field that serves at least one of these earns its place. A field that fails all three — typically a self-report-y free-text field where the LLM grades its own work without any structural check — is dead weight. It costs prompt tokens, costs output tokens, costs validator complexity, and produces no actionable signal.

**Five drops:**

1. `causal_cluster` — "diversity audit" purpose declared in the schema comment but **never wired** in code; validator only checks the field is non-empty. Dead weight.
2. `orthogonality_defense` — self-defense for a thing the validator can't verify; no rule references it.
3. `closest_prior_theses_considered` — untyped list of thesis_ids overlapping with the typed `prior_lever_outcomes` and `alternatives_considered`; no validator rule references it.
4. `evidence_strength` — LLM self-rating its own confidence as `direct`/`proxy`/`mixed`/`speculative`; no validator rule references it beyond the Literal enum restriction. Motivated-reasoning bias makes the self-rating noise-dominated.
5. `why_not_overfit` — LLM defending its own thesis against the overfit accusation; no validator rule references it. Motivated-reasoning bias.

**Two kept fields with upgraded gates:**

6. **Tighten the validator gate on `dimension_novelty`** — from "length ≥30 chars" (token tax) to a structural check: must mention at least one specific keyword from the family's `MECHANISM_DIMENSIONS` enum or from §5.8's landscape rendering. Proves the LLM engaged with the actual dimension surface, not just wrote generic novelty prose.
7. **Tighten the validator gate on `novel_connection`** — this field already has a structural **conditional** gate at `thesis_validator.py:1633-1641` that fires when computed `theme_keywords` overlap with priors is ≥50% (cluster-overlap = "high"). When fired, requires ≥N chars in `novel_connection`. Today the check is length-only — fluffable. Upgrade in the same shape as `dimension_novelty`: text must reference a specific concept from the high-overlap priors' shared `theme_keywords` (proving the LLM engaged with what makes its thesis a variation of the cluster), or a specific `mechanism_dimension` change that moves the thesis structurally out of the high-overlap cluster.

**Why `novel_connection` is kept (not dropped — corrected from prior draft):** the conditional gate at line 1633 IS a real structural forcing function (computed from `theme_keywords` intersection, not self-reported). The length-only check is weak but the gate condition is sound. Dropping the field would remove the only forcing function the validator has for *per-thesis* high-overlap proposals (cluster-fixation handles persistent fixation; this handles single-thesis high-overlap that hasn't yet triggered cluster-fixation).

## 2. Non-goals

- **Spec A1 / A2 / B / C / D scope** — A3 is independent of all four. Schema content only.
- **Renaming `ResearchThesis`** — class name stays. We delete fields from it; we don't restructure or rename the class.
- **DB schema migration** — pre-cutover rows in `research_thesis_attempts.thesis_details_json` keep their old payload (with the dropped fields present). Readers ignore the dropped fields; new writes omit them.
- **Touching the prompt's *render* of the surviving fields** — Spec A's §5.6 still applies; this spec just changes *which* fields it has to render.
- **Adding new fields** — A3 only removes. Any new field proposal is a separate spec.

## 2.1 No backward compatibility — hard cutover

Same policy as A1/A2: in the same PR, all six fields are removed from the `ResearchThesis` Pydantic schema, all prompt text instructing the LLM to emit them is removed, all validator rules referencing them are deleted, all tests asserting on them are updated.

- **Pre-cutover DB rows** retain the dropped fields in their JSON blobs (Pydantic ignores unknown keys when re-parsing → no read error).
- **Post-cutover LLM output** doesn't include the fields (prompt no longer asks for them). If the LLM emits them anyway, Pydantic's default behavior of accepting extras is preserved — they get stripped on validation.
- No deprecation aliases, no soft fallback. The schema is the contract.

## 3. Background

`ResearchThesis` (research_types.py:139–211) has 35 fields, accumulated as new rules and gates were added over time. Spec A §5.0.6 audits each field for "required by validator" + "useful for next round's conductor." A3 adds a third lens: **does this field actually serve one of the three reasoning goals, or is it cost without signal?**

The dominant failure mode is **self-report fields**: text or enum picked by the LLM to grade its own work. The LLM has motivated reasoning to self-grade favorably ("yes my evidence is `direct`", "yes my thesis is novel", "no my thesis is not overfit"). Without an external grader, these fields are rubber-stamps the model applies to itself. They consume tokens but produce no signal usable for the three goals.

Production evidence (VPS SSH, 2026-05-28): zero accepted theses exist yet — every conductor invocation rejected pre-flight. So the "self-report fluff" problem is unobserved in production. The drops are preemptive: removing weak fields before the conductor starts producing them at scale.

## 4. Per-field audit and drop rationale

### 4.1 `causal_cluster` (str) — DROP

**Today's role per schema comment** (`research_types.py:151`): "causal family this thesis belongs to, for diversity audits." Per the prompt (`research_prompts.py:101-102, 124-126`): a "human-phrased family name for this thesis's causal story" (e.g. `"opening-session adverse selection"`), explicitly distinct from `mechanism_dimension` (the categorical bucket) and from a config key like `min_stop_pct`. Required to be non-empty when prior theses exist.

**Why drop:** the stated purpose — "diversity audits" — **was never wired** in code. The only place the validator touches `causal_cluster` is `thesis_validator.py:1619, 1904`, which checks the field is non-empty and raises `structural_missing_causal_cluster` if it isn't. No rule groups theses by `causal_cluster`. No rule checks two theses share the same family. No render block reads it for structural reasoning. The field is a tax on the LLM (must write a non-empty family name on every thesis-after-the-first) with **no downstream consumer beyond the existence check**.

**Note on the "rooms vs levers" framing some readers might have had:** an earlier draft of this spec described `causal_cluster` as a "third label for where this thesis sits," redundant with `mechanism_dimension` + `theme_keywords`. That framing was wrong — `causal_cluster` is a **causal-story** label (the "why" family), not a position label. The two concepts are genuinely distinct. The reason to drop the field isn't redundancy; it's that the diversity-audit consumer was never built.

**Alternative considered (and rejected):** wire up an actual diversity-audit gate that checks `causal_cluster` cardinality across the last N accepted theses. Rejected because (a) the cluster-fixation rule on `theme_keywords` already prevents persistent fixation, (b) "diversity by self-reported family name" is a weak signal — the LLM can rename the family to look diverse without changing the thesis structurally, (c) adding a real diversity audit is a feature, not a cleanup; out of A3 scope.

**Validator rule deleted:** the `causal_cluster` non-empty check at `thesis_validator.py:1619` and `:1904` (rejection code `structural_missing_causal_cluster`).

### 4.2 `novel_connection` (str ≥ N chars) — KEEP, UPGRADE GATE (see §4.7)

**Today's role:** "why this thesis connects evidence in a materially new way" when the proposer is staying within a cluster the family has already mined heavily.

**Why kept (correction from prior draft):** the validator has a **conditional structural gate** at `thesis_validator.py:1633-1641`. The gate fires when `_computed_dominant_cluster_overlap(thesis, prior_theses) == "high"` — meaning the new thesis's `theme_keywords` overlap with priors at ≥50%. When it fires, the LLM must provide ≥`_MIN_NOVEL_CONNECTION_CHARS` chars in `novel_connection` explaining why the high-overlap thesis is materially new instead of another variation of the dominant cluster. Rejection code: `structural_novel_connection_too_short`.

This is a real forcing function — the gate condition is computed deterministically from `theme_keywords` intersection (the validator's own structural data), not from a self-report. The length check is weak (LLM can fluff N chars), but the gate's *firing condition* is sound and complementary to the cluster-fixation rule (which catches *persistent* fixation across the last 7 theses; this catches *per-thesis* high-overlap that hasn't yet triggered cluster-fixation).

Dropping `novel_connection` would remove this conditional gate entirely. Keeping it with a stronger length+structural check preserves the forcing function and tightens the bar.

**See §4.7 for the joint gate-upgrade design** that applies the same "must mention a specific structural element" pattern to both `dimension_novelty` and `novel_connection`.

### 4.3 `orthogonality_defense` (str) — DROP

**Today's role:** "why this proposal is mechanism-distinct from the nearest priors" (text self-defense).

**Why drop:** self-report. The LLM defends its own proposal as orthogonal to priors — but the validator can't verify the claim is true; it only checks the field is non-empty. The same intent is served structurally by `prior_lever_outcomes` (typed) and `alternatives_considered` (typed, ≥2 entries gated) — those force the LLM to *cite specific priors* with structured fields, which is auditable.

**What it serves today:** weakly serves goal 2; gateless beyond non-empty.

**Validator rule deleted:** any non-empty check on `orthogonality_defense` (verify in `thesis_validator.py` during implementation; might just be a Pydantic default).

### 4.4 `closest_prior_theses_considered` (list[str]) — DROP

**Today's role:** list of `thesis_id`s the proposer felt this proposal was nearest to.

**Why drop:** overlaps with `prior_lever_outcomes` (typed — also cites prior thesis_ids, plus direction/outcome/why_retry context) and `alternatives_considered` (typed — at least 2 entries with `why_rejected` rationale). Both typed alternatives carry the awareness-of-priors intent with enforceable structure. `closest_prior_theses_considered` adds untyped ids without rationale — easy to fill with a plausible-looking list the LLM never actually compared against.

**What it serves today:** weakly serves goal 2; the typed alternatives do it better.

**Validator rule:** none today — accepted whatever the LLM emits or omits.

### 4.5 `evidence_strength` (Literal `direct`/`proxy`/`mixed`/`speculative`) — DROP

**Today's role:** self-graded confidence calibration.

**Why drop:** pure self-report. The LLM picks the label; the validator accepts whatever's picked. No external grader. The LLM has motivated reasoning to label `direct` because it wants the thesis accepted. Known LLM-calibration problem: self-reported confidence is poorly correlated with actual evidence quality.

If the goal is "make the conductor reason about evidence quality," the better intervention is **typed evidence with required source diversity** — already present as `evidence_citations` (≥1 web_search + ≥1 analyst). That gates the *structure* of evidence, not the LLM's self-assessment.

**What it serves today:** nominally goal 1 (compare outcome vs reasoning — was the self-graded confidence accurate?). In practice, the noise dominates the signal — the LLM almost always picks `direct` or `mixed`, regardless of actual evidence.

**Validator rule:** none today (Literal enum already restricts values; no further gate).

### 4.6 `why_not_overfit` (str) — DROP

**Today's role:** self-defense paragraph against the overfit accusation.

**Why drop:** the LLM that just proposed a potentially-overfit thesis writes the defense against the accusation. Trivially fluffable with "tested across multiple symbols / years / regimes" boilerplate. No external check possible at validation time (overfit detection requires running the backtest, which happens after acceptance).

If the goal is "force structural anti-overfit thinking," the better intervention is **structural disqualifiers** — already present as the `disqualifiers` field with required typed entries (`{name, condition, severity, kind}`). A thesis with no overfit-related disqualifier is a thesis with no externalizable anti-overfit story, which is a stronger signal than a free-text paragraph.

**Validator rule:** none today (Pydantic default empty string accepted).

### 4.7 Related upgrades — tighten gates on `dimension_novelty` and `novel_connection` (both kept; strengthen checks)

Both fields today rely on length-only gates. Both ask the LLM to text-justify novelty. Both can be fluffed with generic prose. This sub-section upgrades both gates to require **structural grounding** — the text must reference a specific named element from the validator's structural data, not just clear a length bar.

#### 4.7a `dimension_novelty` gate

**Today's gate:** `dimension_novelty` text must be ≥30 chars (`thesis_validator.py:1481`, `_MIN_DIMENSION_NOVELTY_CHARS`). Always applies.

**Problem:** 30 chars is a token tax, not a quality bar. The LLM can write "this is a novel dimension because new" and pass.

**Upgraded gate:** in addition to the length check, `dimension_novelty` text must reference at least one of:

- a specific name from the family's `MECHANISM_DIMENSIONS` enum (e.g. `signal_quality`, `regime_conditioning`, etc.), **or**
- a `mechanism_dimension` value from the §5.8 landscape rendering (which lists dimensions with their saturated/active/unexplored bucket).

Rejection code if zero matches: `thesis_quality_dimension_novelty_not_grounded`. Evidence payload: `{"text": ..., "matched_dimensions": [], "valid_dimensions": [...]}`.

#### 4.7b `novel_connection` gate

**Today's gate:** `novel_connection` text must be ≥`_MIN_NOVEL_CONNECTION_CHARS` chars (`thesis_validator.py:1633-1641`). Fires **conditionally** when `_computed_dominant_cluster_overlap(thesis, prior_theses) == "high"` (≥50% `theme_keywords` overlap with priors).

**Problem:** the gate condition is sound (structural), but the length check is fluffable. The LLM can write a long-but-generic paragraph and pass.

**Upgraded gate:** when the gate fires, in addition to the length check, `novel_connection` text must reference at least one of:

- a specific token from the high-overlap priors' shared `theme_keywords` (proving the LLM acknowledges the overlap with a named lever, e.g. mentions `stop_distance` if that's what's shared), **or**
- a specific `mechanism_dimension` change that moves the thesis structurally out of the high-overlap cluster (mention of a different `MECHANISM_DIMENSIONS` enum value than the dominant cluster's).

Rejection code if zero matches: `thesis_quality_novel_connection_not_grounded`. Evidence payload: `{"text": ..., "overlapping_keywords": [...], "thesis_keywords": [...], "matched_tokens": []}`.

#### Shared implementation contract

Both gates use the same "grounded-mention" pattern:

1. Tokenize the field's text (case-insensitive whitespace + punctuation split).
2. Intersect the token set against the field's expected reference set (the `MECHANISM_DIMENSIONS` enum for `dimension_novelty`; the overlapping `theme_keywords` + `MECHANISM_DIMENSIONS` for `novel_connection`).
3. Require ≥1 intersection. On zero, reject with the field-specific code and evidence payload.

Both gates ship behind the same code helper to avoid drift: `_check_grounded_mention(text: str, valid_tokens: set[str]) -> bool`.

### 4.8 Fields explicitly kept (for clarity)

The strong fields per Spec A §5.0.6's audit + this spec's three-goal lens, all preserved:

| Field | Serves | Gate |
|---|---|---|
| `thesis_id` | identity | required (Spec A2 makes it system-assigned) |
| `hypothesis` | G1 | required, non-empty |
| `mechanism` | G1 | required, non-empty |
| `mechanism_dimension` | G2 | required, from `MECHANISM_DIMENSIONS` enum |
| `dimension_novelty` | G2 | required, length ≥30 + **§4.7a grounded-mention upgrade** |
| `novel_connection` | G2 + G3 (conditional, per `_computed_dominant_cluster_overlap`) | length ≥ N + **§4.7b grounded-mention upgrade** |
| `thesis_role` | G2 | Literal enum |
| `theme_keywords` | G3 | required for cluster-fixation + whipsaw rules |
| `expected_effects` | G1 | required non-empty list |
| `disqualifiers` | G1 + G3 | required non-empty list of typed entries |
| `evidence` | G1 (legacy) | preserved until Spec B retires |
| `evidence_citations` | G3 | required ≥1 web_search + ≥1 analyst |
| `falsification_or_alternative` | G1 | required, non-empty |
| `prior_lever_outcomes` | G3 | typed; §6.1 binds prior_thesis_id to snapshot |
| `alternatives_considered` | G2 + G3 | typed, ≥2 entries |
| `source_code_verification` | G1 | required, ≥40 chars |
| `config_changes` | G1 | required non-empty (or `requires_code_change=True`) |
| `requires_code_change` + `requested_primitives` | G2 | paired; engine-starvation rule |
| `new_dimension_name`, `why_existing_dimensions_do_not_fit`, `mechanism_family_definition` | G2 conditional | required only when `mechanism_dimension == "emergent"` |

## 5. Schema diff

**Before** (`research_types.py:139–211`, 35 fields):

```python
class ResearchThesis(BaseModel):
    thesis_id: str
    strategy_family: str
    hypothesis: str
    mechanism: str

    mechanism_dimension: str = ""
    dimension_novelty: str = ""
    causal_cluster: str = ""                          # ← DROP §4.1
    dominant_cluster_overlap: Literal["", "low", "medium", "high"] = ""
    underexplored_dimensions_considered: list[str] = Field(default_factory=list)
    novel_connection: str = ""                        # KEEP — gate upgraded per §4.2 + §4.7b
    closest_prior_theses_considered: list[str] = Field(default_factory=list)  # ← DROP §4.4
    orthogonality_defense: str = ""                   # ← DROP §4.3
    evidence_strength: Literal["", "direct", "proxy", "mixed", "speculative"] = ""  # ← DROP §4.5
    thesis_role: Literal[...] = ""
    falsification_or_alternative: str = ""
    new_dimension_name: str = ""
    why_existing_dimensions_do_not_fit: str = ""
    mechanism_family_definition: str = ""
    expected_reuse_across_future_theses: str = ""

    evidence: list[str] = Field(default_factory=list)

    base_contract_id: str = ""
    base_config_path: str = ""

    config_changes: dict[str, Any] = Field(default_factory=dict)
    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)

    requires_code_change: bool = False
    requested_primitives: list[str] = Field(default_factory=list)

    why_not_overfit: str = ""                         # ← DROP §4.6
    theme_keywords: list[str] = Field(default_factory=list)
    prior_lever_outcomes: list[PriorLeverOutcome] = Field(default_factory=list)
    alternatives_considered: list[Alternative] = Field(default_factory=list)
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)
    source_code_verification: str = ""
```

**After** (30 fields — 5 dropped; `novel_connection` kept with upgraded gate):

```python
class ResearchThesis(BaseModel):
    thesis_id: str
    strategy_family: str
    hypothesis: str
    mechanism: str

    mechanism_dimension: str = ""
    dimension_novelty: str = ""                       # gate upgraded per §4.7a
    dominant_cluster_overlap: Literal[...] = ""       # kept (computed in validator)
    underexplored_dimensions_considered: list[str] = Field(default_factory=list)
    novel_connection: str = ""                        # gate upgraded per §4.7b
    thesis_role: Literal[...] = ""
    falsification_or_alternative: str = ""
    new_dimension_name: str = ""
    why_existing_dimensions_do_not_fit: str = ""
    mechanism_family_definition: str = ""
    expected_reuse_across_future_theses: str = ""

    evidence: list[str] = Field(default_factory=list)
    base_contract_id: str = ""
    base_config_path: str = ""

    config_changes: dict[str, Any] = Field(default_factory=dict)
    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    disqualifiers: list[Disqualifier] = Field(default_factory=list)
    required_diagnostics: list[str] = Field(default_factory=list)
    required_diagnostic_specs: list[DiagnosticRequirementSpec] = Field(default_factory=list)

    requires_code_change: bool = False
    requested_primitives: list[str] = Field(default_factory=list)

    theme_keywords: list[str] = Field(default_factory=list)
    prior_lever_outcomes: list[PriorLeverOutcome] = Field(default_factory=list)
    alternatives_considered: list[Alternative] = Field(default_factory=list)
    evidence_citations: list[EvidenceCitation] = Field(default_factory=list)
    source_code_verification: str = ""
```

## 6. Prompt diff (agent_prompts.py + research_prompts.py)

Every prompt asking the LLM to emit one of the 5 dropped fields is rewritten to not ask. The JSON skeleton in `agent_prompts.py:121` and the OUTPUT field list in `research_prompts.py:117–145` are the two main sites; tests assert on both.

**Removed prompt instructions (5 — `novel_connection` stays):**

- `causal_cluster: "..."` line in the JSON skeleton.
- `closest_prior_theses_considered: [...]` line.
- `orthogonality_defense: "..."` line.
- `evidence_strength: "..."` line.
- `why_not_overfit: "..."` line.

**Upgraded prompt instructions (per §4.7):**

- `dimension_novelty: "must reference a specific mechanism_dimension name from the family's enum, e.g. 'signal_quality', or a dimension from this round's mechanism-landscape block. Generic novelty prose without a named dimension is rejected with code thesis_quality_dimension_novelty_not_grounded."`
- `novel_connection: "REQUIRED only when computed cluster overlap with priors is high (≥50% theme_keywords overlap). When required, must reference at least one specific shared theme_keyword from the high-overlap priors OR a specific mechanism_dimension that structurally moves the thesis out of the dominant cluster. Generic novelty prose without a grounded mention is rejected with code thesis_quality_novel_connection_not_grounded."`

## 7. Validator changes

**Rules deleted:**

- `causal_cluster` non-empty check (`thesis_validator.py:1619, 1904`, code `structural_missing_causal_cluster`).
- Any rule referencing `orthogonality_defense` — by code audit, no such rule exists today (only Pydantic default empty string). Remove field; no rule to delete.
- Any rule referencing `closest_prior_theses_considered` — by code audit, no such rule exists today.
- Any rule referencing `evidence_strength` — Pydantic Literal restricts enum values, no additional rule; just remove the field.
- Any rule referencing `why_not_overfit` — by code audit, no such rule exists today.

**Rules upgraded:**

- **`dimension_novelty` gate (`thesis_validator.py:1481` region):** in addition to existing length ≥30 check, parse the text and require ≥1 case-insensitive token match against the family's `MECHANISM_DIMENSIONS` enum or `last_research_round["family_landscape"].dimensions[*].name`. New rejection code: `thesis_quality_dimension_novelty_not_grounded`. Evidence payload: `{"text": ..., "matched_dimensions": [], "valid_dimensions": [...]}`.

- **`novel_connection` gate (`thesis_validator.py:1633-1641`):** the gate condition stays (fires when `_computed_dominant_cluster_overlap == "high"`). In addition to existing length ≥`_MIN_NOVEL_CONNECTION_CHARS` check, parse the text and require ≥1 case-insensitive token match against either the overlapping `theme_keywords` (computed from the intersection of new-thesis keywords and prior-thesis keywords that triggered the "high" overlap) or the family's `MECHANISM_DIMENSIONS` enum. New rejection code: `thesis_quality_novel_connection_not_grounded`. Evidence payload: `{"text": ..., "overlapping_keywords": [...], "thesis_keywords": [...], "matched_tokens": []}`.

- **Shared helper:** both gates use a single private helper `_check_grounded_mention(text: str, valid_tokens: set[str]) -> set[str]` returning the matched-token set. Promotes one canonical mention-check implementation rather than two diverging copies.

## 8. Migration plan — single PR, single commit per layer

Hard cutover, A1/A2 style:

1. **`research_types.py`**: delete the 5 fields (`causal_cluster`, `orthogonality_defense`, `closest_prior_theses_considered`, `evidence_strength`, `why_not_overfit`). Keep `novel_connection`. Update field-count comments. Update `tests/test_schema_additions.py` and any other schema-shape assertions.
2. **`agent_prompts.py:121`**: remove the 5 JSON-skeleton lines for the dropped fields. Update the `novel_connection` line per §6 (grounded-mention instruction). Update tests in `tests/test_research_conductor_characterization.py` that snapshot the system prompt.
3. **`research_prompts.py:117–145`**: remove the 5 OUTPUT field descriptions for the dropped fields. Update the `dimension_novelty` and `novel_connection` descriptions per §6. Same test update.
4. **`thesis_validator.py`**: delete the `causal_cluster` non-empty rules (`:1619, :1904`). Add the upgraded `dimension_novelty` and `novel_connection` grounded-mention gates per §7 "Rules upgraded." Add the `_check_grounded_mention` shared helper. Update `tests/test_validator_*.py` per-rule tests.
5. **`research_memory.py:latest_thesis_details`**: stop returning the 5 dropped fields (they no longer exist on the schema; remove from the return dict). Update `tests/test_research_memory.py`.
6. **Spec A §5.0.6 inventory**: regenerate the table to mark the 5 dropped fields as DROP (with §4 cross-reference). Spec A §5.6 PROPOSER REASONING render sub-blocks: remove the 5 dropped fields from their sub-blocks. Keep `novel_connection` (now in the "Mechanism novelty" sub-block alongside `dimension_novelty`). (Spec docs are part of the PR.)
7. **Tests final sweep**: every fixture that hand-types a dropped field gets it removed. Some fixtures may have been relying on a dropped field's presence as a contract — those tests need to drop the assertion. Fixtures with `novel_connection` populated need to be checked against the new grounded-mention gate (may need to add a `theme_keyword` mention or `mechanism_dimension` name in the test text).
8. **Final grep gate** (PR not mergeable until all pass):
   - `grep -rn 'causal_cluster\|closest_prior_theses_considered\|orthogonality_defense\|evidence_strength\|why_not_overfit' --include="*.py"` returns zero hits.
   - `grep -rn 'novel_connection' --include="*.py"` returns ≥3 hits (schema field, validator gate, tests). It is NOT in the drop list.
   - `grep -rn 'structural_missing_causal_cluster' --include="*.py"` returns zero hits.
   - `grep -rn 'thesis_quality_dimension_novelty_not_grounded\|thesis_quality_novel_connection_not_grounded' --include="*.py"` returns ≥4 hits (2 rules + 2 tests).
   - `grep -rn '_check_grounded_mention' --include="*.py"` returns ≥2 hits (helper definition + 2 call sites = 3+).
9. **Documentation**: this spec marked Shipped; Spec A's §5.0.6 and §5.6 updated; PR description includes grep-gate output.

## 9. Risk and rollback

**Risks:**

- **Pre-cutover DB rows have the dropped fields in their JSON blobs.** Pydantic accepts unknown keys by default, so re-parsing old rows still works — the dropped fields just get stripped on validation. Mitigation: verify Pydantic `model_config = ConfigDict(extra="ignore")` is set (or default) on `ResearchThesis`; add a regression test that loads a fixture-old row and confirms no error.
- **The LLM may still emit dropped fields** (it's been instructed to for months). Pydantic strips them silently. The agent reflexion can include a one-time note: "the schema dropped fields X, Y, Z — stop emitting them." But this isn't load-bearing; silently stripping is fine.
- **`dimension_novelty` grounded-mention rule may fire false positives** on legitimately novel cross-dimension theses where the LLM writes about *bridging* dimensions without naming them. Mitigation: the rule allows mention of any dimension name (current or from landscape rendering); a thesis that bridges `signal_quality` and `regime_conditioning` will name both. If the rule still over-fires in early operation, downgrade to `severity="warn"` (BehaviorSignal) instead of hard reject.
- **Some existing test fixtures may have been silently relying on the dropped fields' default values** (`evidence_strength=""`, etc.). The fixture would still work because the defaults are gone with the field; missing-key Pydantic check just doesn't fire. Verify per-test that no assertion reads `thesis.causal_cluster` (which would `AttributeError` after the drop). Pre-flight grep catches this.

**Rollback:**

Revert the PR. The schema gets the 6 fields back, the prompt instructions return, the validator rules return. Pre-cutover JSON blobs in the DB still parse cleanly (they always did). The `dimension_novelty` grounded-mention rule disappears — re-add only the length check.

No DB migration is needed in either direction because Pydantic's `extra="ignore"` (default) treats unknown JSON keys as a no-op.

## 10. Success criteria

**Schema:**

- `from research_types import ResearchThesis; ResearchThesis.model_fields` returns 30 keys (not 35).
- The 5 dropped field names are NOT in `ResearchThesis.model_fields`.
- `novel_connection` IS still in `ResearchThesis.model_fields` (it was kept; only its gate was upgraded).

**Hard-cutover grep gate (PR not mergeable until each pass):**

- `grep -rn 'causal_cluster\|closest_prior_theses_considered\|orthogonality_defense\|evidence_strength\|why_not_overfit' --include="*.py"` returns zero hits (excluding this spec's own audit text).
- `grep -rn 'novel_connection' --include="*.py"` returns ≥3 hits (the field is kept).
- `grep -rn '_MIN_NOVEL_CONNECTION_CHARS' --include="*.py"` continues to return hits (constant still in use by the upgraded gate).
- `grep -rn 'structural_missing_causal_cluster' --include="*.py"` returns zero hits.

**Prompt:**

- The output of `_build_conductor_system_prompt(strategy_desc)` does not contain any of the 5 dropped field names. Verified by an assertion in `tests/test_research_conductor_characterization.py`.
- The output *does* contain the upgraded `dimension_novelty` instruction (rejection-code reference or grounded-mention phrase).
- The output *does* contain the upgraded `novel_connection` instruction (rejection-code reference or grounded-mention phrase).

**Validator:**

- Existing tests that previously passed by populating dropped fields still pass after the fixture cleanup (dropped fields removed from fixtures).
- A new test `tests/test_dimension_novelty_grounded.py` asserts: (a) thesis with `dimension_novelty="this is a novel dimension because new"` (no enum mention) is rejected with code `thesis_quality_dimension_novelty_not_grounded`; (b) thesis with `dimension_novelty="introduces a regime overlay distinct from prior signal_quality threshold tweaks"` is accepted.
- A new test `tests/test_novel_connection_grounded.py` asserts: (a) a thesis with high computed cluster overlap and `novel_connection="generic novelty paragraph with enough characters but no specific grounding"` is rejected with code `thesis_quality_novel_connection_not_grounded`; (b) a thesis with high overlap whose `novel_connection` text mentions a shared `theme_keyword` token is accepted; (c) a thesis with LOW overlap is NOT subject to the grounded-mention gate at all (the conditional gate doesn't fire).
- A new test `tests/test_grounded_mention_helper.py` exercises `_check_grounded_mention(text, valid_tokens)` directly: empty text → empty match set; text matching ≥1 token → token in match set; case-insensitivity verified.
- Loading a fixture-old `thesis_details_json` containing the 5 dropped fields parses without error (Pydantic strips the extras).

**Documentation:**

- Spec A §5.0.6 updated: the 6 dropped fields' rows reflect "DROP per Spec A3" with cross-reference.
- Spec A §5.6 render sub-blocks no longer mention the dropped fields.
- This spec marked Shipped.

## 11. Out of scope

- Adding new fields to `ResearchThesis`. A3 only removes.
- Renaming any kept field.
- Re-typing `evidence` from `list[str]` to typed citations — that's Spec B's `evidence_citations` migration.
- Removing `dimension_novelty` itself — the field stays; only its gate is strengthened.
- DB column changes — none.
- Touching MCP tool schemas — none of the dropped fields appear in MCP arg schemas.
