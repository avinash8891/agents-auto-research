# Spec A4a — Conductor OUTPUT Fields System Prompt

**Date:** 2026-05-28
**Status:** Design — split from Spec A4
**Reference:** `research_prompts.py:117-154` current OUTPUT section; sibling specs A4b and A4c.
**Depends on:** A4b for runtime context keys used by conditional fields; A4c for validator rules and drift checks.

---

## 1. Goal

Rewrite only the static OUTPUT instruction section so the conductor LLM sees a
complete, typed, example-backed `ResearchThesis` JSON contract. This spec owns
field names, field shapes, field ordering, producer guidance, and examples.

This spec deliberately does not own round-specific prompt blocks or validator
implementation details. Those live in:

- `2026-05-28-spec-a4-round-runtime-context-prompt.md`
- `2026-05-28-spec-a4-output-validation-and-drift.md`

## 2. Non-goals

- Runtime prompt assembly (`ROUND CONTEXT`, `RECENT REJECTIONS`, prior theses,
  diagnostics paths) — see A4b.
- Validator predicates, rejection codes, generated sidecar files, and drift
  detection — see A4c.
- Adding fields beyond what §4 lists.
- Rewriting the research philosophy beyond removing duplicated schema/output
  contracts from DOCTRINE.

## 2.1 No backward compatibility — hard cutover

The old hand-written OUTPUT prose is replaced wholesale by the generated
OUTPUT section. No deprecation, no A/B rendering.

## 3. Per-field entry template

Every field in the spec's §4 follows this fixed authoring template. The spec
keeps the long labels for reviewability; the LLM-facing renderer compresses
them using the §3.4 compact syntax.

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

## 3.1 Category ordering

OUTPUT fields render in this category order, independent of the §4 heading
order. Referenced fields come before fields that reference them, so the LLM
emits targets before references:

1. Core description
2. Positioning + classification
3. Novelty justification
4. Evidence
5. Predictions + falsification
6. Config + engine
7. Alternatives + prior-work
8. Emergent-dimension contract
9. Diagnostics + code grounding

§4.1 Identity is omitted from LLM-facing OUTPUT and documented in a separate
`## SYSTEM-INJECTED FIELDS (do not emit)` appendix above OUTPUT.

## 3.2 Runtime Context References

Some `Required:` and `Source set:` lines refer to runtime keys such as
`theme_keywords_in_use`, `dimensions_unexplored`, `prior_lever_history`,
`strategy_config_keys`, `prior_theses_snapshot`, and `diagnostic_event_paths`.
Their rendered prompt shape is specified in A4b. This file only names the keys
needed by OUTPUT fields.

## 3.3 Tiebreaker ID Convention

The LLM does not emit IDs inside `evidence_citations`. When a tiebreaker needs
to cite evidence, use positional references: `citation_1`, `citation_2`, etc.,
matching the order of `evidence_citations` in this same JSON object.

## 3.4 Compact LLM Rendering Syntax

The rendered OUTPUT prompt uses compact labels, not the long authoring labels.
This compresses repeated syntax without dropping guidance. Label mapping:

```text
T=Type
F=Format
S=Source set
Cap=Token cap
Req=Required
M=Meaning
G=Producer guidance
Ex=Example
Shape=Inner shape
```

Each rendered field MUST include `T`, `F`, `S`, `Cap`, `Req`, `M`, `G`, and
`Ex`. Typed-object fields MUST also include `Shape`. `G` is mandatory; do not
drop producer guidance to save tokens.

Rendered form:

```text
field_name
T=...; F=...; S=...; Cap=...; Req=...
Shape={...}  # typed-object fields only
M=...
G=...
Ex=...
```

Example compact render:

```text
hypothesis
T=str; F=one sentence; S=free; Cap<=40 words/300 chars; Req=always.
M=Core claim: what should happen and under what conditions.
G=State the mechanism, not the parameter; avoid pure config-tuning claims.
Ex="Adding a 1-hour direction gate filters out counter-trend 5-min pullbacks."
```

Compression rules:

- Remove repeated prose scaffolding, not field semantics.
- Keep one concrete example per field.
- Keep every producer-guidance sentence that changes how the LLM should think.
- Deduplicate only globally shared instructions already stated in §3.1-§3.3.
- Do not render field accounting, migration items, success criteria, validator
  rules, rejection codes, or implementation notes.

---

## 4. Field-by-field OUTPUT entries

**Field accounting** (post-A4 schema, verified against
`ResearchThesis.model_fields` enumeration on 2026-05-28):

- 35 pre-A4 schema fields
- − 13 deletions (12 consolidation drops + `alternatives_considered` replaced)
- + 2 replacements (`deepest_alternative`, `other_alternatives`)
- + 4 integrated additions (`expected_runtime_signal`, `mechanism_lineage`, `if_this_fails_next_thesis`, `confidence_distribution`)
- + 1 inner-shape extension to `ExpectedEffect` (§4.8)
- = **28 fields in the post-A4 schema**

Of those 28:
- 25 render in §4 as LLM-facing OUTPUT entries.
- 3 omitted into `_PROMPT_OMITTED_FIELDS`: `thesis_id` (§4.1), `strategy_family` (§4.1), `required_diagnostic_specs` (§4.10).

### 4.1 Identity — system-injected, omitted from OUTPUT

`thesis_id` and `strategy_family` are documented in a `## SYSTEM-INJECTED
FIELDS (do not emit)` appendix above OUTPUT. The system assigns both.

- `thesis_id`: assigned by the system as `f"{research_round_id}-attempt-{N}"`.
- `strategy_family`: assigned by the system from the active job's family.

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
    Type:        Literal[*MECHANISM_DIMENSIONS] | runtime emergent dimension name
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
                       emergent. Only set "emergent" when no existing or
                       already-emergent dimension fits.
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
                 touches.
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
    Required:    Always
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
                       (or not only) the dimension you chose. The contrast is
                       what makes the novelty claim useful.
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
    Required:    Required when any emitted `theme_keywords` entry appears in
                 ROUND CONTEXT `theme_keywords_in_use`; recommended whenever
                 ROUND CONTEXT `family_cluster_density == "high"`.
    Meaning:     Why this thesis is materially new despite keyword overlap
                 with priors (not just another variation of the dominant cluster).
    Producer guidance: Reference the shared keyword by name and explain the
                       structural difference. Length without grounded mention
                       is not useful. Populate this field whenever ROUND
                       CONTEXT `family_cluster_density == "high"` OR you reuse
                       any keyword from `theme_keywords_in_use`.
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
                       include the dimension you chose.
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
                       order you emit `evidence_citations`), OR a
                       `disqualifiers[i].name`,
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
                       shallow forward planning. Prefer either a different
                       mechanism_dimension or the mechanism named in
                       deepest_alternative.
    Example:     "If this kills, next round tests ADX>30 entry filter
                  (deepest_alternative.mechanism) in signal_quality dimension.
                  Drops the regime-overlay theme; switches to threshold-based
                  filtering."
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
                 # Populating lighter_tiebreaker is optional but helps make
                 # the comparison concrete.
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
                                 loosens stop distance only in high-vol regime —
                                 opposite direction, different context."}]
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
                 # `emergent_dimensions_in_use`; A4b owns the rendered
                 # runtime list.
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
                 Diversity requirement counts: web_search, analyst
                 (subset of source enum).
    Source set:  Free
    Token cap:   ≥2 entries; ≤6 entries
    Required:    Always; MUST contain ≥1 with source="web_search" AND
                 ≥1 with source="analyst".
    Meaning:     Typed evidence with required source diversity. Entries are ordered;
                 tiebreakers can cite them with positional references such as
                 `citation_1`, `citation_2`, etc.
    Producer guidance: ≥1 web_search entry citing external mechanism evidence
                       (paper/source/precedent); ≥1 analyst entry citing
                       trade-level evidence from the strategy's own diagnostics.
                       Other source values are accepted but don't count toward
                       the diversity requirement.
    Example:     [{"source": "web_search",
                   "citation": "Cont et al. on order-flow regime persistence (Journal of Finance, 2021)"},
                  {"source": "analyst",
                   "citation": "round-3 analyst found 62% of stops occur in counter-HTF-trend setups"},
                  {"source": "source_code",
                   "citation": "strategies/orb/signals.py:generate_signals applies use_trend_filter after raw OR breakout conditions"}]
                 # Other sources (source_code, experiment_result, memory)
                 # don't satisfy the web_search/analyst diversity requirement
                 # but are accepted as supporting evidence.
```

```
- confidence_distribution
    Type:        object — see Inner shape
    Format:      typed object — see Inner shape
    Inner shape: ConfidenceDistribution = {
                     data:        Literal["direct", "proxy", "mixed", "speculative"],
                     literature:  Literal["direct", "proxy", "mixed", "speculative"],
                     precedent:   Literal["direct", "proxy", "mixed", "speculative"]
                 }
                 No empty-string escape — every dimension gets a rating.
                 The four values cover the full epistemic spectrum; "" was
                 a soft path the LLM defaulted to under uncertainty.
    Source set:  One-of per dimension
    Token cap:   single object with 3 enum values
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
                 (paths shown in ROUND CONTEXT `diagnostic_event_paths`).
    Token cap:   ≥1 entry recommended; ≤3 entries
    Required:    Optional today; recommended.
    Meaning:     Typed prediction of what should be observable in the
                 runtime event stream if the mechanism is working. Distinct
                 from `expected_effects` (which predicts headline metrics);
                 this predicts the signal-flow behavior the mechanism implies.
    Producer guidance: Predict the SIGNAL-FLOW behavior the mechanism implies,
                       not the metric movement. A regime-overlay thesis should
                       predict that trend_filter_rejected share rises in
                       trending regimes.
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
                       OVERFIT, using a concrete risk name such as
                       trade_count_collapse, cross_symbol_divergence, or
                       regime_specific_overfit. A single disqualifier may
                       cover both roles; include another entry to keep the
                       falsification set plural.
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
                       CONTEXT `strategy_config_keys`; if no shown key can
                       express the mechanism, set requires_code_change=true
                       and request a new primitive instead.
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
    Source set:  One-of: true | false
    Token cap:   single boolean
    Required:    Always
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
    Example:     []
                 # When requires_code_change=true, use:
                 # ["close_confirmed_entry_gate"]
```

### 4.10 Diagnostics + code grounding

```
- source_code_verification
    Type:        str
    Format:      "<repo path>:<function or symbol> — <explanation>"
    Source set:  Free, but cite a strategy source path relevant to the thesis.
    Token cap:   ≥40 chars; ≤200 chars
    Required:    Always
    Meaning:     Citation of the strategy source file and function whose
                 behavior the proposed change touches.
    Producer guidance: Cite the concrete strategy file and symbol that would
                       implement, configure, or constrain the proposed change.
                       Use this to ground the thesis in code, not just prose.
    Example:     "strategies/orb/signals.py:generate_signals — trend filter
                  gates breakouts after raw OR conditions and before final
                  entries are returned."
```

`required_diagnostic_specs` is omitted from OUTPUT until diagnostic-spec fields
are separately defined. It remains listed in `_PROMPT_OMITTED_FIELDS`.

---

## 5. Worked example — fixture

The worked example lives at `tests/fixtures/conductor_prompt_worked_example.json`
(not inline). Canonical positive fixture shape:

```json
{
  "hypothesis": "Enabling the ORB trend filter removes counter-trend breakouts that drove failed continuation trades.",
  "mechanism": "The ORB setup has edge when opening-range breaks continue with the broader daily move. Requiring price to align with the prior-day EMA removes breakouts fighting the prevailing trend, reducing trade count while improving signal quality.",
  "mechanism_dimension": "regime_conditioning",
  "theme_keywords": ["trend_filter", "orb_regime"],
  "thesis_role": "orthogonal_discovery",
  "dimension_novelty": "Moves from signal_quality breakout-threshold tuning to regime_conditioning by filtering ORB entries with prior-day trend alignment.",
  "underexplored_dimensions_considered": ["portfolio_construction", "alpha_decay"],
  "evidence_citations": [
    {"source": "web_search", "citation": "Cont et al. on order-flow regime persistence (Journal of Finance, 2021)"},
    {"source": "analyst",    "citation": "round-3 analyst found counter-trend ORB breakouts had materially worse continuation than trend-aligned breakouts"},
    {"source": "source_code", "citation": "strategies/orb/signals.py:generate_signals applies use_trend_filter after raw OR breakout conditions"}
  ],
  "confidence_distribution": {"data": "direct", "literature": "speculative", "precedent": "proxy"},
  "expected_effects": [
    {"metric": "profit_factor", "direction": "increase",
     "magnitude_range": [0.05, 0.20], "unit": "ratio",
     "rationale": "Trend alignment removes lower-quality counter-trend breakouts, raising win rate by a measurable margin."},
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
  "config_changes": {"use_trend_filter": true, "trend_ema_period": 20},
  "requires_code_change": false,
  "requested_primitives": [],
  "deepest_alternative": {
    "mechanism": "ADX>30 entry filter",
    "why_rejected": "Too strict in low-vol regimes per round-3 analyst evidence (citation_2) — would suppress signals where trend alignment still admits continuation trades, costing trade frequency without addressing counter-trend breakouts directly.",
    "tiebreaker": {"kind": "evidence_citation", "value": "citation_2"}
  },
  "if_this_fails_next_thesis": "If this kills, next round tests ADX>30 entry filter in signal_quality dimension (deepest_alternative.mechanism). Drops the trend-filter theme; switches to threshold-based filtering.",
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
  "source_code_verification": "strategies/orb/signals.py:generate_signals — trend filter gates breakouts after raw OR conditions and before final entries are returned."
}
```

## 6. DOCTRINE / OUTPUT separation

DOCTRINE stays field-agnostic. It may describe research judgment, but it must
not name `ResearchThesis` fields, JSON shapes, enum values, counts,
requiredness, or validator behavior. OUTPUT is the only LLM-facing place that
names schema fields and typed contracts.

The one permitted bridge is OUTPUT-side annotation generated by the OUTPUT
renderer from a `_DOCTRINE_BACKREFS: dict[field, principle]` mapping. The
mapping lives outside the prompt prose. It lets OUTPUT say
`(DOCTRINE: <principle>)` where helpful without making DOCTRINE duplicate
schema contracts.

DOCTRINE:
```
- Evidence: external-only is theory; local-only is data dredging. Ground each
  thesis in both outside mechanism evidence and strategy-specific observations
  when available.
```

Generated OUTPUT entry annotation:
```
Producer guidance: ≥1 web_search entry... (DOCTRINE: Evidence)
```

## 7. Programmatic Rendering Contract

The renderer emits `prompts/conductor_output_section.md` from this spec's field
entries and `ResearchThesis.model_fields`, using the compact LLM syntax in
§3.4. The authoring template remains verbose; the rendered prompt is compact.
It omits validator rule prose and round-specific context blocks. Rejection
codes never render in this OUTPUT section; A4c owns the validation sidecar.

## 8. Migration Items Owned Here

- Add/update `Field(description=...)` text needed by the OUTPUT renderer.
- Add `TiebreakerRef`, `DeepestAlternative`, `ExpectedRuntimeSignal`, and
  `ConfidenceDistribution` typed shapes listed in §4.
- Replace `alternatives_considered` with `deepest_alternative` and
  `other_alternatives` in the schema and OUTPUT.
- Redesign `ExpectedEffect` to `{metric, direction, magnitude_range, unit,
  rationale}` for OUTPUT purposes; A4c owns validation of its conditionals.
- Strip schema-field names and typed contracts out of DOCTRINE; keep only
  field-agnostic principles plus generated OUTPUT-side annotations.

## 9. Success Criteria

- Every LLM-facing `ResearchThesis` field has a complete template entry.
- Every rendered field has compact labels `T/F/S/Cap/Req/M/G/Ex`; typed-object
  fields also have `Shape`.
- Every rendered field preserves producer guidance (`G`) and one concrete
  example (`Ex`).
- Every typed-object field has an `Inner shape:` slot.
- The worked example includes every always-required field and every conditional
  field that fires for the fixture context.
- The rendered OUTPUT section contains no round-specific context blocks,
  recent-rejection block, rejection-code catalogue, validator predicate table,
  or implementation notes.
