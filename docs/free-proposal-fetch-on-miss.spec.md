# Spec: free mechanism proposal + fetch-on-miss + self-compounding primitives

Status: design (reviewed iteratively). Implements the "agent proposes the mechanism it
believes in, not the one its columns allow" capability, with safe persistence so the
research vocabulary compounds across rounds and jobs. Code anchors are from the current
tree (verified, not assumed).

## 1. Goal
Stop the agent local-optimizing inside a fixed ~13-column basis. Let it propose the
mechanism its research implies; when that needs a feature/column that doesn't exist, the
system either BUILDS it (computable from data on hand) or HALTS and asks an operator to
FETCH the data. Every capability the agent causes to be built is **tagged agent-created**,
recorded with provenance, and — for safe cases — promoted so future rounds reuse it.

## 2. Conceptual model (settles "is a primitive just a column?")
`requested_primitive` = a NEW PERMANENT CAPABILITY. Two kinds:
  - ENTRY-FEATURE primitive → a new entry-time COLUMN the `rule` can filter on (data).
  - MANAGEMENT primitive     → new runtime behavior (stop/target/time/exit): a config
                               lever + code in exits.py/strategy.py (behavior, not a column).
Both persist. The `rule` grammar stays strict + leakage-checked (causal_rule.py); we do
NOT loosen it. "I need something new" is expressed via `requested_primitive`, never by a
rule over a phantom column.

## 3. Behaviors to add

### 3a. Free proposal (prompt)
research_prompts.py: reframe so the available-column list is "what exists today," not a
ceiling. New language: "Propose the mechanism your research implies; do not weaken it to
fit existing columns. If it needs a feature that isn't an available column, name it via
requested_primitive and declare the data it requires." Keep the derived column list (it is
what's instantly screenable) but remove the ceiling framing.

### 3b. Structured requested_primitive
research_types.py MechanismProposal.requested_primitive: today a bare str. Replace with a
validated object (or sibling fields):
  - name: str (snake_case)
  - kind: "entry_feature" | "management"
  - description: str
  - required_data: list[str]  (raw inputs needed, e.g. ["ohlcv"], ["trade_signed_volume"])
Contract unchanged: actionable ⇒ rule + (proposed_change | requested_primitive).

### 3c. Compiler decision: build vs fetch-on-miss
Classify the primitive in the compiler/builder path:
  - COMPUTABLE from data already in AUTORESEARCH_DATA_ROOT (OHLCV/calendar/regime feed)
      → builder writes the extractor/behavior (today's path).
  - NEEDS DATA NOT PRESENT
      → DO NOT fabricate. Emit a structured data-acquisition request and HALT the thesis
        into manual_review with operator notification — reuse the existing terminal-state
        machinery (autoresearch_orchestration.py: halted_reason / halted_thesis_id /
        manual_review_theses) and the fail-loud-with-remediation precedent
        (feature_table.FeatureTableMissingError / docs/research-run-prerequisites.md).
  Decision input = `required_data` checked against a declared "available raw inputs"
  manifest for the data root (new, small).

### 3d. Data-acquisition request format (the "ask to fetch")
On a needs-data halt, write a structured request alongside round artifacts + the operator
notification: { feature_name, kind, description, required_data (+granularity),
candidate_sources, requesting_thesis_id, created_by: "agent", created_at (UTC) }.
Operator provisions the data into the root → re-run clears the halt (same pattern as
provisioning regime_labels.parquet today).

## 4. Persistence — the EXACT current reality (verified)
On a successful build, `_record_builder_promotion_candidate` (compiler_builder.py:892)
copies changed files into `runtime/builder-promotions/<family>/<thesis_id>/` and appends a
manifest (`promoted_files`, `promotion_dir`) to `runtime/builder-promotion-queue.jsonl`
(:937). The manifest surfaces in run state (autoresearch_orchestration.py:208-209).

Gaps found (these are what §4a/§4b solve):
  - `builder_capability_registry.jsonl` is **read-only in code** — `_load_builder_capability_registry`
    / `_best_seeded_capability` read it to RE-SEED a fresh builder workspace, but NOTHING
    writes it. The self-learning re-seed can only fire on a human-curated registry.
  - There is **no consumer that APPLIES the promotion queue** to the canonical modules.
    So a built entry-feature never becomes a standing `_FEATURE_COLUMNS` column; a
    next-round plain `rule` over it would fail. "Saved for next round" is, today, a manual
    human merge.
  - This human gate is deliberate (don't silently merge agent-generated behavior code).
    The solutions below keep that gate for behavior, and open a safe auto-path for data.

### 4a. Write the capability registry from the build path (gated, agent-tagged)
Close the read-only gap so the builder learns from its OWN past builds, not just
human-seeded ones.
  - On a build that PASSES (validation + leakage), APPEND an entry to
    `builder_capability_registry.jsonl`:
      { family_name, kind, missing_primitives, config_change_keys, diagnostic_keys,
        promoted_files, promotion_dir, thesis_id, harvest_verdict,
        created_by: "agent", created_at (UTC) }.
  - Gate: write only on a passing build (and prefer graduated); never from a failed build,
    so the seed pool isn't poisoned. Dedup by (family_name, kind, capability signature):
    update in place rather than append duplicates.
  - Effect: `_best_seeded_capability` can now re-seed from agent-created entries — the
    builder compounds its own work. Every entry is tagged `created_by: "agent"` so a human
    can audit / prune the seed pool.

### 4b. Persist ENTRY-FEATURE columns ON BUILD; adopt the RULE on graduation (agent-tagged)
Key principle: **a column existing does not cause overfitting — searching many rules and
keeping the in-sample winner does.** So gate the *hypothesis*, not the *feature*. Two
separate events:

  - ON BUILD (immediately): when an `entry_feature` primitive is computed and passes
    `_assert_leakage_guard` + an auto-generated point-in-time test, register the column in
    an **agent-feature registry** (new: `runtime/agent_features.jsonl`) that the
    feature-table build UNIONs into the schema. Record:
    { column, family_name, definition (extractor ref or declarative formula), required_data,
      requesting_thesis_id, requesting_thesis_verdict, status: "exploratory",
      created_by: "agent", created_at (UTC) }.
    Effect: the column becomes a STANDING entry-time column from the NEXT round on,
    auto-surfaces via research_prompts._entry_filter_columns (no prompt edit), and is
    REUSABLE — another idea next round finds it already computed (no re-request, no
    recompute) and sees the prior verdict (dedup memory). Persisting it is just caching
    computed data; it is harmless on its own.
  - ON GRADUATION: when a thesis USING that column reaches walkforward verdict `graduated`,
    flip the column's `status` → "validated" and adopt the RULE/mechanism into the strategy
    (the config change becomes live). Graduation gates *adoption of the mechanism*, NOT
    whether the feature is available for research.
  - MANAGEMENT primitives are NOT auto-applied — generated exits/strategy behavior stays on
    the human-reviewed promotion queue (§4 reality). Their config key may enter the family's
    allowed_config_keys on review (mirrors how trail_after_r/gap_exclude became levers).
  - Hardening note (future): prefer agent entry-features expressed as a DECLARATIVE,
    sandboxed expression over existing raw inputs (rule-like grammar for feature defs) so
    persisting a column never merges arbitrary Python — only a validated spec. Until then,
    the generated extractor is gated by leakage + point-in-time test + the agent-created tag.

## 5. Agent-created tagging (cross-cutting)
Every capability the agent causes to exist carries provenance: `created_by: "agent"`,
`thesis_id`, `created_at` (UTC, tz-aware per CLAUDE.md J), and its definition/formula.
Applies to: data-acquisition requests (3d), capability-registry entries (4a), agent-feature
registry columns (4b), and promotion-queue manifests. Standing agent-created columns load
from the agent-feature registry (not hand-edited into `_FEATURE_COLUMNS`) so static vs
agent-authored stays visibly separated for audit, trust, and pruning.

## 6. Look-ahead discipline (unchanged, enforced)
Every promoted/standing feature passes `_assert_leakage_guard` (entry-time vs outcome) and
a point-in-time test (only data at/before the entry bar; prior sessions for daily;
known-in-advance dates for calendar). Free to PROPOSE; never free to leak. The rule
validator (causal_rule.validate_entry_rule_references) stays strict.

## 7. Cost of many columns (and why it is NOT solved by hiding them)
Persisting every built column (§4b) raises two real costs — neither is overfitting-by-
existence, and neither is fixed by withholding columns:

1. **Compute** — every round recomputes all columns, so more columns = slower runs. Fix =
   **pruning**: an agent-created column that no kept (validated) thesis has used after N
   rounds is a prune candidate — log it via the agent-feature registry, never silent-drop.
   This is cache eviction (housekeeping), not correctness.

2. **Holdout decay** — testing enough hypotheses against the SAME holdout means one
   eventually looks good by pure luck, so a fixed holdout "wears out" as more ideas hit it.
   More columns → more candidate rules → faster decay. Fix is NOT fewer columns, it is:
   - **Forward harvest** — adoption is gated on registered predictions validated against
     genuinely NEW future data the agent never saw (walkforward). Luck cannot pre-fit data
     that came after the idea, so this is robust to how much was searched in-sample.
   - **Selection-aware bar** — raise the acceptance threshold as the number of hypotheses
     tested grows (more guesses ⇒ more flukes ⇒ demand more proof). Track the count and
     scale the bar.

So: overfitting is controlled on the HYPOTHESIS side (forward harvest + dedup +
selection-aware bar + the causal-story requirement), and column proliferation is a
compute/clutter concern handled by pruning — not by gating feature availability.

## 8. Affected code (anchors)
- research_prompts.py — free-proposal reframe (keep derived column list).
- research_types.py — structured requested_primitive (name/kind/description/required_data).
- autoresearch_research.py — carry kind/required_data on the thesis (next to
  requested_primitives/mechanism_rule); route needs-data → halt.
- compiler_builder.py — computable-vs-needs-data classifier; WRITE capability registry
  (4a); record promotion candidates (exists, :892).
- autoresearch_orchestration.py — needs_data halt state + notification (reuse :208-228).
- feature_table.py / feature_table_extractors.py — union agent-feature registry into the
  schema (4b); leakage guard on agent columns.
- runtime/agent_features.jsonl, runtime/builder_capability_registry.jsonl,
  runtime/builder-promotion-queue.jsonl — provenance stores.
- compiler/data manifest (new) — declares raw inputs available in the data root.

## 9. Tests / verification
- Structured requested_primitive validates; bare-string migrated/rejected.
- Computable primitive → builds + screens; needs-data primitive → halts into manual_review
  + emits acquisition request (no fabrication).
- 4a: a passing build appends a `created_by:"agent"` capability-registry entry; a later
  related build re-seeds from it.
- 4b (persist-on-build): a built+leakage-passing entry_feature appears in the agent-feature
  registry with status "exploratory" and shows up in ENTRY_TIME_COLUMNS + the derived prompt
  list the NEXT round (real-parquet end-to-end test), tagged agent-created — even when its
  originating thesis did NOT graduate (reuse without re-request).
- 4b (adopt-on-graduation): when a thesis using that column graduates, its status flips to
  "validated" and the rule's config change is adopted; column availability is unchanged by
  the verdict.
- Pruning: an agent column unused by any validated thesis after N rounds is flagged a prune
  candidate (logged, not silent-dropped).
- Leakage guard + point-in-time test on every agent column at registration.
- MANAGEMENT primitive is NOT auto-applied (stays on the promotion queue).
- Full suite + pre-commit + scripts/check_prompt_drift.py green.

## 10. Rollout (safe-first)
1. 3a (prompt reframe) + 3b (structured requested_primitive) — low risk.
2. 3c + 3d needs-data halt (reuses halt machinery) — medium.
3. 4a capability-registry write (gated, agent-tagged) — medium; unlocks builder self-learning.
4. 4b persist-on-build entry-feature columns (agent-feature registry, leakage+PIT gated,
   agent-tagged) + adopt-the-rule-on-graduation + pruning + selection-aware bar — highest
   risk; do last, with the end-to-end reuse + leakage + tag tests.
