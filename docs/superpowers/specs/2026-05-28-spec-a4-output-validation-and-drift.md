# Spec A4c — Conductor OUTPUT Validation And Drift

**Date:** 2026-05-28
**Status:** Design — split from Spec A4
**Reference:** A4a OUTPUT fields and A4b runtime context keys.
**Depends on:** A4a for fields; A4b for round context data dependencies.

---

## 1. Goal

Define how generated OUTPUT fields are validated and kept in sync with schema,
validator code, runtime context, and rendered prompt artifacts. This spec owns
validator rules, predicate metadata, generated sidecars, drift detection, and
validation fixtures.

## 2. Non-goals

- LLM-facing field wording and examples — see A4a.
- Per-round runtime prompt content — see A4b.
- Showing rejection-code catalogues to the LLM. Rejection codes reach the LLM
  only through runtime retry feedback such as A4b's `RECENT REJECTIONS` block.

## 3. Validator Rules

## 3.1 Consolidated Validator Rules

All validator rules for the A4a OUTPUT fields, in one place. This section is the
authoritative human-readable source; `prompts/conductor_output_rules.json`
(§4) is the machine-readable derivation. None of this content renders into the
LLM-facing prompt.

### 3.1.1 Core Description

| Field | Rule | Rejection code(s) |
|---|---|---|
| `hypothesis` | Non-empty. | `structural_missing_hypothesis` |
| `mechanism` | Non-empty. | `structural_missing_mechanism` |

### 3.1.2 Positioning + Classification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `mechanism_dimension` | Must be a member of `MECHANISM_DIMENSIONS` or a value in ROUND CONTEXT `emergent_dimensions_in_use`. | `structural_invalid_mechanism_dimension` |
| `theme_keywords` | Non-empty list. Cluster-fixation gate: max 3 of last 7 prior theses share any one of these keywords. | `structural_theme_keywords_empty`, `thesis_quality_theme_cluster_fixation` |
| `thesis_role` | Non-empty; Literal restricts to the three role values. | `structural_thesis_role_required` |

### 3.1.3 Novelty Justification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `dimension_novelty` | Length ≥30 chars AND must mention ≥2 distinct dimension names from `MECHANISM_DIMENSIONS` (forces contrast — own choice + prior dimension). | `structural_dimension_novelty_too_short`, `thesis_quality_dimension_novelty_not_grounded` |
| `novel_connection` | Post-emit conditional: required when ≥1 emitted `theme_keywords` entry appears in ROUND CONTEXT `theme_keywords_in_use`. When required: length ≥120 chars AND must mention a shared keyword by name OR a structurally-distinct `mechanism_dimension`. | `structural_novel_connection_too_short`, `thesis_quality_novel_connection_not_grounded` |
| `underexplored_dimensions_considered` | Required when ROUND CONTEXT `dimensions_unexplored` is non-empty. Each entry must be present in `dimensions_unexplored` AND must not equal this thesis's `mechanism_dimension`. | `structural_underexplored_dimensions_invalid`, `structural_underexplored_includes_chosen` |

### 3.1.4 Alternatives + Prior Work

| Field | Rule | Rejection code(s) |
|---|---|---|
| `deepest_alternative` | Required, non-null. `tiebreaker.value` resolves by exact match: `kind="evidence_citation"` → `citation_N` where `1 ≤ N ≤ len(evidence_citations)`; `kind="disqualifier"` → must equal a `disqualifiers[i].name`; `kind="mechanism_dimension"` → must be a member of `MECHANISM_DIMENSIONS`. | `structural_deepest_alternative_missing`, `structural_deepest_alternative_tiebreaker_unresolved` |
| `other_alternatives` | ≥1 entry; each `why_rejected` ≥40 chars. When `lighter_tiebreaker` is non-null, it resolves by the same rules as `deepest_alternative.tiebreaker`. | `structural_other_alternatives_too_few`, `structural_lighter_tiebreaker_unresolved` |
| `prior_lever_outcomes` | Required when ANY key in `config_changes` appears in any `prior_lever_history[i].config_keys` AND your derived direction differs from `prior_lever_history[i].direction`. `prior_thesis_id` values must exist in ROUND CONTEXT `prior_theses_snapshot`. | `structural_direction_whipsaw_uncited`, `structural_prior_lever_outcomes_unknown_id` |
| `mechanism_lineage` | `thesis_id` entries must appear in ROUND CONTEXT `prior_theses_snapshot`. With ≥3 ancestors sharing the same `mechanism_dimension`, require either (a) a different `mechanism_dimension` on this thesis, OR (b) a `disqualifiers` entry with `kind="mechanism_evidence"` that distinguishes this thesis from the lineage's prior failures. | `thesis_quality_lineage_no_structural_pivot` |
| `if_this_fails_next_thesis` | Non-empty; must reference either a specific `mechanism_dimension` (different from current) OR the `mechanism` text of `deepest_alternative`. | `thesis_quality_next_thesis_not_pre_committed` |

### 3.1.5 Emergent-Dimension Contract

All three conditional on `mechanism_dimension == "emergent"`.

| Field | Rule | Rejection code(s) |
|---|---|---|
| `new_dimension_name` | When emergent: non-empty; not in `MECHANISM_DIMENSIONS`; not in ROUND CONTEXT `emergent_dimensions_in_use`. | `structural_new_dimension_name_duplicates_existing` |
| `why_existing_dimensions_do_not_fit` | When emergent: non-empty (length ≥80 chars). | `structural_emergent_thesis_malformed` |
| `mechanism_family_definition` | When emergent: non-empty (length ≥80 chars). | `structural_emergent_thesis_malformed` |

### 3.1.6 Evidence

| Field | Rule | Rejection code(s) |
|---|---|---|
| `evidence_citations` | ≥2 entries; ≤6 entries; ≥1 with `source="web_search"` AND ≥1 with `source="analyst"`; each `citation` ≥30 chars. | `structural_evidence_citations_missing_source_diversity`, `structural_evidence_citation_too_short` |
| `confidence_distribution` | At least one of `{data, literature}` must be `"direct"` or `"mixed"`. Theses with all three `"speculative"` require a `disqualifiers` entry with `kind="mechanism_evidence"` acknowledging the weak-evidence basis. Greenfield exemption: when ROUND CONTEXT `dimensions_already_explored` is empty, `precedent="speculative"` is not counted against the gate. | `thesis_quality_confidence_distribution_too_weak`, `thesis_quality_confidence_distribution_missing` |

### 3.1.7 Predictions + Falsification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `expected_effects` | Non-empty list (≥2 entries recommended); `magnitude_range` required when `direction in {"increase","decrease"}`; `unit` required and must be a member of `EXPECTED_EFFECT_UNITS` when `magnitude_range` is set; `rationale` required (≥40 chars) when `direction in {"increase","decrease"}`. `magnitude_range[0] < magnitude_range[1]` when set. | `structural_missing_expected_effects`, `structural_expected_effect_magnitude_missing`, `structural_expected_effect_magnitude_range_invalid`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required` |
| `expected_runtime_signal` | Each `event_path` must resolve in ROUND CONTEXT `diagnostic_event_paths`. `lower` set when `expected_relation in {">", ">=", "==", "in_range"}`; `upper` set when `expected_relation in {"<", "<=", "==", "in_range"}`. | `thesis_quality_expected_runtime_signal_path_unknown` |
| `disqualifiers` | ≥2 entries; ≥1 with `kind="mechanism_evidence"`; ≥1 entry whose `name` is in `OVERFIT_DISQUALIFIER_MARKERS` OR whose `condition` (lowercased) contains a member of `OVERFIT_KEYWORD_HINTS`. A single entry may satisfy both `mechanism_evidence` and overfit requirements. | `structural_disqualifiers_too_few`, `structural_disqualifiers_no_mechanism_evidence`, `structural_disqualifiers_no_overfit_address` |

### 3.1.8 Config + Engine

| Field | Rule | Rejection code(s) |
|---|---|---|
| `config_changes` | Non-empty OR `requires_code_change=true`. Each key must appear in ROUND CONTEXT `strategy_config_keys`. | `structural_config_changes_required`, `structural_config_changes_unknown_key` |
| `requires_code_change` | When `true`, `requested_primitives` must be non-empty. | `structural_engine_change_request_malformed` |
| `requested_primitives` | Non-empty paired with `requires_code_change=true`. | `structural_engine_change_request_malformed` |

### 3.1.9 Diagnostics + Code Grounding

| Field | Rule | Rejection code(s) |
|---|---|---|
| `source_code_verification` | Length ≥40 chars; format matches `"<path>:<symbol> — <prose>"`; the cited `<path>` must appear in the conductor attempt's read-paths trace (captured from `read_strategy_source` invocations). | `structural_source_code_verification_too_short`, `structural_source_code_verification_malformed`, `process_source_code_not_read`, `process_source_code_path_not_read` |

### 3.1.10 Escape Hatch

| Field | Rule | Rejection code(s) |
|---|---|---|
| `validator_challenge` | No rule; accepts any object. Logged for human review only. | none |

`validator_challenge` is a meta-field outside `ResearchThesis`, used only when
the LLM believes a recent rejection was wrong. It has this shape:

```json
{
  "challenged_round": 3,
  "challenged_thesis_id": "job-1-round-3-attempt-2",
  "challenged_rejection_code": "structural_other_alternatives_too_few",
  "claim": "The 1-entry minimum should not apply when the rejected thesis already had a deepest_alternative with a resolving tiebreaker.",
  "evidence": "The rejected payload included deepest_alternative.tiebreaker={kind: 'mechanism_dimension', value: 'signal_quality'} and no other unresolved references."
}
```

Guidance: use sparingly. It is logged for human review and does not alter the
validator's decision.

### 3.1.11 Notes For The Implementer

- `prompts/conductor_output_rules.json` is generated from this section + §4.1's `predicate_kind` mapping. Every rule above maps to one or more `predicate_kind` rows in §4.1.
- The `_PROMPT_OMITTED_RULES` set in `check_prompt_drift.py` covers any rejection code the validator emits internally that isn't in this section (e.g. duplicate-thesis-id checks across rounds — system-level, not field-level).

---

## 4. Structured Rule Metadata

The OUTPUT renderer emits, alongside `prompts/conductor_output_section.md`, a
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

### 4.1 Predicate Kinds

Every validator rule line in A4a reduces to a `predicate_kind` from this
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
the thesis — see §6 migration items for `ConductorResult.read_paths`.

## 5. Programmatic Regeneration

The OUTPUT section is machine-generated from `ResearchThesis` by a new
script `scripts/render_output_schema.py`. The script:

- Introspects `ResearchThesis.model_fields`.
- For each field, reads the Pydantic type annotation, default, and `Field(description=...)`.
- Renders each field per A4a's compact LLM syntax (`T/F/S/Cap/Req/M/G/Ex`,
  plus `Shape` for typed objects) and category order, omitting validator rule
  prose from the LLM-facing prompt.
- Renders `EVIDENCE_SOURCES` (full enum) and `EVIDENCE_SOURCES_FOR_DIVERSITY_GATE` (subset) as two distinct lines under `evidence_citations`.
- Resolves enum/marker lists by importing constants (`PRIOR_LEVER_OUTCOMES`, `OVERFIT_DISQUALIFIER_MARKERS`, etc.) — never inlines them in prose.
- Emits `prompts/conductor_output_section.md` (LLM-facing) and `prompts/conductor_output_rules.json` (validator/test machine source). Rejection codes appear in the sidecar only.

The rendered files are checked into git for diff visibility. CI re-runs the
regenerator and fails if the checked-in files are stale.

## 5.1 Validation Fixtures

The worked positive fixture lives at
`tests/fixtures/conductor_prompt_worked_example.json`. The test suite asserts
the fixture passes Pydantic validation, the live `validate_thesis_dict(...)`
call, and every rule declared in `prompts/conductor_output_rules.json`.

A negative-fixture directory `tests/fixtures/conductor_prompt_rejections/`
holds one fixture per rejection code, each minimally violating one rule. Each
negative fixture must trip exactly its named rejection code and no unrelated
code.

## 6. Migration Items Owned Here

- Implement all rejection codes listed in §3.
- Generate and commit `prompts/conductor_output_rules.json`.
- Add `prompt_rules.py` exposing `iter_prompt_declared_rules()`.
- Extend `scripts/check_prompt_drift.py` for schema-prompt parity,
  validator-sidecar parity, no-rule-leakage-in-prompt, category ordering,
  compact-slot completeness, constants-in-prompt, schema-version stamp, and
  sidecar freshness.
- Add positive and negative prompt fixtures; each negative fixture trips exactly
  its named rejection code.
- Thread attempt trace data needed by process-tier predicates, including
  `read_strategy_source` paths, into validator calls.

## 7. CI Drift Detection

Checks in CI via `scripts/check_prompt_drift.py`:

1. Every `ResearchThesis.model_fields` key appears in the rendered OUTPUT
   section unless listed in `_PROMPT_OMITTED_FIELDS`.
2. Every rejection code emitted by `thesis_validator.py` appears in
   `prompts/conductor_output_rules.json` unless listed in `_PROMPT_OMITTED_RULES`.
3. The rendered OUTPUT prompt contains no `Validator rule:` lines and no
   rejection-code catalogue.
4. Referenced-field categories render before referencing-field categories.
5. Every rendered field contains compact slots `T`, `F`, `S`, `Cap`, `Req`,
   `M`, `G`, and `Ex`; every typed-object field also contains `Shape`.
6. Every rendered field preserves producer guidance in `G`; empty or
   title-only guidance fails drift.
7. Every enum/marker list rendered in OUTPUT comes from a tuple/frozenset
   constant, not prose-only duplication.
8. `_build_conductor_system_prompt` includes a schema-version stamp computed
   from `ResearchThesis.model_fields` and the rules sidecar hash.
9. The checked-in rules sidecar matches fresh regeneration.
10. DOCTRINE prose contains no `ResearchThesis.model_fields` names and no
   `-> see <field>` / `→ see <field>` references.

## 8. Success Criteria

- Positive fixture passes Pydantic, live `validate_thesis_dict(...)`, and every
  prompt-declared predicate.
- Each negative fixture trips exactly its expected rejection code.
- `python scripts/check_prompt_drift.py` exits 0.
- Rejection codes never appear in the rendered LLM-facing OUTPUT section.
