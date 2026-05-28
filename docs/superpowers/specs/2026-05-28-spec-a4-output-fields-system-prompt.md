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

## 3.1 Category ordering

OUTPUT fields render in this order. Referenced fields come before fields that
reference them, so the LLM emits targets before references:

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

## 3.2 Runtime Context References

Some `Required:` and `Source set:` lines refer to runtime keys such as
`theme_keywords_in_use`, `dimensions_unexplored`, `prior_lever_history`,
`strategy_config_keys`, `prior_theses_snapshot`, and `diagnostic_event_paths`.
Their rendered prompt shape is specified in A4b. This file only names the keys
needed by OUTPUT fields.

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
                                 loosens stop distance only in high-vol regime —
                                 opposite direction, different context."}]
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
    Example:     []
                 # When requires_code_change=true, use:
                 # ["close_confirmed_entry_gate"]
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
                  "claim": "The 1-entry minimum should not apply when the rejected thesis already had a deepest_alternative with a resolving tiebreaker.",
                  "evidence": "The rejected payload included deepest_alternative.tiebreaker={kind: 'mechanism_dimension', value: 'signal_quality'} and no other unresolved references."}
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
    Example:     "If this kills, next round tests ADX>30 entry filter
                  (deepest_alternative.mechanism) in signal_quality dimension.
                  Drops the regime-overlay theme; switches to threshold-based
                  filtering."
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
entries and `ResearchThesis.model_fields`. It omits validator rule prose and
round-specific context blocks. Rejection codes never render in this OUTPUT
section; A4c owns the validation sidecar.

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
- Every typed-object field has an `Inner shape:` slot.
- The worked example includes every always-required field and every conditional
  field that fires for the fixture context.
- The rendered OUTPUT section contains no `ROUND CONTEXT`, `RECENT REJECTIONS`,
  rejection-code catalogue, validator predicate table, or implementation notes.
