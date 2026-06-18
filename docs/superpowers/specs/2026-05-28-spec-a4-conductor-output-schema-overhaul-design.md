# Spec A4 — Conductor OUTPUT-Schema Instruction Overhaul

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** Spec A §5.0.6 (master field inventory); `research_prompts.py:117–154` (current OUTPUT section being replaced); audit transcript 2026-05-28.
**Depends on:** none — A4 documents the schema **as-is** (all 35 fields of `ResearchThesis`), so it can ship before, after, or independently of A3.
**Blocks:** none
**Parallel with:** Spec A1 (terminology), Spec A2 (id provenance), Spec A3 (schema cleanup — coordinates with A4 if both land; the renderer in §7 absorbs A3's drops automatically), Spec B / C / D

**Spec-A4 vs A3 interaction:** A4 makes the prompt a *programmatic projection* of the schema (§7 regenerator). When A3 lands and removes fields, A4's renderer re-renders the OUTPUT section minus those fields with zero hand-editing required. So A4 is the more durable fix and should ship first when possible — fixing the production-rejection root cause before bothering with which fields to keep.

---

## 1. Goal

**Rewrite the conductor's OUTPUT instruction section** (`research_prompts.py:117–154`) so the LLM produces well-formed, validator-compliant `ResearchThesis` JSON on the first attempt, every time. Cover **all 35 fields** of the current `ResearchThesis` schema (not a curated subset) — including legacy compat fields and fields with weak gates that A3 may later drop. Eliminate the seven systemic problems identified in the 2026-05-28 audit:

1. **Schema-prompt drift** — OUTPUT documents ~17 of ~30 fields; ~9 validator-required fields are unnamed in the prompt.
2. **Shape ambiguity** — typed object fields (`ExpectedEffect`, `Disqualifier`, `Alternative`, `EvidenceCitation`, `PriorLeverOutcome`, `DiagnosticRequirementSpec`) named but their inner shape never shown.
3. **DOCTRINE ↔ OUTPUT disconnect** — soft principles in DOCTRINE imply concrete typed-field contracts but never name the fields or shapes.
4. **Inconsistent field-instruction quality** — some entries are one-liners, some are paragraphs with examples; no template.
5. **Conditional requirements scattered** — emergent-dimension contract (3 conditionally-required fields) completely omitted from OUTPUT.
6. **No worked example** — prompt asks for "ONE JSON object matching the thesis schema" without showing one.
7. **No drift detection** — schema, validator, and prompt drift independently; today they're 18 fields apart.

**Production evidence the failure mode is real:** every conductor attempt on the VPS DBs (3 attempts across 2 jobs, 2026-05-09 to 2026-05-27) was rejected pre-flight; one rejection message was `"validation_failed: expected exactly one thesis, got 0"` — consistent with the LLM not knowing the envelope shape it must produce.

## 2. Non-goals

- **Schema-level changes** — A4 doesn't add, remove, or rename fields. A3 owns that. A4 only restructures *how the schema is communicated to the LLM*.
- **Validator rule changes** — A4 doesn't add or modify rules. The OUTPUT section must accurately reflect the validator's actual rules (post-A3), nothing more.
- **DOCTRINE rewrite** — DOCTRINE stays as soft principles. A4 adds *cross-references* from DOCTRINE to OUTPUT (and vice versa) but doesn't rewrite the prose.
- **Tool-list rewrite** — Spec A §5.10 already handles this. A4 leaves TOOLS untouched.
- **Multi-LLM testing or evaluation harness** — A4 is the prompt redesign; measuring its effect on conductor acceptance rate is downstream work.

## 2.1 No backward compatibility — hard cutover

The OUTPUT section is replaced wholesale in one PR. Old prose deleted, new template applied. No deprecation, no A/B rendering. Pre-cutover prompts disappear from the codebase. Same policy as Spec A1/A2/A3.

## 3. Per-field entry template

Every field in the rewritten OUTPUT section follows this fixed template. The template is what makes the prompt's field instructions consistent and complete.

```
- <field_name>
    Type:        <Python type / typed object name>
    Format:      <free-form prose | short label | enum value | typed list | typed object | bool | int | float | path>
    Source set:  <Free | One-of: [list] | Computed: <how>>
    Token cap:   <~N tokens | unbounded | ≥M chars>
    Required:    <Always | Conditional on <X> | Optional>
    Meaning:     <one sentence — what this field captures>
    Producer guidance: <one sentence — how the LLM should think about producing it>
    Validator rule:    <what the validator hard-rejects | what gets soft-warned | none>
    Example:     <concrete value the LLM can pattern-match against>
```

**Why every field gets every slot:** completeness is the contract. If the LLM has to infer a slot (e.g. "is this required?"), the prompt has failed. If a slot doesn't apply, the entry says "—" or "n/a" explicitly so the reader knows the absence is deliberate, not an oversight.

**Why typed objects get a sub-template:** typed-object fields (`Disqualifier`, etc.) include an additional `Inner shape:` slot rendering the typed class as `{key: type, ...}` so the LLM doesn't guess at nesting.

## 4. Field-by-field OUTPUT entries (post-A3 schema)

Below is the rewritten OUTPUT section. **23 surviving fields** (down from 35 in the current `ResearchThesis` schema per `research_types.py:139–211`), each rendered per §3's template. Fields are grouped into 10 logical categories so the LLM (and human reviewers) can scan by concern.

> **35 → 23 per A4a consolidation.** This OUTPUT section was originally drafted to cover all 35 schema fields. After the field-by-field duplicate audit in **Spec A4a** (`2026-05-28-spec-a4a-field-consolidation-analysis.md`), **12 fields were identified as duplicates of stronger structural alternatives** and dropped from the LLM-facing prompt. The drops are absorbed by **3 surviving fields whose contracts are strengthened** (`alternatives_considered`, `disqualifiers`, and the `dimension_novelty`/`novel_connection` gates). See A4a §3 for the per-set duplicate analysis and A4a §5 for the contract-strengthening summary. **Dropped fields:** `causal_cluster`, `dominant_cluster_overlap`, `closest_prior_theses_considered`, `orthogonality_defense`, `falsification_or_alternative`, `why_not_overfit`, `evidence`, `evidence_strength`, `base_contract_id`, `base_config_path`, `required_diagnostics`, `expected_reuse_across_future_theses`.

**Categories (23 fields total):**

| § | Category | Count | Concern |
|---|---|---|---|
| 4.1  | Identity | 2 | Identifiers — who/what the thesis is |
| 4.2  | Core description | 2 | The claim + the causal story |
| 4.3  | Positioning + classification | 3 | Where the thesis sits in the mechanism landscape |
| 4.4  | Novelty justification | 3 | Why the thesis isn't a near-duplicate of priors |
| 4.5  | Prior-work awareness | 2 | What priors the proposer compared against (typed) |
| 4.6  | Emergent-dimension contract | 3 | Conditional fields required only when proposing a new dimension |
| 4.7  | Evidence | 1 | What backs the claim (typed) |
| 4.8  | Predictions + falsification | 2 | What outcome is predicted, what would invalidate the thesis |
| 4.9  | Config + engine | 3 | Runtime knobs + code-change requests |
| 4.10 | Diagnostics + code grounding | 2 | Metric instrumentation + source-code citation |

**Coordination with A3:** Spec A3 originally drops 6 fields; per A4a's analysis, A3's drop list expands to 12. A4 ships the prompt for the post-consolidation 23-field set. If A3 hasn't shipped yet, the §7 renderer can drop the 12 fields from the rendered OUTPUT immediately based on an explicit allowlist; once A3 lands, the renderer trivially matches the schema by introspection.

### 4.1 Identity (2 fields)

```
- thesis_id
    Type:        str
    Format:      short identifier — LLM-emitted today (prompt says "short_snake_case_name");
                 system-assigned post-A2 (the validator will overwrite whatever you emit).
    Source set:  LLM today / SYSTEM post-A2 (f"{research_round_id}-attempt-{N}")
    Token cap:   <30 chars
    Required:    Yes — required by validator (non-empty check + uniqueness rule)
    Meaning:     Identifier of the (accepted) thesis, unique per round-attempt.
    Producer guidance: Emit a short snake_case label that has not appeared in any
                       prior thesis (cluster-fixation rule will complain otherwise).
                       Post-A2, you may omit; the system assigns it.
    Validator rule:    Non-empty (thesis_validator.py:1584); uniqueness
                       (_check_thesis_id_not_repeated). Rejection codes:
                         structural_missing_thesis_id
                         structural_thesis_id_repeated
    Example:     "htf_direction_gate_v1"
```

```
- strategy_family
    Type:        str
    Format:      family identifier (snake_case, no spaces)
    Source set:  System — set by `_prepare_thesis_for_validation` at autoresearch_research.py:100
    Token cap:   ≤20 chars
    Required:    Yes — but system-set, not LLM-produced. If you emit a value
                 it will be overwritten with the family context the conductor
                 is running for.
    Meaning:     Which strategy family this thesis belongs to (e.g. "ema", "orb").
    Producer guidance: Omit from your JSON output. The controller injects it
                       based on the active job's family.
    Validator rule:    Pydantic-required (no default); no semantic check.
    Example:     "ema"
```

### 4.2 Core description (2 fields)

```
- hypothesis
    Type:        str
    Format:      one sentence (no list, no bullets, no quotes)
    Source set:  Free
    Token cap:   ≤40 words / ≤300 chars (soft guideline; validator checks
                 non-empty only)
    Required:    Always
    Meaning:     The thesis's core claim — what should happen and under what
                 conditions.
    Producer guidance: State the mechanism, not the parameter. "Tighter stops
                       reduce wick-stops in high-vol regimes" is a mechanism;
                       "Set min_stop_distance_pct=0.0035" is a parameter tweak.
    Validator rule:    Non-empty (thesis_validator.py:1591). Rejection code:
                       structural_missing_hypothesis.
    Example:     "Adding a 1-hour direction gate filters out counter-trend
                  5-min pullbacks that drove the strategy's drawdown floor."
```

```
- mechanism
    Type:        str
    Format:      1-3 sentences explaining the causal story
    Source set:  Free
    Token cap:   ≤150 words / ≤1000 chars (soft)
    Required:    Always
    Meaning:     Why the hypothesis should hold — the market-mechanics
                 explanation, in trader/market terms.
    Producer guidance: Describe order flow, regime behavior, or microstructure
                       that explains WHY the hypothesis should be true. Avoid
                       restating the hypothesis in different words.
    Validator rule:    Non-empty (thesis_validator.py:1596). Rejection code:
                       structural_missing_mechanism.
    Example:     "HTF direction acts as a regime overlay — when 1h trend is up,
                  only long 5-min pullbacks fire. Reduces signal count by ~40%
                  but surviving signals have better edge because they align
                  with the dominant regime."
```

### 4.3 Positioning + classification (3 fields — `causal_cluster` dropped per A4a)

```
- mechanism_dimension
    Type:        str
    Format:      enum-style short label
    Source set:  One-of:
                   entry_timing | exit_mechanism | signal_quality |
                   regime_conditioning | portfolio_construction |
                   risk_structure | market_microstructure |
                   execution_costs | universe_selection |
                   alternative_data | alpha_decay | emergent |
                 <a prior accepted "emergent" dimension name>
                 (full list rendered above in VALIDATOR GUARDRAILS — must match)
    Token cap:   single label
    Required:    Always
    Meaning:     Which mechanism family this thesis belongs to.
    Producer guidance: Pick the category that BEST fits the lever you're
                       changing, not the category you wish to explore.
    Validator rule:    Must be a member of MECHANISM_DIMENSIONS
                       (research_types.py:117–136). Rejection code:
                       structural_invalid_mechanism_dimension.
    Example:     "regime_conditioning"
```

```
- theme_keywords
    Type:        list[str]
    Format:      list of 2-3 short noun phrases (snake_case lowercase)
    Source set:  Free
    Token cap:   2-3 entries, each ≤20 chars
    Required:    Always (≥1 entry; ≥2 strongly recommended per DOCTRINE)
    Meaning:     Lever-theme tokens identifying the specific knob this thesis
                 touches. Used by cluster-fixation and direction-whipsaw rules.
    Producer guidance: Use the same token across theses that touch the SAME
                       lever (e.g. always "stop_distance" for min/max stop
                       changes). Do NOT invent a new token for each thesis.
    Validator rule:    Used by cluster-fixation rule (max 3 of last 7 share
                       keywords) and whipsaw rule. Not directly required-
                       non-empty, but theses with empty list will fail
                       downstream gates that compare against it.
    Example:     ["htf_gate", "trend_overlay"]
```

```
- thesis_role
    Type:        Literal["", "orthogonal_discovery",
                          "implementation_unlock",
                          "cleanup_validation_follow_up"]
    Format:      single enum label
    Source set:  One-of (above)
    Token cap:   single label
    Required:    Always (use "" if none of the three apply)
    Meaning:     Categorical commitment about what kind of work this thesis
                 represents.
                   - orthogonal_discovery: tests a lever family not previously
                     explored or kept
                   - implementation_unlock: paves the way for future code-change
                     theses
                   - cleanup_validation_follow_up: ties up loose ends from a
                     prior round's contested finding
    Producer guidance: Pick the role that fits BEFORE you finalize the thesis;
                       if you can't pick, your hypothesis is probably unfocused.
    Validator rule:    Pydantic Literal restricts to the 4 values.
    Example:     "orthogonal_discovery"
```

### 4.4 Novelty justification (3 fields — `dominant_cluster_overlap` dropped per A4a)

```
- dimension_novelty
    Type:        str
    Format:      1-2 sentences
    Source set:  Free, but MUST mention ≥1 specific mechanism_dimension name
                 (per Spec A3 §4.7a grounded-mention gate)
    Token cap:   ≥30 chars (hard), ≤80 words (soft)
    Required:    Always
    Meaning:     Why your chosen mechanism_dimension is structurally novel
                 (not a parameter tweak of prior work in the same dimension).
    Producer guidance: Reference the dimension name explicitly. "This moves
                       from signal_quality (where prior trend_filter_v2 lived)
                       to regime_conditioning — a different lever family."
    Validator rule:    Length ≥30 chars + must mention ≥1 dimension name from
                       the enum (post-Spec-A3). Rejection codes:
                         structural_dimension_novelty_too_short
                         thesis_quality_dimension_novelty_not_grounded
    Example:     "Moves from signal_quality threshold tweaking to
                  regime_conditioning by overlaying a 1h direction gate."
```

```
- novel_connection
    Type:        str
    Format:      1-2 sentences
    Source set:  Free, but MUST mention a specific shared theme_keyword or
                 a structurally-distinct mechanism_dimension (Spec A3 §4.7b
                 grounded-mention)
    Token cap:   ≥`_MIN_NOVEL_CONNECTION_CHARS` chars (hard, when required)
    Required:    REQUIRED IF computed cluster overlap with priors is "high"
                 (≥50% theme_keywords overlap); otherwise OMIT
    Meaning:     Why this high-overlap thesis is materially new (not just
                 another variation of the dominant cluster).
    Producer guidance: Reference the shared keyword by name and explain the
                       structural difference. The presence of N chars without
                       a grounded mention is rejected.
    Validator rule:    Conditional gate at thesis_validator.py:1633-1641;
                       length + (post-A3) grounded-mention. Rejection codes:
                         structural_novel_connection_too_short
                         thesis_quality_novel_connection_not_grounded
    Example:     "Recasts stop_distance as a regime-detection signal rather
                  than an absolute threshold — distinct from prior
                  stop_distance theses that all tested fixed thresholds."
```

```
- underexplored_dimensions_considered
    Type:        list[str]
    Format:      list of mechanism_dimension names
    Source set:  One-of (the mechanism_dimension enum, minus the dimension
                 you picked)
    Token cap:   ≥1 entry when prior theses exist; ≤4 entries
    Required:    REQUIRED when prior theses exist; otherwise OMIT
    Meaning:     The dimensions you considered as alternatives BEFORE picking
                 the one you chose.
    Producer guidance: Pick from the FAMILY LANDSCAPE block above (it shows
                       which dimensions are unexplored). Do NOT include the
                       chosen dimension. Spec A §6.2 emits a soft warn if the
                       chosen dimension has more attempts than every dimension
                       you list here.
    Validator rule:    Well-formedness check
                       (`_validate_underexplored_dimensions`,
                       thesis_validator.py:1421+); §6.2 (Spec A) soft warn
                       for misclassification. Hard rejection code:
                       structural_underexplored_dimensions_invalid.
    Example:     ["portfolio_construction", "regime_conditioning"]
```

### 4.5 Prior-work awareness (2 fields — `closest_prior_theses_considered` + `orthogonality_defense` dropped per A4a; both replaced by the two typed fields below)

```
- prior_lever_outcomes
    Type:        list[PriorLeverOutcome]
    Format:      typed list — see Inner shape
    Inner shape: PriorLeverOutcome = {
                     prior_thesis_id:  str,    # MUST exist in this round's snapshot (Spec A §6.1)
                     lever:            str,    # the shared knob (e.g. "stop_distance")
                     direction_then:   Literal["tighten", "loosen", "extend",
                                                "shorten", "add", "remove"],
                     outcome:          Literal["kept", "killed", "inconclusive"],
                     why_retry:        str (≥40 chars),
                 }
    Source set:  Free; prior_thesis_id values constrained by snapshot
    Token cap:   ≤4 entries
    Required:    Required when the new thesis flips a lever direction tested
                 by a prior (direction-whipsaw rule). Otherwise optional.
    Meaning:     Citations of prior theses that tested the same lever in a
                 different direction — and why this round is justified in
                 retrying it.
    Producer guidance: If you're flipping a lever direction (e.g. tightening
                       a stop a prior thesis loosened), you MUST cite the
                       prior here with direction_then + outcome + why_retry.
                       Otherwise the whipsaw rule rejects.
    Validator rule:    Whipsaw rule (thesis_validator.py:624–660); §6.1 (Spec A)
                       requires prior_thesis_id values to exist in snapshot
                       thesis_ids set. Rejection codes:
                         structural_direction_whipsaw_uncited
                         structural_prior_lever_outcomes_unknown_id
    Example:     [
                   {"prior_thesis_id": "job-11-round-2-attempt-1",
                    "lever": "stop_distance",
                    "direction_then": "tighten",
                    "outcome": "killed",
                    "why_retry": "Prior tightened stop in calm regime; this
                                  tightens only in high-vol regime — different
                                  context entirely."}
                 ]
```

```
- alternatives_considered
    Type:        list[Alternative]
    Format:      typed list — see Inner shape
    Inner shape: Alternative = {
                     mechanism:    str,                  # the alternative idea
                     why_rejected: str (≥40 chars),      # why you didn't pick it
                 }
    Source set:  Free
    Token cap:   ≥2 entries (hard); ≤4 entries
    Required:    Always; ≥2 entries
    Meaning:     Pre-vetted alternative mechanisms considered before picking
                 this one — the "shortlist."
    Producer guidance: **Entry [0] must be the deepest near-equivalent
                       alternative** — the one that, if you had reversed
                       the decision, would have produced a roughly equally
                       strong thesis. Its `why_rejected` must contain a
                       **grounded tiebreaker**: reference a specific
                       `evidence_citations` entry by source/citation
                       substring, OR a specific `disqualifiers` name, OR
                       a specific `mechanism_dimension` from §5.8 landscape.
                       Entries [1..N] are other rejected alternatives —
                       must be DIFFERENT mechanisms, not parameter variants.
                       (This contract-strengthening per A4a §5 absorbs the
                       dropped `competing_hypothesis` proposed-field.)
    Validator rule:    ≥2 entries; each why_rejected ≥40 chars; entry [0]'s
                       why_rejected must contain a grounded tiebreaker
                       (post-A4a: regex match against an `evidence_citations`
                       substring, `disqualifiers` name, or
                       `MECHANISM_DIMENSIONS` enum value). Rejection codes:
                         structural_alternatives_considered_too_short
                         thesis_quality_alternatives_entry_0_tiebreaker_not_grounded
    Example:     [
                   {"mechanism": "wider stop-distance cap",
                    "why_rejected": "Doesn't address the wick-only false-break
                                     root cause directly."},
                   {"mechanism": "session-time entry filter",
                    "why_rejected": "Proxy for the wick problem rather than
                                     the structural fix."}
                 ]
```

### 4.6 Emergent-dimension contract (3 fields — all conditional on `mechanism_dimension == "emergent"`; `expected_reuse_across_future_theses` dropped per A4a)

```
- new_dimension_name
    Type:        str
    Format:      snake_case short name (≤40 chars)
    Source set:  Free, but must NOT duplicate an existing core dimension name
    Token cap:   ≤40 chars
    Required:    REQUIRED IF mechanism_dimension == "emergent"; otherwise OMIT
    Meaning:     The new dimension name you're introducing.
    Producer guidance: Use only when no existing dimension fits. The name
                       you pick can be cited by future theses, so make it
                       semantically clear.
    Validator rule:    When emergent: non-empty + not a core-dimension name
                       (thesis_validator.py:1388+). Rejection code:
                       structural_new_dimension_name_duplicates_core.
    Example:     "session_microstructure"   (when mechanism_dimension="emergent")
```

```
- why_existing_dimensions_do_not_fit
    Type:        str
    Format:      paragraph
    Source set:  Free
    Token cap:   ≥80 chars
    Required:    REQUIRED IF mechanism_dimension == "emergent"; otherwise OMIT
    Meaning:     Why none of the 11 core dimensions could host this thesis.
    Producer guidance: Address each adjacent core dimension explicitly — name
                       it and say why it doesn't fit.
    Validator rule:    When emergent: non-empty. Rejection code:
                       structural_emergent_thesis_malformed.
    Example:     "Not market_microstructure because the mechanism is about
                  session-edge behavior, not order-flow imbalance. Not
                  regime_conditioning because regime is a longer-horizon
                  concept; this is single-session-edge."
```

```
- mechanism_family_definition
    Type:        str
    Format:      paragraph
    Source set:  Free
    Token cap:   ≥80 chars
    Required:    REQUIRED IF mechanism_dimension == "emergent"; otherwise OMIT
    Meaning:     A short definition of what falls inside the new family.
                 Future theses will pattern-match against this.
    Producer guidance: Write the definition as if onboarding a future research
                       round. "Theses in this dimension address X by doing Y."
    Validator rule:    When emergent: non-empty. Rejection code:
                       structural_emergent_thesis_malformed.
    Example:     "Theses that exploit liquidity/volatility asymmetry within
                  the first 30 minutes of the regular session."
```

### 4.7 Evidence (1 field — `evidence` legacy + `evidence_strength` self-report dropped per A4a)

```
- evidence_citations
    Type:        list[EvidenceCitation]
    Format:      typed list — see Inner shape
    Inner shape: EvidenceCitation = {
                     source:   Literal["web_search", "analyst"],
                     citation: str,                       # what was cited
                 }
    Source set:  Free
    Token cap:   ≥2 entries; ≤6 entries
    Required:    Always; MUST contain ≥1 with source="web_search" AND
                 ≥1 with source="analyst"
    Meaning:     Typed evidence with required source diversity. Replaces the
                 legacy `evidence: list[str]` field; both are accepted today
                 (Spec B retires `evidence`).
    Producer guidance: ≥1 web_search entry citing external mechanism evidence
                       (paper/source/precedent); ≥1 analyst entry citing
                       trade-level evidence from the strategy's own diagnostics.
    Validator rule:    ≥1 web_search + ≥1 analyst (per code comment at
                       thesis_validator.py:118). Rejection code:
                       structural_evidence_citations_missing_source_diversity.
    Example:     [
                   {"source": "web_search",
                    "citation": "Cont et al. on order-flow imbalance"},
                   {"source": "analyst",
                    "citation": "round-3 analyst found wick-stops 37% of all stops"}
                 ]
```

### 4.8 Predictions + falsification (2 fields — `falsification_or_alternative` + `why_not_overfit` dropped per A4a; intent absorbed by strengthened `disqualifiers` contract below)

```
- expected_effects
    Type:        list[ExpectedEffect]
    Format:      typed list — see Inner shape below
    Inner shape: ExpectedEffect = {
                     metric:     str,                     # the metric name
                     direction:  Literal["increase",
                                         "decrease",
                                         "decrease_or_same",
                                         "no_change"],
                     rationale:  str (≥40 chars),
                 }
    Source set:  Free (metric names should be builtins OR listed in
                 required_diagnostics)
    Token cap:   ≥2 entries (DOCTRINE D1 expectation); ≤6 entries
    Required:    Always; non-empty
    Meaning:     Per-metric predictions of directional impact, used by the
                 outcome evaluator to compare prediction vs actual.
    Producer guidance: Predict ≥2 coupled metrics (one primary, one secondary)
                       so the mechanism is testable, not just lucky.
    Validator rule:    Non-empty list (thesis_validator.py:1533); metric-name
                       alignment with required_diagnostics
                       (thesis_validator.py:1551). Rejection codes:
                         structural_missing_expected_effects
                         structural_expected_effect_metric_not_declared
    Example:     [
                   {"metric": "profit_factor",
                    "direction": "increase",
                    "rationale": "HTF gate filters counter-trend chop."},
                   {"metric": "trade_count",
                    "direction": "decrease_or_same",
                    "rationale": "Filtering should not collapse frequency."}
                 ]
```

```
- disqualifiers
    Type:        list[Disqualifier]
    Format:      typed list — see Inner shape
    Inner shape: Disqualifier = {
                     name:      str,                       # short label
                     condition: str,                       # falsifying condition
                     severity:  Literal["hard_fail", "soft_fail"],
                     kind:      Literal["metric_threshold",
                                        "mechanism_evidence"],
                 }
    Source set:  Free
    Token cap:   ≥2 entries (post-A4a); ≤5 entries
    Required:    Always; non-empty; **≥2 entries with ≥1 `kind="mechanism_evidence"`
                 AND ≥1 entry addressing overfit risk** (e.g.
                 `trade_count_collapse`, `cross_symbol_divergence`,
                 `regime_specific_overfit`). Per A4a §5 contract-strengthening
                 — absorbs the dropped `falsification_or_alternative` and
                 `why_not_overfit` fields by structurally requiring what
                 those prose fields tried to elicit.
    Meaning:     Stated conditions under which the thesis is wrong. Two
                 sub-roles: (a) mechanism-evidence entries serve as the
                 typed equivalent of the dropped `falsification_or_alternative`
                 — they state the data pattern that would distinguish your
                 mechanism from an alternative; (b) overfit-related entries
                 serve as the typed equivalent of the dropped
                 `why_not_overfit` — they pre-commit to a falsification
                 threshold that catches per-symbol/per-period overfit.
    Producer guidance: At least one disqualifier must test the MECHANISM
                       (kind="mechanism_evidence"), e.g. "wick-only stop-out
                       rate unchanged across regimes." At least one disqualifier
                       must address OVERFIT, e.g. `trade_count_collapse`
                       (drops >50%) or `cross_symbol_divergence` (PF varies
                       >2× across the 8-symbol universe).
    Validator rule:    Non-empty (thesis_validator.py:823, 1681); ≥2 entries
                       post-A4a; ≥1 `kind="mechanism_evidence"`; ≥1 entry
                       whose `name` or `condition` matches one of the
                       overfit-marker patterns. Rejection codes:
                         structural_disqualifiers_too_few
                         structural_disqualifiers_no_mechanism_evidence
                         structural_disqualifiers_no_overfit_address
    Example:     [
                   {"name": "trade_count_collapse",
                    "condition": "trade_count decreases by more than 50%",
                    "severity": "hard_fail",
                    "kind": "metric_threshold"},
                   {"name": "no_regime_separation",
                    "condition": "PF in up-regime not >1.2× PF in down-regime",
                    "severity": "hard_fail",
                    "kind": "mechanism_evidence"}
                 ]
```

### 4.9 Config + engine (3 fields — `base_contract_id` + `base_config_path` legacy-compat dropped per A4a)

```
- config_changes
    Type:        dict[str, Any]
    Format:      {key: new_value, ...} — runtime config keys to set
    Source set:  Free (keys must exist in the strategy's runtime config schema)
    Token cap:   ≤30 keys (hard cap via _proposer_specified_max_keys)
    Required:    Non-empty UNLESS requires_code_change=true
    Meaning:     The runtime-config knobs the proposer chose to set this round.
    Producer guidance: Include EVERY key you want set. Keys you omit remain
                       at family-baseline default — they do NOT inherit from
                       prior rounds' configs.
    Validator rule:    Non-empty OR requires_code_change=true
                       (thesis_validator.py:1510). Rejection code:
                       structural_config_changes_required.
    Example:     {"min_stop_distance_pct": 0.0035, "gap_filter": true,
                  "trail_after_r": 3.0}
```

```
- requires_code_change
    Type:        bool
    Format:      true | false
    Source set:  {true, false}
    Token cap:   single bool
    Required:    Always (defaults to false if omitted)
    Meaning:     Whether this thesis needs new engine primitives that no
                 existing config key can express.
    Producer guidance: Set true ONLY when no combination of existing config
                       keys would test the mechanism. Most theses are false.
    Validator rule:    When true, requested_primitives must be non-empty
                       (thesis_validator.py:863). Rejection code:
                       structural_engine_change_request_malformed.
    Example:     false
```

```
- requested_primitives
    Type:        list[str]
    Format:      list of short snake_case primitive names
    Source set:  Free
    Token cap:   ≤5 entries, each ≤40 chars
    Required:    REQUIRED IF requires_code_change=true; otherwise []
    Meaning:     Names of new primitive functions/filters the engine needs.
    Producer guidance: Use the same name a strategy developer would pick:
                       "close_confirmed_entry_gate", not "gate_for_my_thesis".
    Validator rule:    Non-empty pair with requires_code_change=true. Same
                       rejection code as above.
    Example:     ["close_confirmed_entry_gate"]
```

### 4.10 Diagnostics + code grounding (2 fields — `required_diagnostics` legacy untyped dropped per A4a; canonical typed field is `required_diagnostic_specs` below)

```
- required_diagnostic_specs
    Type:        list[DiagnosticRequirementSpec]
    Format:      typed list — see Inner shape
    Inner shape: DiagnosticRequirementSpec = {
                     metric:    str,                # diagnostic metric name
                     direction: str,                # "increase" / "decrease" / "any"
                     ...                            # see research_types.py for full schema
                 }
    Source set:  Free
    Token cap:   ≤5 entries
    Required:    Optional today; Spec B will make this required for non-builtin
                 metrics (replacing the untyped `required_diagnostics`).
    Meaning:     Typed version of `required_diagnostics` with structured spec
                 per metric. Empty today by convention; populate once Spec B
                 lands.
    Producer guidance: Omit during Spec B transition. Use `required_diagnostics`
                       (untyped) until Spec B retires it.
    Validator rule:    None today; Spec B will add gates.
    Example:     []  (or omit)
```

```
- source_code_verification
    Type:        str
    Format:      "<repo path>:<function or symbol> — <explanation>"
    Source set:  Free, but must match the format pattern
    Token cap:   ≥40 chars (hard); ≤200 chars
    Required:    Always
    Meaning:     Citation of the strategy source file and function whose
                 behavior the proposed change touches.
    Producer guidance: Read the file BEFORE proposing the thesis. Don't
                       fabricate paths. If the change requires a NEW
                       function, name the file where it would go.
    Validator rule:    Length ≥40 chars (per code comment thesis_validator.py:119);
                       (A3 §4.7-style grounded-mention may upgrade to require
                       a path:identifier regex match)
    Example:     "strategies/ema/signals.py:apply_htf_gate — gate evaluated
                  before stop_distance check; placing here ensures the
                  filter sees the raw signal."
```

### 4.11 Optional escape hatch — meta-field, not part of `ResearchThesis` schema (1 field)

```
- validator_challenge  (OPTIONAL)
    Type:        object
    Format:      {challenged_round, challenged_thesis_id,
                  challenged_rejection_code, claim, evidence}
    Source set:  Free
    Token cap:   ≤200 words total
    Required:    Optional. Use only if you believe a recent rejection was
                 wrong.
    Meaning:     A formal challenge to a prior rejection. Logged for human
                 review; does NOT alter the validator's decision.
    Producer guidance: Use sparingly. Most "the validator was wrong" feelings
                       are actually "the validator surfaced something I didn't
                       want to address."
    Validator rule:    No rule — accepts any object.
    Example:     {"challenged_round": 3,
                  "challenged_thesis_id": "job-1-round-3-attempt-2",
                  "challenged_rejection_code": "structural_alternatives_considered_too_short",
                  "claim": "The 2-entry minimum should not apply when…",
                  "evidence": "…"}
```

### 4.12 Proposed schema additions (NOT in current schema; A4 documents the contract for the renderer to absorb when added)

These fields are **first-principles additions** identified during the 2026-05-28 audit. None of them exist in `research_types.py` today. A4's job is to specify their OUTPUT-section contract; a separate spec (Spec A5 or similar) would add them to the schema, validator, and downstream consumers. Documenting them here means the §7 renderer absorbs them automatically when the schema lands them — no second prompt redesign needed.

Each entry follows the same §3 template, with `Required:` always marked `Proposed addition — not in schema yet` so the LLM doesn't try to populate them today.

**5 new top-level fields + 1 inner-shape extension to an existing field:**

```
- expected_runtime_signal   (PROPOSED — not in schema)
    Type:        list[ExpectedRuntimeSignal]
    Format:      typed list — see Inner shape
    Inner shape: ExpectedRuntimeSignal = {
                     event_path:        str,    # dotted path into strategy_diagnostics
                                                # (e.g. "rejection_breakdown.trend_filter_rejected"
                                                #  or "event_counts.signals_generated")
                     expected_relation: Literal[">", ">=", "<", "<=", "==", "between"],
                     expected_value:    float | tuple[float, float],
                     condition:         str,    # when this signal should hold
                                                # (e.g. "in trending regimes" or "overall")
                 }
    Source set:  Free
    Token cap:   ≥1 entry recommended; ≤3 entries
    Required:    Proposed addition — not in schema yet. Do not populate today.
    Goal served: G1 (compare outcome vs reasoning at the MECHANISM level,
                 not just the metric level).
    Meaning:     Typed prediction of what should be observable in
                 strategy_events.jsonl / strategy_diagnostics.json if the
                 mechanism is working. Distinct from `expected_effects`,
                 which predicts headline metrics; this predicts the
                 signal-flow behavior the mechanism implies.
    Producer guidance: Predict the SIGNAL-FLOW behavior the mechanism implies,
                       not the metric movement. Example: a regime-overlay
                       thesis should predict that trend_filter_rejected share
                       rises in trending regimes — the outcome evaluator can
                       check this directly against the diagnostics file.
    Validator rule (proposed):
                 Each entry's `event_path` must resolve in the prior round's
                 diagnostics JSON (so the LLM can't predict against
                 non-existent metrics). Rejection code:
                 thesis_quality_expected_runtime_signal_path_unknown.
    Example:     [
                   {"event_path": "rejection_breakdown.trend_filter_rejected",
                    "expected_relation": ">",
                    "expected_value": 0.3,
                    "condition": "in trending regimes"},
                   {"event_path": "event_counts.signals_generated",
                    "expected_relation": "between",
                    "expected_value": [800, 1500],
                    "condition": "overall (vs ~2400 in baseline)"}
                 ]
```

```
- expected_effects  (PROPOSED INNER-SHAPE EXTENSION — field already exists)
    Today's shape:    {metric, direction, rationale}
    Proposed shape:   {metric, direction, rationale, magnitude_min, magnitude_max}

    Inner shape (post-extension):
                 ExpectedEffect = {
                     metric:         str,
                     direction:      Literal["increase", "decrease",
                                              "decrease_or_same", "no_change"],
                     magnitude_min:  float | None,    # NEW — lower bound of expected range
                     magnitude_max:  float | None,    # NEW — upper bound of expected range
                     rationale:      str (≥40 chars),
                 }
    Required:    Existing field stays required (non-empty list). The two NEW
                 magnitude fields are optional today (default None);
                 proposed spec adds a validator rule requiring BOTH bounds
                 when `direction != "no_change"`.
    Goal served: G1 (quantitative calibration of predictions).
    Meaning:     Quantitative bounds on the predicted metric movement.
                 Today direction is qualitative only — "increase" is a hit
                 whether actual movement is 0.001 or 0.50. Bounds enable
                 real calibration: did the actual movement fall in
                 [magnitude_min, magnitude_max]?
    Producer guidance: Set both bounds when proposing a non-trivial
                       prediction. Wide bounds (e.g. 0.05 to 0.50) signal
                       low confidence; tight bounds (0.10 to 0.15) signal
                       a specific quantitative claim.
    Validator rule (proposed):
                 When `direction in {"increase", "decrease"}`, BOTH
                 magnitude_min and magnitude_max must be set, and
                 magnitude_min < magnitude_max. Rejection code:
                 thesis_quality_expected_effects_magnitude_missing.
    Example:     [
                   {"metric": "profit_factor",
                    "direction": "increase",
                    "magnitude_min": 0.05,
                    "magnitude_max": 0.20,
                    "rationale": "HTF gate should add 5-20% to PF based on
                                  prior regime-overlay literature."}
                 ]
```

```
- mechanism_lineage   (PROPOSED — not in schema)
    Type:        list[str]
    Format:      list of ancestral thesis_ids (most recent ancestor first)
    Source set:  Free; ids must be in the snapshot's thesis_ids set
                 (Spec A §6.1 binding)
    Token cap:   ≤5 entries
    Required:    Proposed addition — not in schema yet.
    Goal served: G2 (force creativity — distinguish iteration from greenfield).
    Meaning:     Explicit dependency chain back to ancestral theses.
                 Distinct from `closest_prior_theses_considered` (which is
                 SIMILARITY) — this is ANCESTRY (direct iteration).
                 If this is a true greenfield thesis with no predecessor,
                 leave empty.
    Producer guidance: List ONLY the predecessor theses this thesis directly
                       evolves from. "v3 of an htf_gate lineage" → list v2
                       and v1. Don't list theses that just happen to be in
                       the same dimension.
    Validator rule (proposed):
                 With ≥3 ancestors in the same `causal_cluster` and same
                 `mechanism_dimension`, require either (a) a different
                 `mechanism_dimension`, or (b) a `disqualifiers` entry with
                 kind="mechanism_evidence" that distinguishes this thesis
                 from the lineage's prior failures. Rejection code:
                 thesis_quality_lineage_no_structural_pivot.
    Example:     ["job-12-round-3-attempt-1", "job-12-round-1-attempt-2"]
                 # this thesis is v3, building on round-3 (v2) which built on round-1 (v1)
```

> **`competing_hypothesis` removed from proposals per A4a §3 Set 3.** The "deepest near-equivalent alternative + tiebreaker" intent is absorbed by `alternatives_considered`'s strengthened contract: entry [0] must be the deepest near-equivalent, with `why_rejected` containing a grounded tiebreaker reference (specific `evidence_citations` source, `disqualifiers` name, or `mechanism_dimension` from §5.8 landscape). See A4a §5 contract-strengthening table.

```
- if_this_fails_next_thesis   (PROPOSED — not in schema)
    Type:        str
    Format:      1-3 sentences
    Source set:  Free
    Token cap:   ≤300 chars
    Required:    Proposed addition — not in schema yet.
    Goal served: G2 (force creativity — pre-commit to next move, prevent
                 random walks after a kill).
    Meaning:     Explicit pre-commitment to the next thesis if THIS one is
                 killed. Forces forward planning; surfaces the conductor's
                 implicit "what next" thinking.
    Producer guidance: State the CONCRETE next thesis you would propose if
                       this round's backtest kills the current thesis. Vague
                       answers ("retry with different parameters") indicate
                       shallow forward planning. Best when the next thesis
                       references one of your own `alternatives_considered`
                       entries — proves the next move is pre-vetted.
    Validator rule (proposed):
                 Non-empty + must reference either (a) a specific
                 `mechanism_dimension` (different from current), OR (b) the
                 mechanism text of one of the `alternatives_considered`
                 entries. Rejection code:
                 thesis_quality_next_thesis_not_pre_committed.
    Example:     "If this kills, next round tests an ATR-based dynamic stop
                  in signal_quality dimension (alternatives_considered[0]).
                  Drops the regime-overlay theme; switches to volatility-
                  responsive stops."
```

```
- confidence_distribution   (PROPOSED — not in schema; would supersede `evidence_strength`)
    Type:        object — see Inner shape
    Inner shape: ConfidenceDistribution = {
                     data:        Literal["", "direct", "proxy", "mixed",
                                          "speculative"],
                     literature:  Literal["", "direct", "proxy", "mixed",
                                          "speculative"],
                     precedent:   Literal["", "direct", "proxy", "mixed",
                                          "speculative"],
                 }
    Source set:  One-of per dimension (above)
    Token cap:   3 enum labels
    Required:    Proposed addition — not in schema yet.
    Goal served: G3 (validation gates with teeth — replace
                 motivated-reasoning-prone single rating with structured
                 per-dimension ratings that the validator can gate against).
    Meaning:     Per-dimension confidence rating:
                   - data:       analyst-grade evidence in this thesis's
                                 strategy diagnostics
                   - literature: external sources via web_search
                   - precedent:  prior accepted theses in this family
                                 or related families
                 Replaces the single-rating `evidence_strength`, which is
                 noise-dominated by motivated reasoning (LLM optimistically
                 self-rates "direct"). Per-dimension rating exposes the
                 weakest link.
    Producer guidance: Rate each dimension separately and honestly. A
                       thesis with data="direct" + literature="speculative"
                       + precedent="proxy" is honest — your strongest
                       evidence is the data, weakest is literature.
                       Avoid all-three="direct" — that signals motivated
                       reasoning, not strong evidence.
    Validator rule (proposed):
                 At least one of {data, literature} must be "direct" or
                 "mixed" for the thesis to be accepted. Theses with all
                 three "" or "speculative" require an explicit disqualifier
                 with kind="mechanism_evidence" acknowledging the weak-
                 evidence basis. Rejection codes:
                 thesis_quality_confidence_distribution_too_weak
                 thesis_quality_confidence_distribution_missing.
    Example:     {"data": "direct",
                  "literature": "speculative",
                  "precedent": "proxy"}
```

#### 4.12 summary (5 proposed additions after A4a dropped `competing_hypothesis`)

| Field | Type | Goal | Replaces / extends |
|---|---|---|---|
| `expected_runtime_signal` | list[object] | G1 | new (complements `expected_effects`) |
| `expected_effects.magnitude_min/max` | inner-shape add | G1 | extends existing `expected_effects` |
| `mechanism_lineage` | list[str] | G2 | new (ancestry — distinct from the dropped `closest_prior_theses_considered`) |
| `if_this_fails_next_thesis` | str | G2 | new (no existing analogue) |
| `confidence_distribution` | object | G3 | supersedes the dropped `evidence_strength` (single rating) |

**Total post-consolidation, post-additions**: **23 surviving current fields + 4 new top-level + 1 inner-shape extension = 27 top-level fields + 3-field extension to ExpectedEffect**. (Started at 35 current + 6 proposed = 41; A4a drops 12 current + 1 proposed = 13 drops; result is 28 minus one inner-shape extension counted separately = 27 top-level.)

The §7 renderer absorbs schema additions automatically when added to `research_types.py`; A4 needs no rewrite at that point.

---

## 5. Worked example — a complete passing thesis

After the field-by-field entries, the OUTPUT section concludes with **one full worked example** of a thesis that would pass validation. The example demonstrates every required-by-default field, with realistic values. This is the LLM's pattern to clone.

```json
{
  "hypothesis": "Adding a 1-hour direction gate filters out counter-trend 5-min pullbacks that historically drove the strategy's drawdown floor.",
  "mechanism": "HTF direction acts as a regime overlay — when the 1h trend is up, only long 5-min pullbacks fire; when down, only shorts. Reduces signal count by ~40% but the surviving signals have better edge.",
  "mechanism_dimension": "regime_conditioning",
  "dimension_novelty": "Moves from signal_quality threshold tweaking (where prior trend_filter_v2 lived) to regime_conditioning by overlaying a 1h direction gate.",
  "thesis_role": "orthogonal_discovery",
  "theme_keywords": ["htf_gate", "trend_overlay"],

  "config_changes": {
    "use_htf_direction_gate": true,
    "htf_timeframe_minutes": 60
  },
  "requires_code_change": false,
  "requested_primitives": [],

  "expected_effects": [
    {"metric": "profit_factor", "direction": "increase",
     "rationale": "HTF gate filters counter-trend chop, raising win rate."},
    {"metric": "trade_count", "direction": "decrease_or_same",
     "rationale": "Filtering should reduce trades but not collapse frequency."}
  ],
  "required_diagnostics": [],

  "disqualifiers": [
    {"name": "trade_count_collapse",
     "condition": "trade_count decreases by more than 50%",
     "severity": "hard_fail", "kind": "metric_threshold"},
    {"name": "no_regime_separation",
     "condition": "PF in up-regime not >1.2× PF in down-regime",
     "severity": "hard_fail", "kind": "mechanism_evidence"}
  ],
  "falsification_or_alternative": "If wick-only stop-out rate is unchanged between gated and ungated regimes, the HTF gate is not the mechanism — improvement may be from baseline drift.",

  "evidence_citations": [
    {"source": "web_search", "citation": "Cont et al. on order-flow regime persistence"},
    {"source": "analyst", "citation": "round-3 analyst found 62% of stops occur in counter-HTF-trend setups"}
  ],

  "prior_lever_outcomes": [],
  "alternatives_considered": [
    {"mechanism": "ADX>30 entry filter",
     "why_rejected": "Too strict in low-vol regimes per fixture analysis."},
    {"mechanism": "session-time entry filter",
     "why_rejected": "Proxy for the regime problem rather than the structural fix."}
  ],

  "source_code_verification": "strategies/ema/signals.py:apply_htf_gate — gate evaluated before stop_distance check; placing here ensures filter sees raw signal."
}
```

## 6. DOCTRINE ↔ OUTPUT cross-references

Each DOCTRINE principle that implies a field gets an inline `→ see <field>` reference. Each OUTPUT entry's `Producer guidance` line references the relevant DOCTRINE principle by name. Example:

DOCTRINE line 91-92:
```
- Evidence: cite at least one external source (web_search) AND one trade-level
  finding (analyst). External-only is theory; analyst-only is data dredging.
  → see field `evidence_citations` in OUTPUT (typed; ≥1 web + ≥1 analyst required).
```

OUTPUT entry for `evidence_citations`:
```
Producer guidance: ≥1 web_search entry... (DOCTRINE: Evidence)
```

The bidirectional link ensures the LLM can navigate from norm to typed contract and back without inferring.

## 7. Programmatic regeneration

The OUTPUT section is **machine-generated from `ResearchThesis`** by a new script `scripts/render_output_schema.py`. The script:

- Introspects `ResearchThesis.model_fields`.
- For each field, reads:
  - the Pydantic type annotation,
  - the field's default value,
  - the field's docstring (extracted from `# inline comments` in research_types.py, or a new structured docstring per field per a separate refactor),
  - the validator rules that reference the field (parsed from a `_FIELD_VALIDATION_RULES` dict that mirrors `thesis_validator.py`).
- Renders each field per §3's template.
- Emits a markdown block that gets included into `_build_conductor_system_prompt` as a single string interpolation.

**Rationale:** today's OUTPUT section is a hand-maintained string in `research_prompts.py`. When a field is added/removed/renamed in the schema, the prompt drifts. Machine generation makes drift impossible by construction.

**Implementation:** the rendered OUTPUT block is checked into git as `prompts/conductor_output_section.md`. CI re-runs the regenerator and fails if the checked-in file is stale. `_build_conductor_system_prompt` reads the file and interpolates into the larger prompt template.

## 8. Drift detection

Three checks added to CI:

1. **Schema-prompt parity** — `scripts/check_prompt_drift.py` (already exists, extended): every field in `ResearchThesis.model_fields` must appear in `prompts/conductor_output_section.md` UNLESS explicitly marked in a `_PROMPT_OMITTED_FIELDS` set (the Drop / system-set / legacy fields per Spec A3 §5.0.6). Fails the build on mismatch.

2. **Validator-prompt parity** — every rejection code emitted by `thesis_validator.py` (extracted by parsing `rejection_code=...` literals) must be referenced in at least one field's `Validator rule:` line in the OUTPUT section, OR explicitly listed in a `_PROMPT_OMITTED_RULES` set (rules that the LLM cannot pre-empt — e.g. dedup rules). Fails the build on missing rejection codes in the prompt.

3. **Schema-version stamp** — `_build_conductor_system_prompt` includes a `# Output schema version: <hash>` line at the top of the OUTPUT section. Hash is computed from `ResearchThesis.model_fields` + validator rule list at startup. If the version stamp changes, conductor reflexions get a note: "the output schema has changed; verify your output matches the new shape."

## 9. Migration plan — single PR, single commit per layer

Hard cutover, A1/A2/A3 style. All commits land together.

1. **`scripts/render_output_schema.py`**: new file. Implements the regeneration logic per §7. Unit-tested independently against fixture schemas.
2. **`prompts/conductor_output_section.md`**: new file. Generated output, checked in for git diff visibility. CI re-runs the regenerator and fails on stale check-in.
3. **`research_prompts.py`**: `_build_conductor_system_prompt` rewritten to read `prompts/conductor_output_section.md` and interpolate. DOCTRINE updated with cross-references per §6. The old OUTPUT prose (lines 117–154) is **deleted**.
4. **`scripts/check_prompt_drift.py`**: extended per §8. New checks added; CI gate added.
5. **Tests**: `tests/test_conductor_prompt_v3.py` updated to assert:
   - Every `ResearchThesis.model_fields` key appears in the rendered system prompt OR in `_PROMPT_OMITTED_FIELDS`.
   - Every validator rejection code appears in the rendered system prompt OR in `_PROMPT_OMITTED_RULES`.
   - The worked-example JSON in the rendered prompt passes `validate_thesis_dict(...)` end-to-end.
   - Schema-version-stamp line is present.
6. **`research_types.py`**: add a structured docstring per field if not present (Pydantic `Field(..., description="...")` is the canonical place). Existing fields without descriptions get them filled in to drive the per-field `Meaning:` line in the renderer.
7. **Final grep gate** (PR not mergeable until all pass):
   - `grep -n 'OUTPUT$' research_prompts.py` returns a single match (the interpolated section, not hand-written prose).
   - `pytest tests/test_conductor_prompt_v3.py` passes including the new parity assertions.
   - `python scripts/check_prompt_drift.py` returns exit 0.
8. **Documentation**: this spec marked Shipped; Spec A's §5.0.6 cross-references the rendered OUTPUT file as the operational source.

## 10. Risk and rollback

**Risks:**

- **Renderer bugs ship a malformed prompt.** Mitigation: §8 drift detection catches schema-prompt mismatches; the rendered prompt is checked in as a file so git diff makes the change visible. Plus the new test in §9 step 5 runs `validate_thesis_dict` on the worked example — if the example doesn't pass, the prompt isn't shipped.
- **`Field(..., description="...")` adds churn to every existing field in research_types.py.** Mitigation: do it in the same PR; one file touched, ~30 description strings added. Reviewable.
- **DOCTRINE cross-references add maintenance load.** Mitigation: cross-references are one-line additions next to existing principles; they get updated when either side moves. The drift script can be extended later to enforce bidirectional links if drift recurs.
- **The renderer is a new code surface that can break independently of the schema.** Mitigation: unit-tested against a fixture schema in isolation; rendering bug ≠ schema bug.

**Rollback:** revert the PR. `_build_conductor_system_prompt` returns to the hand-written prose. Lose the drift detection and worked example until the rewrite is re-landed.

## 11. Success criteria

**Coverage:**

- Every key in `ResearchThesis.model_fields` (all 35 fields today; ~30 post-A3 if it ships first) appears in the rendered OUTPUT section OR in `_PROMPT_OMITTED_FIELDS` (explicit allowlist). The OUTPUT section in §4 documents all 35 today.
- Every typed-object field (`ExpectedEffect`, `Disqualifier`, `Alternative`, `EvidenceCitation`, `PriorLeverOutcome`, `DiagnosticRequirementSpec`) has its `Inner shape:` slot populated with the full `{key: type, ...}` rendering.
- Every conditional-requirement contract (emergent-dimension trio, novel_connection on high overlap, requested_primitives on requires_code_change=true) is named in the relevant fields' `Required:` lines.

**Worked example:**

- The OUTPUT section's worked-example JSON passes `validate_thesis_dict(...)` end-to-end against a fixture round context. Verified by an assertion in `tests/test_conductor_prompt_v3.py`.

**Drift detection:**

- `scripts/check_prompt_drift.py` extended with schema-prompt parity + validator-prompt parity checks. CI gates on both.
- Schema-version-stamp present in the rendered prompt.

**Cross-references:**

- Every DOCTRINE principle that implies a field has a `→ see <field>` cross-reference.
- Every OUTPUT entry's `Producer guidance` has a `(DOCTRINE: <principle>)` back-reference where applicable.

**Production behavioral signal (to measure post-deploy):**

- Conductor's per-attempt acceptance rate (theses that pass Stage 1 validation on first emit) is the metric. Pre-deploy baseline is 0 of 3 on VPS (100% rejection). Post-deploy target: ≥50% acceptance on the first 10 attempts.

## 12. Out of scope

- Schema-field additions or removals (Spec A3 owns).
- Validator rule additions or removals (Spec A3 §4.7-style upgrades may run in A4-adjacent PRs but are not A4's deliverable).
- DOCTRINE rewrites beyond adding cross-references.
- Auto-regenerating DOCTRINE from `_PRINCIPLES` constants (possible follow-up; not in A4).
- Multi-model prompt A/B testing.
- Changing the JSON envelope key `suggested_theses` (today the LLM emits `{"suggested_theses": [thesis]}`).
