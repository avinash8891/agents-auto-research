# Spec A4a — `ResearchThesis` Field Consolidation Analysis

**Date:** 2026-05-28
**Status:** Analysis — drives field-count decisions in Spec A3 and Spec A4
**Reference:** Spec A4 (full per-field OUTPUT contract), Spec A3 (schema cleanup), `research_types.py:139–211` (current schema)
**Depends on:** none
**Drives:** A3's drop list; A4's §4 surviving field set + §4.12 proposed additions

---

## 1. Goal

After auditing all 41 fields (35 current schema fields + 6 proposed additions in Spec A4 §4.12) against the three reasoning goals (compare outcome vs reasoning, force creativity, validate via gates with teeth), identify **duplicate field sets** where multiple fields serve the same purpose, and pick the **strongest, most meaningful field** in each set to absorb the rest.

**Net outcome:** 35 current fields → **23 surviving** (12 drops); 6 proposed additions → 5 (1 proposed-but-dropped). Three surviving fields' contracts are strengthened to absorb the dropped fields' purposes.

## 2. Audit criteria (per field)

For each duplicate set, the strongest field is the one with **all three**:

1. **Structural gate** — validator can verify the field's claim mechanically (not just a length check or non-empty check).
2. **Distinct downstream consumer** — at least one component reads the field to do something specific (cluster-fixation rule, outcome evaluator, etc.).
3. **Non-self-report or self-report-paired-with-verification** — either the value comes from a structural source, or the LLM's self-report can be cross-checked against deterministic data.

Fields that fail all three are candidates for drop. Sets where one field meets all three become the canonical replacement.

## 3. The 8 duplicate sets

### Set 1 — Position / classification (3 fields → 2 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `mechanism_dimension` | Non-empty + enum membership (thesis_validator.py:1480) | §5.8 landscape, §5.1 prompt, validator | Solid (enum-gated) |
| `theme_keywords` | Used by cluster-fixation rule (`:411`) + whipsaw rule (`:624`) + computed-overlap (`:1632`) | Heavy: 3 validator rules + §5.1 prompt | Solid (structurally used) |
| **`causal_cluster`** | Non-empty check only (`:1619, 1904`); **no diversity-audit consumer ever built** | None beyond non-empty check | Self-report family-name |

**Keep:** `mechanism_dimension` + `theme_keywords` (genuinely distinct concepts: WHERE the thesis sits vs WHICH LEVER it touches; both have structural gates).
**Drop:** `causal_cluster`.

**Why drop:** the field's declared purpose is "diversity audits" but no rule ever consumed it for that. The validator only checks non-empty. Any future diversity-audit gate could derive the same signal from `(mechanism_dimension × theme_keywords)` — those two together pin down the conceptual position more reliably than a self-reported family label.

---

### Set 2 — Novelty self-report (3 fields → 2 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `dimension_novelty` | Length ≥30 + (Spec A3) grounded-mention against `MECHANISM_DIMENSIONS` enum | §5.6 prompt | Self-report w/ structural gate post-A3 |
| `novel_connection` | **Conditional** length-gate when `_computed_dominant_cluster_overlap == "high"` (`:1633-1641`) | §5.6 prompt | Self-report w/ structural condition |
| **`dominant_cluster_overlap`** | Pydantic Literal restricts enum; **LLM value DISCARDED — validator uses computed value (`:1632`)** | None (LLM value never used) | Self-report; effectively dead |

**Keep:** `dimension_novelty` (always-fires gate) + `novel_connection` (conditional-fires gate when computed overlap is high). Together they cover both novelty axes (dimension-level + high-overlap edge case).
**Drop:** `dominant_cluster_overlap`.

**Why drop:** the LLM's self-rating is already discarded by the validator in favor of a deterministic computed value. The field is already dead from the validator's perspective — it just wastes prompt tokens to ask for it.

---

### Set 3 — Prior-work awareness (5 fields → 3 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `prior_lever_outcomes` | Whipsaw rule (`:624-660`); Spec A §6.1 unknown-id rule | Validator | Typed + gated |
| `alternatives_considered` | ≥2 entries (per schema doc); per-entry `why_rejected` ≥40 chars | §5.6 prompt | Typed + gated |
| `mechanism_lineage` (PROPOSED) | Once added: structural-pivot gate after ≥3 same-cluster ancestors | Proposed | Typed list; ancestry-distinct |
| **`closest_prior_theses_considered`** | None — no validator rule references it | None | Untyped list |
| **`orthogonality_defense`** | None — no validator rule references it | None | Self-report prose |
| **`competing_hypothesis`** (PROPOSED) | Could be added but redundant with `alternatives_considered[0]` | Proposed | Object — overlaps with alternatives_considered |

**Keep:** `prior_lever_outcomes` (whipsaw substrate), `alternatives_considered` (the shortlist — contract strengthened: entry [0] = deepest near-equivalent with grounded tiebreaker), `mechanism_lineage` (PROPOSED — ancestry, structurally distinct from similarity).
**Drop:** `closest_prior_theses_considered`, `orthogonality_defense`, `competing_hypothesis` (PROPOSED).

**Why drop:**
- `closest_prior_theses_considered` — bare list with no per-entry rationale; the typed `prior_lever_outcomes` and `alternatives_considered` carry the same "I considered these priors" intent with enforceable structure.
- `orthogonality_defense` — prose self-defense ("why my thesis is distinct from nearest priors"); no validator can verify. Typed `prior_lever_outcomes` + `alternatives_considered` cover the same intent structurally.
- `competing_hypothesis` (PROPOSED) — is a special case of `alternatives_considered[0]`. Strengthening `alternatives_considered`'s contract to require entry [0] be the deepest near-equivalent + grounded tiebreaker captures the same intent without a separate field.

---

### Set 4 — Falsification + anti-overfit (3 fields → 1 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `disqualifiers` | Non-empty (`:823, 1681`); ≥1 entry `kind="mechanism_evidence"` required | Outcome evaluator (post-backtest) | Typed + gated |
| **`falsification_or_alternative`** | Length ≥80 chars only (`:1668-1671`) | §5.6 prompt | Self-report prose; length-only |
| **`why_not_overfit`** | None — no validator rule | None | Self-report prose |

**Keep:** `disqualifiers` (typed falsification triggers, structurally verifiable post-backtest). Strengthen its contract: require ≥2 entries (currently ≥1) with one `mechanism_evidence` entry addressing the alternative explanation AND one entry addressing overfit (e.g. `trade_count_collapse`).
**Drop:** `falsification_or_alternative`, `why_not_overfit`.

**Why drop:**
- `falsification_or_alternative` — the "alternative that would invalidate" intent is exactly what `mechanism_evidence`-kind disqualifier captures structurally. The prose version's length-only gate is fluffable; the typed alternative is enforceable post-backtest.
- `why_not_overfit` — the LLM that just proposed a potentially-overfit thesis writes its own defense. Trivially fluffable with "tested across multiple symbols/years" boilerplate. The structural alternative — a typed disqualifier addressing the overfit case — is enforceable and forces concrete falsification conditions.

---

### Set 5 — Evidence + confidence (4 fields → 2 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `evidence_citations` | ≥1 `source="web_search"` + ≥1 `source="analyst"` (per code comment `:118`) | §5.6 prompt | Typed + source-diversity gated |
| `confidence_distribution` (PROPOSED) | Once added: ≥1 of {data, literature} must be `direct` or `mixed` | Proposed | Per-dimension self-rating with structural gate |
| **`evidence`** | None (legacy untyped) | §5.6 prompt during Spec B transition | Untyped; Spec B retires |
| **`evidence_strength`** | Pydantic Literal only; no further gate | None | Single LLM self-rating; motivated reasoning |

**Keep:** `evidence_citations` (typed, source-diversity gated) + `confidence_distribution` (PROPOSED — per-dimension rating with validator gate against all-speculative).
**Drop:** `evidence` (legacy; Spec B retires), `evidence_strength` (single self-rating superseded by per-dimension `confidence_distribution`).

**Why drop:**
- `evidence` — Spec B's typed `evidence_citations` is the canonical evidence field; the legacy untyped `list[str]` exists only during transition.
- `evidence_strength` — the LLM optimistically self-rates `direct` because it wants the thesis accepted (motivated reasoning). Per-dimension `confidence_distribution` exposes the weakest link by structure: `{data: "direct", literature: "speculative", precedent: "proxy"}` is honest in a way `evidence_strength: "direct"` can never be.

---

### Set 6 — Config + legacy compat (3 fields → 1 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `config_changes` | Non-empty OR `requires_code_change=true` (`:1510`) | Compiler, §5.2 prompt | Compiler-mutated (LLM emits, compiler may rewrite) |
| **`base_contract_id`** | Must stay empty; rejected if populated (`:1741+`) | None | Legacy compat (dead) |
| **`base_config_path`** | When non-empty: relative repo path checks (`:279+`); must stay empty in practice | None | Legacy compat (dead) |

**Keep:** `config_changes`.
**Drop:** `base_contract_id`, `base_config_path`.

**Why drop:** both are legacy fields whose only role today is rejecting any non-empty value. Their declared purpose (pointing at base contracts) was superseded by the family-resolution path. The fields exist to defensively reject populated values that should never appear — that's pure dead weight. Removing them removes a class of "always-rejected" validator branches.

---

### Set 7 — Diagnostics (2 fields → 1 surviving)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `required_diagnostic_specs` | None today; Spec B adds typed gates | Spec B compiler | Typed |
| **`required_diagnostics`** | Alignment check at `:1551` (every non-builtin metric in `expected_effects` must appear here) | Compiler diagnostics generation | Untyped (Spec B retires) |

**Keep:** `required_diagnostic_specs` (typed; Spec B canonical).
**Drop:** `required_diagnostics` (Spec B retires this legacy untyped list).

**Why drop:** identical "what diagnostics does this thesis need" intent, but `required_diagnostic_specs` is typed (per-metric `{metric, direction, ...}` records) and will gain validator gates under Spec B. Two parallel surfaces serving the same purpose; the typed one wins.

---

### Set 8 — Forward-looking (3 fields → 1 surviving in this set)

| Field | Validator gate | Downstream consumer | Source quality |
|---|---|---|---|
| `if_this_fails_next_thesis` (PROPOSED) | Once added: non-empty + grounded reference to a different `mechanism_dimension` or `alternatives_considered` entry | Proposed | Forward pre-commitment |
| **`expected_reuse_across_future_theses`** | None | None | Speculative LLM prose |
| `mechanism_family_definition` | When `mechanism_dimension == "emergent"`: non-empty | Validator (emergent path) | Conditional contract — DISTINCT purpose |

**Keep:** `if_this_fails_next_thesis` (PROPOSED — pre-commits to next move; gate-able by non-empty + grounded reference). `mechanism_family_definition` stays — it serves the distinct emergent-dimension contract, not the forward-looking concern.
**Drop:** `expected_reuse_across_future_theses`.

**Why drop:** speculative prose claims about hypothetical future theses. No validator gate, no consumer, low signal. The `if_this_fails_next_thesis` forward-commitment is a more rigorous forward-looking concern: it requires a concrete next move, gateable by reference to an existing `alternatives_considered` entry.

---

## 4. Net field count

| Bucket | Before | After |
|---|---|---|
| Current schema fields | 35 | **23 surviving** (12 drops) |
| Proposed additions (A4 §4.12) | 6 | **5 surviving** (`competing_hypothesis` dropped) |
| Inner-shape extension to existing field | 0 | 1 (`expected_effects.magnitude_min/max`) |
| **Total LLM-facing fields (post-consolidation, post-A4 proposals)** | 41 | **28 + 1 extension** |

### 4.1 The 23 surviving current fields

| # | Field | Category |
|---|---|---|
| 1 | `thesis_id` | Identity |
| 2 | `strategy_family` | Identity (system-set, not LLM-emitted) |
| 3 | `hypothesis` | Core description |
| 4 | `mechanism` | Core description |
| 5 | `mechanism_dimension` | Positioning |
| 6 | `theme_keywords` | Positioning |
| 7 | `thesis_role` | Positioning |
| 8 | `dimension_novelty` | Novelty justification (gate upgraded by A3) |
| 9 | `novel_connection` | Novelty justification (gate upgraded by A3) |
| 10 | `underexplored_dimensions_considered` | Novelty justification |
| 11 | `prior_lever_outcomes` | Prior-work awareness |
| 12 | `alternatives_considered` | Prior-work awareness (contract strengthened — entry [0] = deepest competing) |
| 13 | `new_dimension_name` | Emergent-dimension contract (conditional) |
| 14 | `why_existing_dimensions_do_not_fit` | Emergent-dimension contract (conditional) |
| 15 | `mechanism_family_definition` | Emergent-dimension contract (conditional) |
| 16 | `evidence_citations` | Evidence |
| 17 | `expected_effects` | Predictions + falsification |
| 18 | `disqualifiers` | Predictions + falsification (contract strengthened — ≥2 entries with mechanism_evidence + overfit-related) |
| 19 | `config_changes` | Config + engine |
| 20 | `requires_code_change` | Config + engine (paired) |
| 21 | `requested_primitives` | Config + engine (paired) |
| 22 | `required_diagnostic_specs` | Diagnostics + code grounding |
| 23 | `source_code_verification` | Diagnostics + code grounding |

### 4.2 The 12 dropped current fields

`causal_cluster`, `dominant_cluster_overlap`, `closest_prior_theses_considered`, `orthogonality_defense`, `falsification_or_alternative`, `why_not_overfit`, `evidence`, `evidence_strength`, `base_contract_id`, `base_config_path`, `required_diagnostics`, `expected_reuse_across_future_theses`.

### 4.3 The 5 surviving proposed additions

| Field | Replaces / extends |
|---|---|
| `expected_runtime_signal` | New — complements `expected_effects` with mechanism-level signal verification |
| `expected_effects.magnitude_min/max` | Inner-shape extension — adds quantitative bounds to existing `expected_effects` |
| `mechanism_lineage` | New — ancestry chain (distinct from `closest_prior_theses_considered` which is dropped) |
| `if_this_fails_next_thesis` | New — forward pre-commitment |
| `confidence_distribution` | Supersedes dropped `evidence_strength`; per-dimension rating with structural gate |

### 4.4 Dropped proposal

`competing_hypothesis` — redundant with `alternatives_considered[0]` once that field's contract is strengthened to require entry [0] be the deepest near-equivalent with a grounded tiebreaker.

## 5. Contract-strengthening summary (3 fields)

Three surviving fields absorb the purposes of dropped fields by strengthening their contracts:

| Field | Old contract | New contract | Absorbs dropped fields |
|---|---|---|---|
| `alternatives_considered` | ≥2 typed entries; per-entry `why_rejected` ≥40 chars | Same, PLUS: entry [0] must be the deepest near-equivalent + per-entry `why_rejected` must mention a specific `evidence_citations` source, `disqualifiers` name, OR `mechanism_dimension` (grounded tiebreaker) | `competing_hypothesis`, `closest_prior_theses_considered`, `orthogonality_defense` |
| `disqualifiers` | ≥1 typed entry; ≥1 `kind="mechanism_evidence"` | Same, PLUS: ≥2 entries total, with one entry addressing the alternative explanation AND one addressing the overfit risk (e.g. `trade_count_collapse`-style) | `falsification_or_alternative`, `why_not_overfit` |
| `dimension_novelty` / `novel_connection` | Length-only check | Length + grounded-mention against `MECHANISM_DIMENSIONS` enum or shared `theme_keywords` (Spec A3 §4.7) | `dominant_cluster_overlap` (its computed-value role is preserved by the validator's internal computation; the LLM-facing field is gone) |

## 6. Cross-spec impact

- **Spec A3** (schema cleanup): expands the drop list from 6 fields → 12 fields. The 6 fields A3 currently drops + `dominant_cluster_overlap`, `falsification_or_alternative`, `evidence`, `evidence_strength` (deferred to Spec B retire — could land in A3 if priority allows), `base_contract_id`, `base_config_path`, `expected_reuse_across_future_theses`. Plus the three contract strengthenings to `alternatives_considered`, `disqualifiers`, and the existing `dimension_novelty`/`novel_connection` gates.

- **Spec A4** (OUTPUT instruction overhaul): §4 trimmed from 35 entries to **23 entries**; §4.12 trimmed from 6 proposed additions to 5 (drops `competing_hypothesis`). Category counts in §4.1–§4.10 reflect the new 23-field set. The §7 renderer absorbs A3's drops automatically once the schema changes land — no rewrite of A4 required when the cleanup ships.

- **Spec A1 / A2**: unchanged. They touch terminology and id provenance, not field consolidation.

## 7. Open questions

1. **`evidence` retirement timing** — A3 doesn't drop `evidence` today because Spec B retires it. Should A3 land the drop now (treating `evidence_citations` as the only evidence surface immediately) or wait for B? Current recommendation: include in A3's drop list, since A4's prompt already gives `evidence_citations` precedence.

2. **`required_diagnostics` retirement timing** — same question as `evidence`. Spec B canonical is `required_diagnostic_specs`. Recommendation: include in A3 drop list.

3. **`expected_effects` inner-shape extension** — should the magnitude bounds be added in A3 (alongside the schema cleanup) or A4 (alongside the OUTPUT rewrite that documents them)? Either is defensible; A4 is closer to the change's user-visible impact, A3 owns schema mods.

4. **Conditional rendering for `mechanism_lineage`** — when this field lands, should the §5.6 renderer always show it (even when empty list = no ancestry) or only when non-empty? Recommendation: show only when non-empty (the empty-list signal is itself meaningful — a true greenfield thesis).
