# Spec A4 — Conductor OUTPUT-Schema Instruction Overhaul

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** `research_prompts.py:117–154` (current OUTPUT section being replaced); audit transcript 2026-05-28.
**Depends on:** none — A4 documents the schema as-is plus targeted shape changes; ships in one PR.
**Blocks:** none
**Parallel with:** Spec A1 (terminology), Spec A2 (id provenance), Spec A3 (schema cleanup), Spec B (diagnostic specs).

---

## 1. Goal

Rewrite the conductor's OUTPUT instruction section so the LLM produces
well-formed, validator-compliant `ResearchThesis` JSON on the first attempt,
every time. Eliminate the seven systemic problems identified in the
2026-05-28 audit:

1. **Schema-prompt drift** — OUTPUT documents ~17 of ~30 fields; ~9 validator-required fields are unnamed.
2. **Shape ambiguity** — typed object fields are named but their inner shape is never shown.
3. **DOCTRINE ↔ OUTPUT disconnect** — soft principles imply concrete typed-field contracts but never name the fields or shapes.
4. **Inconsistent field-instruction quality** — some entries are one-liners, some paragraphs; no template.
5. **Conditional requirements scattered** — emergent-dimension contract, novel-connection conditional, code-change conditional, etc.
6. **No worked example** — prompt asks for "ONE JSON object matching the thesis schema" without showing one.
7. **No drift detection** — schema, validator, and prompt drift independently.

**Production evidence:** every conductor attempt on the VPS DBs (3 attempts
across 2 jobs, 2026-05-09 to 2026-05-27) was rejected pre-flight; one
rejection was `"validation_failed: expected exactly one thesis, got 0"` —
consistent with the LLM not knowing the envelope shape it must produce.

## 2. Non-goals

- **Adding fields beyond what §4 lists.** §4.12's proposed additions are scoped here; nothing else.
- **DOCTRINE rewrite.** DOCTRINE stays as soft principles. A4 adds cross-references; it doesn't rewrite the prose.
- **Tool-list rewrite.** Spec A §5.10 handles this.
- **Multi-LLM testing harness.** A4 is the prompt redesign; measuring its effect on conductor acceptance rate is downstream work.

## 2.1 No backward compatibility — hard cutover

The OUTPUT section is replaced wholesale in one PR. Old prose deleted, new
template applied. No deprecation, no A/B rendering. Same policy as Spec
A1/A2/A3.

## 3. Per-field entry template

Every field in the spec's §4 follows this fixed template.

```
- <field_name>
    Type:        <Python type / typed object name>
    Format:      <free-form prose | short label | enum value | typed list | typed object>
    Source set:  <Free | One-of: [list] | Constrained by <ROUND CONTEXT key>>
    Token cap:   <~N tokens | unbounded | ≥M chars>
    Required:    <Always | Conditional on <X> | Optional>
    Meaning:     <one sentence — what this field captures>
    Producer guidance: <one or two sentences — how the LLM should think about producing it>
    Example:     <concrete value the LLM can pattern-match against>
```

Typed-object fields get an additional `Inner shape:` slot rendering the typed
class as `{key: type, ...}` so the LLM doesn't guess at nesting.

### 3.0.1 The `Validator rule:` slot does NOT render into the LLM prompt

The `Validator rule:` slot exists in this spec for reviewers and implementers.
It is the source of truth for what `thesis_validator.py` enforces and what
rejection codes it emits. The §7 renderer **omits** it from
`prompts/conductor_output_section.md`. Reasoning:

- Token budget — rule lines don't change emit behavior in the desired direction.
- Goodharting — showing the LLM "must mention ≥2 distinct dimension names"
  produces theses that count dimension names rather than write real contrast.
  Showing it "must resolve by exact match against a citation_N id" produces
  theses that pick any id rather than the right one. Producer guidance
  internalizes the rule's intent in LLM-actionable terms; the rule line
  invites letter-of-the-law compliance.
- Single source of truth — `prompts/conductor_output_rules.json` (§6.2) is
  the rule surface the validator and tests import. The rendered prompt is
  not a second copy.

Rejection codes reach the LLM only when it has actually tripped one. A
separate `## RECENT REJECTIONS` block above OUTPUT renders the last N codes
the conductor saw for this thesis-attempt sequence (sourced from the
existing `list_rejections` tool). The LLM sees codes that already happened,
not the full rule catalogue.

## 3.1 Category ordering

The §7 renderer emits §4 categories in this order. Fields referenced by other
fields' typed contents render before fields referencing them, so the LLM
emits targets before references:

1. Core description (§4.2)
2. Positioning + classification (§4.3)
3. Novelty justification (§4.4)
4. Evidence (§4.7)
5. Predictions + falsification (§4.8)
6. Alternatives + prior-work (§4.5)
7. Emergent-dimension contract (§4.6)
8. Config + engine (§4.9)
9. Diagnostics + code grounding (§4.10)
10. Optional escape hatch (§4.11)
11. Proposed additions (§4.12)

§4.1 Identity is omitted from LLM-facing OUTPUT and documented in a separate
`## SYSTEM-INJECTED FIELDS (do not emit)` appendix above OUTPUT.

`scripts/check_prompt_drift.py` asserts referenced-field categories render
before referencing-field categories.

## 3.2 ROUND CONTEXT block

The system prompt renders a `## ROUND CONTEXT` block immediately above
OUTPUT. Conditional `Required:` lines reference its keys literally.

```
## ROUND CONTEXT (computed by conductor before LLM call)

Treat values below as ground truth. Reference them literally in conditional
fields; do not paraphrase entries or invent counts not shown here.

family_cluster_density: high | medium | low | none
  (high = the family has >=3 prior theses sharing >=2 theme_keywords each
   in the last 7 rounds; signals you must work harder on novelty)

dimensions_already_explored: (capped at 12; tail summarized)
  - signal_quality (4 attempts; 1 kept)
  - regime_conditioning (1 attempt; killed)
  (and N more)

dimensions_unexplored: (capped at 12)
  - portfolio_construction
  - alpha_decay

emergent_dimensions_in_use: (capped at 8)
  - session_microstructure (introduced job-9-round-2)

theme_keywords_in_use: (top 12 by attempt count; tail summarized)
  - stop_distance (5)
  - htf_gate (2)
  (and N more)

prior_lever_history: (top 12 by recency; structured for overlap detection)
  - config_keys: [ema_length]
    direction: tighten          # derived by validator from key prefix
                                # convention + value-change sign (see
                                # `_direction_from_value_change` in
                                # thesis_validator.py:576)
    prior_thesis_id: job-11-round-2-attempt-1
    outcome: killed
  - config_keys: [rr_ratio]
    direction: widen
    prior_thesis_id: job-9-round-4-attempt-2
    outcome: kept
  (and N more)
  # Overlap check: if any key in your config_changes appears in a
  # `config_keys` entry above AND your direction (derived from your
  # value vs family-baseline) differs from the prior's `direction`,
  # populate `prior_lever_outcomes` citing that `prior_thesis_id`.
  # No "lever concept" mapping is needed — the config key IS the
  # canonical identifier; `direction` is derived deterministically
  # from key naming convention (min_/max_/floor_/ceiling_/cap_) plus
  # value-change sign, falling back to text tokens.

strategy_config_keys: (the family's `allowed_config_keys` frozenset
                      — for EMA, source: strategies/ema/research.py:63)
  - ema_length (int)
  - timeframe_long (str)
  - timeframe_short (str)
  - rr_ratio (float)
  - direction_bias (str)
  - entry_cutoff_time (str)
  - max_trades_per_day (int)
  - gap_filter (bool)
  - gap_pct (float)
  - use_range_shift (bool)
  - range_shift_lookback (int)
  # Keys reachable only via the compiler's primitive-injection layer
  # (e.g. min_stop_distance_pct, trail_after_r) are NOT in this set —
  # use requires_code_change=true + requested_primitives for those.

prior_theses_snapshot: (top 20 by recency; for mechanism_lineage references)
  - thesis_id: job-12-round-3-attempt-1
    mechanism_dimension: regime_conditioning
    outcome: killed
  - thesis_id: job-12-round-1-attempt-2
    mechanism_dimension: regime_conditioning
    outcome: killed
  (and N more)

diagnostic_event_paths: (paths that resolve in the prior round's diagnostics JSON)
  - rejection_breakdown.trend_filter_rejected
  - rejection_breakdown.stop_hit_rejected
  - event_counts.signals_generated
  - event_counts.entries_taken
  (and N more)
  # Use only paths from this list in `expected_runtime_signal.event_path`.

theme_keywords_overlap_signal:
  # Self-check: emit your theme_keywords; if ANY token matches an entry in
  # `theme_keywords_in_use` above, `novel_connection` is REQUIRED at emit.
  # The validator runs this same check post-emit using the rendered list.

citation_id_convention:
  # The validator assigns positional ids citation_1, citation_2, ... to
  # entries of `evidence_citations` by array position. Reference these in
  # `deepest_alternative.tiebreaker.value` (and other tiebreakers). No
  # ROUND CONTEXT key carries them — they are determined by your own
  # emission order.
```

Size caps are hard. The renderer sorts each list by attempt count (or
recency, for `emergent_dimensions_in_use`) and emits a `(and N more)` tail
line so the LLM knows the view is truncated.

## 3.2.1 RECENT REJECTIONS block

The conductor prompt also renders a `## RECENT REJECTIONS` block above OUTPUT
when prior attempts in this thesis-attempt sequence were rejected. Source:
the existing `list_rejections` MCP tool. Format:

```
## RECENT REJECTIONS (last 3 attempts in this round)

attempt 1: structural_other_alternatives_too_few
attempt 2: structural_deepest_alternative_tiebreaker_unresolved
attempt 3: thesis_quality_dimension_novelty_not_grounded
```

This is the ONLY place rejection codes appear in the LLM-facing prompt. The
LLM sees codes it has already tripped — not the full rule catalogue. Codes
the LLM has not tripped do not get surfaced; the spec's rule catalogue lives
in `prompts/conductor_output_rules.json` for the validator's use, not the
LLM's.

When the block is empty (first attempt of a round), the renderer emits no
block at all — silence beats noise.

## 3.3 Tiebreaker id strategy

`evidence_citations` is rendered without an LLM-emitted `id` field. The
validator assigns positional ids `citation_{i+1}` to each entry by array
position. `deepest_alternative.tiebreaker.value` references these ids.
No LLM bookkeeping.

---

## 4. Field-by-field OUTPUT entries

**Field accounting** (post-A4 schema, verified against
`ResearchThesis.model_fields` enumeration on 2026-05-28):

- 35 pre-A4 schema fields
- − 13 deletions (12 A4a-consolidation drops + `alternatives_considered` replaced; see §9.1)
- + 2 replacements (`deepest_alternative`, `other_alternatives`)
- + 4 proposed additions (§4.12)
- + 1 inner-shape extension to `ExpectedEffect` (§4.8)
- = **28 fields in the post-A4 schema**

Of those 28:
- 25 render in §4 as LLM-facing OUTPUT entries.
- 3 omitted into `_PROMPT_OMITTED_FIELDS`: `thesis_id` (§4.1), `strategy_family` (§4.1), `required_diagnostic_specs` (§4.10).
- §4.11 `validator_challenge` is a meta-field outside the `ResearchThesis` schema.

### 4.1 Identity — system-injected, omitted from OUTPUT

`thesis_id` and `strategy_family` are documented in a `## SYSTEM-INJECTED
FIELDS (do not emit)` appendix above OUTPUT. The system assigns both.

- `thesis_id`: assigned by the validator as `f"{research_round_id}-attempt-{N}"`.
- `strategy_family`: assigned by `_prepare_thesis_for_validation` at `autoresearch_research.py:100` based on the active job's family.

### 4.2 Core description

```
- hypothesis
    Type:        str
    Format:      one sentence (no list, no bullets, no quotes)
    Source set:  Free
    Token cap:   ≤40 words / ≤300 chars
    Required:    Always
    Meaning:     The thesis's core claim — what should happen and under what conditions.
    Producer guidance: State the mechanism, not the parameter. "Tighter stops
                       reduce wick-stops in high-vol regimes" is a mechanism;
                       "Set min_stop_distance_pct=0.0035" is a parameter tweak.
    Example:     "Adding a 1-hour direction gate filters out counter-trend
                  5-min pullbacks that drove the strategy's drawdown floor."
```

```
- mechanism
    Type:        str
    Format:      1-3 sentences explaining the causal story
    Source set:  Free
    Token cap:   ≤150 words / ≤1000 chars
    Required:    Always
    Meaning:     Why the hypothesis should hold — the market-mechanics
                 explanation, in trader/market terms.
    Producer guidance: Describe order flow, regime behavior, or microstructure
                       that explains WHY the hypothesis should be true. Avoid
                       restating the hypothesis in different words; avoid
                       academic prose and math-speak.
    Example:     "HTF direction acts as a regime overlay — when 1h trend is up,
                  only long 5-min pullbacks fire. Reduces signal count by ~40%
                  but surviving signals have better edge because they align
                  with the dominant regime."
```

### 4.3 Positioning + classification

```
- mechanism_dimension
    Type:        Literal[*MECHANISM_DIMENSIONS]
    Format:      enum value
    Source set:  One-of: entry_timing | exit_mechanism | signal_quality |
                 regime_conditioning | portfolio_construction | risk_structure |
                 market_microstructure | execution_costs | universe_selection |
                 alternative_data | alpha_decay | emergent |
                 <a value from emergent_dimensions_in_use in ROUND CONTEXT>
    Token cap:   single label
    Required:    Always
    Meaning:     Which mechanism family this thesis belongs to.
    Producer guidance: Pick the category that BEST fits the lever you're
                       changing, not the category you wish to explore. Prefer
                       an existing core dimension or an entry from
                       `emergent_dimensions_in_use` over inventing a new
                       emergent. Only set "emergent" when you can pass §4.6's
                       three conditional checks; the LLM's incentive is to
                       look novel, but the spec asks for accuracy.
    Example:     "regime_conditioning"
```

```
- theme_keywords
    Type:        list[str]
    Format:      list of 2-3 short noun phrases (snake_case lowercase)
    Source set:  Free; reuse from ROUND CONTEXT `theme_keywords_in_use` when applicable
    Token cap:   2-3 entries, each ≤20 chars
    Required:    Always (≥1 entry; ≥2 strongly recommended)
    Meaning:     Lever-theme tokens identifying the specific knob this thesis
                 touches. Used by cluster-fixation and direction-whipsaw rules.
    Producer guidance: Reuse a token from ROUND CONTEXT `theme_keywords_in_use`
                       if your thesis touches the same lever. Only invent a new
                       token for a genuinely new lever. Use the two slots for
                       DIFFERENT facets — the lever you're touching AND the
                       regime/timeframe/context it applies in. Two near-
                       synonyms waste a slot and weaken the cluster signal.
    Example:     ["htf_gate", "high_vol_regime"]
```

```
- thesis_role
    Type:        Literal["orthogonal_discovery",
                          "implementation_unlock",
                          "cleanup_validation_follow_up"]
    Format:      single enum label
    Source set:  One-of (above)
    Token cap:   single label
    Required:    Always — empty value rejected.
    Meaning:     Categorical commitment about what kind of work this thesis represents.
                   - orthogonal_discovery: tests a lever family that has not
                     been previously explored in this family's history.
                   - implementation_unlock: paves the way for future code-change theses.
                   - cleanup_validation_follow_up: ties up loose ends from a
                     prior round's contested finding.
    Producer guidance: Pick the role that fits BEFORE you finalize the thesis;
                       if you can't pick, your hypothesis is probably unfocused.
    Example:     "orthogonal_discovery"
```

### 4.4 Novelty justification

```
- dimension_novelty
    Type:        str
    Format:      1-2 sentences
    Source set:  Free, but MUST mention ≥1 specific mechanism_dimension name
    Token cap:   ≥30 chars (hard), ≤80 words (soft)
    Required:    Always
    Meaning:     Why your chosen mechanism_dimension is structurally novel
                 (not a parameter tweak of prior work in the same dimension).
    Producer guidance: Name the PRIOR dimension you're moving away from, not
                       (or not only) the dimension you chose. The validator
                       counts any dimension mention; the prompt asks for the
                       contrast. "This moves from signal_quality (where prior
                       trend_filter_v2 lived) to regime_conditioning — a
                       different lever family."
    Example:     "Moves from signal_quality threshold tweaking to
                  regime_conditioning by overlaying a 1h direction gate."
```

```
- novel_connection
    Type:        str
    Format:      1-2 sentences
    Source set:  Free, but MUST mention a specific shared theme_keyword or
                 a structurally-distinct mechanism_dimension
    Token cap:   ≥120 chars (hard, when required)
    Required:    Pre-emit warn: ROUND CONTEXT `family_cluster_density == "high"`
                 signals you must work harder. Hard validator gate fires
                 post-emit: required when ≥1 of your emitted `theme_keywords`
                 appears in ROUND CONTEXT `theme_keywords_in_use`. The validator
                 runs exactly this check using the same list rendered to you —
                 deterministic, not a fuzzy "last 7 rounds" inference.
    Meaning:     Why this thesis is materially new despite keyword overlap
                 with priors (not just another variation of the dominant cluster).
    Producer guidance: Reference the shared keyword by name and explain the
                       structural difference. Length without grounded mention
                       is rejected. Belt-and-suspenders: populate this field
                       whenever ROUND CONTEXT `family_cluster_density == "high"`
                       OR you reuse any keyword from `theme_keywords_in_use`.
                       Over-populating is cheap; mispredicting the post-emit
                       gate is a rejection.
    Example:     "Recasts stop_distance as a regime-detection signal rather
                  than an absolute threshold — distinct from prior
                  stop_distance theses that all tested fixed thresholds."
```

```
- underexplored_dimensions_considered
    Type:        list[str]
    Format:      list of mechanism_dimension names
    Source set:  One-of: entries from ROUND CONTEXT `dimensions_unexplored`
    Token cap:   ≥1 entry when ROUND CONTEXT lists any; ≤4 entries
    Required:    REQUIRED when ROUND CONTEXT `dimensions_unexplored` is non-empty.
    Meaning:     The dimensions you considered as alternatives before picking
                 the one you chose.
    Producer guidance: Pick from ROUND CONTEXT `dimensions_unexplored`. Do NOT
                       include the dimension you chose. A soft warn fires if
                       the chosen dimension has more attempts than every
                       dimension you list here.
    Example:     ["portfolio_construction", "alpha_decay"]
                 # NB: must NOT include the dimension you chose; pick from
                 # ROUND CONTEXT `dimensions_unexplored` only.
```

### 4.5 Alternatives + prior-work

```
- deepest_alternative
    Type:        DeepestAlternative
    Format:      typed object — see Inner shape
    Inner shape: DeepestAlternative = {
                     mechanism:    str,
                     why_rejected: str (≥40 chars),
                     tiebreaker:   TiebreakerRef = {
                         kind:  Literal["evidence_citation",
                                        "disqualifier",
                                        "mechanism_dimension"],
                         value: str
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
                       tiebreaker must reference a target you committed to in
                       this same thesis: a citation by positional id
                       (`citation_1`, `citation_2`, ... corresponding to the
                       order you emit `evidence_citations` — see ROUND CONTEXT
                       `citation_id_convention`), OR a `disqualifiers[i].name`,
                       OR a `mechanism_dimension` from the enum. The why_rejected
                       prose can paraphrase; the tiebreaker must resolve by
                       exact match.
    Example:     {"mechanism": "ADX>30 entry filter",
                  "why_rejected": "Too strict in low-vol regimes per round-3
                                   analyst evidence (citation_2) — would
                                   suppress signals where the HTF gate still
                                   admits them.",
                  "tiebreaker": {"kind": "evidence_citation",
                                 "value": "citation_2"}}
    Alternate examples (rotate the tiebreaker kind):
                 // kind="disqualifier" — anchors on this thesis's own
                 // disqualifier list. Rejection on the alternative would
                 // come from the same gate the current thesis pre-commits to.
                 {"mechanism": "wider stop-distance cap",
                  "why_rejected": "Would trip our own no_regime_separation
                                   disqualifier — alternative leaves regime
                                   conditioning unaddressed.",
                  "tiebreaker": {"kind": "disqualifier",
                                 "value": "no_regime_separation"}}

                 // kind="mechanism_dimension" — the alternative sits in a
                 // dimension the family has already explored and killed.
                 {"mechanism": "ADX threshold tuning",
                  "why_rejected": "Lives in signal_quality dimension; family
                                   has 4 prior signal_quality attempts with
                                   1 kept — diminishing returns.",
                  "tiebreaker": {"kind": "mechanism_dimension",
                                 "value": "signal_quality"}}
```

```
- other_alternatives
    Type:        list[Alternative]
    Format:      typed list — see Inner shape
    Inner shape: Alternative = {
                     mechanism:         str,
                     why_rejected:      str (≥40 chars),
                     lighter_tiebreaker: TiebreakerRef | None
                 }
    Source set:  Free
    Token cap:   ≥1 entry; ≤4 entries
    Required:    Always (≥1 entry)
    Meaning:     Other rejected alternatives. lighter_tiebreaker is optional;
                 populating it signals deeper vetting.
    Producer guidance: Each entry is a DIFFERENT mechanism, not a parameter
                       variant. why_rejected must be substantively distinct
                       from deepest_alternative.why_rejected.
    Example:     [{"mechanism": "session-time entry filter",
                   "why_rejected": "Proxy for the regime problem rather than
                                    the structural fix; cannot distinguish
                                    high-vol from low-vol opens within the
                                    same session.",
                   "lighter_tiebreaker": null},
                  {"mechanism": "VWAP-distance entry filter",
                   "why_rejected": "Family already tested VWAP-distance under
                                    signal_quality dimension; that lineage was
                                    killed — see citation_2 analyst evidence.",
                   "lighter_tiebreaker": {"kind": "mechanism_dimension",
                                          "value": "signal_quality"}}]
                 # Populating lighter_tiebreaker is optional but signals
                 # deeper vetting — reviewers grade it favorably.
```

```
- prior_lever_outcomes
    Type:        list[PriorLeverOutcome]
    Format:      typed list — see Inner shape
    Inner shape: PriorLeverOutcome = {
                     prior_thesis_id: str,          # must exist in ROUND CONTEXT snapshot
                     lever:           str,          # the shared knob
                     direction_then:  str,          # past-tense verb; see hints below
                     outcome:         Literal[*PRIOR_LEVER_OUTCOMES],
                                                    # kept | killed | inconclusive
                     why_retry:       str (≥40 chars)
                 }
                 PRIOR_LEVER_DIRECTION_HINTS (guidance, not gate):
                   tightened, loosened, extended, shortened,
                   filtered_in, filtered_out, added, removed
    Source set:  Free; prior_thesis_id constrained by snapshot
    Token cap:   ≤4 entries
    Required:    Required when ANY key in your `config_changes` appears in
                 the `config_keys` field of a `prior_lever_history` entry AND
                 your direction (derived from the value vs family-baseline)
                 differs from the prior's `direction`. The structured
                 prior_lever_history makes this a config-key-set intersection,
                 not a prose inference.
    Meaning:     Citations of prior theses that tested the same lever in a
                 different direction — and why this round is justified in
                 retrying it.
    Producer guidance: For each key in your `config_changes`, scan
                       `prior_lever_history` entries for any whose `config_keys`
                       list contains that key. If found AND the prior's
                       `direction` differs from yours, cite that prior here with
                       its `prior_thesis_id` + the shared `lever` (use the
                       lever-concept name from prior_lever_history, not the
                       config-key name) + direction_then + outcome + why_retry.
                       `direction_then` is free text; use a past-tense verb.
    Example:     [{"prior_thesis_id": "job-11-round-2-attempt-1",
                   "lever": "stop_distance",
                   "direction_then": "tightened",
                   "outcome": "killed",
                   "why_retry": "Prior tightened stop in calm regime; this
                                 tightens only in high-vol regime — different
                                 context entirely."}]
```

### 4.6 Emergent-dimension contract

All three fields conditional on `mechanism_dimension == "emergent"`.

```
- new_dimension_name
    Type:        str
    Format:      snake_case short name (≤40 chars)
    Source set:  Free; must not duplicate any entry in MECHANISM_DIMENSIONS
                 or ROUND CONTEXT `emergent_dimensions_in_use`.
    Token cap:   ≤40 chars
    Required:    REQUIRED IF mechanism_dimension == "emergent"; otherwise OMIT.
    Meaning:     The new dimension name you're introducing.
    Producer guidance: Use only when no existing dimension fits. The name
                       you pick can be cited by future theses, so make it
                       semantically clear. If you find yourself stretching
                       an emergent definition to fit your thesis, prefer an
                       existing core dimension — the LLM's incentive is to
                       look novel; the spec asks for accuracy.
    Example:     "open_drive_asymmetry"
                 # NB: must not appear in ROUND CONTEXT
                 # `emergent_dimensions_in_use` (which the §3.2 sample
                 # shows includes `session_microstructure`).
```

```
- why_existing_dimensions_do_not_fit
    Type:        str
    Format:      paragraph
    Source set:  Free
    Token cap:   ≥80 chars
    Required:    REQUIRED IF mechanism_dimension == "emergent"; otherwise OMIT.
    Meaning:     Why none of the core dimensions could host this thesis.
    Producer guidance: Address each adjacent core dimension explicitly — name
                       it and say why it doesn't fit.
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
    Required:    REQUIRED IF mechanism_dimension == "emergent"; otherwise OMIT.
    Meaning:     A short definition of what falls inside the new family.
                 Future theses pattern-match against this.
    Producer guidance: Write the definition as if onboarding a future research
                       round. Abstract — describe the family, not this thesis.
    Example:     "Theses in this dimension address asymmetric liquidity or
                  volatility behavior in a specific session window (open,
                  close, lunch). Distinct from market_microstructure
                  (order-flow level) and regime_conditioning (multi-session)."
```

### 4.7 Evidence

```
- evidence_citations
    Type:        list[EvidenceCitation]
    Format:      typed list — see Inner shape
    Inner shape: EvidenceCitation = {
                     source:   Literal[*EVIDENCE_SOURCES],
                                  # web_search | analyst | source_code |
                                  # experiment_result | memory
                     citation: str (≥30 chars)
                 }
                 Diversity gate counts: web_search, analyst (subset of source enum).
    Source set:  Free
    Token cap:   ≥2 entries; ≤6 entries
    Required:    Always; MUST contain ≥1 with source="web_search" AND
                 ≥1 with source="analyst".
    Meaning:     Typed evidence with required source diversity. The validator
                 assigns positional ids `citation_1`, `citation_2`, ... to
                 entries by array position; the LLM does not emit ids.
    Producer guidance: ≥1 web_search entry citing external mechanism evidence
                       (paper/source/precedent); ≥1 analyst entry citing
                       trade-level evidence from the strategy's own diagnostics.
                       Other source values are accepted but don't count toward
                       the diversity gate.
    Example:     [{"source": "web_search",
                   "citation": "Cont et al. on order-flow regime persistence (Journal of Finance, 2021)"},
                  {"source": "analyst",
                   "citation": "round-3 analyst found 62% of stops occur in counter-HTF-trend setups"},
                  {"source": "source_code",
                   "citation": "strategies/ema/signals.py:apply_htf_gate references higher-timeframe trend, confirming gate exists"}]
                 # Non-gate sources (source_code, experiment_result, memory)
                 # don't satisfy the web_search/analyst diversity gate but
                 # are accepted as supporting evidence.
```

### 4.8 Predictions + falsification

```
- expected_effects
    Type:        list[ExpectedEffect]
    Format:      typed list — see Inner shape
    Inner shape: ExpectedEffect = {
                     metric:          str,
                     direction:       Literal["increase", "decrease",
                                              "increase_or_same",
                                              "decrease_or_same",
                                              "not_worse_than"],
                     magnitude_range: tuple[float, float] | None,
                     unit:            Literal[*EXPECTED_EFFECT_UNITS] | None,
                                       # ratio | pct | bps | trades | dollars |
                                       # sharpe_points | count
                     rationale:       str (≥40 chars)  # required when
                                       # direction in {"increase","decrease"};
                                       # optional otherwise
                 }
    Source set:  Free
    Token cap:   ≥2 entries; ≤6 entries
    Required:    Always; non-empty. magnitude_range required when
                 direction in {"increase", "decrease"}; unit required when
                 magnitude_range is set; rationale required when direction is
                 a directional claim, optional for "*_or_same" / "not_worse_than".
    Meaning:     Per-metric predictions of directional impact and quantitative
                 bounds, used by the outcome evaluator to compare prediction
                 vs actual.
    Producer guidance: Predict ≥2 coupled metrics (one primary outcome, one
                       mechanism check) so the mechanism is testable, not
                       just lucky. Wide magnitude_range signals low confidence;
                       tight range signals a specific quantitative claim.
    Example:     [{"metric": "profit_factor", "direction": "increase",
                   "magnitude_range": [0.05, 0.20], "unit": "ratio",
                   "rationale": "HTF gate filters counter-trend chop, raising win rate by a measurable margin."},
                  {"metric": "trade_count", "direction": "decrease_or_same",
                   "magnitude_range": null, "unit": null,
                   "rationale": null}]   # rationale optional for *_or_same
```

```
- disqualifiers
    Type:        list[Disqualifier]
    Format:      typed list — see Inner shape
    Inner shape: Disqualifier = {
                     name:      str,
                     condition: str,
                     severity:  Literal["hard_fail", "soft_fail"],
                     kind:      Literal["metric_threshold", "mechanism_evidence"]
                 }
    Source set:  Free
    Token cap:   ≥2 entries; ≤5 entries
    Required:    Always; ≥2 entries; ≥1 with kind="mechanism_evidence";
                 ≥1 addressing overfit risk.
    Meaning:     Stated conditions under which the thesis is wrong.
                 Two sub-roles:
                   (a) mechanism-evidence entries state the data pattern that
                       would distinguish your mechanism from an alternative.
                   (b) overfit entries pre-commit to a falsification threshold
                       that catches per-symbol or regime-specific overfit.
    Producer guidance: At least one disqualifier must test the MECHANISM
                       (kind="mechanism_evidence"). At least one must address
                       OVERFIT — either name it from OVERFIT_DISQUALIFIER_MARKERS
                       (trade_count_collapse, cross_symbol_divergence,
                       regime_specific_overfit) OR write a condition mentioning
                       an overfit keyword (overfit, lookahead, regime_specific,
                       symbol_specific, etc.). A single disqualifier may
                       satisfy both requirements (e.g. kind="mechanism_evidence"
                       AND name="regime_specific_overfit"); you then need only
                       one more entry to hit the ≥2 minimum.
    Example:     [{"name": "trade_count_collapse",
                   "condition": "trade_count decreases by more than 50%",
                   "severity": "hard_fail", "kind": "metric_threshold"},
                  {"name": "no_regime_separation",
                   "condition": "PF in up-regime not >1.2× PF in down-regime",
                   "severity": "hard_fail", "kind": "mechanism_evidence"}]
```

### 4.9 Config + engine

```
- config_changes
    Type:        dict[str, Any]
    Format:      {key: new_value, ...} — runtime config keys to set
    Source set:  Keys constrained by ROUND CONTEXT `strategy_config_keys`.
                 Values free.
    Token cap:   ≤30 keys
    Required:    Non-empty UNLESS requires_code_change=true.
    Meaning:     The runtime-config knobs the proposer chose to set this round.
    Producer guidance: Include EVERY key you want set. Keys you omit remain
                       at family-baseline default — they do NOT inherit from
                       prior rounds' configs. Only use keys shown in ROUND
                       CONTEXT `strategy_config_keys`; unknown keys are
                       rejected (set requires_code_change=true and request
                       a new primitive instead).
    Example:     {"ema_length": 21,
                  "rr_ratio": 2.5,
                  "gap_filter": true}
                 # All three keys are from the §3.2 ROUND CONTEXT
                 # `strategy_config_keys` sample (EMA's `allowed_config_keys`).
                 # Keys reachable only via primitive injection (e.g.
                 # min_stop_distance_pct, trail_after_r) require
                 # requires_code_change=true + requested_primitives.
```

```
- requires_code_change
    Type:        bool
    Format:      true | false
    Required:    Always (defaults to false if omitted)
    Meaning:     Whether this thesis needs new engine primitives that no
                 existing config key can express.
    Producer guidance: Set true ONLY when no combination of existing config
                       keys would test the mechanism. Most theses are false.
    Example:     false
```

```
- requested_primitives
    Type:        list[str]
    Format:      list of short snake_case primitive names
    Source set:  Free
    Token cap:   ≤5 entries, each ≤40 chars
    Required:    REQUIRED IF requires_code_change=true; otherwise [].
    Meaning:     Names of new primitive functions/filters the engine needs.
    Producer guidance: Use the same name a strategy developer would pick:
                       "close_confirmed_entry_gate", not "gate_for_my_thesis".
    Example:     ["close_confirmed_entry_gate"]
```

### 4.10 Diagnostics + code grounding

```
- source_code_verification
    Type:        str
    Format:      "<repo path>:<function or symbol> — <explanation>"
    Source set:  Free, but the cited path must have been read during this attempt.
    Token cap:   ≥40 chars; ≤200 chars
    Required:    Always
    Meaning:     Citation of the strategy source file and function whose
                 behavior the proposed change touches.
    Producer guidance: Call the `read_strategy_source` MCP tool on the path
                       BEFORE proposing this thesis. Cite the exact path you
                       read; the validator checks the read trace and rejects
                       if the cited path was never opened during this attempt.
    Example:     "strategies/ema/signals.py:apply_htf_gate — gate evaluated
                  before stop_distance check; placing here ensures the
                  filter sees the raw signal."
```

`required_diagnostic_specs` is omitted from OUTPUT until Spec B lands.
Listed in `_PROMPT_OMITTED_FIELDS` with a comment naming Spec B as the
unblocker.

### 4.11 Optional escape hatch — meta-field, not part of `ResearchThesis` schema

```
- validator_challenge  (OPTIONAL)
    Type:        object
    Format:      {challenged_round, challenged_thesis_id,
                  challenged_rejection_code, claim, evidence}
    Token cap:   ≤200 words total
    Required:    Optional. Use only if you believe a recent rejection was wrong.
    Meaning:     A formal challenge to a prior rejection. Logged for human
                 review; does NOT alter the validator's decision.
    Producer guidance: Use sparingly. Most "the validator was wrong" feelings
                       are actually "the validator surfaced something I didn't
                       want to address."
    Example:     {"challenged_round": 3,
                  "challenged_thesis_id": "job-1-round-3-attempt-2",
                  "challenged_rejection_code": "structural_other_alternatives_too_few",
                  "claim": "The 1-entry minimum should not apply when…",
                  "evidence": "…"}
```

### 4.12 Proposed schema additions

Four new top-level fields. The inner-shape extension to `expected_effects`
(`magnitude_range` + `unit`) is already inlined in §4.8.

```
- expected_runtime_signal
    Type:        list[ExpectedRuntimeSignal]
    Format:      typed list — see Inner shape
    Inner shape: ExpectedRuntimeSignal = {
                     event_path:        str,    # dotted path into strategy_diagnostics
                     expected_relation: Literal["in_range", ">", ">=", "<", "<=", "=="],
                     lower:             float | None,
                     upper:             float | None,
                     condition:         str     # when this signal should hold
                 }
                 lower set when relation in {">", ">=", "==", "in_range"};
                 upper set when relation in {"<", "<=", "==", "in_range"}.
    Source set:  Free; event_path must resolve in the prior round's diagnostics
                 (paths shown in ROUND CONTEXT or upstream context block).
    Token cap:   ≥1 entry recommended; ≤3 entries
    Required:    Optional today; recommended.
    Meaning:     Typed prediction of what should be observable in the
                 runtime event stream if the mechanism is working. Distinct
                 from `expected_effects` (which predicts headline metrics);
                 this predicts the signal-flow behavior the mechanism implies.
    Producer guidance: Predict the SIGNAL-FLOW behavior the mechanism implies,
                       not the metric movement. A regime-overlay thesis should
                       predict that trend_filter_rejected share rises in
                       trending regimes — the outcome evaluator checks this
                       directly against the diagnostics file.
    Example:     [{"event_path": "rejection_breakdown.trend_filter_rejected",
                   "expected_relation": ">", "lower": 0.3, "upper": null,
                   "condition": "in trending regimes"},
                  {"event_path": "event_counts.signals_generated",
                   "expected_relation": "in_range",
                   "lower": 800, "upper": 1500,
                   "condition": "overall (vs ~2400 in baseline)"}]
                 # First entry uses ">" with only lower bound. Second uses
                 # "in_range" with both bounds — relation/lower/upper
                 # conditional pairing demonstrated.
```

```
- mechanism_lineage
    Type:        list[str]
    Format:      list of ancestral thesis_ids (most recent ancestor first)
    Source set:  Free; ids must be in the round snapshot's thesis_ids set
    Token cap:   ≤5 entries
    Required:    Optional. Empty list = greenfield thesis with no predecessor.
    Meaning:     Explicit ancestry chain back to predecessor theses.
                 Distinct from SIMILARITY (cluster overlap) — this is direct
                 iteration.
    Producer guidance: List ONLY the predecessor theses this thesis directly
                       evolves from. Don't list theses that just happen to be
                       in the same dimension.
    Example:     ["job-12-round-3-attempt-1", "job-12-round-1-attempt-2"]
                 # At ≥3 ancestors all in the same mechanism_dimension, the
                 # pivot rule fires: either set a different mechanism_dimension
                 # on this thesis, OR add a disqualifier with
                 # kind="mechanism_evidence" naming the structural pivot.
```

```
- if_this_fails_next_thesis
    Type:        str
    Format:      1-3 sentences
    Source set:  Free
    Token cap:   ≤300 chars
    Required:    Always
    Meaning:     Pre-commitment to the next thesis if THIS one is killed.
                 Surfaces the conductor's implicit "what next" thinking.
    Producer guidance: State the CONCRETE next thesis you would propose if
                       this round's backtest kills the current thesis. Vague
                       answers ("retry with different parameters") indicate
                       shallow forward planning. Must reference either a
                       different mechanism_dimension OR the mechanism named
                       in deepest_alternative — this is a hard validator gate,
                       not a stylistic preference.
    Example:     "If this kills, next round tests an ATR-based dynamic stop
                  in signal_quality dimension (deepest_alternative.mechanism).
                  Drops the regime-overlay theme; switches to volatility-
                  responsive stops."
```

```
- confidence_distribution
    Type:        object — see Inner shape
    Inner shape: ConfidenceDistribution = {
                     data:        Literal["direct", "proxy", "mixed", "speculative"],
                     literature:  Literal["direct", "proxy", "mixed", "speculative"],
                     precedent:   Literal["direct", "proxy", "mixed", "speculative"]
                 }
                 No empty-string escape — every dimension gets a rating.
                 The four values cover the full epistemic spectrum; "" was
                 a soft path the LLM defaulted to under uncertainty.
    Source set:  One-of per dimension
    Required:    Always; all three dimensions must be set.
    Meaning:     Per-dimension confidence rating:
                   - data:       analyst-grade evidence in this strategy's diagnostics
                   - literature: external sources via web_search
                   - precedent:  prior accepted theses in this family or related families
                 Per-dimension rating exposes the weakest link rather than
                 letting a single self-rating average it out.
    Producer guidance: Rate each dimension separately and honestly. A thesis
                       with data="direct" + literature="speculative"
                       + precedent="proxy" is honest — your strongest evidence
                       is the data, weakest is literature. Avoid
                       all-three="direct" — that signals motivated reasoning,
                       not strong evidence.
    Example:     {"data": "direct", "literature": "speculative", "precedent": "proxy"}
```

---

## 4.13 Validator rules — consolidated

All validator rules for the §4 fields, in one place. This section is the
authoritative human-readable source; `prompts/conductor_output_rules.json`
(§6.2) is the machine-readable derivation. Per §3.0.1, none of this content
renders into the LLM-facing prompt.

### 4.13.1 §4.2 Core description

| Field | Rule | Rejection code(s) |
|---|---|---|
| `hypothesis` | Non-empty. | `structural_missing_hypothesis` |
| `mechanism` | Non-empty. | `structural_missing_mechanism` |

### 4.13.2 §4.3 Positioning + classification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `mechanism_dimension` | Must be a member of `MECHANISM_DIMENSIONS` or a value in ROUND CONTEXT `emergent_dimensions_in_use`. | `structural_invalid_mechanism_dimension` |
| `theme_keywords` | Non-empty list. Cluster-fixation gate: max 3 of last 7 prior theses share any one of these keywords. | `structural_theme_keywords_empty`, `thesis_quality_theme_cluster_fixation` |
| `thesis_role` | Non-empty; Literal restricts to the three role values. | `structural_thesis_role_required` |

### 4.13.3 §4.4 Novelty justification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `dimension_novelty` | Length ≥30 chars AND must mention ≥2 distinct dimension names from `MECHANISM_DIMENSIONS` (forces contrast — own choice + prior dimension). | `structural_dimension_novelty_too_short`, `thesis_quality_dimension_novelty_not_grounded` |
| `novel_connection` | Post-emit conditional: required when ≥1 emitted `theme_keywords` entry appears in ROUND CONTEXT `theme_keywords_in_use`. When required: length ≥120 chars AND must mention a shared keyword by name OR a structurally-distinct `mechanism_dimension`. | `structural_novel_connection_too_short`, `thesis_quality_novel_connection_not_grounded` |
| `underexplored_dimensions_considered` | Required when ROUND CONTEXT `dimensions_unexplored` is non-empty. Each entry must be present in `dimensions_unexplored` AND must not equal this thesis's `mechanism_dimension`. | `structural_underexplored_dimensions_invalid`, `structural_underexplored_includes_chosen` |

### 4.13.4 §4.5 Alternatives + prior-work

| Field | Rule | Rejection code(s) |
|---|---|---|
| `deepest_alternative` | Required, non-null. `tiebreaker.value` resolves by exact match: `kind="evidence_citation"` → `citation_N` where `1 ≤ N ≤ len(evidence_citations)`; `kind="disqualifier"` → must equal a `disqualifiers[i].name`; `kind="mechanism_dimension"` → must be a member of `MECHANISM_DIMENSIONS`. | `structural_deepest_alternative_missing`, `structural_deepest_alternative_tiebreaker_unresolved` |
| `other_alternatives` | ≥1 entry; each `why_rejected` ≥40 chars. When `lighter_tiebreaker` is non-null, it resolves by the same rules as `deepest_alternative.tiebreaker`. | `structural_other_alternatives_too_few`, `structural_lighter_tiebreaker_unresolved` |
| `prior_lever_outcomes` | Required when ANY key in `config_changes` appears in any `prior_lever_history[i].config_keys` AND your derived direction differs from `prior_lever_history[i].direction`. `prior_thesis_id` values must exist in ROUND CONTEXT `prior_theses_snapshot`. | `structural_direction_whipsaw_uncited`, `structural_prior_lever_outcomes_unknown_id` |

### 4.13.5 §4.6 Emergent-dimension contract

All three conditional on `mechanism_dimension == "emergent"`.

| Field | Rule | Rejection code(s) |
|---|---|---|
| `new_dimension_name` | When emergent: non-empty; not in `MECHANISM_DIMENSIONS`; not in ROUND CONTEXT `emergent_dimensions_in_use`. | `structural_new_dimension_name_duplicates_existing` |
| `why_existing_dimensions_do_not_fit` | When emergent: non-empty (length ≥80 chars). | `structural_emergent_thesis_malformed` |
| `mechanism_family_definition` | When emergent: non-empty (length ≥80 chars). | `structural_emergent_thesis_malformed` |

### 4.13.6 §4.7 Evidence

| Field | Rule | Rejection code(s) |
|---|---|---|
| `evidence_citations` | ≥2 entries; ≤6 entries; ≥1 with `source="web_search"` AND ≥1 with `source="analyst"`; each `citation` ≥30 chars. | `structural_evidence_citations_missing_source_diversity`, `structural_evidence_citation_too_short` |

### 4.13.7 §4.8 Predictions + falsification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `expected_effects` | Non-empty list (≥2 entries recommended); `magnitude_range` required when `direction in {"increase","decrease"}`; `unit` required and must be a member of `EXPECTED_EFFECT_UNITS` when `magnitude_range` is set; `rationale` required (≥40 chars) when `direction in {"increase","decrease"}`. `magnitude_range[0] < magnitude_range[1]` when set. | `structural_missing_expected_effects`, `structural_expected_effect_magnitude_missing`, `structural_expected_effect_magnitude_range_invalid`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required` |
| `disqualifiers` | ≥2 entries; ≥1 with `kind="mechanism_evidence"`; ≥1 entry whose `name` is in `OVERFIT_DISQUALIFIER_MARKERS` OR whose `condition` (lowercased) contains a member of `OVERFIT_KEYWORD_HINTS`. A single entry may satisfy both `mechanism_evidence` and overfit requirements. | `structural_disqualifiers_too_few`, `structural_disqualifiers_no_mechanism_evidence`, `structural_disqualifiers_no_overfit_address` |

### 4.13.8 §4.9 Config + engine

| Field | Rule | Rejection code(s) |
|---|---|---|
| `config_changes` | Non-empty OR `requires_code_change=true`. Each key must appear in ROUND CONTEXT `strategy_config_keys`. | `structural_config_changes_required`, `structural_config_changes_unknown_key` |
| `requires_code_change` | When `true`, `requested_primitives` must be non-empty. | `structural_engine_change_request_malformed` |
| `requested_primitives` | Non-empty paired with `requires_code_change=true`. | `structural_engine_change_request_malformed` |

### 4.13.9 §4.10 Diagnostics + code grounding

| Field | Rule | Rejection code(s) |
|---|---|---|
| `source_code_verification` | Length ≥40 chars; format matches `"<path>:<symbol> — <prose>"`; the cited `<path>` must appear in the conductor attempt's read-paths trace (captured from `read_strategy_source` invocations). | `structural_source_code_verification_too_short`, `structural_source_code_verification_malformed`, `process_source_code_not_read`, `process_source_code_path_not_read` |

### 4.13.10 §4.11 Escape hatch

| Field | Rule | Rejection code(s) |
|---|---|---|
| `validator_challenge` | No rule; accepts any object. Logged for human review only. | none |

### 4.13.11 §4.12 Proposed additions

| Field | Rule | Rejection code(s) |
|---|---|---|
| `expected_runtime_signal` | Each `event_path` must resolve in ROUND CONTEXT `diagnostic_event_paths`. `lower` set when `expected_relation in {">", ">=", "==", "in_range"}`; `upper` set when `expected_relation in {"<", "<=", "==", "in_range"}`. | `thesis_quality_expected_runtime_signal_path_unknown` |
| `mechanism_lineage` | `thesis_id` entries must appear in ROUND CONTEXT `prior_theses_snapshot`. With ≥3 ancestors sharing the same `mechanism_dimension`, require either (a) a different `mechanism_dimension` on this thesis, OR (b) a `disqualifiers` entry with `kind="mechanism_evidence"` that distinguishes this thesis from the lineage's prior failures. | `thesis_quality_lineage_no_structural_pivot` |
| `if_this_fails_next_thesis` | Non-empty; must reference either a specific `mechanism_dimension` (different from current) OR the `mechanism` text of `deepest_alternative`. | `thesis_quality_next_thesis_not_pre_committed` |
| `confidence_distribution` | At least one of `{data, literature}` must be `"direct"` or `"mixed"`. Theses with all three `"speculative"` require a `disqualifiers` entry with `kind="mechanism_evidence"` acknowledging the weak-evidence basis. Greenfield exemption: when ROUND CONTEXT `dimensions_already_explored` is empty, `precedent="speculative"` is not counted against the gate. | `thesis_quality_confidence_distribution_too_weak`, `thesis_quality_confidence_distribution_missing` |

### 4.13.12 Notes for the implementer

- `prompts/conductor_output_rules.json` is generated from this section + §6.2.1's `predicate_kind` mapping. Every rule above maps to one or more `predicate_kind` rows in §6.2.1.
- The `_PROMPT_OMITTED_RULES` set in `check_prompt_drift.py` covers any rejection code the validator emits internally that isn't in this section (e.g. duplicate-thesis-id checks across rounds — system-level, not field-level).

---

## 5. Worked example — fixture

The worked example lives at `tests/fixtures/conductor_prompt_worked_example.json`
(not inline). The test suite asserts the fixture passes Pydantic validation,
the live `validate_thesis_dict(...)` call, and every rule declared in
`prompts/conductor_output_rules.json` (§6.2).

A negative-fixture directory `tests/fixtures/conductor_prompt_rejections/`
holds one fixture per rejection code, each minimally violating one rule.

Canonical positive fixture shape — note `requires_code_change: true` with
empty `config_changes`, since HTF gating is a new primitive (not in EMA's
`allowed_config_keys`):

```json
{
  "hypothesis": "Adding a 1-hour direction gate filters out counter-trend 5-min pullbacks that drove the strategy's drawdown floor.",
  "mechanism": "HTF direction acts as a regime overlay — when 1h trend is up, only long 5-min pullbacks fire; when down, only shorts. Reduces signal count by ~40% but surviving signals have better edge.",
  "mechanism_dimension": "regime_conditioning",
  "theme_keywords": ["htf_gate", "trend_overlay"],
  "thesis_role": "orthogonal_discovery",
  "dimension_novelty": "Moves from signal_quality threshold tweaking (where prior trend_filter_v2 lived) to regime_conditioning by overlaying a 1h direction gate.",
  "underexplored_dimensions_considered": ["portfolio_construction", "alpha_decay"],
  "evidence_citations": [
    {"source": "web_search", "citation": "Cont et al. on order-flow regime persistence (Journal of Finance, 2021)"},
    {"source": "analyst",    "citation": "round-3 analyst found 62% of stops occur in counter-HTF-trend setups"},
    {"source": "source_code", "citation": "strategies/ema/signals.py:apply_htf_gate confirms gate placement before stop_distance check"}
  ],
  "expected_effects": [
    {"metric": "profit_factor", "direction": "increase",
     "magnitude_range": [0.05, 0.20], "unit": "ratio",
     "rationale": "HTF gate filters counter-trend chop, raising win rate by a measurable margin."},
    {"metric": "trade_count", "direction": "decrease_or_same",
     "magnitude_range": null, "unit": null,
     "rationale": null}
  ],
  "expected_runtime_signal": [
    {"event_path": "rejection_breakdown.trend_filter_rejected",
     "expected_relation": ">", "lower": 0.3, "upper": null,
     "condition": "in trending regimes"}
  ],
  "disqualifiers": [
    {"name": "trade_count_collapse", "condition": "trade_count decreases by more than 50%",
     "severity": "hard_fail", "kind": "metric_threshold"},
    {"name": "no_regime_separation",
     "condition": "PF in up-regime not >1.2× PF in down-regime",
     "severity": "hard_fail", "kind": "mechanism_evidence"}
  ],
  "deepest_alternative": {
    "mechanism": "ADX>30 entry filter",
    "why_rejected": "Too strict in low-vol regimes per round-3 analyst evidence (citation_2) — would suppress signals where the HTF gate still admits them, costing trade frequency without addressing the wick-only stop-out mechanism.",
    "tiebreaker": {"kind": "evidence_citation", "value": "citation_2"}
  },
  "other_alternatives": [
    {"mechanism": "session-time entry filter",
     "why_rejected": "Proxy for the regime problem rather than the structural fix; cannot distinguish high-vol from low-vol opens within the same session.",
     "lighter_tiebreaker": null},
    {"mechanism": "VWAP-distance entry filter",
     "why_rejected": "Family already tested VWAP-distance under signal_quality dimension; that lineage was killed — see citation_2 analyst evidence.",
     "lighter_tiebreaker": {"kind": "mechanism_dimension", "value": "signal_quality"}}
  ],
  "prior_lever_outcomes": [],
  "mechanism_lineage": [],
  "config_changes": {},
  "requires_code_change": true,
  "requested_primitives": ["htf_direction_gate"],
  "source_code_verification": "strategies/ema/signals.py:apply_htf_gate — gate evaluated before stop_distance check; placing here ensures filter sees raw signal.",
  "if_this_fails_next_thesis": "If this kills, next round tests ADX>30 entry filter in signal_quality dimension (deepest_alternative.mechanism). Drops the regime-overlay theme; switches to threshold-based filtering.",
  "confidence_distribution": {"data": "direct", "literature": "speculative", "precedent": "proxy"}
}
```

## 6. DOCTRINE ↔ OUTPUT cross-references

Each DOCTRINE principle that implies a field gets an inline `→ see <field>`
reference. The reverse direction (per-field DOCTRINE backref) is **added
by the §7 renderer** from a `_DOCTRINE_BACKREFS: dict[field, principle]`
mapping — not hand-written in this spec's §4 entries. The §4 entries
above are the authoring source; the rendered prompt carries the
`(DOCTRINE: <principle>)` annotations.

DOCTRINE:
```
- Evidence: cite at least one external source (web_search) AND one trade-level
  finding (analyst). External-only is theory; analyst-only is data dredging.
  → see field `evidence_citations` in OUTPUT.
```

OUTPUT entry for `evidence_citations`:
```
Producer guidance: ≥1 web_search entry... (DOCTRINE: Evidence)
```

## 6.2 Structured rule metadata

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
    }
  ]
}
```

A `prompt_rules.py` module exposes `iter_prompt_declared_rules() →
Iterable[(rule_id, predicate_callable)]` by mapping `predicate_kind` values
to predicate functions. The validator imports the same mapping. The test
suite uses it directly to assert the positive fixture passes every predicate
and each negative fixture trips exactly its one named rule.

### 6.2.1 Predicate kinds — full enumeration

Every `Validator rule:` line in §4 reduces to a `predicate_kind` from this
table. The renderer fails if a §4 rule cannot be expressed in this set.

| predicate_kind | required_args | data_dependencies | covers |
|---|---|---|---|
| `non_empty` | `{field}` | thesis | structural_missing_* |
| `min_length` | `{field, min}` | thesis | _too_short codes |
| `literal_membership` | `{field, constant_name}` | thesis + constant import | structural_invalid_mechanism_dimension, structural_evidence_citation_too_short, etc. |
| `tiebreaker_resolves` | `{field, lookup_tables}` | thesis | structural_deepest_alternative_tiebreaker_unresolved, structural_lighter_tiebreaker_unresolved |
| `list_min_length` | `{field, min}` | thesis | structural_other_alternatives_too_few, structural_disqualifiers_too_few |
| `list_min_with_kind` | `{field, kind_field, kind_value, min}` | thesis | structural_disqualifiers_no_mechanism_evidence |
| `list_any_matches_marker_or_keyword` | `{field, name_field, condition_field, markers_constant, keywords_constant}` | thesis + constants | structural_disqualifiers_no_overfit_address |
| `list_members_in_round_context_set` | `{field, round_context_key}` | thesis + round_context | structural_underexplored_dimensions_invalid |
| `list_members_not_equal` | `{field, ref_field}` | thesis | structural_underexplored_includes_chosen |
| `grounded_mention_distinct_count` | `{field, constant_name, min_distinct}` | thesis + constant | dimension_novelty ≥2-mention rule |
| `theme_keyword_overlap_triggers_field` | `{trigger_field, target_field, round_context_key}` | thesis + round_context | structural_novel_connection_too_short, thesis_quality_novel_connection_not_grounded |
| `whipsaw_from_prior_lever_history` | `{config_changes_field, target_field, round_context_key}` | thesis + round_context | structural_direction_whipsaw_uncited |
| `prior_thesis_ids_in_snapshot` | `{field, round_context_key}` | thesis + round_context | structural_prior_lever_outcomes_unknown_id, thesis_quality_lineage_no_structural_pivot (id check) |
| `lineage_pivot_required` | `{lineage_field, dimension_field, ancestors_round_context_key, disqualifiers_field, min_ancestors}` | thesis + round_context | thesis_quality_lineage_no_structural_pivot (pivot check) |
| `event_path_resolves` | `{field, round_context_key}` | thesis + round_context | thesis_quality_expected_runtime_signal_path_unknown |
| `config_keys_in_round_context_set` | `{field, round_context_key}` | thesis + round_context | structural_config_changes_unknown_key |
| `magnitude_range_required_for_direction` | `{effects_field, direction_values_requiring_range}` | thesis | structural_expected_effect_magnitude_missing |
| `magnitude_range_well_formed` | `{effects_field}` | thesis | structural_expected_effect_magnitude_range_invalid |
| `unit_required_when_magnitude_set` | `{effects_field, unit_constant}` | thesis + constant | structural_expected_effect_unit_invalid |
| `rationale_required_for_directional` | `{effects_field, direction_values_requiring_rationale, min_len}` | thesis | structural_expected_effect_rationale_required |
| `confidence_strength_floor` | `{confidence_field, strong_values, greenfield_round_context_key}` | thesis + round_context | thesis_quality_confidence_distribution_too_weak |
| `next_thesis_references_pivot_or_deepest` | `{field, dimension_field, deepest_alternative_field}` | thesis | thesis_quality_next_thesis_not_pre_committed |
| `path_in_read_trace` | `{field, attempt_trace_key, path_extractor}` | thesis + attempt_trace | process_source_code_path_not_read |
| `tool_invoked_in_trace` | `{tool_name, attempt_trace_key}` | attempt_trace | process_source_code_not_read |

Process-tier predicates (`path_in_read_trace`, `tool_invoked_in_trace`)
require the validator to receive the attempt's tool-call trace alongside
the thesis — see §9 plumbing items for `ConductorResult.read_paths`.

## 7. Programmatic regeneration

The OUTPUT section is machine-generated from `ResearchThesis` by a new
script `scripts/render_output_schema.py`. The script:

- Introspects `ResearchThesis.model_fields`.
- For each field, reads the Pydantic type annotation, default, and `Field(description=...)`.
- Renders each field per §3's template in the §3.1 category order, **omitting the `Validator rule:` slot** (per §3.0.1 — that slot exists only in the spec doc and the rules sidecar, not in the LLM-facing prompt).
- Renders `EVIDENCE_SOURCES` (full enum) and `EVIDENCE_SOURCES_FOR_DIVERSITY_GATE` (subset) as two distinct lines under `evidence_citations`.
- Resolves enum/marker lists by importing constants (`PRIOR_LEVER_OUTCOMES`, `OVERFIT_DISQUALIFIER_MARKERS`, etc.) — never inlines them in prose.
- Emits `prompts/conductor_output_section.md` (LLM-facing) and `prompts/conductor_output_rules.json` (validator/test machine source). Rejection codes appear in the sidecar only.

The rendered files are checked into git for diff visibility. CI re-runs the
regenerator and fails if the checked-in files are stale.

## 8. Drift detection

Checks in CI via `scripts/check_prompt_drift.py`:

1. **Schema-prompt parity** — every field in `ResearchThesis.model_fields` appears in `prompts/conductor_output_section.md` UNLESS in `_PROMPT_OMITTED_FIELDS` (`thesis_id`, `strategy_family`, `required_diagnostic_specs`).
2. **Validator-sidecar parity** — every rejection code emitted by `thesis_validator.py` (extracted by parsing `rejection_code=...` literals) is referenced in `prompts/conductor_output_rules.json` OR listed in `_PROMPT_OMITTED_RULES`. Rejection codes do not appear in the rendered prompt; the sidecar is the machine-readable source.
3. **No rule leakage in prompt** — `prompts/conductor_output_section.md` must not contain `Validator rule:` lines or any `structural_*` / `thesis_quality_*` / `process_*` rejection code literals. CI greps for these and fails the build if found.
4. **Category ordering** — referenced-field categories render before referencing-field categories.
5. **Constants in prompt** — every enum/marker list rendered in the prompt originates from a tuple/frozenset constant in `research_types.py`. No prose-only lists.
6. **Schema-version stamp** — `_build_conductor_system_prompt` includes a `# Output schema version: <hash>` line, computed from `ResearchThesis.model_fields` + rules sidecar hash.
7. **Rules sidecar freshness** — `prompts/conductor_output_rules.json` matches a fresh regeneration.

## 9. Migration plan — single PR

1. **`research_types.py`**:
   - **Schema deletions** (per A4a consolidation; no backcompat). Remove from `ResearchThesis`:
     - `causal_cluster`
     - `dominant_cluster_overlap`
     - `closest_prior_theses_considered`   (intent absorbed by new `mechanism_lineage`)
     - `orthogonality_defense`             (intent absorbed by strengthened `deepest_alternative.tiebreaker`)
     - `evidence_strength`                 (superseded by new `confidence_distribution`)
     - `falsification_or_alternative`      (intent absorbed by strengthened `disqualifiers` contract)
     - `expected_reuse_across_future_theses`
     - `evidence`                          (legacy list[str]; replaced by `evidence_citations`)
     - `base_contract_id`                  (legacy compat)
     - `base_config_path`                  (legacy compat)
     - `required_diagnostics`              (legacy untyped; replaced by `required_diagnostic_specs`)
     - `why_not_overfit`                   (intent absorbed by strengthened `disqualifiers` overfit-marker rule)
     - `alternatives_considered`           (replaced by `deepest_alternative` + `other_alternatives`)
   - Add `TiebreakerRef`, `DeepestAlternative` models; extend `Alternative` with `lighter_tiebreaker: TiebreakerRef | None`.
   - Add `deepest_alternative` and `other_alternatives` to `ResearchThesis`.
   - Add `ExpectedRuntimeSignal`, `ConfidenceDistribution` models.
   - Add fields `expected_runtime_signal`, `mechanism_lineage`, `if_this_fails_next_thesis`, `confidence_distribution`.
   - Redesign `ExpectedEffect`: replace `threshold` with `magnitude_range: tuple[float,float] | None`; keep `unit`; conditional rules per §4.8.
   - Add `EvidenceCitation.citation` `min_length=30`.
   - Drop `""` from `thesis_role` Literal AND from all three `ConfidenceDistribution` sub-field Literals.
   - Add constants: `PRIOR_LEVER_OUTCOMES`, `PRIOR_LEVER_DIRECTION_HINTS`, `EVIDENCE_SOURCES`, `EVIDENCE_SOURCES_FOR_DIVERSITY_GATE`, `OVERFIT_DISQUALIFIER_MARKERS`, `OVERFIT_KEYWORD_HINTS`, `EXPECTED_EFFECT_UNITS`. Import-time assertions pair each `Literal` with its constant.
   - Add `Field(description=...)` to every field that lacks one.
2. **`thesis_validator.py`**:
   - Implement all new rejection codes listed in §4 (including `structural_config_changes_unknown_key`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required`).
   - Tiebreaker resolution against positional `citation_N` ids.
   - Overfit gate: structural name match OR keyword match in condition.
   - `source_code_verification` path-level gate using read-paths trace.
   - `config_changes` key membership check against the family's runtime-config schema.
   - `dimension_novelty` rule strengthened to require ≥2 distinct dimension names.
   - `confidence_distribution` greenfield exemption when `dimensions_already_explored` is empty.
   - Replace all hardcoded marker/enum prose-references with imports from the new constants.
3. **`research_tools_mcp.py`**:
   - Add `read_strategy_source(path: str) -> str` MCP tool. Sandbox policy:
     - **Path-prefix allowlist** (relative to repo root, resolved before
       comparison): `strategies/`, `compiler_*.py`, `research_types.py`,
       `trace_sdk.py`. Anything outside this set returns
       `"error: path not in allowlist"` without disclosing whether the path
       exists.
     - **Path resolution**: `pathlib.Path(repo_root / path).resolve(strict=True)`.
       The resolved path must be `is_relative_to(repo_root)` AND start with
       one of the allowlist prefixes. Rejects `../` traversal and symlinks
       escaping the repo by construction (resolve + relative-to check).
     - **Symlink handling**: `strict=True` resolves symlinks; the target
       (post-resolution) must satisfy the allowlist. Symlinks pointing
       outside the repo or into non-allowlisted prefixes fail closed.
     - **Size cap**: reject files >200 KB with `"error: file exceeds size cap"`.
     - **Output**: the file contents as UTF-8 string; non-text files (binary
       sniff via NUL byte check in first 8 KB) return `"error: binary file"`.
   - Every successful call appends the resolved relative path (string) to a
     per-attempt `read_paths` set captured by the conductor runner (§4 below).
   - The tool emits structured trace events `{tool: "read_strategy_source",
     path: "...", bytes: N}` for audit.
4. **Trace plumbing** for §4.10 path-level gate:
   - `research_types.py`: extend `ConductorResult` with `read_paths: frozenset[str] = frozenset()`.
   - `research_conductor.py` (or wherever the MCP tool-call observer lives): capture every `read_strategy_source` invocation's `path` argument into the conductor's per-attempt accumulator; emit it into `ConductorResult.read_paths` on return.
   - `autoresearch_research.py`: thread `read_paths` through to `validate_thesis_dict(thesis, round_context=..., attempt_trace=...)` as part of the `attempt_trace` object alongside `tools_called`.
   - `thesis_validator.py`: accept the new `attempt_trace` kwarg; the `path_in_read_trace` predicate (per §6.2.1) consumes it.
5. **`research_prompts.py`** (or `autoresearch_research.py`):
   - Build the `## ROUND CONTEXT` block with size caps per §3.2, including the new `prior_lever_history` (structured), `strategy_config_keys`, `prior_theses_snapshot`, `diagnostic_event_paths`, `theme_keywords_overlap_signal`, and `citation_id_convention` sub-blocks.
   - Add `## SYSTEM-INJECTED FIELDS (do not emit)` appendix for `thesis_id`, `strategy_family`.
   - `_build_conductor_system_prompt` reads `prompts/conductor_output_section.md` and interpolates.
   - DOCTRINE updated with §6 cross-references; old OUTPUT prose deleted.
6. **DOCTRINE backref mapping** (per §6):
   - `prompts/_doctrine_backrefs.py` (or a dict in the renderer) holds `{field_name: doctrine_principle}` mappings; the §7 renderer annotates each `Producer guidance` line with `(DOCTRINE: <principle>)` where a mapping exists.
   - CI gate: every key in the backref mapping must be a real field; every field referenced by a `→ see` line in DOCTRINE must appear in the backref mapping (drift detection extension to §8).
7. **`scripts/render_output_schema.py`** (new): per §7.
8. **`prompts/conductor_output_section.md`** (new, generated): committed.
9. **`prompts/conductor_output_rules.json`** (new, generated): committed.
10. **`prompt_rules.py`** (new): exposes `iter_prompt_declared_rules()` per §6.2.1.
11. **`scripts/check_prompt_drift.py`** (extended): per §8.
12. **`tests/fixtures/conductor_prompt_worked_example.json`** (new).
13. **`tests/fixtures/conductor_prompt_rejections/`** (new dir): one fixture per rejection code, each marked with `__expected_rejection_code__`.
14. **`tests/test_conductor_prompt_v3.py`**:
    - Positive fixture passes Pydantic + live validator + every prompt-declared rule.
    - Each negative fixture trips exactly its named rejection code.
    - `## ROUND CONTEXT` present with all required keys: `prior_lever_history` (structured), `strategy_config_keys`, `prior_theses_snapshot`, `diagnostic_event_paths`, `theme_keywords_overlap_signal`, `citation_id_convention`.
    - Category ordering verified.
    - No hardcoded enum/marker strings in the rendered prompt.
    - DOCTRINE backref mapping covers every field whose Producer guidance has a DOCTRINE relation.
15. **Final grep gate** (PR not mergeable until all pass):
    - `grep -n 'OUTPUT$' research_prompts.py` returns a single match (the interpolated section).
    - `pytest tests/test_conductor_prompt_v3.py` passes.
    - `python scripts/check_prompt_drift.py` exits 0.

## 10. Risk and rollback

**Risks:**

- **Renderer bugs ship a malformed prompt.** Mitigation: §8 drift detection catches schema-prompt mismatches; the rendered prompt is checked in so git diff makes changes visible. The test suite runs `validate_thesis_dict` on the positive fixture — if it doesn't pass, CI fails.
- **`ExpectedEffect.threshold` removal breaks downstream readers.** Mitigation: grep for `.threshold` on `ExpectedEffect` and migrate at the same time. Spec-B-style telemetry that read `threshold` migrates with it.
- **Tiebreaker positional-id convention surprises the LLM at first.** Mitigation: ROUND CONTEXT explicitly renders `evidence_citations_available_ids` so the LLM sees the convention in use. Producer guidance on `deepest_alternative` references it.
- **ROUND CONTEXT computation pulls validated data into the prompt path.** Mitigation: ROUND CONTEXT is a pure projection of snapshot state already used by the validator; same source of truth, new render target. Unit-test the projection independently.
- **`source_code_verification` path-level gate too strict.** Mitigation: post-deploy success criteria includes monitoring rejections under `process_source_code_path_not_read`; non-zero rate after 4 weeks prompts a guidance revision, not gate removal.

**Rollback:** revert the PR. `_build_conductor_system_prompt` returns to the
hand-written prose. Lose drift detection, the worked example fixture, and
the structural rule sidecar until re-landed.

## 11. Success criteria

**Coverage:**

- Every key in `ResearchThesis.model_fields` appears in the rendered OUTPUT section OR in `_PROMPT_OMITTED_FIELDS`.
- Every typed-object field has its `Inner shape:` slot populated.
- Every conditional-requirement contract is named in the relevant `Required:` line and references a ROUND CONTEXT key the LLM can read.

**Worked example:**

- `tests/test_conductor_prompt_v3.py` asserts the positive fixture passes (a) Pydantic, (b) `validate_thesis_dict`, and (c) every rule in `prompts/conductor_output_rules.json`.
- Each negative fixture trips exactly its named rejection code.

**Drift detection:**

- `scripts/check_prompt_drift.py` extended with schema-prompt parity, validator-sidecar parity, no-rule-leakage-in-prompt, category ordering, constants-in-prompt, schema-version-stamp, and rules-sidecar-freshness checks. CI gates on all.
- Rejection codes never appear in the rendered LLM-facing prompt; they live in `prompts/conductor_output_rules.json` only.

**Cross-references:**

- Every DOCTRINE principle that implies a field has a `→ see <field>` cross-reference.
- Every OUTPUT entry's `Producer guidance` has a `(DOCTRINE: <principle>)` back-reference where applicable.

**Structural integrity:**

- No hardcoded enum/marker string list appears in the rendered prompt.
- `deepest_alternative` and `other_alternatives` replace `alternatives_considered` end-to-end.
- `ExpectedEffect` schema is `{metric, direction, magnitude_range, unit, rationale}`; `threshold` removed; consumers migrated.

**Production behavioral signal (post-deploy):**

- Conductor's per-attempt acceptance rate (theses that pass Stage 1 validation on first emit) — pre-deploy baseline is 0 of 3 on VPS (100% rejection). Target: ≥50% acceptance on the first 10 attempts.
- Secondary: ≥80% of accepted theses have `deepest_alternative.tiebreaker` resolving on first emit.
- Secondary: ≥30% of accepted theses populate at least one `other_alternatives[i].lighter_tiebreaker`.
- Secondary: 0 rejections with code `process_source_code_path_not_read` after 4 weeks; non-zero prompts a guidance revision.

## 12. Out of scope

- Schema-field additions beyond §4.12.
- Validator rule additions beyond §4's `Validator rule:` lines.
- DOCTRINE rewrites beyond §6 cross-references.
- Multi-model prompt A/B testing.
- Changing the JSON envelope key `suggested_theses`.
