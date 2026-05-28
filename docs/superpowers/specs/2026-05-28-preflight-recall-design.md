# Pre-Flight Recall, Corpus Synthesis, and Semantic Dedup for Thesis Planning

**Date:** 2026-05-28
**Status:** Design
**Scope:** Planner thesis-creation path only. Research subagent integration deferred.

## 1. Goal

When the planner enters a thesis-creation round, it must:

1. See the **top-K most-relevant prior theses + outcomes** for the family/goal at hand (recall).
2. See a **structured map of the mechanism landscape**: which dimensions are saturated, active, or unexplored (pattern surfacing).
3. Run an explicit **synthesis turn** before drafting, whose only job is to identify connections, gaps, and contradictions in the corpus.
4. Have its draft thesis checked against the prior corpus by **semantic similarity** (dedup), with a soft-gate override path.

The user-visible win is the elimination of re-litigation ("`EMA crossover 9/21` proposed again three jobs after being killed") and the explicit invitation for lateral thinking across kept/killed priors — without inventing a new retrieval system, since the primitives already exist in `research_memory.py`.

## 2. Non-Goals

- A unified prompt assembler / `ContextAssembler` covering all agent roles. Pre-flight needs exactly one injection point in `research_prompts.py`; full assembly refactor is a separate spec, and would conflict with the existing `docs/superpowers/plans/2026-05-04-prompt-variant-framework.md` work.
- Per-role token budgets / working-memory window enforcement.
- Wave 2: applying pre-flight to the research subagent's hypothesis-suggestion path. Same primitive, different injection point; out of scope here.
- Level 4 — latent dimension discovery via clustering / density estimation over the embedding space. Research-grade; earn the right to build it by shipping and measuring Levels 1–3 first.
- A new vector store. ChromaDB already in use via MemPalace (`research_memory._resolve_palace_dir`); a second store violates rule **B** (One home per concept).
- Replacing the thesis validator. Two rule edits, no architectural change.

## 3. Background

### 3.1 What already exists

`research_memory.py` exposes the retrieval primitives that this design composes:

| Function | Purpose | Where it reads from |
| --- | --- | --- |
| `list_past_theses(root, …)` | Paginated list of prior thesis attempts | `*_backtest_runs.db` via `BacktestRunDB.list_research_thesis_attempts` |
| `get_past_thesis(root, thesis_id, …)` | Full attempt detail for one thesis | Same |
| `list_experiment_results(root, …)` | Paginated list of backtest outcomes | `*_backtest_runs.db` |
| `get_experiment_result(root, thesis_id, …)` | Compact + detail tiers for one experiment | Same |
| `search_research_findings(query, …)` | Vector search over saved findings | MemPalace ChromaDB, `wing="research_findings"`, with `research_findings.jsonl` fallback |
| `save_research_finding(…)` | Adds a drawer to the palace | MemPalace ChromaDB |

These are wired into `research_tools_mcp.py` and surface to the conductor and subagents as MCP tools. The conductor's system prompt is built in `research_prompts._build_conductor_system_prompt` (called from `research_conductor.py:128`).

### 3.2 Why "tools-list + process-validator" is not enough

The current setup tells the agent what tools exist and asserts post-hoc that required tools were called. This catches "didn't try" but not:

- **Query quality.** `search_research_findings("EMA crossover")` passes the validator but misses a prior phrased "fast/slow MA cross 9-21" — keyword overlap is low while semantic similarity is high.
- **Engagement.** The agent may call a tool, read the result, and propose the same idea anyway.
- **Dedup.** Eyeballing 25 hypotheses for "is mine ~90% the same?" is unreliable for humans and LLMs alike.
- **Landscape awareness.** Tools surface instances. They don't surface the shape of the search space (which dimensions are saturated vs. untouched).
- **Synthesis.** No tool nudges the agent to *combine* a killed prior with a kept one to generate something new.

Pre-flight + landscape + synthesis-turn + dedup attack each of these. The validator stays as the behavior-side safety net, with two rule edits aligned to the new state of the world.

## 4. Architecture

### 4.1 Module layout

```
preflight_recall.py              ← new
  ├─ PreflightIntent             dataclass: (family, round_goal_text, draft_hypothesis?)
  ├─ build_prior_attempts_block  → str  (Level 1: top-K similar priors)
  ├─ build_landscape_block       → str  (Level 2: corpus summary by dimension)
  ├─ dedup_check                 → DedupResult  (Level 1 verification, post-draft)
  └─ _thesis_corpus_index        internal: lazy ChromaDB collection accessor

preflight_synthesis_turn.py      ← new
  └─ build_synthesis_prompt      → str  (Level 3: explicit lateral-thinking prompt)

research_prompts.py              ← edited
  └─ _build_conductor_system_prompt now composes:
       <existing instructions>
       + build_prior_attempts_block(intent)
       + build_landscape_block(family)
       + build_synthesis_prompt()  (when in thesis-creation stage)

research_conductor.py            ← edited
  └─ after thesis draft emitted, call dedup_check; on hit, return to agent
     with structured rejection until override or revision.

thesis_validator.py              ← edited
  ├─ Retire (or downgrade to soft-warn) "called list_past_theses" presence rule
  └─ Add rule: when ResearchThesis.dimension_novelty == "new" (or similar
              novelty flag), the thesis must reference at least one
              pre_flight_prior_thesis_id in contrasted_priors and explain
              the contrast.

backtest_run_db.py               ← edited (small)
  └─ Add aggregation query: theses grouped by (mechanism_dimension,
     validator_status) for landscape block. Pure read, no schema change.
```

No new dependency. ChromaDB and MemPalace are already in the import graph via `research_memory.py`.

### 4.2 Two retrieval queries, two purposes

| Pass | When | Query input | Returns | Used for |
| --- | --- | --- | --- | --- |
| **Pre-flight (broad)** | Before LLM call in thesis-creation round | `family + round_goal_text` | Top-K (default 8) priors with outcomes | Prime planner with relevant landscape |
| **Dedup (narrow)** | After agent emits draft thesis | Full `hypothesis + mechanism` of draft | Top-1 prior + cosine score | Soft-gate against near-duplicates |

Both share the same `thesis_corpus` ChromaDB wing; only the query text and `n_results` differ. The corpus is populated lazily from `*_backtest_runs.db` on first call per process (see §4.4).

### 4.3 The synthesis turn

In the thesis-creation stage, the planner runs **two LLM turns** instead of one:

1. **Synthesis turn.** System prompt extended with the prior-attempts block and the landscape block. User prompt:
   > "Given the priors above and the dimension landscape, identify 2–3 unexploited combinations, contradictions, or gaps you notice in the corpus. Do not draft a thesis yet."

   Output: a structured `synthesis_observations` list. Persisted to the round artifact for the validator and for audit.

2. **Drafting turn.** Same context plus the synthesis output. User prompt:
   > "Pick the most promising angle from your synthesis above and draft a single thesis. If you claim novelty, cite the priors you contrasted against in `contrasted_priors`."

The split forces lateral consideration before commitment. Single-turn prompting anchors the agent on the most-recent prior; two-turn splits that anchoring.

Cost: ~1.3× the planning round's token spend, against a higher hit rate of genuinely novel theses. The synthesis turn uses the cheaper model in the router (when present) — see §11.

### 4.4 Thesis corpus indexing

On first access per process, `preflight_recall._thesis_corpus_index()`:

1. Reads all rows from `*_backtest_runs.db` via existing `BacktestRunDB.list_research_thesis_attempts`.
2. For each row, upserts a drawer into ChromaDB `wing="thesis_corpus"` with id `f"thesis_{thesis_id}_attempt_{attempt_number}"`. The document is `f"{hypothesis}\n\n{mechanism}"`. Metadata includes `thesis_id, attempt_number, job_id, strategy_family, validator_status, mechanism_dimension, dimension_novelty, created_at_utc, run_id`.
3. Uses ChromaDB's default embedder (sentence-transformers all-MiniLM-L6-v2). No new dependency, no API cost.

Subsequent calls within a process check a tombstone file or row-count delta to skip re-upserts; deletions are handled by Chroma's idempotent upsert on the same id.

**Cold start.** If the corpus has fewer than `AUTORESEARCH_PREFLIGHT_COLD_START_THRESHOLD` (default 5) entries for a family, `build_prior_attempts_block` returns an empty block, `build_landscape_block` returns "no prior runs for this family", and `dedup_check` returns `DedupResult.skipped(reason="cold_start")`. A structured `PREFLIGHT_COLD_START` log line is emitted (rule **H**). No errors propagated.

### 4.5 Data flow

```
[planner enters thesis-creation round]
        │
        ▼
build_prior_attempts_block(intent)          ──┐
build_landscape_block(family)                 │  ◀── ChromaDB thesis_corpus
                                              │      + *_backtest_runs.db aggregations
_build_conductor_system_prompt ── augmented ──┘
        │
        ▼
[Turn 1: synthesis] ─────────► synthesis_observations
        │
        ▼
[Turn 2: drafting]  ─────────► draft thesis (with contrasted_priors when claiming novelty)
        │
        ▼
dedup_check(draft) ─────────► DedupResult
        │
        ▼
   cosine ≥ θ ?
   ┌────┴────┐
   no        yes
   │         │
   │         ▼
   │   [return to agent with match + score]
   │         │
   │   agent revises OR provides dedup_override_justification
   │         │
   └─────────┘
        │
        ▼
thesis_validator (with edited rules) ─────► accept / reject
        │
        ▼
[thesis enters run queue]
```

## 5. Components

### 5.1 `PreflightIntent`

```python
@dataclass(frozen=True)
class PreflightIntent:
    family: str                  # strategy family slug, e.g. "ema"
    round_goal_text: str         # planner's intent string for this round
    draft_hypothesis: str = ""   # filled only for dedup_check; empty for pre-flight
    draft_mechanism: str = ""    # same
```

Constructed by the conductor from existing fields. `round_goal_text` is the planner's already-existing round-intent string assembled in `autoresearch_orchestration.py`; no new field added upstream.

### 5.2 `build_prior_attempts_block(intent, *, k: int | None = None) -> str`

- `k` defaults to `_preflight_k()` (env: `AUTORESEARCH_PREFLIGHT_K`, default 8). Lazy accessor function, **not a module-level constant**, per CLAUDE.md hygiene rule on env-var-backed tunables.
- Query string: `f"{intent.family}: {intent.round_goal_text}"`.
- ChromaDB `query` with `where={"strategy_family": intent.family}` to scope by family. Falls back to unfiltered when the family-scoped result count < `k // 2`.
- Returns a markdown block:

```markdown
## Prior attempts to consider (top-8 by relevance)

### thesis_id=ema_pullback_v3 — KILLED (job 11, round 4)
- mechanism_dimension: trend_filters
- dimension_novelty: existing
- hypothesis: …(120 chars)…
- mechanism: …(120 chars)…
- validation_failure_reason: …(160 chars)…
- run_id: …

### thesis_id=… — KEPT (job 9, round 2)
…
```

Truncation budgets are per-entry (180–300 chars per field) — same constants already used in `_index_entry` and `_short_text` of `research_memory.py`.

### 5.3 `build_landscape_block(family) -> str`

Aggregates `*_backtest_runs.db` rows for the family by `mechanism_dimension` and `validator_status`. Pure SQL — added as a helper on `BacktestRunDB` (`list_dimension_summary(family)`).

Returns:

```markdown
## Mechanism landscape (family=ema)

Dimensions explored:
- trend_filters         → 12 attempts, 2 kept, 10 killed   (saturated)
- vol_regime_filter     →  4 attempts, 1 kept,  3 killed   (active)
- exit_management       →  3 attempts, 0 kept,  3 killed   (active)

Adjacent / underexplored:
- session_time × vol_regime → 0 attempts
- exit_management × trend_filters → 0 attempts

Killed-prior failure clusters:
- chop_sensitivity   (6 theses)
- parameter_overfit  (4 theses)
- execution_slippage (2 theses)
```

"Saturated" / "active" / "underexplored" are computed by thresholds on attempt counts (env-tunable via `AUTORESEARCH_LANDSCAPE_SATURATED_AT`, default 8). Failure-cluster names come from a small fixed taxonomy mapped from `rejection_reason` / `validation_failure_reason`. Unknown reasons fall into `other`.

### 5.4 `build_synthesis_prompt() -> str`

Stateless string returning the synthesis-turn user prompt. Exact text lives in this module so it's edited in one place (rule **B**, one home per concept).

### 5.5 `dedup_check(draft_intent) -> DedupResult`

```python
@dataclass(frozen=True)
class DedupResult:
    triggered: bool                  # True iff cosine >= threshold
    skipped: bool                    # True for cold start, empty corpus, etc.
    skip_reason: str = ""
    matched_thesis_id: str = ""
    matched_attempt_number: int = 0
    similarity: float = 0.0
    matched_outcome: str = ""        # validator_status of the match
    matched_summary: str = ""        # short hypothesis text of the match
```

- Query string: `f"{draft.hypothesis}\n\n{draft.mechanism}"`.
- `n_results=1`. Threshold from `_dedup_threshold()` (env: `AUTORESEARCH_DEDUP_THRESHOLD`, default 0.88).
- On trigger, the conductor surfaces the result to the agent as a structured rejection and requires either a revision or a populated `dedup_override_justification` field on the thesis.

### 5.6 `dedup_override_justification` on `ResearchThesis`

New optional field. Empty by default. When dedup triggered and the agent chose to override:

```json
{
  "dedup_override_justification": {
    "matched_thesis_id": "ema_pullback_v3",
    "matched_attempt_number": 2,
    "similarity": 0.91,
    "load_bearing_difference": "Prior thesis used 9/21 EMAs on 5m bars; this uses 9/21 EMAs on 1m bars with same-direction higher-TF filter. The HTF gate is the load-bearing change because the prior's failure mode was choppy 5m noise."
  }
}
```

Validator rejects when:
- `dedup_override_justification.load_bearing_difference` is missing or under 60 chars.
- `dedup_override_justification.matched_thesis_id` doesn't resolve to a real prior.

No silent overrides. Every override is in the thesis record and the trace.

## 6. Validator changes

Two rule edits, all other rules unchanged.

### 6.1 Retire (or downgrade to soft-warn)

**Rule:** "Conductor must have called `list_past_theses` in this round."

Disposition: **soft-warn** for one release cycle, then remove. Pre-flight makes the call's *presence* uninformative — the planner saw the data without the call. Soft-warn during the transition catches any code paths that haven't been migrated.

Location: `thesis_validator.py`. Specific rule key TBD during implementation — to be identified by grepping for `list_past_theses` in `thesis_validator.py` during the writing-plans phase.

### 6.2 Add: novelty-claim must cite contrasted priors

**Rule (new):** When `ResearchThesis.dimension_novelty in {"new", "new_dimension"}` (or equivalent novelty marker — exact field confirmed during writing-plans), the thesis must:

- Populate `contrasted_priors: list[ContrastedPrior]` with at least one entry.
- Each entry references a `prior_thesis_id` that exists in the corpus.
- Each entry includes a `contrast_explanation` of at least 80 chars.

Soft-rejects on first violation within the round (returns to agent with structured complaint); hard-rejects on the second violation within the same round.

### 6.3 Rules explicitly preserved

- L6/L7 tool-order gates (`tests/test_l6_l7_tool_order_gates.py`).
- Diagnostics-before-verdict.
- `mechanism_dimension` required.
- Evidence count and shape.
- All schema rules.
- Cross-stage invariants.

## 7. Configuration

Lazy accessor functions, **not module-level constants** (CLAUDE.md hygiene rule):

| Function | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `_preflight_k()` | `AUTORESEARCH_PREFLIGHT_K` | `8` | Top-K size for pre-flight block |
| `_dedup_threshold()` | `AUTORESEARCH_DEDUP_THRESHOLD` | `0.88` | Cosine cutoff for dedup trigger |
| `_cold_start_threshold()` | `AUTORESEARCH_PREFLIGHT_COLD_START_THRESHOLD` | `5` | Min corpus size to enable pre-flight |
| `_landscape_saturated_at()` | `AUTORESEARCH_LANDSCAPE_SATURATED_AT` | `8` | Attempt count above which a dimension is "saturated" |
| `_synthesis_enabled()` | `AUTORESEARCH_SYNTHESIS_TURN_ENABLED` | `true` | Kill switch for the synthesis turn |

Each accessor validates its env var (int parse, range check) and raises with the named env var on bad input.

## 8. Error handling

| Failure | Behavior |
| --- | --- |
| ChromaDB unavailable / `_resolve_palace_dir` fails | All blocks return empty strings; `dedup_check` returns `skipped(reason="palace_unavailable")`. Structured log line. Round proceeds. |
| Corpus empty for family | Cold-start path (§4.4). |
| Embedding call fails | Same as ChromaDB-unavailable. |
| Dedup overrides itself recursively (agent keeps overriding) | Per-round override count capped at 1. Second override attempt within a round → hard-reject by validator. |
| Synthesis turn produces malformed output | Validator on synthesis-turn output: must contain ≥1 numbered observation. Malformed → one retry, then proceed to drafting without synthesis (logged as `SYNTHESIS_TURN_DEGRADED`). |
| `mechanism_dimension` missing on a prior | Bucketed as `unknown_dimension` in landscape block; not silently dropped. |

All failures are **fail-open for retrieval** (no recall ≠ broken run) and **fail-loud for validator rules** (a missing `contrasted_priors` on a novelty thesis is a real rejection).

## 9. Testing strategy

Test against real data from `*_backtest_runs.db`, with no toy names (CLAUDE.md user testing rules).

### 9.1 Unit (per module)

- `preflight_recall`:
  - `build_prior_attempts_block` returns top-K by family, falls back unfiltered on small family.
  - `build_landscape_block` aggregates correctly against a fixture DB with known mechanism_dimension/validator_status distribution.
  - `dedup_check` triggers on a known near-duplicate (re-embed a real prior thesis with paraphrased wording and assert it matches).
  - Cold-start returns empty block + skipped result; structured log emitted.
  - Each lazy accessor reads its env var at call time, not at import.

- `preflight_synthesis_turn`: prompt assembly is stable; malformed-output handling triggers retry then degradation.

- `backtest_run_db.list_dimension_summary`: aggregations match hand-computed expectations on a fixture.

### 9.2 Integration

- End-to-end planner round with a populated corpus → asserts that `_build_conductor_system_prompt` output contains the prior-attempts block, the landscape block, and (when stage is thesis-creation) the synthesis prompt.
- Dedup trigger → agent override path → validator accepts the override only when `load_bearing_difference` and `matched_thesis_id` are valid.
- Dedup trigger → agent revises → second draft passes dedup.
- Validator: novelty thesis without `contrasted_priors` is rejected; with valid `contrasted_priors` is accepted.
- Validator soft-warn: `list_past_theses` not called → emits warning, does not reject.
- Cold start: brand-new family with empty `*_backtest_runs.db` → planner runs cleanly with empty pre-flight blocks.

### 9.3 Rerun & state-transition tests (CLAUDE.md rule)

- Second run after first populates corpus: incremental upsert into `thesis_corpus`, no duplicates, no rebuild.
- Manual deletion of a row from `*_backtest_runs.db` → ChromaDB drawer stays (acceptable; documented). Re-index is a separate operation.

### 9.4 Behavior assertions, not structural

- Assertions check counts, scores, and content (`assert "ema_pullback_v3" in block`), never just `assert block is not None`. Per rule **G**.

## 10. Migration plan

One PR, one deliverable, in this order:

1. `preflight_recall.py` + `preflight_synthesis_turn.py` modules with full test coverage.
2. `BacktestRunDB.list_dimension_summary` helper.
3. `research_prompts._build_conductor_system_prompt` extended to compose the new blocks.
4. `research_conductor.py` two-turn flow + dedup-check call site.
5. `thesis_validator.py` rule edits (soft-warn retire + novelty-citation add).
6. End-to-end test against a real fixture DB.

No staged rollout flag. Behavior change is contained to thesis-creation rounds; cold-start path covers new families with no corpus.

## 11. Open considerations (deliberately *not* in scope, recorded for future specs)

- **Model routing within the synthesis turn.** Synthesis is a meta-reasoning task that doesn't need Opus tokens. When a model router exists (Cost & Workflow Optimization spec), route the synthesis turn to a cheaper tier. For now: same model as drafting.
- **Wave 2: research subagent integration.** Same `preflight_recall` module, second injection point in the subagent's prompt build path.
- **Level 4: latent dimension discovery.** Clustering / density estimation over the embedding space. Earn the right by measuring whether Levels 1–3 hit a ceiling first.
- **Cross-family pre-flight.** Currently `where={"strategy_family": ...}`. A future spec could add controlled cross-family recall for genuinely orthogonal mechanisms.
- **Reconciliation with `prompt-variant-framework`.** Touched in §2; needs its own brainstorm.

## 12. Success criteria

- A planning round on a populated `ema_backtest_runs.db` shows the prior-attempts block, landscape block, and synthesis observations in its system prompt and round artifact.
- A near-duplicate proposed thesis (paraphrased version of a known prior) is caught by dedup, with the matched `thesis_id` and similarity surfaced to the agent.
- A novelty-claiming thesis without `contrasted_priors` is rejected by the validator with a clear, actionable reason.
- A cold-start run (new family, empty corpus) completes without errors and produces a thesis.
- Two consecutive runs against the same `*_backtest_runs.db` do not produce duplicate ChromaDB drawers.
- Token cost per planning round is at most ~1.5× the pre-change baseline (synthesis turn + injected blocks). Documented in the run artifact's usage block.
