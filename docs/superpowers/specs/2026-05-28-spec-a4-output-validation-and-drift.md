# Spec A4c — Conductor OUTPUT Validation And Drift

**Date:** 2026-05-28
**Status:** Design — split from Spec A4
**Reference:** A4a OUTPUT system-prompt fields and A4b runtime context keys.
**Depends on:** A4a for fields; A4b for round context data dependencies.

---

## 1. Goal

Define how the A4a OUTPUT system-prompt JSON contract is validated before a run
and kept in sync with schema, validator code, runtime context, and rendered
prompt artifacts.

This spec is broader than the system-prompt OUTPUT text itself: it owns pre-run
validator rules for emitted `ResearchThesis` JSON, adjacent process and
compiler-contract gates, predicate metadata, generated sidecars, drift
detection, validation fixtures, and validator retry-feedback behavior.
Rejection-code catalogues never render into the LLM-facing A4a OUTPUT section;
rejection codes reach the LLM only through runtime retry feedback such as A4b's
`RECENT REJECTIONS` block.

## 2. Non-goals

- LLM-facing field wording and examples — see A4a.
- Per-round runtime prompt content — see A4b.
- Post-run outcome evaluation of accepted theses — see A4d.

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

Stage 2 owns post-compile contract resolution needed before execution. A4d owns
whether predictions actually occurred after execution.

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
| `expected_effects` presence, coupled-metric count, magnitude/unit/rationale conditionals | `mechanical` | `_validate_expected_effects_present`, new predicates | A4a leaves `expected_effects[*].metric` source set free and omits diagnostic-spec authoring from OUTPUT. Do not run a Stage 1 OUTPUT gate that requires deleted `required_diagnostics` or omitted `required_diagnostic_specs`. |
| `expected_runtime_signal` shape and event-path resolution | `mechanical` | new A4 predicate implementation | Validates declared path/relation only. |
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
part of this cleanup. A4a omits it from LLM OUTPUT until diagnostic-spec fields
are separately defined, but the compiler/evaluator contract may still use
system/compiler-generated specs after Stage 1 OUTPUT validation.

### 3.3 Comprehensive Validator Inventory

`(O)` marks validators that operate on the LLM OUTPUT / `ResearchThesis`
contract. `remove` marks current Stage 1 OUTPUT gates that are stale after A4a's
field changes. Validators without `(O)` are adjacent process, compiler-contract,
or drift validators that still affect the A4 boundary.

Every validator gate in this inventory must define:

- **Applicability:** whether the gate is always required, conditional,
  optional-if-present, system-injected, compiler-contract, CI-only, or removed.
- **Logic:** the exact data read and condition checked by code.
- **Feedback:** the concise retry message returned to the research conductor in
  `RECENT REJECTIONS`. Feedback must name the field or process behavior to fix,
  but must not dump internal stack traces or validator implementation details.

Applicability values:

- `always_required` — every thesis attempt must satisfy this gate.
- `conditional_required` — the gate is mandatory only when its trigger
  condition is true; otherwise persist `skipped_not_applicable`.
- `optional_if_present` — absence is valid; if the field/value is present, the
  gate validates it.
- `system_injected` — validates system/compiler-added data, not LLM-authored
  repairable OUTPUT.
- `compiler_contract` — runs after compilation, not as Stage 1 OUTPUT
  validation.
- `ci_always` — CI/operator drift gate, not per-thesis runtime validation.
- `removed` — stale legacy gate that must not run after A4a.

### 3.4 Per-Attempt Gate Persistence

The database is the authoritative audit trail for validator decisions and
validator challenges. Files such as
`runtime/jobs/job-N/research/round-M/theses/<thesis_id>/rejection.json` and
`runtime/jobs/job-N/research/round-M/theses/<thesis_id>/challenge.json` are
derived read artifacts for prompt rendering and debugging; they must not be the
only durable place where rejection gates, acceptance gates, retry feedback, or
challenge claims live.

Persist one DB row per validator gate per thesis attempt. The storage contract
must answer, for every attempt:

- which validator gates were evaluated;
- which gates passed, warned, rejected, were skipped as not applicable, or were
  not reached because an earlier gate rejected;
- which gate or gates caused the final rejection;
- the exact feedback string sent back to the research conductor for rejected
  attempts.

Required DB shape:

```sql
CREATE TABLE IF NOT EXISTS research_thesis_attempt_gate_results (
    thesis_attempt_id TEXT NOT NULL,
    research_round_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    thesis_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    validator_tier TEXT NOT NULL,
    gate_id TEXT NOT NULL,
    applicability TEXT NOT NULL,
    rejection_code TEXT NOT NULL,
    status TEXT NOT NULL,
    feedback_message TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY (thesis_attempt_id, gate_id)
);

CREATE TABLE IF NOT EXISTS research_thesis_attempt_challenges (
    challenge_id TEXT PRIMARY KEY,
    thesis_attempt_id TEXT NOT NULL,
    research_round_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    thesis_id TEXT NOT NULL,
    challenged_round INTEGER NOT NULL,
    challenged_thesis_id TEXT NOT NULL,
    challenged_rejection_code TEXT NOT NULL,
    claim TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
```

`status` is the normalized audit outcome, not necessarily the native return
value of the validator function. The full vocabulary is:

- `pass` — gate evaluated and accepted the thesis.
- `warn` — gate evaluated and emitted a non-blocking policy warning.
- `reject` — gate evaluated and blocked the attempt.
- `skipped_not_applicable` — gate's condition did not apply to this thesis.
- `not_evaluated` — gate was not reached because an earlier blocking gate ended
  validation.

Not every gate can emit every status. Each gate's allowed statuses are derived
from its `Applicability` value plus tier/condition shape:

| Gate type | Allowed statuses | Notes |
|---|---|---|
| Required process gate | `pass`, `reject`, `not_evaluated` | If the caller provides no observable attempt trace, the gate is not evaluated by that validation path; do not mark it `pass`. |
| Conditional process provenance gate | `pass`, `reject`, `skipped_not_applicable`, `not_evaluated` | A4a makes `source_code_verification` always required for thesis attempts, so its read-trace gate does not skip for normal Stage 1 thesis validation. `skipped_not_applicable` is reserved for non-OUTPUT validation paths that explicitly do not validate a thesis attempt. |
| Mechanical required-field / schema / enum gate | `pass`, `reject`, `not_evaluated` | No `warn`: deterministic shape failures either block or pass. |
| Mechanical conditional/reference gate | `pass`, `reject`, `skipped_not_applicable`, `not_evaluated` | Example: emergent-dimension gates skip for non-emergent theses; overlap-triggered `novel_connection` gates skip when overlap is below threshold. |
| Research-policy hard gate | `pass`, `reject`, `skipped_not_applicable`, `not_evaluated` | No `warn` unless the gate is explicitly defined as warning-capable in §3.3. |
| Research-policy warning-capable gate | `pass`, `warn`, `reject`, `skipped_not_applicable`, `not_evaluated` | Use only for gates whose logic explicitly defines a non-blocking warning path, such as weak `confidence_distribution` before the hard-reject conditions are also met. |
| Stage 2/compiler-contract gate | `pass`, `reject`, `skipped_not_applicable`, `not_evaluated` | `not_evaluated` is valid when Stage 1 already rejected. |
`not_evaluated` is never emitted by an individual gate. It is inserted by the
validation orchestrator when a prior blocking gate prevents later gates from
running. `warn` is never allowed for purely mechanical gates.

`gate_id` must match the rule id from `prompts/conductor_output_rules.json` for
generated/prompt-declared gates, or the explicit inventory name in §3.3 for
process and compiler-contract gates. `applicability` must copy the
gate's §3.3 `Applicability` value so DB analytics can distinguish missing
required gates from valid optional skips. `rejection_code` is empty for passing
gates that do not have a code; for reject/warn rows it must match the
validator's emitted code. `feedback_message` is empty for pass and inapplicable
rows; for reject/warn rows it stores the conductor-facing message, not a stack
trace. `evidence_json` stores structured evidence used by the gate, redacted of
secrets and raw payloads.

Because the primary key is `(thesis_attempt_id, gate_id)`, each gate records one
final status per attempt. If a warning-capable gate also meets hard-reject
conditions, `reject` is the persisted status and the warning details are folded
into `evidence_json`. If the same list-shaped gate fails for multiple entries,
persist one gate row with all failing indexes/items aggregated in
`evidence_json`, not one row per list item.

The existing `research_thesis_attempts` row remains the attempt summary. It
must keep:

- `validator_status` / outcome summary, e.g. `compiled` or
  `rejected_attempt_N`;
- `selected_for_execution`;
- `validation_failure_reason` as the exact top-level feedback returned to the
  next conductor retry for rejected attempts.

Do not duplicate full gate state into `thesis_details_json`. That field may
keep a compact compatibility summary such as the first rejection code, but the
complete gate audit lives in `research_thesis_attempt_gate_results`.

Acceptance persistence: a thesis that reaches `compiled` must still have gate
rows. Passing accepted attempts should show `pass` or `skipped_not_applicable`
for every gate evaluated by that validation path, plus `not_evaluated` for any
later-stage gates that truly did not run. This prevents "compiled" from being a
black box.

Rejection persistence: when validation rejects, insert rows for all gates whose
outcome is known. If a batched mechanical pass finds multiple failures, persist
each failing gate as `reject`; the top-level `validation_failure_reason` is the
conductor-facing aggregate feedback. If a fail-fast policy gate rejects before
mechanical validation, persist later gates as `not_evaluated` so analytics can
distinguish "passed" from "never checked."

Challenge persistence: when a thesis attempt includes `validator_challenge`,
persist it to `research_thesis_attempt_challenges` before validation mutates or
rejects the thesis. A challenge never changes the validator decision and never
satisfies a gate; it is an operator-review record that says the conductor
believes a prior rejection may be wrong. `challenge.json` is derived from this
DB row for compatibility with prompt/debug tooling and must not be the only
source of the challenge claim.

#### Process Validators

| Gate | Scope | Owner | Rejection code(s) | Status | Applicability | Logic | Feedback to research conductor |
|---|---|---|---|---|---|---|---|
| Required process tools called. | process | `_validate_process` | `process_required_tools_not_called` | keep | `conditional_required` | Read the attempt trace when it is available. Require `list_round_results` and `web_search` for each new thesis-proposal attempt, because the conductor must inspect prior round state and external mechanism evidence before proposing OUTPUT. Format-only retries in the same round may reuse previously observed tool evidence if the retry context carries the prior attempt's tool trace and evidence summary. Require `analyze_trades` only when the caller sets `require_analyst_tool=true` for contexts that need trade-level analyst evidence. If `tools_called is None`, skip the process gate because the caller did not observe tool usage; if it is an empty set and no reusable prior-attempt trace is attached, the gate must fire. | "This attempt has no usable record of required research tool(s): {missing_tools}. Run those tools first, or retry with the prior attempt's tool trace/evidence summary attached." |
| Source-code path read trace for `source_code_verification`. | process | attempt-trace extension | `process_source_code_not_read`, `process_source_code_path_not_read` | add | `always_required` | Extract the repo-relative path from `source_code_verification`. Because A4a marks this OUTPUT field `Required: Always`, require at least one source-reading tool call in the attempt trace and require the extracted path to appear in normalized read paths for every normal Stage 1 thesis-proposal attempt. If a caller invokes validation without any observable attempt trace, the orchestrator records this gate as `not_evaluated`, not `pass` or `skipped_not_applicable`. This validates provenance, not string shape; the mechanical OUTPUT gate still owns `path:symbol` syntax and symbol existence. | `process_source_code_not_read`: "You cited source code but did not read any source file in this attempt." `process_source_code_path_not_read`: "You cited {path}, but that path was not read in this attempt. Read the cited file or cite the file you actually inspected." |

#### Research Policy Validators

| Gate | Scope | Owner | Rejection code(s) | Status | Applicability | Logic | Feedback to research conductor |
|---|---|---|---|---|---|---|---|
| Theme-cluster fixation across recent priors. | `(O)` | `_detect_theme_cluster_fixation` | `thesis_quality_theme_cluster_fixation` | keep | `conditional_required` | Compare emitted `theme_keywords` with recent prior thesis `theme_keywords`. Reject when one emitted keyword appears in more than the allowed recent-prior threshold, because the behavior is cluster fixation, not a shape failure. `novel_connection` is not an escape hatch for this gate; it satisfies the separate overlap-explanation contract but does not make repeated cluster selection acceptable. Do not rely on LLM self-reported cluster labels. | "This thesis reuses dominant theme keyword(s) {keywords} too often in recent history. Choose a structurally different mechanism or different theme keywords." |
| Direction whipsaw on a prior lever without acknowledgement. | `(O)` | `_detect_direction_whipsaw` retargeted to A4b `prior_lever_history` | `thesis_quality_direction_whipsaw` | keep / retarget | `conditional_required` | For each emitted `config_changes` key, scan ROUND CONTEXT `prior_lever_history[*].config_keys`. If the key intersects and the new direction differs from `prior_lever_history[*].direction`, require a matching `prior_lever_outcomes` entry whose `prior_thesis_id` resolves and whose `lever` matches the prior lever concept. Do not infer direction from free prose when structured prior-lever context is available. | "You reversed or retried lever {lever} from prior thesis {prior_thesis_id} without a `prior_lever_outcomes` acknowledgement. Add the prior outcome and explain why this retry is different." |
| Missing substantive mechanism-evidence disqualifier. | `(O)` | `_detect_missing_mechanism_evidence_disqualifier` | `thesis_quality_missing_mechanism_evidence_disqualifier` | keep | `always_required` | Require at least one `disqualifiers` entry with `kind="mechanism_evidence"` and a condition long/specific enough to distinguish the proposed mechanism from a metric-only improvement. This is a quality gate in addition to the mechanical kind/count gate. | "Add a mechanism-evidence disqualifier: a concrete data pattern that would show the mechanism is false, not just that headline metrics failed." |
| Needs-code starvation after repeated code-change theses. | `(O)` | `_detect_needs_code_starvation` | `thesis_quality_needs_code_starvation` | keep | `conditional_required` | Inspect recent prior theses. If the last configured threshold of code-change theses required new primitives and none were run/compiled, reject another `requires_code_change=true` thesis unless the current thesis clearly unblocks implementation with requested primitives. | "Recent rounds already requested code changes without a run. Propose a runnable config-only thesis or explain the specific primitive needed to unblock implementation." |
| Config-key overlap with prior theses. | `(O)` | `_detect_config_key_overlap` | `config_validity_config_key_overlap_real` | keep as policy-routed config gate | `conditional_required` | Compare emitted `config_changes.keys()` against prior thesis config keys. Reject repeated key sets only when overlap exceeds the configured threshold AND none of these deterministic escape criteria is present: different `mechanism_dimension` from the overlapping prior, a resolving `prior_lever_outcomes` entry for that prior, a `novel_connection` naming a different runtime regime/context, or new diagnostic evidence cited in `evidence_citations`. This remains research policy even though the code prefix is `config_validity_*`. | "Config keys {keys} were already tested recently. Use a different lever, cite the prior in `prior_lever_outcomes`, switch mechanism dimension/context, or cite new diagnostic evidence that justifies the retry." |
| Neighboring-threshold parameter nudge. | `(O)` | `_detect_neighboring_threshold` | `config_validity_neighboring_threshold` | keep as policy-routed config gate | `conditional_required` | For numeric config keys shared with priors, compare old and new values. Reject only when the new value is within the configured neighboring band of the prior value, default ratio `[0.5, 2.0]`, the absolute delta is not zero, and no deterministic escape criterion is present. Escape criteria: `prior_lever_outcomes` cites the prior and explains the retry, `dimension_novelty` names a different mechanism dimension/context, or `evidence_citations` includes diagnostic evidence supporting a boundary at the new value. | "The new value for {key} is a neighboring threshold move from prior value {old_value}. Cite the prior and justify the boundary with mechanism/diagnostic evidence, or choose a materially different lever." |
| Repeated same-dimension `mechanism_lineage` without pivot/disqualifier. | `(O)` | new behavior signal | `thesis_quality_lineage_no_structural_pivot` | add | `conditional_required` | Resolve `mechanism_lineage` ids to prior snapshots. If at least 3 ancestors share the current `mechanism_dimension`, require either a different current dimension from the ancestor cluster or a mechanism-evidence disqualifier that names the structural distinction from failed ancestors. | "Your lineage repeats the same mechanism dimension without a structural pivot. Change dimension or add a mechanism-evidence disqualifier that distinguishes this thesis from the ancestor failures." |
| Weak `confidence_distribution`. | `(O)` | new behavior signal | `thesis_quality_confidence_distribution_too_weak` | add as warning / conditional block | `always_required` | Read `confidence_distribution`. Do not hard-reject merely because evidence is honestly weak. Emit `warn` when all counted dimensions are `speculative`; emit `reject` instead when all counted dimensions are `speculative` AND the thesis lacks both (a) a mechanism-evidence disqualifier acknowledging the weak basis and (b) a concrete `if_this_fails_next_thesis` pivot. Greenfield contexts may have `precedent="speculative"` without counting it against this gate. One gate row is persisted; `reject` dominates `warn` when both conditions are true. | "Your confidence distribution is weak. Either strengthen evidence, or explicitly handle the weak basis with a mechanism-evidence disqualifier and concrete next-thesis pivot." |
| Weak `if_this_fails_next_thesis` pre-commitment. | `(O)` | new behavior signal or predicate-backed policy | `thesis_quality_next_thesis_not_pre_committed` | add | `always_required` | Require `if_this_fails_next_thesis` to name either (a) the exact `deepest_alternative.mechanism`, or (b) a different `mechanism_dimension` plus a concrete mechanism phrase of at least 5 words. Reject vague retries such as parameter retuning without a named pivot. | "Make `if_this_fails_next_thesis` concrete: cite the deepest alternative exactly, or name a different mechanism dimension plus the mechanism you would test next." |

#### Mechanical OUTPUT Validators

These gates must be finalized against A4a OUTPUT meanings and producer
guidance, not against legacy validator behavior.

| Gate | Scope | Owner | Rejection code(s) | Status | Applicability | Logic | Feedback to research conductor |
|---|---|---|---|---|---|---|---|
| Pydantic schema/type validation. | `(O)` | `ResearchThesis.model_validate` and nested Pydantic models | Pydantic `ValidationError` before named gate mapping | keep / extend | `always_required` | Validate all emitted JSON against `ResearchThesis` and nested A4 models. Enforce enum literals, booleans, lists, dicts, and nested minimum lengths defined in `research_types.py`. Normalize only legacy payloads at explicit compatibility boundaries; do not silently rewrite new A4 source names or shapes. | "The JSON does not match the required schema at {field_path}: {short_error}. Regenerate valid JSON using the OUTPUT field shapes." |
| `thesis_id` non-empty. | `(O)` system-injected | `_collect_inline_structural_failures` | `structural_missing_thesis_id` | keep | `system_injected` | Ensure the system-injected id exists after normalization. The LLM does not repair this; missing id indicates orchestration failure. | "System error: thesis id was missing. Regenerate with a system-assigned id." |
| `hypothesis` non-empty. | `(O)` | `_collect_inline_structural_failures` | `structural_missing_hypothesis` | keep | `always_required` | Require non-empty text. Do not semantic-score here; A4a says it captures the core causal claim. | "Fill `hypothesis` with the core causal claim being tested." |
| `mechanism` non-empty. | `(O)` | `_collect_inline_structural_failures` | `structural_missing_mechanism` | keep | `always_required` | Require non-empty text. Do not accept a duplicate of `hypothesis` if a cheap exact/near-exact duplicate check exists; semantic quality otherwise belongs to policy/review. | "Fill `mechanism` with the market-mechanics explanation, not just the hypothesis restated." |
| `mechanism_dimension` valid. | `(O)` | `_validate_mechanism_dimension` | `structural_missing_mechanism_dimension`, `structural_mechanism_dimension_invalid` | keep | `always_required` | Require a non-empty value in `MECHANISM_DIMENSIONS` or ROUND CONTEXT `emergent_dimensions_in_use`; allow `"emergent"` only when the emergent-dimension contract passes. | "Choose a valid `mechanism_dimension` from the allowed dimensions, or use `emergent` with all emergent-dimension fields completed." |
| `theme_keywords` non-empty. | `(O)` | generated predicate / inline structural extension | `structural_theme_keywords_empty` | add | `always_required` | Require at least one non-empty keyword. A4a recommends 2-3 but only hard-rejects empty because over-constraining keyword count can block valid narrow theses. | "Add at least one concrete `theme_keywords` entry for the mechanism lever." |
| `thesis_role` required literal. | `(O)` | Pydantic plus generated predicate | `structural_thesis_role_required` | add | `always_required` | A4a marks `thesis_role` `Required: Always`; validate presence in the raw emitted JSON before any schema default can mask omission, and require one of A4a's three literals: `orthogonal_discovery`, `implementation_unlock`, `cleanup_validation_follow_up`. | "Set `thesis_role` to one of the allowed role labels." |
| `dimension_novelty` length and grounding. | `(O)` | `_collect_inline_structural_failures` plus generated predicate | `structural_dimension_novelty_too_short`, `structural_dimension_novelty_not_grounded` | keep / add grounded predicate | `always_required` | Enforce length >=30 chars. Require mention of at least one specific `MECHANISM_DIMENSIONS` value; prefer two distinct mentions only as guidance, because A4a hard source set says >=1 and producer guidance asks the prior dimension by name. | "Rewrite `dimension_novelty` to name at least one mechanism dimension and explain the structural contrast; naming the prior dimension is preferred." |
| `underexplored_dimensions_considered` membership/exclusion. | `(O)` | `_validate_underexplored_dimensions` | `structural_underexplored_dimensions_invalid`, `structural_underexplored_includes_chosen` | keep | `conditional_required` | When ROUND CONTEXT `dimensions_unexplored` contains at least one dimension other than this thesis's `mechanism_dimension`, require at least one entry. Every entry must be in that runtime list and must not equal this thesis's `mechanism_dimension`. If the only unexplored dimension is the chosen dimension, persist `skipped_not_applicable` for the required-entry subcondition while still rejecting any emitted entry that is outside the runtime list or equals the chosen dimension. | "Pick `underexplored_dimensions_considered` only from ROUND CONTEXT `dimensions_unexplored`, and do not include the chosen mechanism dimension." |
| `novel_connection` required and grounded. | `(O)` | `_collect_inline_structural_failures` plus generated predicate | `structural_novel_connection_too_short`, `structural_novel_connection_not_grounded` | keep / add grounded predicate | `conditional_required` | Trigger when any emitted `theme_keywords` entry appears in ROUND CONTEXT `theme_keywords_in_use`. When triggered, require >=120 chars and mention either the shared keyword by exact token or a structurally distinct `MECHANISM_DIMENSIONS` value. `family_cluster_density=="high"` is recommended by A4a but not hard rejection unless keyword overlap also triggers. | "Because you reused prior theme keyword(s) {keywords}, fill `novel_connection` with >=120 chars naming the shared keyword or distinct mechanism dimension and explaining why this is materially new." |
| Emergent-dimension conditional fields. | `(O)` | `_validate_emergent_dimension` | `structural_emergent_thesis_malformed`, `structural_new_dimension_name_duplicates_existing` | keep / update | `conditional_required` | If `mechanism_dimension=="emergent"`, require `new_dimension_name`, `why_existing_dimensions_do_not_fit`, and `mechanism_family_definition`; the two prose fields must be >=80 chars. `new_dimension_name` must not appear in `MECHANISM_DIMENSIONS` or ROUND CONTEXT `emergent_dimensions_in_use`. If dimension is not emergent, these fields should be omitted or empty according to schema. | "For an emergent dimension, provide a non-duplicate `new_dimension_name`, explain why existing dimensions do not fit, and define the reusable mechanism family." |
| `deepest_alternative` required and tiebreaker resolution. | `(O)` | new generated predicates | `structural_deepest_alternative_missing`, `structural_deepest_alternative_tiebreaker_unresolved` | add | `always_required` | Require non-null object. Validate `why_rejected >=40` through schema. Resolve `tiebreaker`: `evidence_citation` value must be `citation_N` where `N` is in emitted citation array bounds; `disqualifier` value must exactly match a disqualifier `name`; `mechanism_dimension` value must be one of A4a's `mechanism_dimension` enum values from `MECHANISM_DIMENSIONS`. Runtime `emergent_dimensions_in_use` values are valid for the thesis's own `mechanism_dimension`, but not for this tiebreaker kind unless A4a is later expanded. | "Add a valid `deepest_alternative` and make its `tiebreaker.value` resolve to an emitted citation id, disqualifier name, or mechanism dimension." |
| `other_alternatives` count and tiebreakers. | `(O)` | new generated predicates | `structural_other_alternatives_too_few`, `structural_lighter_tiebreaker_unresolved` | add | `always_required` | Require at least one entry. Enforce each `why_rejected >=40` through schema. If `lighter_tiebreaker` is present, resolve it with the same lookup rules as `deepest_alternative.tiebreaker`. | "Add at least one rejected alternative mechanism, and fix any `lighter_tiebreaker` so it resolves to a valid citation, disqualifier, or dimension." |
| `prior_lever_outcomes[*].prior_thesis_id` resolution. | `(O)` | new generated predicate | `structural_prior_lever_outcomes_unknown_id` | add | `optional_if_present` | Every emitted `prior_thesis_id` must exist in ROUND CONTEXT `prior_theses_snapshot`. This mechanical gate only checks ids; the whipsaw policy gate decides when entries are required. | "The `prior_lever_outcomes` id {prior_thesis_id} is not in the prior snapshot. Use a real prior thesis id from ROUND CONTEXT or remove the entry." |
| `mechanism_lineage[*]` resolution. | `(O)` | new generated predicate | `structural_mechanism_lineage_unknown_id` | add | `optional_if_present` | Every emitted lineage id must exist in ROUND CONTEXT `prior_theses_snapshot`. Empty list is valid for greenfield theses. | "The `mechanism_lineage` id {thesis_id} is not in the prior snapshot. Use real ancestor ids only, or leave lineage empty." |
| `if_this_fails_next_thesis` non-empty. | `(O)` | generated predicate plus policy gate | `structural_next_thesis_missing`, `thesis_quality_next_thesis_not_pre_committed` | add | `always_required` | A4a marks this field `Required: Always`. The mechanical predicate rejects an empty value with `structural_next_thesis_missing`; the policy gate then checks that non-empty text cites `deepest_alternative.mechanism` exactly or names a different `mechanism_dimension` plus a concrete mechanism phrase. | `structural_next_thesis_missing`: "Fill `if_this_fails_next_thesis` with the concrete next thesis you would test if this one fails." `thesis_quality_next_thesis_not_pre_committed`: "Make `if_this_fails_next_thesis` concrete: cite the deepest alternative exactly, or name a different mechanism dimension plus the mechanism you would test next." |
| `evidence_citations` count, length, and A4a diversity. | `(O)` | `_collect_research_contract_failures` plus generated predicates | `structural_evidence_citations_missing_source_diversity`, `structural_evidence_citation_too_short`, `structural_evidence_citations_too_many` | keep / retarget source names | `always_required` | Require 2-6 entries. Each `citation` must be >=30 chars. Per A4a, require at least one `source="web_search"` and at least one `source="analyst"`; accept `source_code`, `experiment_result`, and `memory` as supporting sources but do not count them toward this diversity gate. Do not rewrite `experiment_result` to non-schema values. | "Provide 2-6 evidence citations, each >=30 chars, including at least one `web_search` and one `analyst` citation. Other sources are supporting only." |
| `confidence_distribution` required object and enums. | `(O)` | Pydantic plus generated predicates | `structural_confidence_distribution_missing`, `structural_confidence_distribution_invalid` | add | `always_required` | Require object with `data`, `literature`, and `precedent`; each value must be one of `direct`, `proxy`, `mixed`, `speculative`. Empty strings are invalid. Evidence weakness beyond enum completeness is handled by the research-policy warning/conditional block gate, not this mechanical gate. | "Set `confidence_distribution` with data/literature/precedent ratings using only direct, proxy, mixed, or speculative." |
| `expected_effects` presence and count. | `(O)` | `_validate_expected_effects_present` plus generated predicates | `structural_missing_expected_effects`, `structural_expected_effects_not_coupled` | keep | `always_required` | Require a non-empty list with at least two distinct `metric` values, matching A4a's coupled-metrics guidance. | "Add at least two `expected_effects` with distinct metrics: one headline outcome and one mechanism check." |
| Stage 1 `expected_effects` metric-backing gate. | `(O)` | legacy metric-backing loop | `structural_expected_effect_metric_unbacked` | remove from normal Stage 1 OUTPUT validation | `removed` | A4a gives `expected_effects[*].metric` `Source set: Free` and omits `required_diagnostic_specs` from OUTPUT. Normal Stage 1 validation must not reject a thesis merely because a non-builtin metric is absent from omitted diagnostic specs. Metric producibility is validated after compilation by Stage 2 when diagnostic specs exist. Do not require or read deleted LLM field `required_diagnostics`. | "Do not reject Stage 1 OUTPUT for free-form metric names. Validate producibility in Stage 2 after the compiler contract exists." |
| `expected_effects` magnitude/unit/rationale conditionals. | `(O)` | `_collect_research_contract_failures` plus generated predicates | `structural_expected_effect_magnitude_missing`, `structural_expected_effect_magnitude_range_invalid`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required` | add A4 conditionals | `conditional_required` | For directions `increase` and `decrease`, require `magnitude_range` and `rationale >=40`. When `magnitude_range` is set, require two numeric bounds with lower < upper and require `unit` in `EXPECTED_EFFECT_UNITS`. For `increase_or_same`, `decrease_or_same`, and `not_worse_than`, rationale and range are optional. | "Directional expected effect {metric} needs a valid magnitude range, unit, and >=40-char rationale. Non-directional guardrail effects may leave range/rationale null." |
| `expected_runtime_signal` shape and path resolution. | `(O)` | new generated predicates | `structural_expected_runtime_signal_invalid` | add | `optional_if_present` | If present, each `event_path` must be in ROUND CONTEXT `diagnostic_event_paths`. Bound pairing must match relation: `>`/`>=` need `lower`; `<`/`<=` need `upper`; `==` and `in_range` need both. `condition` must be non-empty by schema. | "Fix `expected_runtime_signal`: use an event path from ROUND CONTEXT and provide lower/upper bounds required by the selected relation." |
| `disqualifiers` count/kind/overfit marker. | `(O)` | `_collect_inline_structural_failures` plus generated predicates | `structural_disqualifiers_too_few`, `structural_disqualifiers_no_mechanism_evidence`, `structural_disqualifiers_no_overfit_address` | keep / add | `always_required` | Require at least two entries, at least one `kind="mechanism_evidence"`, and at least one overfit-risk entry identified by approved marker name or keyword in the condition. A single entry may satisfy mechanism-evidence and overfit roles, but total count must remain >=2. | "Provide at least two disqualifiers, including one mechanism-evidence disqualifier and one overfit-risk disqualifier." |
| `config_changes` non-empty or code-change. | `(O)` | `_validate_thesis_specifies_change` | `structural_config_changes_required` | keep | `conditional_required` | Require `config_changes` to be non-empty unless `requires_code_change=true`. This follows A4a: config keys express runnable changes; code-change theses must request primitives instead. | "Set at least one `config_changes` key, or set `requires_code_change=true` and request the needed primitive." |
| `requested_primitives` when code change required. | `(O)` | `_validate_thesis_specifies_change` | `structural_engine_change_request_malformed` | keep | `conditional_required` | If `requires_code_change=true`, require at least one non-empty primitive name in `requested_primitives`. If `requires_code_change=false`, `requested_primitives` should be empty. | "When `requires_code_change=true`, list the primitive(s) needed. Otherwise leave `requested_primitives` empty." |
| `source_code_verification` format, repo path, family path, symbol. | `(O)` | `_collect_source_code_verification_failures` | `structural_source_code_verification_too_short`, `structural_source_code_verification_malformed` | keep with split codes | `always_required` | Require >=40 chars and A4a's exact format `<repo path>:<symbol> — <explanation>`, including the em dash separator. Path must be a repo-relative Python strategy source file, exist in the repo, and include the cited symbol. Explanation after the dash must be non-empty and describe how the cited symbol constrains or implements the proposed change. | "Fix `source_code_verification` to cite an existing strategy source path and symbol in `<path>:<symbol> — <explanation>` format." |
| Legacy `alternatives_considered` gate. | `(O)` | `_collect_research_contract_failures` | `structural_alternatives_considered_invalid` | remove | `removed` | Delete this Stage 1 gate. A4a replacement is `deepest_alternative` plus `other_alternatives`. | "Use `deepest_alternative` and `other_alternatives`; `alternatives_considered` is no longer part of OUTPUT." |
| Legacy `evidence_strength` gate. | `(O)` | `_collect_research_contract_failures` | `structural_missing_evidence_strength` | remove | `removed` | Delete this Stage 1 gate. A4a replacement is `confidence_distribution`. | "Use `confidence_distribution`; `evidence_strength` is no longer part of OUTPUT." |
| Legacy `falsification_or_alternative` gate. | `(O)` | `_collect_inline_structural_failures` | `structural_falsification_invalid` | remove | `removed` | Delete this Stage 1 gate. A4a replacement is typed `disqualifiers`. | "Use typed `disqualifiers`; `falsification_or_alternative` is no longer part of OUTPUT." |
| Legacy `causal_cluster` gate. | `(O)` | `_collect_inline_structural_failures` | `structural_missing_causal_cluster` | remove | `removed` | Delete this Stage 1 gate. Replacement signals are `theme_keywords`, computed overlap, `novel_connection`, and optional `mechanism_lineage`. | "Use `theme_keywords`, `novel_connection`, and `mechanism_lineage`; `causal_cluster` is no longer part of OUTPUT." |
| Legacy `expected_reuse_across_future_theses` emergent gate. | `(O)` | `_validate_emergent_dimension` | `structural_emergent_thesis_malformed` | remove | `removed` | Delete this required field from emergent checks. A4a replacement is `mechanism_family_definition`. | "Use `mechanism_family_definition`; `expected_reuse_across_future_theses` is no longer part of OUTPUT." |

#### Config Validity Validators

| Gate | Scope | Owner | Rejection code(s) | Status | Applicability | Logic | Feedback to research conductor |
|---|---|---|---|---|---|---|---|
| Metadata keys leaked into `config_changes`. | `(O)` | `_collect_mechanical_config_validity_failures` | `config_validity_config_changes_metadata_leak` | keep | `optional_if_present` | Reject metadata/control keys in `config_changes` such as ids, paths, contract metadata, schema names, or inherited-base references. Config changes must contain runtime knobs only. | "Remove metadata/control key(s) {keys} from `config_changes`; only runtime strategy config keys belong there." |
| `config_changes` keys resolve against A4b `strategy_config_keys`. | `(O)` | new generated predicate | `structural_config_changes_unknown_key` | add | `conditional_required` | Compare every emitted config key with ROUND CONTEXT `strategy_config_keys`. Unknown keys always reject in `config_changes`, matching A4a's source set. If no known key can express the mechanism, the thesis must leave unknown/planned keys out of `config_changes`, set `requires_code_change=true`, and name the needed primitive in `requested_primitives`. | "`config_changes` contains unknown key(s) {keys}. Use keys from ROUND CONTEXT `strategy_config_keys`; if the mechanism needs a new key, omit that key and request a code primitive." |
| `base_config_path` path syntax / runtime path / inheritance. | compiler contract | `_validate_base_config_path`, `_collect_mechanical_config_validity_failures` today | `config_validity_base_config_path_invalid`, `config_validity_base_config_path_runtime_construction`, `config_validity_base_config_path_inheritance_blocked` | move out of Stage 1 | `compiler_contract` | Do not validate this as an LLM OUTPUT field. After compilation, validate only compiler-generated contract inheritance paths: repo-relative, allowed location, not runtime-constructed from user data, and permitted by family policy. | "Compiled contract inheritance path {path} is invalid or not allowed. The compiler must use an approved base config path." |
| `base_contract_id` inheritance blocked. | compiler contract | `_collect_mechanical_config_validity_failures` today | `config_validity_base_contract_id_not_allowed` | move out of Stage 1 | `compiler_contract` | Do not validate this as an LLM OUTPUT field. If the compiler emits contract inheritance, enforce family policy there. | "Compiled contract inheritance by base_contract_id is not allowed for this family/path." |

#### Stage 2 / Compiler-Contract Validators

| Gate | Scope | Owner | Rejection code(s) | Status | Applicability | Logic | Feedback to research conductor |
|---|---|---|---|---|---|---|---|
| Hypothesis/config alignment against compiled `runtime_config`. | compiler contract | `_detect_stage_2_hypothesis_config_misalignment`, `validate_stage_2` | `hypothesis_config_misalignment` | keep | `compiler_contract` | After compilation, compare thesis `hypothesis`/`mechanism` concepts with actual `runtime_config` keys using family `key_concepts`. Reject when compiled config does not operationalize the stated mechanism. | "The compiled runtime config does not test the stated hypothesis/mechanism. Adjust config changes or rewrite the thesis so they match." |
| Strategy family alignment unconfigured. | compiler contract | `validate_stage_2` / `_load_family_key_concepts` | `hypothesis_config_alignment_unconfigured` | keep / tighten | `compiler_contract` | Hard-fail when the family name is empty, the family is unregistered, `family_research_spec` cannot load, or a production family has empty `key_concepts`, because Stage 2 cannot know whether compiled config tests the thesis. Only families in an explicit scaffold/demo allowlist may skip alignment. This is an operator/compiler configuration problem, not an LLM OUTPUT repair. | "Stage 2 alignment is not configured for strategy family {strategy_family}. Populate `FamilyResearchSpec.key_concepts` or add the family to the explicit scaffold/demo allowlist." |
| Required diagnostics and free-form predicted metrics resolve post-compile. | compiler contract | `_collect_stage_2_required_diagnostic_failures` plus expected-effect metric producibility check | `required_diagnostic_missing_post_compile` | keep / extend | `compiler_contract` | Resolve each `required_diagnostic_specs[*].key` or alias against the compiled contract and `runtime_config`. For `expected_effects[*].metric`, accept built-in evaluator metrics directly; for non-builtin metrics, require the compiled contract to produce a matching diagnostic spec, alias, runtime metric, or experiment-evaluation surface. Reject after compilation when a metric predicted by the thesis cannot be produced by the compiled strategy. | "Required diagnostic or predicted metric {diagnostic_key} is not produced by the compiled contract/runtime config. Add the diagnostic or remove the prediction that depends on it." |
| Compiled base config inheritance validation. | compiler contract | Stage 2/compiler-contract validation | `config_validity_base_config_path_invalid`, `config_validity_base_config_path_runtime_construction`, `config_validity_base_config_path_inheritance_blocked`, `config_validity_base_contract_id_not_allowed` | add/move here | `compiler_contract` | Validate compiler-generated base config inheritance only after contract construction. Enforce path allowlists, no runtime string construction, no disallowed base contract id inheritance, and family policy. | "Compiled base config inheritance is invalid: {reason}. Fix the compiler contract, not the LLM OUTPUT JSON." |

#### Drift / Prompt Contract Validators

| Gate | Scope | Owner | Failure mode | Status | Applicability | Logic | CI / operator feedback |
|---|---|---|---|---|---|---|---|
| `ResearchThesis.model_fields` match rendered OUTPUT fields except `_PROMPT_OMITTED_FIELDS`. | drift | `scripts/check_prompt_drift.py` | CI failure | keep / replace legacy inspected-field list | `ci_always` | Introspect schema fields and rendered OUTPUT headings. Fail if any schema field is missing from rendered OUTPUT or any rendered field is absent from schema, except explicitly omitted system/compiler fields. The current legacy `VALIDATOR_INSPECTED_FIELDS` list must be replaced because it still names deleted fields and cannot detect A4 sidecar drift. | "Drift check failed: schema field/rendered OUTPUT mismatch: {field}." |
| Validator rejection codes match `prompts/conductor_output_rules.json` except `_PROMPT_OMITTED_RULES`. | drift | `scripts/check_prompt_drift.py` | CI failure | add | `ci_always` | Extract rejection codes emitted by validators and compare with generated sidecar. Fail if a validator emits a code with no sidecar rule or a sidecar rule has no validator implementation, unless omitted. This covers A4 rules sidecar drift; it does not render rejection codes into the LLM-facing prompt. | "Drift check failed: rejection code {code} is not synchronized between validator and rules sidecar." |
| Rendered OUTPUT has no validator rule prose or rejection-code catalogue. | drift | `scripts/check_prompt_drift.py` | CI failure | keep | `ci_always` | Scan rendered prompt for rejection-code patterns and validator-only headings. Fail if implementation feedback leaks into the LLM-facing schema prompt. | "Drift check failed: validator-only rule text leaked into rendered OUTPUT." |
| Rendered fields include compact slots and typed fields include `Shape`. | drift | `scripts/check_prompt_drift.py` | CI failure | keep | `ci_always` | For every rendered field, require compact slots `T`, `F`, `S`, `Cap`, `Req`, `M`, `G`, `Ex`; require `Shape` for typed-object/list fields. | "Drift check failed: rendered field {field} is missing compact slot(s) {slots}." |
| Rendered `G` guidance preserves decision criteria and is not title-only. | drift | `scripts/check_prompt_drift.py` | CI failure | keep | `ci_always` | Compare rendered guidance against authoring metadata. Fail empty, title-only, or criteria-free guidance, especially where A4a names source constraints, conditionals, or reference behavior. | "Drift check failed: field {field} guidance lost required decision criteria." |
| Rendered `M`, `G`, and `Ex` obey A4a compression rules. | drift | `scripts/check_prompt_drift.py` | CI failure | keep | `ci_always` | Allow compact wording but fail missing meaning, missing guidance constraints, or examples whose value shape no longer matches the schema. | "Drift check failed: field {field} compressed text lost meaning/guidance/example shape." |
| Rendered constants come from code constants. | drift | `scripts/check_prompt_drift.py` | CI failure | keep | `ci_always` | Detect enum/marker lists rendered from prose literals instead of imported tuples/frozensets. | "Drift check failed: rendered constant list for {field} is not generated from code constants." |
| Prompt schema-version stamp matches schema/rules hash. | drift | `scripts/check_prompt_drift.py` | CI failure | add | `ci_always` | Compute hash from `ResearchThesis.model_fields` plus rules sidecar. Fail if `_build_conductor_system_prompt` stamp differs. | "Drift check failed: prompt schema-version stamp is stale." |
| DOCTRINE contains no schema-field names or field-reference arrows. | drift | `scripts/check_prompt_drift.py` | CI failure | keep | `ci_always` | Scan DOCTRINE prose for `ResearchThesis.model_fields` names and `-> see <field>` / `→ see <field>` patterns. | "Drift check failed: DOCTRINE duplicates OUTPUT schema-field contract {field}." |

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
| `dimension_novelty` | Length ≥30 chars AND must mention ≥1 specific dimension name from `MECHANISM_DIMENSIONS`. A4a producer guidance asks for the prior dimension being moved away from; the hard gate enforces one grounded dimension mention and the feedback should push toward the prior-dimension contrast. | `structural_dimension_novelty_too_short`, `structural_dimension_novelty_not_grounded` |
| `novel_connection` | Post-emit conditional: required when ≥1 emitted `theme_keywords` entry appears in ROUND CONTEXT `theme_keywords_in_use`. When required: length ≥120 chars AND must mention a shared keyword by name OR a structurally-distinct `mechanism_dimension`. | `structural_novel_connection_too_short`, `structural_novel_connection_not_grounded` |
| `underexplored_dimensions_considered` | Required when ROUND CONTEXT `dimensions_unexplored` contains at least one dimension other than this thesis's `mechanism_dimension`. Each entry must be present in `dimensions_unexplored` AND must not equal this thesis's `mechanism_dimension`. If the only unexplored dimension is the chosen dimension, the required-entry subcondition is skipped but emitted invalid/chosen entries still reject. | `structural_underexplored_dimensions_invalid`, `structural_underexplored_includes_chosen` |

### 4.4 Alternatives + Prior Work

| Field | Rule | Rejection code(s) |
|---|---|---|
| `deepest_alternative` | Required, non-null. `tiebreaker.value` resolves by exact match: `kind="evidence_citation"` → `citation_N` where `1 ≤ N ≤ len(evidence_citations)`; `kind="disqualifier"` → must equal a `disqualifiers[i].name`; `kind="mechanism_dimension"` → must be a member of A4a's enum-backed `MECHANISM_DIMENSIONS`. Runtime `emergent_dimensions_in_use` values are valid for the thesis's own `mechanism_dimension`, but not for this tiebreaker kind unless A4a is later expanded. | `structural_deepest_alternative_missing`, `structural_deepest_alternative_tiebreaker_unresolved` |
| `other_alternatives` | ≥1 entry; each `why_rejected` ≥40 chars. When `lighter_tiebreaker` is non-null, it resolves by the same rules as `deepest_alternative.tiebreaker`. | `structural_other_alternatives_too_few`, `structural_lighter_tiebreaker_unresolved` |
| `prior_lever_outcomes` | Required when ANY key in `config_changes` appears in any `prior_lever_history[i].config_keys` AND your derived direction differs from `prior_lever_history[i].direction`. `prior_thesis_id` values must exist in ROUND CONTEXT `prior_theses_snapshot`. | `thesis_quality_direction_whipsaw`, `structural_prior_lever_outcomes_unknown_id` |
| `mechanism_lineage` | `thesis_id` entries must appear in ROUND CONTEXT `prior_theses_snapshot`. With ≥3 ancestors sharing the same `mechanism_dimension`, require either (a) a different `mechanism_dimension` on this thesis, OR (b) a `disqualifiers` entry with `kind="mechanism_evidence"` that distinguishes this thesis from the lineage's prior failures. | `structural_mechanism_lineage_unknown_id`, `thesis_quality_lineage_no_structural_pivot` |
| `if_this_fails_next_thesis` | Non-empty; must reference either the exact `deepest_alternative.mechanism` OR a specific `mechanism_dimension` different from current plus a concrete mechanism phrase. Empty values are structural failures; vague/non-committed values are research-policy failures. | `structural_next_thesis_missing`, `thesis_quality_next_thesis_not_pre_committed` |

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
| `evidence_citations` | ≥2 entries; ≤6 entries; required source coverage follows A4a: at least one `web_search` and at least one `analyst`. `source_code`, `experiment_result`, and `memory` are accepted supporting sources but do not satisfy the diversity requirement. Each `citation` ≥30 chars. | `structural_evidence_citations_missing_source_diversity`, `structural_evidence_citation_too_short`, `structural_evidence_citations_too_many` |
| `confidence_distribution` | Required object with `data`, `literature`, and `precedent`. Empty/invalid enum values are structural failures. Evidence weakness is a policy signal: all counted dimensions `"speculative"` emits `warn`, and emits `reject` instead when the thesis also lacks both a mechanism-evidence disqualifier acknowledging the weak basis and a concrete `if_this_fails_next_thesis` pivot. Greenfield exemption: when ROUND CONTEXT `dimensions_already_explored` is empty, `precedent="speculative"` is not counted against the gate. One final status is persisted for the gate; `reject` dominates `warn`. | `structural_confidence_distribution_missing`, `structural_confidence_distribution_invalid`, `thesis_quality_confidence_distribution_too_weak` |

### 4.7 Predictions + Falsification

| Field | Rule | Rejection code(s) |
|---|---|---|
| `expected_effects` | Non-empty list with ≥2 distinct metrics. A4a leaves `metric` source set free, so normal Stage 1 OUTPUT validation does not reject non-builtin metric names for lacking an omitted diagnostic spec. `magnitude_range` required when `direction in {"increase","decrease"}`; `unit` required and must be a member of `EXPECTED_EFFECT_UNITS` when `magnitude_range` is set; `rationale` required (≥40 chars) when `direction in {"increase","decrease"}`. `magnitude_range[0] < magnitude_range[1]` when set. Metric producibility is a Stage 2/compiler-contract check when diagnostic specs exist. | `structural_missing_expected_effects`, `structural_expected_effects_not_coupled`, `structural_expected_effect_magnitude_missing`, `structural_expected_effect_magnitude_range_invalid`, `structural_expected_effect_unit_invalid`, `structural_expected_effect_rationale_required`; Stage 2 may emit `required_diagnostic_missing_post_compile` |
| `expected_runtime_signal` | Each `event_path` must resolve in ROUND CONTEXT `diagnostic_event_paths`. `lower` set when `expected_relation in {">", ">=", "==", "in_range"}`; `upper` set when `expected_relation in {"<", "<=", "==", "in_range"}`. | `structural_expected_runtime_signal_invalid` |
| `disqualifiers` | ≥2 entries; ≥1 with `kind="mechanism_evidence"`; ≥1 entry whose `name` is in `OVERFIT_DISQUALIFIER_MARKERS` OR whose `condition` (lowercased) contains a member of `OVERFIT_KEYWORD_HINTS`. A single entry may satisfy both `mechanism_evidence` and overfit requirements. | `structural_disqualifiers_too_few`, `structural_disqualifiers_no_mechanism_evidence`, `structural_disqualifiers_no_overfit_address` |

### 4.8 Config + Engine

| Field | Rule | Rejection code(s) |
|---|---|---|
| `config_changes` | Non-empty OR `requires_code_change=true`. Each emitted key must appear in ROUND CONTEXT `strategy_config_keys`; planned future keys for code-change theses do not belong here. | `structural_config_changes_required`, `structural_config_changes_unknown_key` |
| `requires_code_change` | When `true`, `requested_primitives` must be non-empty. | `structural_engine_change_request_malformed` |
| `requested_primitives` | Non-empty paired with `requires_code_change=true`. | `structural_engine_change_request_malformed` |

### 4.9 Diagnostics + Code Grounding

| Field | Rule | Rejection code(s) |
|---|---|---|
| `source_code_verification` | Length ≥40 chars; format matches A4a's exact `"<path>:<symbol> — <prose>"` shape, including the em dash separator; the cited path must be a repo-relative strategy Python file and the symbol must exist. | `structural_source_code_verification_too_short`, `structural_source_code_verification_malformed` |
| source-code read trace | The cited `<path>` from `source_code_verification` must appear in the conductor attempt's read-path trace captured from source-reading tools for every normal Stage 1 thesis attempt. If no attempt trace is available to the validation path, the orchestrator records this process gate as `not_evaluated`, not `pass` or `skipped_not_applicable`. | `process_source_code_not_read`, `process_source_code_path_not_read` |

### 4.10 Escape Hatch

| Field | Rule | Rejection code(s) |
|---|---|---|
| `validator_challenge` | No rule; accepts any object. Logged for human review only. | none |

`validator_challenge` is a meta-field outside `ResearchThesis`, used only when
the LLM believes a recent rejection was wrong. It is persisted in
`research_thesis_attempt_challenges`; `challenge.json` is a derived read
artifact next to `rejection.json`, not the source of truth. It has this shape:

```json
{
  "challenged_round": 3,
  "challenged_thesis_id": "job-1-round-3-attempt-2",
  "challenged_rejection_code": "structural_other_alternatives_too_few",
  "claim": "The 1-entry minimum should not apply when the rejected thesis already had a deepest_alternative with a resolving tiebreaker.",
  "evidence": "The rejected payload included deepest_alternative.tiebreaker={kind: 'mechanism_dimension', value: 'signal_quality'} and no other unresolved references."
}
```

Guidance: use sparingly. It is logged for human review, persisted in DB, and
does not alter the validator's decision.
Because `validator_challenge` is outside `ResearchThesis`, schema-prompt drift
checks must treat it as an explicit meta-field exception: it may be documented
near OUTPUT as an optional attachment, but it must not be required to appear in
`ResearchThesis.model_fields` or `prompts/conductor_output_rules.json`.

### 4.11 Notes For The Implementer

- `prompts/conductor_output_rules.json` is generated from this section + §5.1's `predicate_kind` mapping for Stage 1 OUTPUT/prompt-declared field predicates only. Process-provenance, DB-persistence, repository artifact, and Stage 2/compiler-contract checks stay in the validator inventory and may have gate ids, but they do not render as prompt-declared sidecar predicates unless §5.1 explicitly lists them.
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

Every Stage 1 OUTPUT/prompt-declared field predicate in §4 reduces to a
`predicate_kind` from this table. The renderer fails if an OUTPUT predicate
rule cannot be expressed in this set. Process-provenance, persistence,
repository artifact checks, and Stage 2/compiler-contract rules are not
renderer-owned predicates unless explicitly listed here.

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
| `list_source_diversity` | `{field, source_field, required_sources}` | thesis | structural_evidence_citations_missing_source_diversity |
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
| `expected_effect_metric_backed` | `{effects_field, builtin_metrics_constant, diagnostic_specs_field}` | compiled contract + constants | Stage 2/compiler-contract predicate only. Not part of normal A4a Stage 1 OUTPUT validation because `expected_effects[*].metric` is free-form and `required_diagnostic_specs` is omitted from OUTPUT. |
| `list_distinct_field_min` | `{field, nested_field, min}` | thesis | structural_expected_effects_not_coupled |
| `magnitude_range_required_for_direction` | `{effects_field, direction_values_requiring_range}` | thesis | structural_expected_effect_magnitude_missing |
| `magnitude_range_well_formed` | `{effects_field}` | thesis | structural_expected_effect_magnitude_range_invalid |
| `unit_required_when_magnitude_set` | `{effects_field, unit_constant}` | thesis + constant | structural_expected_effect_unit_invalid |
| `rationale_required_for_directional` | `{effects_field, direction_values_requiring_rationale, min_len}` | thesis | structural_expected_effect_rationale_required |
| `required_object_fields` | `{field, required_fields}` | thesis | structural_confidence_distribution_invalid |
| `confidence_strength_floor` | `{confidence_field, strong_values, greenfield_round_context_key}` | thesis + round_context | thesis_quality_confidence_distribution_too_weak |
| `next_thesis_references_pivot_or_deepest` | `{field, dimension_field, deepest_alternative_field}` | thesis | structural_next_thesis_missing, thesis_quality_next_thesis_not_pre_committed |
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
- Align evidence source names with `EvidenceSource`; accepted supporting
  sources include `experiment_result`, not `round_result`, and the A4a diversity
  gate counts only `web_search` + `analyst`.
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
- Add DB persistence for per-attempt gate outcomes using
  `research_thesis_attempt_gate_results`. Rejected attempts must save every
  known rejecting gate and the exact feedback returned to the conductor;
  accepted attempts must save pass/skip/not-evaluated rows so `compiled` is not
  the only acceptance signal. Each row must also persist the gate's
  `Applicability` value from §3.3.
- Add DB persistence for `validator_challenge` using
  `research_thesis_attempt_challenges`. The row must be written before
  validation can reject the thesis and must include the challenged round,
  challenged thesis id, challenged rejection code, claim, evidence, and
  attempt linkage.
- Keep `rejection.json` generation as a read artifact sourced from the same
  validator result data that writes the DB rows. Do not let filesystem artifact
  persistence become the only source of gate-code truth.
- Keep `challenge.json` generation as a read artifact sourced from
  `research_thesis_attempt_challenges`. Do not let filesystem artifact
  persistence become the only source of validator-challenge truth.

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
10. `validator_challenge` is treated as an explicit meta-field exception: it may
   be documented outside OUTPUT, but schema-prompt drift must not require it in
   `ResearchThesis.model_fields` or the rules sidecar.
11. DOCTRINE prose contains no `ResearchThesis.model_fields` names and no
   `-> see <field>` / `→ see <field>` references.

## 9. Success Criteria

- Positive fixture passes Pydantic, live `validate_thesis_dict(...)`, and every
  prompt-declared predicate.
- Each negative fixture trips exactly its expected rejection code.
- DB tests prove `research_thesis_attempt_gate_results` records per-gate
  `pass`, `warn`, `reject`, `skipped_not_applicable`, and `not_evaluated`
  outcomes for every attempt, with `applicability` populated from the gate
  inventory.
- A rejected-attempt integration test proves `validation_failure_reason` stores
  the exact conductor retry feedback and the gate-results table stores the
  rejecting `gate_id`, `rejection_code`, `feedback_message`, and `evidence_json`.
- A validator-challenge integration test proves a challenge is persisted in
  `research_thesis_attempt_challenges` with attempt linkage and that
  `challenge.json` can be regenerated from the DB row.
- An accepted-attempt integration test proves a `compiled` thesis has persisted
  passing/skipped gate rows, not only `selected_for_execution=1`.
- `python scripts/check_prompt_drift.py` exits 0.
- Rejection codes never appear in the rendered LLM-facing OUTPUT section.
