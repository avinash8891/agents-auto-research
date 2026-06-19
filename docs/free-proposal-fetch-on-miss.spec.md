# Spec: free mechanism proposal + fetch-on-miss + self-compounding primitives

Status: design (reviewed iteratively). Implements the "agent proposes the mechanism it
believes in, not the one its columns allow" capability, with safe persistence so the
research vocabulary compounds across rounds and jobs. Code anchors are from the current
tree (verified, not assumed).

## 1. Goal
Stop the agent local-optimizing inside a fixed column basis. Let it propose the
mechanism its research implies; when that needs a feature/column that doesn't exist, the
system either BUILDS it (computable from data on hand) or HALTS and asks an operator to
FETCH the data. Every capability the agent causes to be built is tagged agent-created,
recorded with provenance, and, for safe cases, promoted so future rounds reuse it.

## 2. Conceptual model
`requested_primitive` = a NEW PERMANENT CAPABILITY. Two kinds:
  - ENTRY-FEATURE primitive -> a new entry-time COLUMN the `rule` can filter on (data).
  - MANAGEMENT primitive -> new runtime behavior (stop/target/time/exit): a config lever
    + code in exits.py/strategy.py (behavior, not a column).

The `rule` grammar stays strict and leakage-checked (causal_rule.py); we do NOT loosen it.
"I need something new" is expressed via `requested_primitive`, never by a rule over a
phantom column.

An entry feature is not a thesis verdict. If the thesis that requested a feature is
discarded, only that thesis/rule is treated as failed. The feature remains neutral and
available as exploratory vocabulary for future mechanisms.

## 3. Behaviors to add

### 3a. Free proposal (prompt)
research_prompts.py: reframe so the available-column list is "what exists today," not a
ceiling. New language: "Propose the mechanism your research implies; do not weaken it to
fit existing columns. If it needs a feature that isn't an available column, name it via
requested_primitive and declare the data it requires." Keep the derived column list (it is
what's instantly screenable) but remove the ceiling framing.

### 3b. Structured requested_primitive
research_types.py `MechanismProposal` gets a `requested_primitive` object:
  - name: str (snake_case)
  - kind: "entry_feature" | "management"
  - description: str
  - required_data: list[str] (raw inputs needed, e.g. ["ohlcv"], ["trade_signed_volume"])

Actionable contract changes to:
  - actionable=false: no `proposed_change` or `requested_primitive` required.
  - actionable=true: requires predictions and either `proposed_change` OR
    `requested_primitive`.
  - if both are present, `proposed_change` is the immediate config change and
    `requested_primitive` is the missing capability needed to support it.

`ResearchThesis.requested_primitives` remains the downstream list form derived from
`MechanismProposal.requested_primitive.name`.

### 3c. Compiler decision: build vs fetch-on-miss
Classify the primitive in the compiler/builder path:
  - COMPUTABLE from available raw inputs -> builder writes the extractor/behavior.
  - NEEDS DATA NOT PRESENT -> DO NOT fabricate. Emit a structured data-acquisition
    request and HALT the thesis into manual_review with operator notification, reusing
    the existing terminal-state machinery (autoresearch_orchestration.py:
    halted_reason / halted_thesis_id / manual_review_theses).

Decision input = `required_data` checked against `runtime/raw_input_manifest.json` only
when a requested primitive appears:

```json
{"available_raw_inputs": ["ohlcv", "calendar", "regime_feed"]}
```

The manifest is operator-maintained for the data root. Missing or malformed manifest =
fail loud before building requested primitives, but must not break ordinary research runs
that do not request new primitives.

### 3d. Data-acquisition request format
On a needs-data halt, write a structured request alongside round artifacts + the operator
notification:

```json
{
  "feature_name": "signed_volume",
  "kind": "entry_feature",
  "description": "...",
  "required_data": [{"name": "trade_signed_volume", "granularity": "tick"}],
  "candidate_sources": [],
  "requesting_thesis_id": "...",
  "created_by": "agent",
  "created_at": "UTC timestamp"
}
```

Write the request at:

```text
runtime/jobs/job-<id>/research/round-<n>/data_acquisition_request.json
```

State bookkeeping:
  - set `halted_reason = "needs_data"` (do not overload `"requires_code_change"`).
  - set `halted_thesis_id` to the requesting thesis.
  - append the structured request to `data_requests`.
  - set `next_action.type = "manual_review"`.

Operator provisions the data into the root and updates `runtime/raw_input_manifest.json`.
On re-run, auto-clear the halt only if:
  - `halted_reason == "needs_data"`;
  - `halted_thesis_id == request.requesting_thesis_id`;
  - the request path is under that halted job/round artifact directory; and
  - every `required_data.name` in the request is now present in the manifest.

If any check fails, stay halted and report the mismatch.

## 4. Persistence - current reality
On a successful build, `_record_builder_promotion_candidate` copies changed files into
`runtime/builder-promotions/<family>/<thesis_id>/` and appends a manifest to
`runtime/builder-promotion-queue.jsonl`. The queue is review input; it does not apply
changes to canonical modules.

Gaps found:
  - `builder_capability_registry.jsonl` is read-only in code: `_load_builder_capability_registry`
    / `_best_seeded_capability` read it to re-seed a fresh builder workspace, but nothing
    writes it.
  - There is no consumer that applies the promotion queue to canonical modules.
  - This human gate stays for behavior code. The safe auto-path below applies only to
    entry-feature metadata that passed leakage and point-in-time checks.

### 4a. Write the capability registry from the build path
On a build that passes validation, append an entry to
`runtime/builder_capability_registry.jsonl`:

```json
{
  "family_name": "ema",
  "kind": "entry_feature",
  "missing_primitives": ["rvol_spike"],
  "config_change_keys": [],
  "diagnostic_keys": [],
  "promoted_files": [],
  "promotion_dir": "...",
  "thesis_id": "...",
  "build_status": "passed",
  "created_by": "agent",
  "created_at": "UTC timestamp"
}
```

JSONL stays append-only. Dedup is latest-wins by `(family_name, kind, missing_primitives,
config_change_keys, diagnostic_keys)` when reading; compaction can be a later cleanup.

### 4b. Persist ENTRY-FEATURE columns on build
Key principle: a column existing does not cause overfitting; searching many rules and
keeping the in-sample winner does. Gate the hypothesis, not the feature.

ON BUILD: when an `entry_feature` primitive is computed and passes `_assert_leakage_guard`
+ a point-in-time test, register the column in `runtime/agent_features.jsonl`. Column
names are globally unique. A second request for the same column name must use the same
formula; a different formula is a hard validation error, not an auto-rename.
Formula dependencies may reference static columns or active agent features for the same
strategy family. Inactive or unknown dependencies block registration. The dependency
graph must be acyclic.

Auto-persisted entry features must be declarative formulas. V1 supports only allowlisted
arithmetic and time-safe helpers over existing static columns or active same-family agent
features. Arbitrary generated Python extractors are treated like management primitives:
they can be built as artifacts, but they stay behind the human promotion queue and do not
become standing columns automatically.

```json
{
  "column": "rvol_spike",
  "formula": "rvol / rolling_mean(rvol, 20)",
  "required_data": ["ohlcv"],
  "requesting_thesis_id": "...",
  "families": {
    "ema": {
      "status": "exploratory",
      "requesting_thesis_id": "...",
      "requesting_thesis_verdict": "build_passed"
    }
  },
  "created_by": "agent",
  "created_at": "UTC timestamp"
}
```

If another strategy family requests the same column with the same formula, attach that
family to the existing registry entry by adding a new `families.<family>` status. Do not
create a duplicate entry.

feature_table.py gets one schema helper that returns static `_FEATURE_COLUMNS` plus
active agent-feature registry columns for the requested strategy family. `ENTRY_TIME_COLUMNS`,
`_feature_columns()`, leakage classification, evidence rendering, and prompt rendering all
derive from that same helper so the registry is not a shadow schema.

ON WALKFORWARD GRADUATION: flip the column status to "validated" for that strategy family
only. Do not auto-adopt the free-form rule into live strategy behavior here; live behavior
still enters through the existing config/code promotion path. The validated feature simply
remains available for future research in that family.

ON PRUNING: flip the family status to "inactive". Do not delete the registry entry; the
definition and history remain available so future agents can see why it stopped surfacing.
Any active dependent feature for that family also becomes inactive with reason
`inactive_dependency`.

ON REACTIVATION: a new thesis may request an inactive feature again. Reactivation flips
that family's status from "inactive" to "exploratory" only if the formula is unchanged
and every dependency is active for that family.

MANAGEMENT primitives are NOT auto-applied. Generated exits/strategy behavior stays on
the human-reviewed promotion queue.

Declarative formula v1 deliberately excludes conditionals, joins, groupby, custom
functions, and Python snippets. Those can be revisited after the registry and dependency
rules are proven.

## 5. Agent-created tagging
Every capability the agent causes to exist carries provenance: `created_by: "agent"`,
`thesis_id`, `created_at` (UTC, tz-aware), and its definition/formula. Applies to:
data-acquisition requests, capability-registry entries, agent-feature registry columns,
and promotion-queue manifests.

## 6. Status vocabulary
Use these names consistently:

| Status | Source | Meaning |
| --- | --- | --- |
| build_passed | compiler_builder.py | generated code/config passed builder validation |
| keep/discard | experiment_decision.py | in-sample experiment decision |
| graduated/demoted | walkforward.py | future-window validation result |
| exploratory | runtime/agent_features.jsonl | feature is available for research but not validated |
| validated | runtime/agent_features.jsonl | feature was used by a graduated thesis |
| inactive | runtime/agent_features.jsonl | feature is retained for history but not surfaced |
| needs_data | state.halted_reason | operator must provision raw input before build can resume |

## 7. Look-ahead discipline
Every standing feature passes `_assert_leakage_guard` (entry-time vs outcome) and a
point-in-time test (only data at/before the entry bar; prior sessions for daily;
known-in-advance dates for calendar). Free to PROPOSE; never free to leak. The rule
validator stays strict.

## 8. Cost of many columns
Persisting every built column raises two real costs:

1. Compute: every round recomputes all columns. Fix = pruning: an agent-created column
   that no validated thesis has used after N rounds is a prune candidate, logged via the
   agent-feature registry and never silent-dropped.
2. Holdout decay: testing many hypotheses against the same holdout wears it out. Fix =
   forward harvest plus a selection-aware acceptance bar keyed by the number of tested
   hypotheses.

## 9. Affected code
- research_prompts.py - free-proposal reframe and new output shape.
- research_types.py - `MechanismProposal.requested_primitive` and validator contract.
- autoresearch_research.py - carry kind/required_data onto the thesis; route needs-data
  to manual_review with `halted_reason = "needs_data"` and `data_requests`.
- compiler_builder.py - computable-vs-needs-data classifier; append capability registry;
  latest-wins read dedup.
- autoresearch_orchestration.py - needs_data halt state + notification.
- feature_table.py / feature_table_extractors.py - one schema helper that unions static
  columns and active `runtime/agent_features.jsonl` columns for a strategy family.
- runtime/raw_input_manifest.json - available raw inputs.
- runtime/agent_features.jsonl, runtime/builder_capability_registry.jsonl,
  runtime/builder-promotion-queue.jsonl - provenance stores.

## 10. Tests / verification
- Structured `requested_primitive` validates; actionable without both `proposed_change`
  and `requested_primitive` rejects; actionable with only `requested_primitive` passes.
- Prompt drift check covers `requested_primitive`.
- Computable primitive builds; needs-data primitive halts into manual_review and emits
  acquisition request without fabrication.
- Missing/malformed `runtime/raw_input_manifest.json` fails loud only for requested
  primitives, not ordinary research runs.
- `needs_data` resume clears only when the request matches `halted_thesis_id`, lives under
  the halted round artifact directory, and all required inputs are now in the manifest.
- Auto-persisted entry_feature formulas accept only the v1 declarative expression subset;
  generated Python extractors stay behind promotion review.
- Passing build appends a `created_by:"agent"` capability-registry entry; later reads use
  latest-wins dedup.
- Built+leakage-passing entry_feature appears in `runtime/agent_features.jsonl` with
  family status "exploratory" and shows up in ENTRY_TIME_COLUMNS + the derived prompt
  list for that family the next round.
- Same column + same formula attaches a new family status; same column + different
  formula fails validation.
- Formula dependencies on unknown, inactive, cross-family-only, or cyclic agent features
  fail validation.
- Walkforward graduation flips that family's feature status to "validated"; it does not
  auto-apply strategy behavior or validate the feature for other families.
- Discarding the requesting thesis does not mark the feature failed; failure/novelty
  memory applies to the specific thesis/rule.
- Inactive features can reactivate only through a new same-formula request with active
  dependencies.
- Promotion-queue manifests include `created_by:"agent"` and UTC `created_at`.
- Pruning flips unused exploratory family statuses to "inactive", cascades inactive status
  to same-family dependents, and never deletes entries.
- Leakage guard + point-in-time test on every agent column at registration.
- MANAGEMENT primitive is not auto-applied.
- Full suite + pre-commit + scripts/check_prompt_drift.py green.

## 11. Rollout
1. Prompt reframe + structured `requested_primitive`.
2. Needs-data halt + `runtime/raw_input_manifest.json`.
3. Capability-registry append + latest-wins read.
4. Agent-feature registry + schema helper + walkforward status flip + inactive pruning.
