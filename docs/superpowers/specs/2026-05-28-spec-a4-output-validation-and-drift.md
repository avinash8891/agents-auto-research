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

## 3. Validator Gate Ownership

The validator is tiered. New A4 rules must be assigned to the tier that owns
the data dependency, not merely to the OUTPUT field category where the field
renders.

Current Stage 1 order is:

1. `process` — validates observed process/tool traces before judging thesis
   content.
2. `research_policy` — validates behavior patterns and research-quality policy
   through behavioral signals.
3. `mechanical` — batches deterministic schema, presence, shape, and reference
   failures.
4. `config_validity` — split ownership: artifact/schema config failures are
   mechanical; repeated-tuning behavior remains research-policy even when the
   rejection code is prefixed `config_validity_*`.

Stage 2 owns post-compile contract resolution. Post-run evaluation owns whether
runtime predictions actually occurred.

### 3.1 A4 Field Rules By Validator Tier

| Field / rule | Validator tier | Implementation owner | Notes |
|---|---|---|---|
| Required process tools, evidence-producing tools, source-code read trace | `process` | `_validate_process` plus attempt-trace extension | Tool/path evidence belongs to process, not field mechanics. |
| `hypothesis`, `mechanism`, `thesis_role`, `theme_keywords` presence/enum/list shape | `mechanical` | `_collect_inline_structural_failures` or generated predicate sidecar | Deterministic thesis-local checks. |
| `mechanism_dimension` membership, including `emergent_dimensions_in_use` | `mechanical` | `_validate_mechanism_dimension` | Uses constants plus A4b runtime context. |
| `dimension_novelty` length and required dimension-name references | `mechanical` | `_collect_inline_structural_failures` plus generated predicate sidecar | Deterministic text/constant-membership check only; do not add free-form semantic judgment here. |
| `novel_connection` required by theme overlap | `mechanical` | `_collect_inline_structural_failures` or generated predicate sidecar | The trigger is deterministic from emitted `theme_keywords` plus A4b context. |
| Theme-cluster fixation across priors | `research_policy` | `_detect_theme_cluster_fixation` | Behavioral pattern gate, not a `theme_keywords` shape rule. |
| `underexplored_dimensions_considered` membership/exclusion | `mechanical` | `_validate_underexplored_dimensions` | Runtime-reference check against A4b context. |
| `deepest_alternative` required and tiebreaker resolution | `mechanical` | new A4 predicate implementation | Tiebreaker references must resolve to citation ids, disqualifier names, or mechanism dimensions. |
| `other_alternatives` count and optional tiebreaker resolution | `mechanical` | new A4 predicate implementation | Replaces the deleted `alternatives_considered` gate. |
| `prior_lever_outcomes` id resolution | `mechanical` | new A4 predicate implementation | Deterministic reference into `prior_theses_snapshot`. |
| Missing `prior_lever_outcomes` when a prior lever is reversed | `research_policy` | `_detect_direction_whipsaw` | Behavioral anti-flip policy; citations are supporting data. |
| `mechanism_lineage` id resolution | `mechanical` | new A4 predicate implementation | Runtime-reference check into `prior_theses_snapshot`. |
| Lineage with repeated same-dimension ancestors requiring pivot/disqualifier | `research_policy` | new behavior signal | This is anti-stagnation research policy, not a JSON-shape rule. |
| `if_this_fails_next_thesis` non-empty and references pivot/deepest alternative | `research_policy` with a mechanical reference predicate if implemented literally | new behavior signal or generated predicate | Its purpose is forward-planning quality; only exact reference resolution is mechanical. |
| Emergent-dimension fields | `mechanical` | `_validate_emergent_dimension` | Conditional schema/runtime-reference contract. |
| `evidence_citations` count, citation length, source diversity | `mechanical` | `_collect_research_contract_failures` or generated predicates | Evidence-source diversity is deterministic. |
| `confidence_distribution` object/enum completeness | `mechanical` | Pydantic plus generated predicate sidecar | Shape only. |
| `confidence_distribution` strength floor / speculative-basis handling | `research_policy` | new behavior signal | Epistemic-quality policy. Replaces the deleted `evidence_strength` gate. |
| `expected_effects` presence, metric backing, magnitude/unit/rationale conditionals | `mechanical` | `_validate_expected_effects_present`, metric-backing loop, new predicates | Metric backing reads `required_diagnostic_specs[*].key`; do not reintroduce deleted `required_diagnostics` as an LLM field. |
| `expected_runtime_signal` shape and event-path resolution | `mechanical` | new A4 predicate implementation | Validates declared path/relation only. |
| Whether `expected_runtime_signal` happened | post-run evaluator | evaluator, not Stage 1 validator | Do not reject a thesis before runtime for an outcome that cannot exist yet. |
| `disqualifiers` count/kind/overfit marker | `mechanical` | existing disqualifier checks plus new count predicate | Replaces the deleted `falsification_or_alternative` gate. |
| Missing mechanism-evidence disqualifier quality | `research_policy` | `_detect_missing_mechanism_evidence_disqualifier` | Behavioral/quality signal. |
| `config_changes` non-empty-or-code-change and unknown keys | `mechanical` / `config_validity` | `_validate_thesis_specifies_change`; new A4b key predicate | Deterministic config contract. |
| Config metadata leak | `config_validity` mechanical | `_collect_mechanical_config_validity_failures` | Deterministic config-shape failure on `config_changes`; base path/base contract inheritance belongs to Stage 2/compiler-contract validation, not Stage 1 thesis JSON. |
| Config-key overlap and neighboring threshold repetition | `research_policy` with `config_validity_*` codes | `_detect_config_key_overlap`, `_detect_neighboring_threshold` | Current redesign routes these through behavior policy. |
| `requires_code_change` / `requested_primitives` shape | `mechanical` | `_validate_thesis_specifies_change` | Deterministic pair contract. |
| Needs-code starvation over prior rounds | `research_policy` | `_detect_needs_code_starvation` | Behavioral pattern gate. |
| `source_code_verification` format/path/symbol | `mechanical` artifact check | `_collect_source_code_verification_failures` | Shape and repo artifact existence. |
| `source_code_verification` path actually read by the attempt | `process` | attempt-trace extension | Requires read-path trace data, not just thesis JSON. |
| `validator_challenge` | none / logging only | challenge persistence path | Meta escape hatch outside `ResearchThesis`; never rejects by itself. |

### 3.2 Legacy Field Gate Removal Required

A4a removes several fields from LLM-emitted thesis JSON. Validator work must
remove direct Stage 1 gates that still enforce those deleted field names and
replace them with the A4 field that owns the same meaning:

| Deleted field gate | Replacement owner |
|---|---|
| `alternatives_considered` | `deepest_alternative` + `other_alternatives` mechanical checks |
| `evidence_strength` | `confidence_distribution` mechanical shape + research-policy strength floor |
| `falsification_or_alternative` | `disqualifiers` mechanical count/kind checks + mechanism-evidence policy |
| `causal_cluster` | `theme_keywords`, computed theme overlap, and `mechanism_lineage` policy |
| `dominant_cluster_overlap` | computed overlap from prior `theme_keywords`; never trust LLM self-report |
| `orthogonality_defense` / closest-prior prose fields | `dimension_novelty`, `underexplored_dimensions_considered`, and `mechanism_lineage` |
| `expected_reuse_across_future_theses` | `mechanism_family_definition` for emergent dimensions only |
| `base_config_path` / `base_contract_id` as Stage 1 thesis fields | Stage 2/compiler-contract validation may still validate compiled contract inheritance; do not keep these as `ResearchThesis` gates |
| `required_diagnostics` as LLM OUTPUT | `required_diagnostic_specs` remains system/compiler-side; Stage 2 may still validate compiled contract diagnostics |

Do not remove `required_diagnostic_specs` or Stage 2 diagnostic validation as
part of this cleanup. A4a omits it from LLM OUTPUT, but the compiler/evaluator
contract still uses it.

### 3.3 Comprehensive Validator Inventory

`(O)` marks validators that operate on the LLM OUTPUT / `ResearchThesis`
contract. `remove` marks current Stage 1 OUTPUT gates that are stale after A4a's
field changes. Validators without `(O)` are adjacent process, compiler-contract,
post-run, or drift validators that still affect the A4 boundary.

#### Process Validators

| Validator | Scope | Owner | Rejection code(s) | Status |
|---|---|---|---|---|
| Required process tools called: `list_round_results`, `web_search`, and `analyze_trades` when analyst evidence is required. | process | `_validate_process` | `process_required_tools_not_called` | keep |
| Source-code path read trace for `source_code_verification`. | process | attempt-trace extension | `process_source_code_not_read`, `process_source_code_path_not_read` | add |

#### Research Policy Validators

| Validator | Scope | Owner | Rejection code(s) | Status |
|---|---|---|---|---|
| Theme-cluster fixation across recent priors. | `(O)` | `_detect_theme_cluster_fixation` | `thesis_quality_theme_cluster_fixation` | keep |
| Direction whipsaw on a prior lever without `prior_lever_outcomes` acknowledgement. | `(O)` | `_detect_direction_whipsaw` | `thesis_quality_direction_whipsaw` | keep / retarget to A4 `prior_lever_outcomes` |
| Missing substantive mechanism-evidence disqualifier. | `(O)` | `_detect_missing_mechanism_evidence_disqualifier` | `thesis_quality_missing_mechanism_evidence_disqualifier` | keep |
| Needs-code starvation after repeated code-change theses. | `(O)` | `_detect_needs_code_starvation` | `thesis_quality_needs_code_starvation` | keep |
| Config-key overlap with prior theses. | `(O)` | `_detect_config_key_overlap` | `config_validity_config_key_overlap_real` | keep as policy-routed config gate |
| Neighboring-threshold parameter nudge. | `(O)` | `_detect_neighboring_threshold` | `config_validity_neighboring_threshold` | keep as policy-routed config gate |
| Repeated same-dimension `mechanism_lineage` without pivot/disqualifier. | `(O)` | new behavior signal | `thesis_quality_lineage_no_structural_pivot` | add |
| Weak `confidence_distribution` / all-speculative basis without mechanism evidence. | `(O)` | new behavior signal | `thesis_quality_confidence_distribution_too_weak` | add |
| Weak or vague `if_this_fails_next_thesis` pre-commitment. | `(O)` | new behavior signal or predicate-backed policy | `thesis_quality_next_thesis_not_pre_committed` | add |

#### Mechanical OUTPUT Validators

| Validator | Scope | Owner | Rejection code(s) | Status |
|---|---|---|---|---|
| Pydantic schema/type validation for `ResearchThesis` and nested OUTPUT objects, including enum fields and `Field(min_length=...)` constraints. | `(O)` | `ResearchThesis.model_validate` and nested Pydantic models | Pydantic `ValidationError` before named gate mapping | keep / extend with A4 nested shapes |
| `thesis_id` non-empty. | `(O)` system-injected | `_collect_inline_structural_failures` | `structural_missing_thesis_id` | keep |
| `hypothesis` non-empty. | `(O)` | `_collect_inline_structural_failures` | `structural_missing_hypothesis` | keep |
| `mechanism` non-empty. | `(O)` | `_collect_inline_structural_failures` | `structural_missing_mechanism` | keep |
| `dimension_novelty` length and grounded dimension references. | `(O)` | `_collect_inline_structural_failures` plus generated predicate | `structural_dimension_novelty_too_short`, `structural_dimension_novelty_not_grounded` | keep / add grounded predicate |
| `mechanism_dimension` non-empty and valid. | `(O)` | `_validate_mechanism_dimension` | `structural_missing_mechanism_dimension`, `structural_mechanism_dimension_invalid` | keep |
| Emergent-dimension conditional fields. | `(O)` | `_validate_emergent_dimension` | `structural_emergent_thesis_malformed`, `structural_new_dimension_name_duplicates_existing` | keep / update deleted field list |
| `underexplored_dimensions_considered` membership and chosen-dimension exclusion. | `(O)` | `_validate_underexplored_dimensions` | `structural_underexplored_dimensions_invalid`, `structural_underexplored_includes_chosen` | keep |
| `novel_connection` required when keyword overlap triggers it. | `(O)` | `_collect_inline_structural_failures` plus generated predicate | `structural_novel_connection_too_short`, `structural_novel_connection_not_grounded` | keep / add grounded predicate |
| `config_changes` non-empty OR `requires_code_change=true`. | `(O)` | `_validate_thesis_specifies_change` | `structural_config_changes_required` | keep with renamed code from current `structural_missing_config_or_code_change` |
| `requested_primitives` non-empty when `requires_code_change=true`. | `(O)` | `_validate_thesis_specifies_change` | `structural_engine_change_request_malformed` | keep with renamed code from current `structural_missing_requested_primitives` |
| `expected_effects` present. | `(O)` | `_validate_expected_effects_present` | `structural_missing_expected_effects` | keep |
| `expected_effects` metric backing through builtin metrics or `required_diagnostic_specs[*].key`. | `(O)` | metric-backing loop / generated predicate | `structural_expected_effect_metric_unbacked` | keep / retarget from deleted `required_diagnostics` |
| `expected_effects` coupling / distinct metrics / rationale. | `(O)` | `_collect_research_contract_failures` plus generated predicates | `structural_expected_effects_not_coupled`, `structural_expected_effect_magnitude_missing`, `structural_expected_effect_magnitude_range_invalid`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required` | keep / add A4 shape conditionals |
| `evidence_citations` context-aware source diversity, count, and citation length. | `(O)` | `_collect_research_contract_failures` plus generated predicates | `structural_evidence_citations_missing_source_diversity`, `structural_evidence_citation_too_short`, `structural_evidence_citations_too_many` | keep / retarget context names |
| `source_code_verification` format, repo path, family path, symbol exists. | `(O)` | `_collect_source_code_verification_failures` | `structural_source_code_verification_too_short`, `structural_source_code_verification_malformed` | keep with split codes from current consolidated code |
| `disqualifiers` count, mechanism-evidence kind, overfit marker. | `(O)` | `_collect_inline_structural_failures` plus generated predicates | `structural_disqualifiers_too_few`, `structural_disqualifiers_no_mechanism_evidence`, `structural_disqualifiers_no_overfit_address` | keep / add count and overfit predicates |
| `deepest_alternative` required and tiebreaker resolution. | `(O)` | new generated predicates | `structural_deepest_alternative_missing`, `structural_deepest_alternative_tiebreaker_unresolved` | add |
| `other_alternatives` count, rejected-reason length, and optional tiebreaker resolution. | `(O)` | new generated predicates | `structural_other_alternatives_too_few`, `structural_lighter_tiebreaker_unresolved` | add |
| `prior_lever_outcomes[*].prior_thesis_id` resolves. | `(O)` | new generated predicate | `structural_prior_lever_outcomes_unknown_id` | add |
| `mechanism_lineage[*]` resolves. | `(O)` | new generated predicate | `structural_mechanism_lineage_unknown_id` | add |
| `confidence_distribution` required object and enum fields. | `(O)` | Pydantic plus generated predicates | `structural_confidence_distribution_missing`, `structural_confidence_distribution_invalid` | add |
| `expected_runtime_signal` shape, event-path resolution, and bound/relation consistency. | `(O)` | new generated predicates | `structural_expected_runtime_signal_invalid` | add |
| `theme_keywords` non-empty. | `(O)` | generated predicate / inline structural extension | `structural_theme_keywords_empty` | add |
| `thesis_role` literal membership. | `(O)` | Pydantic plus generated predicate | `structural_thesis_role_required` | add |
| `alternatives_considered` count and blank-mechanism gate. | `(O)` | `_collect_research_contract_failures` | `structural_alternatives_considered_invalid` | remove; replaced by `deepest_alternative` + `other_alternatives` |
| `evidence_strength` required. | `(O)` | `_collect_research_contract_failures` | `structural_missing_evidence_strength` | remove; replaced by `confidence_distribution` |
| `falsification_or_alternative` length. | `(O)` | `_collect_inline_structural_failures` | `structural_falsification_invalid` | remove; replaced by `disqualifiers` |
| `causal_cluster` required when priors exist. | `(O)` | `_collect_inline_structural_failures` | `structural_missing_causal_cluster` | remove; replaced by `theme_keywords`, computed overlap, and `mechanism_lineage` |
| `expected_reuse_across_future_theses` as emergent required field. | `(O)` | `_validate_emergent_dimension` | `structural_emergent_thesis_malformed` | remove; replaced by `mechanism_family_definition` |

#### Config Validity Validators

| Validator | Scope | Owner | Rejection code(s) | Status |
|---|---|---|---|---|
| Metadata keys leaked into `config_changes`. | `(O)` | `_collect_mechanical_config_validity_failures` | `config_validity_config_changes_metadata_leak` | keep |
| `config_changes` keys resolve against A4b `strategy_config_keys`. | `(O)` | new generated predicate | `structural_config_changes_unknown_key` | add |
| `base_config_path` path syntax / runtime path / inheritance. | compiler contract | `_validate_base_config_path`, `_collect_mechanical_config_validity_failures` today | `config_validity_base_config_path_invalid`, `config_validity_base_config_path_runtime_construction`, `config_validity_base_config_path_inheritance_blocked` | move out of Stage 1 `ResearchThesis` validation |
| `base_contract_id` inheritance blocked. | compiler contract | `_collect_mechanical_config_validity_failures` today | `config_validity_base_contract_id_not_allowed` | move out of Stage 1 `ResearchThesis` validation |

#### Stage 2 / Compiler-Contract Validators

| Validator | Scope | Owner | Rejection code(s) | Status |
|---|---|---|---|---|
| Hypothesis/config alignment against compiled `runtime_config`. | compiler contract | `_detect_stage_2_hypothesis_config_misalignment`, `validate_stage_2` | `hypothesis_config_misalignment` | keep |
| Strategy family missing `key_concepts`, making alignment unenforceable. | compiler contract | `validate_stage_2` | `hypothesis_config_alignment_unconfigured` | keep |
| Required diagnostics resolve through `required_diagnostic_specs` key/alias or `runtime_config`. | compiler contract | `_collect_stage_2_required_diagnostic_failures` | `required_diagnostic_missing_post_compile` | keep |
| Compiled base config inheritance validation. | compiler contract | Stage 2/compiler-contract validation | `config_validity_base_config_path_invalid`, `config_validity_base_config_path_runtime_construction`, `config_validity_base_config_path_inheritance_blocked`, `config_validity_base_contract_id_not_allowed` | add/move here from Stage 1 |

#### Post-run Evaluation Validators

| Validator | Scope | Owner | Rejection code(s) | Status |
|---|---|---|---|---|
| Expected effects pass/fail against backtest metrics. | evaluator | evaluator / `BacktestVerdict` | `passed_effects`, `failed_effects` verdict fields | keep outside Stage 1 |
| Disqualifiers triggered by run results. | evaluator | evaluator / `BacktestVerdict` | `triggered_disqualifiers`, `unparsed_disqualifiers` verdict fields | keep outside Stage 1 |
| Required diagnostics missing after run. | evaluator | evaluator / `BacktestVerdict` | `missing_required_diagnostics` verdict field | keep outside Stage 1 |
| `expected_runtime_signal` actually occurred. | evaluator | future runtime-signal evaluator | TBD verdict/rejection field | add outside Stage 1 |

#### Drift / Prompt Contract Validators

| Validator | Scope | Owner | Failure mode | Status |
|---|---|---|---|---|
| `ResearchThesis.model_fields` match rendered OUTPUT fields except `_PROMPT_OMITTED_FIELDS`. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |
| Validator rejection codes match `prompts/conductor_output_rules.json` except `_PROMPT_OMITTED_RULES`. | drift | `scripts/check_prompt_drift.py` | CI failure | add |
| Rendered OUTPUT has no validator rule prose or rejection-code catalogue. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |
| Rendered fields include compact slots and typed fields include `Shape`. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |
| Rendered `G` guidance preserves decision criteria and is not title-only. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |
| Rendered `M`, `G`, and `Ex` obey A4a compression rules. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |
| Rendered constants come from code constants, not prose-only duplication. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |
| Prompt schema-version stamp matches schema/rules hash. | drift | `scripts/check_prompt_drift.py` | CI failure | add |
| DOCTRINE contains no schema-field names or field-reference arrows. | drift | `scripts/check_prompt_drift.py` | CI failure | keep |

## 4. Consolidated Validator Rules

All validator rules for the A4a OUTPUT fields, in one place. This section is the
authoritative human-readable source; `prompts/conductor_output_rules.json`
(§5) is the machine-readable derivation. None of this content renders into the
LLM-facing prompt.

### 4.1 Core Description

| Field | Rule | Rejection code(s) |
|---|---|---|
| `hypothesis` | Non-empty. | `structural_missing_hypothesis` |
| `mechanism` | Non-empty. | `structural_missing_mechanism` |

### 4.2 Positioning + Classification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `mechanism_dimension` | Must be non-empty and a member of `MECHANISM_DIMENSIONS` or a value in ROUND CONTEXT `emergent_dimensions_in_use`. | `structural_missing_mechanism_dimension`, `structural_mechanism_dimension_invalid` |
| `theme_keywords` | Non-empty list. Cluster-fixation gate: max 3 of last 7 prior theses share any one of these keywords. | `structural_theme_keywords_empty`, `thesis_quality_theme_cluster_fixation` |
| `thesis_role` | Non-empty; Literal restricts to the three role values. | `structural_thesis_role_required` |

### 4.3 Novelty Justification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `dimension_novelty` | Length ≥30 chars AND must mention ≥2 distinct dimension names from `MECHANISM_DIMENSIONS` (forces contrast — own choice + prior dimension). | `structural_dimension_novelty_too_short`, `structural_dimension_novelty_not_grounded` |
| `novel_connection` | Post-emit conditional: required when ≥1 emitted `theme_keywords` entry appears in ROUND CONTEXT `theme_keywords_in_use`. When required: length ≥120 chars AND must mention a shared keyword by name OR a structurally-distinct `mechanism_dimension`. | `structural_novel_connection_too_short`, `structural_novel_connection_not_grounded` |
| `underexplored_dimensions_considered` | Required when ROUND CONTEXT `dimensions_unexplored` is non-empty. Each entry must be present in `dimensions_unexplored` AND must not equal this thesis's `mechanism_dimension`. | `structural_underexplored_dimensions_invalid`, `structural_underexplored_includes_chosen` |

### 4.4 Alternatives + Prior Work

| Field | Rule | Rejection code(s) |
|---|---|---|
| `deepest_alternative` | Required, non-null. `tiebreaker.value` resolves by exact match: `kind="evidence_citation"` → `citation_N` where `1 ≤ N ≤ len(evidence_citations)`; `kind="disqualifier"` → must equal a `disqualifiers[i].name`; `kind="mechanism_dimension"` → must be a member of `MECHANISM_DIMENSIONS`. | `structural_deepest_alternative_missing`, `structural_deepest_alternative_tiebreaker_unresolved` |
| `other_alternatives` | ≥1 entry; each `why_rejected` ≥40 chars. When `lighter_tiebreaker` is non-null, it resolves by the same rules as `deepest_alternative.tiebreaker`. | `structural_other_alternatives_too_few`, `structural_lighter_tiebreaker_unresolved` |
| `prior_lever_outcomes` | Required when ANY key in `config_changes` appears in any `prior_lever_history[i].config_keys` AND your derived direction differs from `prior_lever_history[i].direction`. `prior_thesis_id` values must exist in ROUND CONTEXT `prior_theses_snapshot`. | `thesis_quality_direction_whipsaw`, `structural_prior_lever_outcomes_unknown_id` |
| `mechanism_lineage` | `thesis_id` entries must appear in ROUND CONTEXT `prior_theses_snapshot`. With ≥3 ancestors sharing the same `mechanism_dimension`, require either (a) a different `mechanism_dimension` on this thesis, OR (b) a `disqualifiers` entry with `kind="mechanism_evidence"` that distinguishes this thesis from the lineage's prior failures. | `structural_mechanism_lineage_unknown_id`, `thesis_quality_lineage_no_structural_pivot` |
| `if_this_fails_next_thesis` | Non-empty; must reference either a specific `mechanism_dimension` (different from current) OR the `mechanism` text of `deepest_alternative`. | `thesis_quality_next_thesis_not_pre_committed` |

### 4.5 Emergent-Dimension Contract

All three conditional on `mechanism_dimension == "emergent"`.

| Field | Rule | Rejection code(s) |
|---|---|---|
| `new_dimension_name` | When emergent: non-empty; not in `MECHANISM_DIMENSIONS`; not in ROUND CONTEXT `emergent_dimensions_in_use`. | `structural_emergent_thesis_malformed`, `structural_new_dimension_name_duplicates_existing` |
| `why_existing_dimensions_do_not_fit` | When emergent: non-empty (length ≥80 chars). | `structural_emergent_thesis_malformed` |
| `mechanism_family_definition` | When emergent: non-empty (length ≥80 chars). | `structural_emergent_thesis_malformed` |

### 4.6 Evidence

| Field | Rule | Rejection code(s) |
|---|---|---|
| `evidence_citations` | ≥2 entries; ≤6 entries; required source coverage depends on evidence context: trades → `web_search` + `analyst`; no-trades → `web_search` + `experiment_result`; cold-start → `web_search`. Each `citation` ≥30 chars. | `structural_evidence_citations_missing_source_diversity`, `structural_evidence_citation_too_short`, `structural_evidence_citations_too_many` |
| `confidence_distribution` | Required object with `data`, `literature`, and `precedent`. At least one of `{data, literature}` must be `"direct"` or `"mixed"`. Theses with all three `"speculative"` require a `disqualifiers` entry with `kind="mechanism_evidence"` acknowledging the weak-evidence basis. Greenfield exemption: when ROUND CONTEXT `dimensions_already_explored` is empty, `precedent="speculative"` is not counted against the gate. | `structural_confidence_distribution_missing`, `structural_confidence_distribution_invalid`, `thesis_quality_confidence_distribution_too_weak` |

### 4.7 Predictions + Falsification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `expected_effects` | Non-empty list with ≥2 distinct metrics. Each non-builtin metric must resolve through `required_diagnostic_specs[*].key`. `magnitude_range` required when `direction in {"increase","decrease"}`; `unit` required and must be a member of `EXPECTED_EFFECT_UNITS` when `magnitude_range` is set; `rationale` required (≥40 chars) when `direction in {"increase","decrease"}`. `magnitude_range[0] < magnitude_range[1]` when set. | `structural_missing_expected_effects`, `structural_expected_effects_not_coupled`, `structural_expected_effect_metric_unbacked`, `structural_expected_effect_magnitude_missing`, `structural_expected_effect_magnitude_range_invalid`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required` |
| `expected_runtime_signal` | Each `event_path` must resolve in ROUND CONTEXT `diagnostic_event_paths`. `lower` set when `expected_relation in {">", ">=", "==", "in_range"}`; `upper` set when `expected_relation in {"<", "<=", "==", "in_range"}`. | `structural_expected_runtime_signal_invalid` |
| `disqualifiers` | ≥2 entries; ≥1 with `kind="mechanism_evidence"`; ≥1 entry whose `name` is in `OVERFIT_DISQUALIFIER_MARKERS` OR whose `condition` (lowercased) contains a member of `OVERFIT_KEYWORD_HINTS`. A single entry may satisfy both `mechanism_evidence` and overfit requirements. | `structural_disqualifiers_too_few`, `structural_disqualifiers_no_mechanism_evidence`, `structural_disqualifiers_no_overfit_address` |

### 4.8 Config + Engine

| Field | Rule | Rejection code(s) |
|---|---|---|
| `config_changes` | Non-empty OR `requires_code_change=true`. Each key must appear in ROUND CONTEXT `strategy_config_keys`. | `structural_config_changes_required`, `structural_config_changes_unknown_key` |
| `requires_code_change` | When `true`, `requested_primitives` must be non-empty. | `structural_engine_change_request_malformed` |
| `requested_primitives` | Non-empty paired with `requires_code_change=true`. | `structural_engine_change_request_malformed` |

### 4.9 Diagnostics + Code Grounding

| Field | Rule | Rejection code(s) |
|---|---|---|
| `source_code_verification` | Length ≥40 chars; format matches `"<path>:<symbol> — <prose>"`; the cited path must be a repo-relative strategy Python file and the symbol must exist. | `structural_source_code_verification_too_short`, `structural_source_code_verification_malformed` |
| source-code read trace | The cited `<path>` from `source_code_verification` must appear in the conductor attempt's read-path trace captured from source-reading tools. | `process_source_code_not_read`, `process_source_code_path_not_read` |

### 4.10 Escape Hatch

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

### 4.11 Notes For The Implementer

- `prompts/conductor_output_rules.json` is generated from this section + §5.1's `predicate_kind` mapping. Every rule above maps to one or more `predicate_kind` rows in §5.1.
- The `_PROMPT_OMITTED_RULES` set in `check_prompt_drift.py` covers any rejection code the validator emits internally that isn't in this section (e.g. duplicate-thesis-id checks across rounds — system-level, not field-level).

---

## 5. Structured Rule Metadata

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

### 5.1 Predicate Kinds

Every validator rule line in §4 reduces to a `predicate_kind` from this table.
The renderer fails if a §4 rule cannot be expressed in this set.

| predicate_kind | required_args | data_dependencies | covers |
|---|---|---|---|
| `non_empty` | `{field}` | thesis | structural_missing_* |
| `required_object` | `{field}` | thesis | structural_deepest_alternative_missing, structural_confidence_distribution_missing |
| `min_length` | `{field, min}` | thesis | _too_short codes |
| `nested_min_length` | `{field, nested_field, min}` | thesis | structural_evidence_citation_too_short |
| `literal_membership` | `{field, constant_name}` | thesis + constant import | structural_mechanism_dimension_invalid, structural_thesis_role_required, structural_expected_effect_unit_invalid |
| `tiebreaker_resolves` | `{field, lookup_tables}` | thesis | structural_deepest_alternative_tiebreaker_unresolved, structural_lighter_tiebreaker_unresolved |
| `list_min_length` | `{field, min}` | thesis | structural_other_alternatives_too_few, structural_disqualifiers_too_few |
| `list_max_length` | `{field, max}` | thesis | structural_evidence_citations_too_many |
| `list_min_with_kind` | `{field, kind_field, kind_value, min}` | thesis | structural_disqualifiers_no_mechanism_evidence |
| `list_any_matches_marker_or_keyword` | `{field, name_field, condition_field, markers_constant, keywords_constant}` | thesis + constants | structural_disqualifiers_no_overfit_address |
| `list_source_diversity_by_context` | `{field, source_field, evidence_context_field, required_sources_by_context}` | thesis + evidence_context | structural_evidence_citations_missing_source_diversity |
| `list_members_in_round_context_set` | `{field, round_context_key}` | thesis + round_context | structural_underexplored_dimensions_invalid |
| `list_members_not_equal` | `{field, ref_field}` | thesis | structural_underexplored_includes_chosen |
| `grounded_mention_distinct_count` | `{field, constant_name, min_distinct}` | thesis + constant | structural_dimension_novelty_not_grounded |
| `theme_keyword_overlap_triggers_field` | `{trigger_field, target_field, round_context_key}` | thesis + round_context | structural_novel_connection_too_short, structural_novel_connection_not_grounded |
| `whipsaw_from_prior_lever_history` | `{config_changes_field, target_field, round_context_key}` | thesis + round_context | thesis_quality_direction_whipsaw |
| `prior_thesis_ids_in_snapshot` | `{field, round_context_key}` | thesis + round_context | structural_prior_lever_outcomes_unknown_id, structural_mechanism_lineage_unknown_id |
| `lineage_pivot_required` | `{lineage_field, dimension_field, ancestors_round_context_key, disqualifiers_field, min_ancestors}` | thesis + round_context | thesis_quality_lineage_no_structural_pivot (pivot check) |
| `conditional_field_contract` | `{trigger_field, trigger_value, required_fields, min_lengths}` | thesis | structural_emergent_thesis_malformed |
| `field_not_in_constant_or_context` | `{field, constant_name, round_context_key}` | thesis + constants + round_context | structural_new_dimension_name_duplicates_existing |
| `event_path_resolves` | `{field, round_context_key}` | thesis + round_context | structural_expected_runtime_signal_invalid |
| `config_keys_in_round_context_set` | `{field, round_context_key}` | thesis + round_context | structural_config_changes_unknown_key |
| `non_empty_mapping_or_true_flag` | `{mapping_field, flag_field}` | thesis | structural_config_changes_required |
| `true_flag_requires_non_empty_list` | `{flag_field, list_field}` | thesis | structural_engine_change_request_malformed |
| `expected_effect_metric_backed` | `{effects_field, builtin_metrics_constant, diagnostic_specs_field}` | thesis + constants | structural_expected_effect_metric_unbacked |
| `list_distinct_field_min` | `{field, nested_field, min}` | thesis | structural_expected_effects_not_coupled |
| `magnitude_range_required_for_direction` | `{effects_field, direction_values_requiring_range}` | thesis | structural_expected_effect_magnitude_missing |
| `magnitude_range_well_formed` | `{effects_field}` | thesis | structural_expected_effect_magnitude_range_invalid |
| `unit_required_when_magnitude_set` | `{effects_field, unit_constant}` | thesis + constant | structural_expected_effect_unit_invalid |
| `rationale_required_for_directional` | `{effects_field, direction_values_requiring_rationale, min_len}` | thesis | structural_expected_effect_rationale_required |
| `required_object_fields` | `{field, required_fields}` | thesis | structural_confidence_distribution_invalid |
| `confidence_strength_floor` | `{confidence_field, strong_values, greenfield_round_context_key}` | thesis + round_context | thesis_quality_confidence_distribution_too_weak |
| `next_thesis_references_pivot_or_deepest` | `{field, dimension_field, deepest_alternative_field}` | thesis | thesis_quality_next_thesis_not_pre_committed |
| `source_code_verification_shape` | `{field}` | thesis + repository | structural_source_code_verification_too_short, structural_source_code_verification_malformed |
| `path_in_read_trace` | `{field, attempt_trace_key, path_extractor}` | thesis + attempt_trace | process_source_code_path_not_read |
| `tool_invoked_in_trace` | `{tool_name, attempt_trace_key}` | attempt_trace | process_source_code_not_read |

Process-tier predicates (`path_in_read_trace`, `tool_invoked_in_trace`)
require the validator to receive the attempt's tool-call trace alongside
the thesis — see §7 migration items for `ConductorResult.read_paths`.

## 6. Programmatic Regeneration

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

## 6.1 Validation Fixtures

The worked positive fixture lives at
`tests/fixtures/conductor_prompt_worked_example.json`. The test suite asserts
the fixture passes Pydantic validation, the live `validate_thesis_dict(...)`
call, and every rule declared in `prompts/conductor_output_rules.json`.

A negative-fixture directory `tests/fixtures/conductor_prompt_rejections/`
holds one fixture per rejection code, each minimally violating one rule. Each
negative fixture must trip exactly its named rejection code and no unrelated
code.

## 7. Migration Items Owned Here

- Implement all rejection codes listed in §4.
- Remove Stage 1 validator references to deleted LLM OUTPUT fields listed in
  §3.2 and replace them with the A4 replacement-owner gates.
- Move any remaining `base_config_path` / `base_contract_id` inheritance checks
  out of Stage 1 `ResearchThesis` validation and into Stage 2/compiler-contract
  validation.
- Align evidence-context source names with `EvidenceSource`; no-trades evidence
  uses `experiment_result`, not `round_result`.
- Generate and commit `prompts/conductor_output_rules.json`.
- Add `prompt_rules.py` exposing `iter_prompt_declared_rules()`.
- Extend `scripts/check_prompt_drift.py` for schema-prompt parity,
  validator-sidecar parity, no-rule-leakage-in-prompt, compact-slot
  completeness, M/G/Ex compression integrity, constants-in-prompt,
  schema-version stamp, and sidecar freshness.
- Add positive and negative prompt fixtures; each negative fixture trips exactly
  its named rejection code.
- Thread attempt trace data needed by process-tier predicates, including
  `read_strategy_source` paths, into validator calls.

## 8. CI Drift Detection

Checks in CI via `scripts/check_prompt_drift.py`:

1. Every `ResearchThesis.model_fields` key appears in the rendered OUTPUT
   section unless listed in `_PROMPT_OMITTED_FIELDS`.
2. Every rejection code emitted by `thesis_validator.py` appears in
   `prompts/conductor_output_rules.json` unless listed in `_PROMPT_OMITTED_RULES`.
3. The rendered OUTPUT prompt contains no `Validator rule:` lines and no
   rejection-code catalogue.
4. Every rendered field contains compact slots `T`, `F`, `S`, `Cap`, `Req`,
   `M`, `G`, and `Ex`; every typed-object field also contains `Shape`.
5. Every rendered field preserves producer guidance in `G`; empty or
   title-only guidance fails drift. `G` must retain decision criteria,
   required reference behavior, source constraints, and boundary examples from
   A4a's authoring entry.
6. Rendered `M`, `G`, and `Ex` follow A4a's compression rules: compact text is
   allowed, but missing meaning, missing guidance criteria, or shape-mismatched
   examples fail drift.
7. Every enum/marker list rendered in OUTPUT comes from a tuple/frozenset
   constant, not prose-only duplication.
8. `_build_conductor_system_prompt` includes a schema-version stamp computed
   from `ResearchThesis.model_fields` and the rules sidecar hash.
9. The checked-in rules sidecar matches fresh regeneration.
10. DOCTRINE prose contains no `ResearchThesis.model_fields` names and no
   `-> see <field>` / `→ see <field>` references.

## 9. Success Criteria

- Positive fixture passes Pydantic, live `validate_thesis_dict(...)`, and every
  prompt-declared predicate.
- Each negative fixture trips exactly its expected rejection code.
- `python scripts/check_prompt_drift.py` exits 0.
- Rejection codes never appear in the rendered LLM-facing OUTPUT section.
