# Pre-Flight Recall, Corpus Synthesis, and Semantic Dedup for Thesis Planning

**Date:** 2026-05-28
**Status:** Design (final — grounded against `research_prompts.py`, `research_conductor.py`, `thesis_validator.py`, `research_memory.py`)
**Scope:** Conductor's thesis-creation path only. Research subagent integration deferred.

## 1. Goal

When the conductor builds the **per-round user prompt** for thesis creation, the agent must enter the LLM call with:

1. **Recall** — top-K most-relevant prior theses + outcomes (Level 1).
2. **Pattern surfacing** — mechanism landscape (saturated vs active vs unexplored dimensions) (Level 2).
3. **Synthesis substrate** — killed/kept pairs grouped by dimension, ready for hybrid proposals (Level 3).
4. **Synthesis turn** — explicit lateral-thinking LLM turn before drafting, asked to find combinations / contradictions / gaps (Level 3).
5. **Post-draft semantic dedup** — soft-gate against near-duplicates with auditable override.

The user-visible win is the elimination of re-litigation ("`EMA crossover 9/21` proposed again three jobs after being killed") and the explicit invitation for lateral thinking across kept/killed priors — without inventing a new retrieval system, since the primitives already exist in `research_memory.py`.

## 2. Non-Goals

- Unified prompt assembler / `ContextAssembler` for all agent roles. Pre-flight needs exactly one injection point in `research_conductor.py`'s user-prompt construction. A full assembly refactor is a separate spec and conflicts with the in-flight `docs/superpowers/plans/2026-05-04-prompt-variant-framework.md` work.
- Per-role token budgets / working-memory window enforcement.
- Wave 2: applying pre-flight to the research subagent's hypothesis-suggestion path. Same primitive, different injection point.
- Level 4 — latent dimension discovery via clustering / density estimation over the embedding space.
- A new vector store. ChromaDB is already in use via MemPalace (`research_memory._resolve_palace_dir`); a second store violates rule **B** (One home per concept).
- Retiring `_validate_process`'s `_REQUIRED_PROCESS_TOOLS`. That list is `("list_experiment_results", "web_search")` — `list_past_theses` was never in it. Nothing to retire.

## 3. Background — ground truth from code

### 3.1 Where the system prompt and user prompt are built

**System prompt — static per family.** `research_prompts._build_conductor_system_prompt(strategy_description)` (line 18) takes a single argument: the static per-family description. It contains identity, tools list, schema, doctrine. It does **not** vary round-to-round.

**User prompt — round-specific.** Built inline in `research_conductor.py:131–175`. Composed from:
- `research_round` (number)
- `_render_resolution_context(resolution_context)`
- `LATEST EXPERIMENT OUTCOME:` — full `latest_outcome` JSON
- `EXPERIMENT RESULTS SUMMARY:` — `experiment_results` string
- Trades / events / diagnostics file paths
- `rejection_feedback` (when set)
- `rejection_artifact.render_rejection_block` + `compute_escalation_directive` (current-job context)

**There is no `round_goal_text` or `round_intent` field.** The "goal" is implicit: "propose the next thesis." Round-specific intent is carried by `latest_outcome`, `rejection_feedback`, and the rejection-pattern block.

**Implication:** pre-flight's injection point is the **user prompt**, not the system prompt.

### 3.2 What's already in `research_memory.py`

| Function | Purpose | Source |
| --- | --- | --- |
| `list_past_theses(root, …)` | Paginated prior thesis attempts | `*_backtest_runs.db` via `BacktestRunDB.list_research_thesis_attempts` |
| `get_past_thesis(root, thesis_id, …)` | Full attempt detail | Same |
| `list_experiment_results(root, …)` | Paginated outcomes | Same |
| `get_experiment_result(root, thesis_id, …)` | Compact + detail tiers | Same |
| `search_research_findings(query, …)` | Vector search over saved findings | MemPalace ChromaDB `wing="research_findings"` + `research_findings.jsonl` fallback |
| `save_research_finding(…)` | Adds a drawer | MemPalace ChromaDB |
| `latest_thesis_details(root, thesis_id, …)` | Compact dict of the most-recent attempt | `*_backtest_runs.db` |

All wired into `research_tools_mcp.py` and exposed to the conductor as MCP tools.

### 3.3 What `thesis_validator.py` already enforces

Rules that touch prior theses and novelty (line numbers from `thesis_validator.py`):

| Rule | Function | What it checks |
| --- | --- | --- |
| Process gate | `_validate_process` (340) | Required tools called: **`list_experiment_results`**, **`web_search`**. (NOT `list_past_theses`.) |
| Thesis ID uniqueness | `_check_thesis_id_not_repeated` (668) | New `thesis_id` not in any prior attempt |
| Underexplored dimensions | `_validate_underexplored_dimensions` (1417) | When priors exist: list non-empty, all values are known dimensions, chosen dim not in list |
| Direction whipsaw | (615) | When a prior tested the same `theme_keywords` in the opposite numeric direction, new thesis must cite it in `prior_lever_outcomes` |
| Theme-overlap → novel_connection | (1634) | When proposed `theme_keywords` intersect prior `theme_keywords` heavily, `novel_connection` ≥ `_MIN_NOVEL_CONNECTION_CHARS` |
| Dimension novelty | `_validate_dimension_novelty` (~1602) | `dimension_novelty` ≥30 chars |
| Causal cluster | (1619) | `causal_cluster` non-empty when priors exist |

These rules are **structural**: they check field shapes and theme intersections. None of them can verify that **content** values (`prior_thesis_id` strings, `underexplored_dimensions_considered` choices) actually correspond to the real prior corpus. Pre-flight is what unlocks that check.

### 3.4 Why "tools-list-in-prompt + process validator" is not enough

The current setup tells the agent what tools exist and asserts post-hoc that `list_experiment_results` and `web_search` were called. This does not address:

- **Query quality.** Even if the agent called retrieval tools, `search_research_findings("EMA crossover")` passes the validator but misses a prior phrased "fast/slow MA cross 9-21" — keyword overlap is low while semantic similarity is high.
- **Engagement.** The agent may call a tool, read the result, and propose the same idea anyway.
- **Dedup.** Eyeballing 25 hypotheses for "is mine ~90% the same?" is unreliable.
- **Landscape awareness.** Tools surface instances. They don't surface the shape of the search space.
- **Synthesis.** No tool nudges the agent to *combine* a killed prior with a kept one to generate something new.
- **Content checks on novelty fields.** The validator can't assert `prior_lever_outcomes[].prior_thesis_id` resolves to a real prior without a canonical "what was in front of the planner" set.

## 4. Architecture

### 4.1 Module layout

```
preflight_recall.py              ← new
  ├─ PreflightIntent             dataclass
  ├─ build_prior_attempts_block  → str  (Level 1: top-K)
  ├─ build_landscape_block       → str  (Level 2: dimension/status counts + adjacency gaps)
  ├─ build_dimension_pairs_block → str  (Level 3: killed/kept pairs by dimension)
  ├─ dedup_check                 → DedupResult  (Level 1 verification, post-draft)
  └─ _thesis_corpus_index        internal: lazy ChromaDB collection accessor

preflight_synthesis_turn.py      ← new
  └─ build_synthesis_user_prompt → str  (Level 3: lateral-thinking LLM turn instructions)

backtest_run_db.py               ← edited
  ├─ list_dimension_summary(family)   aggregations for landscape block
  ├─ list_killed_kept_pairs(family)   per-dimension killed/kept pair lookup
  └─ list_round_attempts(research_round_id)
                                       structured rejected-attempt rows for §5.8.3

autoresearch_research.py         ← edited
  └─ _resolve_conductor_inputs (line 435): enrich latest_outcome with
       runtime_config (§5.8.1), diagnostics_summary (§5.8.2), and
       this_round_rejected_attempts (§5.8.3).

research_conductor.py            ← edited
  ├─ user-prompt construction (lines 131-175): append pre-flight blocks
  │  AND new last-experiment enrichment blocks (§5.8.1, §5.8.2, §5.8.3)
  ├─ optional synthesis-turn LLM call before the existing drafting LLM call
  └─ post-draft dedup_check; on trigger, return to agent with match for revision/override

thesis_validator.py              ← edited (extension, not retire)
  ├─ Extend prior_lever_outcomes validation: cited prior_thesis_id values
  │  must resolve to corpus entries (content check, not just structural)
  └─ Extend underexplored_dimensions_considered validation: when corpus stats
     are available, soft-warn if the chosen dimension has FEWER prior attempts
     than ALL of the dimensions the agent listed as underexplored
     (i.e. the agent labeled its own choice as underexplored without warrant)

research_types.py                ← edited (small)
  ├─ Add ResearchThesis.dedup_override_justification: DedupOverride | None
  └─ Add DedupOverride dataclass: matched_thesis_id, similarity, load_bearing_difference

research_prompts.py              ← edited (small static reword)
  └─ Tool-list description block (lines 50-58): reword two lines to clarify
     that pre-flight pre-loads top-K relevant priors in the user prompt;
     list_past_theses / list_experiment_results remain available for deep
     follow-up only. Prevents the agent from redundantly fetching context
     it already has.
```

No new dependency. ChromaDB and MemPalace are already in the import graph via `research_memory.py`.

### 4.2 Three retrievals: relevance half, diversity half, dedup

Pre-flight is **two-pass** (not single-pass) to avoid the well-documented retrieval-homogeneity problem: pure cosine top-K concentrated near the just-failed direction inadvertently nudges the agent toward more-of-the-same proposals that dedup then rejects. The standard fixes are MMR within a relevance pass plus an explicit diversity pass; we apply both.

| Pass | When | Query input | Returns | Used for |
| --- | --- | --- | --- | --- |
| **Relevance half** | User-prompt build, every round | `family + latest_outcome[mechanism, validator_status, validation_failure_reason] + rejection_feedback` | Top `K/2` via cosine, re-ranked with **MMR** (`lambda_mult` default 0.5) | What's near what just happened, with redundancy removed |
| **Diversity half** | Same | `family + theme_keywords_of_latest_outcome` | Top `K/2` from corpus filtered by `where_not={"mechanism_dimension": <just_failed_dim>}`, ranked by cosine | Guaranteed cross-dimension priors so the agent sees somewhere to go |
| **Dedup (narrow)** | After agent emits draft thesis | Draft `hypothesis + mechanism` text | Top-1 prior + cosine score | Soft-gate against near-duplicates |

All three share the same `thesis_corpus` ChromaDB wing.

**Outcome-balance floor** applies to the **union** of relevance + diversity halves: at least 2 KEPT and at least 2 KILLED in the returned K, when both exist in the corpus. Backfill from the runner-up bucket if a half can't meet its share.

**Cold-path for diversity half.** When the just-failed dimension is the only populated dimension in the family corpus, `where_not` returns nothing — the diversity half degrades to empty and the relevance half is allowed to fill the full K. Logged as `PREFLIGHT_DIVERSITY_DEGRADED`.

### 4.3 The synthesis turn

In the thesis-creation stage, the conductor runs **two LLM turns** instead of one:

1. **Synthesis turn.** User prompt = existing per-round context **+** prior-attempts block **+** landscape block **+** dimension-pairs block **+** synthesis instruction:
   > "Given the priors, landscape, and dimension pairs above, identify 2–3 unexploited combinations, contradictions, or gaps you notice in the corpus. Output a JSON array of `{observation, supporting_thesis_ids[]}`. Do not draft a thesis yet."

   Output: `synthesis_observations`, persisted to the round artifact and forwarded into Turn 2.

2. **Drafting turn.** Same context, plus the synthesis output appended. Existing drafting instruction.

The split forces lateral consideration before commitment. Single-turn prompting anchors the agent on the most-recent prior; two-turn splits that anchoring.

Cost: ~1.3× the round's planning-LLM tokens. The synthesis turn produces a small structured output (not a thesis), so its output tokens are bounded.

Kill switch: `AUTORESEARCH_SYNTHESIS_TURN_ENABLED` (default `true`). When `false`, the blocks still get injected but no separate turn runs.

### 4.4 Thesis corpus indexing

On first access per process, `preflight_recall._thesis_corpus_index()`:

1. Reads all rows from `*_backtest_runs.db` via existing `BacktestRunDB.list_research_thesis_attempts`.
2. For each row, upserts a drawer into ChromaDB `wing="thesis_corpus"` with id `f"thesis_{thesis_id}_attempt_{attempt_number}"`. Document text: `f"{hypothesis}\n\n{mechanism}"`. Metadata: `thesis_id, attempt_number, job_id, strategy_family, validator_status, mechanism_dimension, dimension_novelty, theme_keywords (list), created_at_utc, run_id, validation_failure_reason`.
3. Uses ChromaDB's default embedder (sentence-transformers all-MiniLM-L6-v2). No new dependency, no API cost.

Subsequent calls within a process: compare row count in `*_backtest_runs.db` vs the count of drawers in `thesis_corpus`. If equal, skip; if delta, upsert only new rows (ids are deterministic).

**Cold start.** If `thesis_corpus` filtered by `strategy_family` has fewer than `_cold_start_threshold()` (default 5) entries, `build_prior_attempts_block` returns an empty block, `build_landscape_block` returns "no prior runs for this family", `build_dimension_pairs_block` returns empty, and `dedup_check` returns `DedupResult.skipped(reason="cold_start")`. A structured `PREFLIGHT_COLD_START` log line is emitted (rule **H**). No errors propagated.

### 4.5 Data flow

```
[conductor builds user prompt for round N]
        │
        ▼
   intent = PreflightIntent(family, latest_outcome, rejection_feedback)
        │
        ▼
   prior_attempts_block ──┐
   landscape_block        │── appended to user_prompt
   dimension_pairs_block ─┘
        │
        ▼
[Turn 1: synthesis] ─────► synthesis_observations
   user_prompt + synthesis_instruction
        │
        ▼
[Turn 2: drafting]  ─────► draft thesis
   user_prompt + synthesis_observations + drafting_instruction
        │
        ▼
   dedup_check(draft.hypothesis + draft.mechanism)
        │
        ▼
   cosine ≥ θ ?
   ┌────┴────┐
   no        yes
   │         │
   │         ▼
   │   [return to agent with matched_thesis_id + similarity]
   │         │
   │   agent revises OR sets thesis.dedup_override_justification
   │         │
   └─────────┘
        │
        ▼
   thesis_validator (existing rules + 2 extensions)
        │
        ▼
   [thesis enters run queue]
```

## 5. Components

### 5.1 `PreflightIntent`

```python
@dataclass(frozen=True)
class PreflightIntent:
    family: str
    latest_outcome: dict          # already in scope in run_conductor()
    rejection_feedback: str = ""  # already in scope in run_conductor()
    draft_hypothesis: str = ""    # filled only for dedup
    draft_mechanism: str = ""     # filled only for dedup
```

All upstream data is already passed to the conductor. No changes required to `autoresearch_controller.py` or `autoresearch_orchestration.py`.

Query string for pre-flight is built inside `preflight_recall` from this intent. Helper:

```python
def _query_text_for_recall(intent: PreflightIntent) -> str:
    parts = [f"family={intent.family}"]
    lo = intent.latest_outcome or {}
    if lo.get("mechanism"):
        parts.append(f"prior_mechanism={lo['mechanism']}")
    if lo.get("validator_status"):
        parts.append(f"prior_outcome={lo['validator_status']}")
    if lo.get("validation_failure_reason"):
        parts.append(f"prior_failure={lo['validation_failure_reason']}")
    if intent.rejection_feedback:
        parts.append(f"rejection_feedback={intent.rejection_feedback}")
    return "; ".join(parts)
```

On round 0 (no `latest_outcome`), the query is just `f"family={intent.family}"` — wide net, expected.

### 5.2 `build_prior_attempts_block(intent, *, k=None) -> str`

Two-pass retrieval with MMR re-ranking on the relevance half. Concretely:

1. **`k` defaults to `_preflight_k()`** (env `AUTORESEARCH_PREFLIGHT_K`, default 8). Lazy accessor.

2. **Split.** `relevance_share = k // 2`; `diversity_share = k - relevance_share`. Tunable via `_preflight_relevance_share()` (env `AUTORESEARCH_PREFLIGHT_RELEVANCE_SHARE`, default `0.5`).

3. **Relevance half (with MMR).**
   - ChromaDB query with `where={"strategy_family": intent.family}`, `n_results=relevance_share * 3` (over-fetch by 3x to give MMR room to re-rank).
   - Compute pairwise cosine among the over-fetched candidates.
   - Greedy MMR selection: start with the highest cosine-to-query. For each subsequent pick `c`, score:
     `mmr(c) = lambda_mult * cos(c, query) - (1 - lambda_mult) * max(cos(c, s) for s in selected)`
     Pick `argmax`. `lambda_mult` from `_preflight_mmr_lambda()` (env `AUTORESEARCH_PREFLIGHT_MMR_LAMBDA`, default `0.5`; 1.0 = pure relevance, 0.0 = pure diversity).
   - Stop at `relevance_share` selections.

4. **Diversity half (cross-dimension).**
   - Determine `just_failed_dim = intent.latest_outcome.get("mechanism_dimension")`.
   - When set: ChromaDB query with `where={"strategy_family": intent.family, "$and": [{"mechanism_dimension": {"$ne": just_failed_dim}}]}`, `n_results=diversity_share * 2`. Query text: `f"family={family}; explore mechanisms different from {just_failed_dim}"` (when present, append `theme_keywords` of latest_outcome). Greedy top by cosine, no MMR (the dimension-exclusion already enforces diversity).
   - When `just_failed_dim` is unset or empty (round 0): diversity half degrades — pull `diversity_share` random non-overlapping picks from the full family corpus.

5. **Union + outcome-balance floor.** Combine the two halves (deduplicate by `thesis_id+attempt_number`). From the union, enforce "≥2 KEPT and ≥2 KILLED" by demoting and replacing if needed:
   - If KEPT < 2 in union but ≥2 in family corpus: pull the next-best KEPT (by cosine to relevance query) and swap out the lowest-MMR-score KILLED.
   - Mirror for KILLED.
   - If a half can't meet its share (e.g. diversity returned 0 because corpus is single-dim), the other half backfills, capped at `k` total.

6. **Cold-path logging.** When the diversity half degrades (returned 0 cross-dim entries) emit `PREFLIGHT_DIVERSITY_DEGRADED` with reason; when MMR is short-circuited (relevance candidates < relevance_share) emit `PREFLIGHT_MMR_DEGRADED`.

7. **Render.** Markdown sections, one per entry: `thesis_id`, `outcome`, `mechanism_dimension`, `hypothesis` (≤180 chars), `mechanism` (≤180 chars), `validation_failure_reason` (≤160 chars), `job_id`, `round_number`, **`config_changes`** (key→value pairs from `config_changes_json` stored per attempt). Config-changes rendering: up to `_preflight_config_changes_max_keys()` (env `AUTORESEARCH_PREFLIGHT_CONFIG_CHANGES_MAX_KEYS`, default `5`) key→value pairs shown verbatim; if more keys exist, append `"+{N} more keys: [k1, k2, ...]"` listing only the additional key names. Long string values truncated to 60 chars. Without the actual values, the agent can spot which knobs were touched but not whether a specific value (e.g. `ema_period=8`) has already been tried — surfacing values closes that gap. Sections grouped by half — relevance entries first under "## Closest priors", then diversity entries under "## Cross-dimension priors (for synthesis)".

This design is grounded in the well-established RAG-diversity literature: MMR (`lambda_mult`) is the production-default for retrieval diversity (LangChain, LlamaIndex, Azure AI Search, Elastic, Bigtable), and two-pass relevance+diversity hybridization is the standard agentic-context-engineering pattern (Elastic Search Labs, multi-stage RAG pipelines). The dimension-exclusion technique in the diversity half is borrowed from MAP-Elites style structured exploration used by QuantEvolve, FunSearch, and AlphaEvolve — adapted to our schema by using `mechanism_dimension` as the structured axis.

### 5.3 `build_landscape_block(family) -> str`

Aggregates `*_backtest_runs.db` rows for the family. Two queries on `BacktestRunDB`:

1. **`list_dimension_summary(family)`** → groups by `mechanism_dimension`, returns `(dimension, total, kept, killed)` per row. Classifies each as **saturated** (`total ≥ _landscape_saturated_at()`, default 8), **active** (1 ≤ total < threshold), or **unexplored** (total = 0 — only listed when the dimension appears in `MECHANISM_DIMENSIONS` constant but has no attempts).

2. **Adjacency gaps**: defined concretely via `theme_keywords`. For every pair of dimensions `(A, B)` where each individually has ≥3 attempts, count theses whose `theme_keywords` cross both dimensions (any prior in dim A whose theme_keywords intersect priors in dim B). If the count is 0, surface as "**adjacent pair never combined: A × B**".

Renders:

```markdown
## Mechanism landscape (family=ema)

Dimensions explored:
- trend_filters         → 12 attempts, 2 kept, 10 killed   (saturated)
- vol_regime_filter     →  4 attempts, 1 kept,  3 killed   (active)
- exit_management       →  3 attempts, 0 kept,  3 killed   (active)

Adjacent pairs never combined:
- trend_filters × vol_regime_filter (each has attempts; no thesis spans both)
- exit_management × regime_conditioning

Unexplored dimensions (zero attempts):
- universe_selection, alternative_data
```

### 5.4 `build_dimension_pairs_block(family) -> str`

**(Level 3 closure.)** For each `mechanism_dimension` with at least 1 KILLED **and** at least 1 KEPT in the corpus:

1. Pick the most-recent KILLED entry for that dimension.
2. Pick the KEPT entry for that dimension with the largest validation-metric improvement vs baseline (fallback: most-recent KEPT).
3. Render as a pair with an empty "Possible hybrid:" slot for the synthesis turn to populate.

```markdown
## Killed/kept pairs by dimension (synthesis substrate)

### Dimension: trend_filters
- KILLED: ema_trend_filter_v2 (job=12, round=5) — ADX>25 entry filter; failed chop_sensitivity
- KEPT:   ema_htf_gate (job=11, round=2) — 1h-direction gate; PF 1.08 → 1.34

### Dimension: vol_regime_filter
- KILLED: ema_vol_quantile (job=10, round=3) — top-decile vol skip; PF unchanged
- KEPT:   ema_vol_regime_v1 (job=11, round=6) — overnight-ATR multiple skip; PF 1.10 → 1.41
```

Limit: at most `_pairs_block_max_dimensions()` (default 5) dimensions rendered — sorted by total attempt count descending. Synthesis-turn output writes one observation per pair it deems worth hybridizing.

Helper: `BacktestRunDB.list_killed_kept_pairs(family)`.

### 5.5 `build_synthesis_user_prompt() -> str`

Stateless string returning the synthesis-turn user-prompt instruction (Turn 1 wrapping). One home for the exact wording. Includes the JSON output schema:

```json
{"synthesis_observations": [
  {"observation": "string ≥80 chars", "supporting_thesis_ids": ["..."]}
]}
```

### 5.6 `dedup_check(intent) -> DedupResult`

```python
@dataclass(frozen=True)
class DedupResult:
    triggered: bool
    skipped: bool
    skip_reason: str = ""
    matched_thesis_id: str = ""
    matched_attempt_number: int = 0
    similarity: float = 0.0
    matched_outcome: str = ""
    matched_summary: str = ""
```

- Query: `f"{intent.draft_hypothesis}\n\n{intent.draft_mechanism}"`.
- `n_results=1`. Threshold from `_dedup_threshold()` (env: `AUTORESEARCH_DEDUP_THRESHOLD`, default 0.88).
- On trigger, conductor surfaces the result to the agent as a structured rejection. Agent must either revise OR populate `ResearchThesis.dedup_override_justification`.

### 5.7 `DedupOverride` (new field on `ResearchThesis`)

```python
@dataclass
class DedupOverride:
    matched_thesis_id: str
    similarity: float
    load_bearing_difference: str   # ≥60 chars, validator-enforced
```

Validator rejects when:
- `load_bearing_difference` is missing or < 60 chars.
- `matched_thesis_id` doesn't resolve to a real prior in the corpus.
- A round has more than 1 override attempt (capped per round).

### 5.8 Last-experiment context enrichment in the user prompt

The current user prompt surfaces metrics + verdict + `previous_thesis` for the last experiment, but leaves three first-principles gaps. Each is a small, in-scope edit on top of the same user-prompt construction we're already changing.

#### 5.9.1 `runtime_config` values inline

**Today:** user prompt shows `config_path` (file path), not the values.
**Gap:** the agent has to call the analyst (or read the YAML) to know "last run used `ema_period=5, atr_multiple=1.5`." Cheap info, paid for expensively.
**Change:** in `_resolve_conductor_inputs` (`autoresearch_research.py:435`), add `latest_outcome["runtime_config"] = _resolve_runtime_config_for_record(...)` (the function already exists and is already called — its return value is currently used only for resolution context).
**Render in user prompt:** new block `LATEST EXPERIMENT CONFIG (values used):` printing key→value pairs from `runtime_config`, capped at `_last_run_config_max_keys()` (env `AUTORESEARCH_LAST_RUN_CONFIG_MAX_KEYS`, default 20). Overflow: `"+{N} more: [k1, k2, ...]"` listing remaining key names. Long values truncated to 80 chars.
**Cost:** ~10–30 extra tokens per round. Eliminates a class of analyst calls.

#### 5.9.2 `strategy_diagnostics_json` summary inline

**Today:** user prompt shows `diagnostics_file` path. The diagnostics JSON itself contains a compact summary (event counts, rejection breakdown, trade analysis verdict) that the conductor's analyst already reads on every round.
**Gap:** the agent must spend an `analyze_trades` call to surface ~30 lines of structured data that could ride the user prompt for ~free.
**Change:** `_resolve_conductor_inputs` reads the diagnostics file (with try/except + fail-open per rule **H**) and extracts the existing summary shape used elsewhere in `research_memory._experiment_compact_detail` — specifically `event_counts`, `rejection_breakdown`, `trade_analysis`, `verdict` subfields. Stored as `latest_outcome["diagnostics_summary"]`.
**Render in user prompt:** new block `LATEST EXPERIMENT DIAGNOSTICS (summary):` showing the four subfields verbatim (JSON-formatted). The full diagnostics file path remains available for deep dives.
**Failure mode:** file unreadable or schema unexpected → block omitted, `LATEST_DIAGNOSTICS_DEGRADED` logged. Round proceeds.
**Cost:** ~200–500 tokens per round. Removes ~1 analyst call per round on average.

#### 5.9.3 In-round rejected thesis attempts as structured data

**Today:** when a round had multiple validator-rejected attempts before one succeeded, the rejections are flattened into `rejection_block` text via `rejection_artifact.render_rejection_block`. The conductor sees the rejection text but not the structured `(attempt_number, validator_status, validation_failure_reason, hypothesis, mechanism)` per rejected attempt.
**Gap:** the synthesis turn (§4.3) benefits significantly from seeing "round 7 attempt 1 was rejected for X with hypothesis Y; attempt 2 was rejected for Z; attempt 3 succeeded." That's substrate for lateral thinking the current text block doesn't deliver.
**Change:** new helper `BacktestRunDB.list_round_attempts(research_round_id)` returning the rejected attempts for the *most recent completed round of the current job*. Called inside `_resolve_conductor_inputs`; result attached as `latest_outcome["this_round_rejected_attempts"]`. Empty list when no rejections.
**Render in user prompt:** new block `THIS ROUND'S REJECTED ATTEMPTS (structured):` rendering each rejected attempt as `attempt_number, validator_status, validation_failure_reason, hypothesis (≤180 chars), mechanism_dimension`. Capped at `_max_round_rejected_attempts()` (env `AUTORESEARCH_MAX_ROUND_REJECTED_ATTEMPTS`, default 5). Older rejections accessible via existing `list_rejections` MCP tool.
**Interaction with §11.1 reflexion:** this is structured data, not LLM-summarized reflexion. Reflexion stays as-is (separate channel).
**Cost:** ~50–300 tokens depending on rejection count. Reduces analyst calls and improves synthesis-turn quality.

### 5.9 Tool-description edit in `research_prompts.py`

The conductor's system prompt currently advertises `list_past_theses` / `get_past_thesis` / `list_experiment_results` / `get_experiment_result` as if they were the primary path to prior context. Once pre-flight pre-loads the top-K relevant priors in the user prompt, those tools become **follow-up tools**, not primary-context tools. Without an edit, the agent will sometimes call them redundantly, wasting tokens and round-trips.

**Change.** Replace lines 50–58 of `research_prompts.py` with reworded text that:
- States explicitly that the round's user prompt already contains top-K relevant priors + landscape + dimension pairs.
- Repositions the tools as for deep follow-up: "use only when you need a thesis NOT in the pre-flight block, or a level of detail beyond the summary."
- Leaves the tool signatures unchanged — only the description text changes. No tool registration touched.

This is a static reword. It does not change MCP wiring or `research_tools_mcp.py`. The tools remain fully available; their advertised purpose is sharpened.

Test: a unit test on `_build_conductor_system_prompt` asserts the new wording is present, mentions "pre-loaded", and removes the impression that calling these tools is the agent's primary path to context.

## 6. Validator changes

**No retires.** Two extensions of existing rules; one new field-level rule for the dedup override.

### 6.1 Extension: `prior_lever_outcomes` content check

Current state: direction-whipsaw check (line 615) requires `prior_lever_outcomes` to cite an opposite-direction prior when applicable, but only checks that the cited `prior_thesis_id` is referenced by string — it never asserts the id resolves to a real prior.

**New check:** when `prior_lever_outcomes` is non-empty, every `prior_thesis_id` must exist in the corpus snapshot the pre-flight block was built from. The corpus snapshot's `thesis_id` set is passed to the validator alongside `prior_theses` (already passed today).

- **Severity:** hard reject. A hallucinated `prior_thesis_id` is the same class of error as a hallucinated function name.
- **Rejection code:** `structural_prior_lever_outcomes_unknown_id`.
- **Evidence:** the unknown ids; the set of valid ids (truncated).

### 6.2 Extension: `underexplored_dimensions_considered` content check (soft-warn)

Current state: `_validate_underexplored_dimensions` (line 1417) checks non-empty, valid values, chosen-dim-not-in-list. It does **not** check whether the listed underexplored dimensions are actually less-explored than the chosen one.

**New check (soft-warn, not reject):** when corpus stats exist, emit a `BehaviorSignal` (severity="warn") when the chosen `mechanism_dimension` has **strictly more** prior attempts than **every** dimension in `underexplored_dimensions_considered`. This means the agent labeled its own choice as underexplored without warrant.

- **Severity:** warn (`severity="warn"`, not "block"). Surfaces in reflexion, doesn't kill the round.
- **Behavior code:** `thesis_quality_underexplored_misclassification`.
- **Rationale for soft, not hard:** there are legitimate reasons a more-explored dimension still wins (recent kept result, new variant); blocking would be too aggressive. The warn forces the agent to acknowledge it in the next round.

### 6.3 New rule: dedup override well-formedness

Already covered in §5.7. Rejection code `structural_dedup_override_invalid` when the override fails its content rules.

### 6.4 Rules explicitly preserved unchanged

- `_validate_process` and `_REQUIRED_PROCESS_TOOLS` — unchanged.
- `_check_thesis_id_not_repeated` — unchanged.
- Theme-overlap / `novel_connection` — unchanged.
- Direction-whipsaw structural check — unchanged.
- `causal_cluster` requirement — unchanged.
- `dimension_novelty` ≥30 chars — unchanged.
- L6/L7 tool-order gates — unchanged.
- All other rules — unchanged.

## 7. Configuration

Lazy accessor functions, **not module-level constants** (CLAUDE.md hygiene rule):

| Function | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `_preflight_k()` | `AUTORESEARCH_PREFLIGHT_K` | `8` | Top-K size for pre-flight block (union of halves) |
| `_preflight_relevance_share()` | `AUTORESEARCH_PREFLIGHT_RELEVANCE_SHARE` | `0.5` | Fraction of K spent on relevance half; rest on diversity half |
| `_preflight_mmr_lambda()` | `AUTORESEARCH_PREFLIGHT_MMR_LAMBDA` | `0.5` | MMR `lambda_mult` in relevance half (1.0=pure relevance, 0.0=pure diversity) |
| `_dedup_threshold()` | `AUTORESEARCH_DEDUP_THRESHOLD` | `0.88` | Cosine cutoff for dedup trigger |
| `_cold_start_threshold()` | `AUTORESEARCH_PREFLIGHT_COLD_START_THRESHOLD` | `5` | Min per-family corpus size to enable pre-flight |
| `_landscape_saturated_at()` | `AUTORESEARCH_LANDSCAPE_SATURATED_AT` | `8` | Attempt count above which a dimension is "saturated" |
| `_pairs_block_max_dimensions()` | `AUTORESEARCH_PAIRS_BLOCK_MAX_DIMENSIONS` | `5` | Cap on pairs rendered |
| `_synthesis_enabled()` | `AUTORESEARCH_SYNTHESIS_TURN_ENABLED` | `true` | Kill switch for the synthesis turn |
| `_kept_floor()`, `_killed_floor()` | `AUTORESEARCH_PREFLIGHT_KEPT_FLOOR`, `..._KILLED_FLOOR` | `2`, `2` | Outcome-balance floors in the union |
| `_preflight_config_changes_max_keys()` | `AUTORESEARCH_PREFLIGHT_CONFIG_CHANGES_MAX_KEYS` | `5` | Max config_changes key→value pairs rendered per prior |
| `_last_run_config_max_keys()` | `AUTORESEARCH_LAST_RUN_CONFIG_MAX_KEYS` | `20` | Max runtime_config keys inlined for the last experiment (§5.8.1) |
| `_max_round_rejected_attempts()` | `AUTORESEARCH_MAX_ROUND_REJECTED_ATTEMPTS` | `5` | Max rejected-attempt entries rendered for the current round (§5.8.3) |

Each accessor validates its env var (int parse, range check) and raises with the named env var on bad input.

## 8. Error handling

| Failure | Behavior |
| --- | --- |
| ChromaDB unavailable / `_resolve_palace_dir` fails | All blocks return empty strings; `dedup_check` returns `skipped(reason="palace_unavailable")`. Structured log line. Round proceeds. |
| Corpus empty for family | Cold-start path (§4.4). |
| Embedding call fails | Same as ChromaDB-unavailable. |
| Dedup overrides itself recursively (agent keeps overriding) | Per-round override count capped at 1. Second override attempt → hard-reject (`structural_dedup_override_invalid`, evidence `{"reason": "more_than_one_override_in_round"}`). |
| Synthesis turn produces malformed JSON | One retry. On second failure: skip synthesis output, proceed to drafting with pure context blocks. Log `SYNTHESIS_TURN_DEGRADED`. |
| `mechanism_dimension` missing on a prior | Bucketed as `unknown_dimension` in landscape; not silently dropped. |
| Corpus snapshot not passed to validator | Soft-skip the `prior_lever_outcomes` content check; structural check still runs. |

**Fail-open for retrieval** (no recall ≠ broken run). **Fail-loud for validator rules** (a hallucinated `prior_thesis_id` is a real rejection).

## 9. Testing strategy

Real data from `*_backtest_runs.db`. No toy thesis names. No mocked internals. (CLAUDE.md testing rules.)

### 9.1 Unit (per module)

- `preflight_recall`:
  - `_query_text_for_recall` builds expected string for cold-start, normal, and rejection-feedback cases.
  - `build_prior_attempts_block` respects the outcome-balance floor; returns ≤K entries; handles family with all-killed corpus.
  - `build_landscape_block` aggregations match a fixture-DB hand-computed table; adjacency-pair detection via `theme_keywords` is correct.
  - `build_dimension_pairs_block` picks most-recent killed + best-improvement kept per dimension; honors max-dimensions cap.
  - `dedup_check` triggers on a known near-duplicate (re-embed a real prior with paraphrased wording).
  - Cold start: empty blocks + skipped result + structured log.
  - Each lazy accessor reads env at call time.
  - **MMR behavior:** with `lambda_mult=1.0`, relevance half matches pure-cosine ordering. With `lambda_mult=0.0`, second pick is the furthest candidate from the first regardless of relevance. With `lambda_mult=0.5`, candidates that are near-duplicates of the first pick are demoted. Tested with a synthetic case: 5 near-clones + 5 spread-out candidates → at `0.5`, relevance half returns ≤1 clone in the first 3.
  - **Two-pass union:** when corpus has theses in both `just_failed_dim` and other dimensions, the returned K block contains entries from both halves; when corpus is single-dim, diversity half is empty and relevance half fills K (no exception).

- `preflight_synthesis_turn`: prompt assembly stable; malformed-output retry+degradation.

- `BacktestRunDB.list_dimension_summary` and `list_killed_kept_pairs`: aggregations match hand-computed expectations against a real fixture.

### 9.2 Integration

- End-to-end conductor round with a populated corpus → user prompt contains all three blocks and the synthesis instruction. Assertions check counts and content (`assert "ema_pullback_v3" in user_prompt`), not just non-null (rule **G**).
- Dedup trigger → agent override path → validator accepts override only when `load_bearing_difference ≥ 60` and `matched_thesis_id` resolves.
- Dedup trigger → agent revises → second draft passes.
- Validator extension §6.1: thesis with `prior_lever_outcomes[].prior_thesis_id="ghost_id"` → hard reject.
- Validator extension §6.2: thesis whose chosen dimension has more attempts than every "underexplored" one → warn behavior signal (not reject).
- Cold start: brand-new family, empty DB → round runs cleanly, empty blocks, no errors.

### 9.3 Rerun & state-transition

- Second run after first populates corpus: incremental upsert into `thesis_corpus`, no duplicates, no full rebuild.
- Manual deletion of a row from `*_backtest_runs.db` → drawer stays (documented limitation).

## 10. Migration plan

One PR, one deliverable, in this order:

1. `preflight_recall.py` (with MMR + two-pass retrieval) + `preflight_synthesis_turn.py` modules with full test coverage.
2. `BacktestRunDB.list_dimension_summary` + `list_killed_kept_pairs` + `list_round_attempts` helpers.
3. `research_types.ResearchThesis.dedup_override_justification` field (Pydantic optional).
4. `research_prompts.py` tool-description reword (§5.9).
5. `autoresearch_research.py` `_resolve_conductor_inputs` enrichments (§5.8): runtime_config, diagnostics_summary, this_round_rejected_attempts attached to `latest_outcome`.
6. `research_conductor.py` user-prompt augmentation (pre-flight blocks + new last-experiment blocks) + two-turn flow + dedup call site.
7. `thesis_validator.py` two extensions (§6.1, §6.2) + dedup-override well-formedness rule (§6.3).
8. End-to-end test against a real fixture DB; commit per CLAUDE.md verification rules.

No staged rollout flag. Behavior change is contained to thesis-creation rounds; cold-start path covers new families.

## 11. Open considerations (deliberately not in scope)

- **Model routing within the synthesis turn.** The synthesis turn is a meta-reasoning task that doesn't need Opus tokens. Route to a cheaper tier when a model router exists. For now: same model as drafting.
- **Wave 2: research subagent integration.** Same `preflight_recall` module; second injection point in the subagent prompt path.
- **Level 4: latent dimension discovery.** Clustering / density estimation over embedding space. Earn the right by measuring whether Levels 1–3 hit a ceiling.
- **Cross-family pre-flight.** Currently `where={"strategy_family": ...}`. A future spec could add controlled cross-family recall for genuinely orthogonal mechanisms.
- **Reconciliation with `prompt-variant-framework`.** Touched in §2.
- **Deprecation of `list_past_theses` MCP tool.** Pre-flight + the §5.8 description edit make the tool largely redundant for primary context. Leave the tool available (deep follow-up is still useful); revisit deprecation after one quarter of telemetry on call frequency post-pre-flight.
- **Periodic insight curation (QuantEvolve-style).** QuantEvolve (arxiv:2510.18569) re-curates accumulated insights every 50 generations — filter redundancy, consolidate findings, document failed approaches. Our `research_findings` MemPalace wing grows continuously without re-curation; over many jobs it will drift toward redundancy. A future spec could add a scheduled curation pass that re-embeds and de-duplicates findings, with consolidated meta-findings replacing clusters of near-identical entries. Out of scope for v1 but a natural extension once we have telemetry on findings-wing growth rate.
- **Field-wise / point-wise novelty (NoveltyAgent-style).** Current dedup embeds `hypothesis + mechanism` as one document. NoveltyAgent (arxiv:2603.20884) decomposes manuscripts into discrete novelty points and checks each independently. For us this could mean separate embeddings for `hypothesis`, `mechanism`, `theme_keywords` — with dedup firing only when multiple fields match. More expressive; not required for v1.
- **MAP-Elites grid maintenance.** QuantEvolve, FunSearch, AlphaEvolve all organize their populations into a feature-space grid (one elite per niche). We use `mechanism_dimension` as a structured axis only in retrieval — never as a hard population constraint. Whether to enforce a "one accepted thesis per (family, dimension, theme_cluster) cell" rule is a meaningful future paradigm choice; defer until we have evidence the current soft approach insufficiently diversifies the accepted-thesis stream.

### 11.1 Related work informing this design

| Source | What we adopted | What we deliberately did not |
| --- | --- | --- |
| [MMR / LangChain / Azure / Elastic](https://www.elastic.co/search-labs/blog/maximum-marginal-relevance-diversify-results) | `lambda_mult`-based re-ranking in relevance half (§5.2) | Hybrid lexical+semantic (BM25+vec) — not needed; corpus is small + homogeneous |
| [QuantEvolve (arxiv:2510.18569)](https://arxiv.org/html/2510.18569v1) | Dimension-axis structured exploration (mapped to `mechanism_dimension`); landscape view | Full MAP-Elites grid + island model + α exploit/explore parameter — we're rounds-based, not evolutionary |
| [FunSearch / AlphaEvolve / OpenEvolve](https://github.com/codelion/openevolve) | Past attempts (kept + killed) injected into LLM context | Behavioral hash dedup; embedding cosine is the right signal for thesis text |
| [AI Scientist v1 critical eval (arxiv:2502.14297)](https://arxiv.org/abs/2502.14297) | Cautionary tale → embeddings over keyword search; soft-gate dedup with override path | Keyword-only novelty (their documented failure mode) |
| [NoveltyAgent (arxiv:2603.20884)](https://arxiv.org/pdf/2603.20884) | Self-validation as a pattern: dedup result goes back to agent for revise-or-override | Point-wise novelty per field — possible v2 refinement (see above) |
| [Auto Researching convergence (arxiv:2603.15916)](https://arxiv.org/pdf/2603.15916) | Empirical justification for prioritizing dimension-switching over hyperparameter tuning (Level 2 landscape block) | Cross-architecture meta-search — out of our scope |
| [Memory Management Impact on LLM Agents (arxiv:2505.16067)](https://arxiv.org/pdf/2505.16067) | Selective addition: validator gates what enters the corpus; misaligned-experience risk acknowledged | Two-tier memory hierarchy — overkill for current corpus size |

## 12. Success criteria

- A planning round on a populated `ema_backtest_runs.db` shows the prior-attempts block (with explicit "Closest priors" + "Cross-dimension priors" sub-sections), landscape block, dimension-pairs block, and synthesis observations in its user prompt and round artifact.
- The top-K block contains at least 2 KEPT and 2 KILLED entries when both exist in the corpus.
- When the corpus has theses in multiple dimensions, at least one entry in the returned K is from a dimension **other than** `latest_outcome.mechanism_dimension`. (Two-pass retrieval working.)
- MMR re-ranking demoted at least one near-clone in a synthetic 5-clones + 5-spread test fixture at `lambda_mult=0.5`.
- Rendered prior-attempts entries show `config_changes` key→value pairs (up to the cap), not just key names — verified by asserting a specific value (e.g. `"ema_period": 8`) appears in the block for a fixture prior known to have changed that key.
- User prompt contains a `LATEST EXPERIMENT CONFIG (values used):` block with concrete key→value pairs from the prior round's `runtime_config` (§5.8.1).
- User prompt contains a `LATEST EXPERIMENT DIAGNOSTICS (summary):` block with `event_counts`, `rejection_breakdown`, `trade_analysis`, `verdict` subfields when the diagnostics file is readable; block omitted with a `LATEST_DIAGNOSTICS_DEGRADED` log when not (§5.8.2).
- User prompt contains a `THIS ROUND'S REJECTED ATTEMPTS (structured):` block listing rejected attempts of the most recent completed round of the current job — `attempt_number`, `validator_status`, `validation_failure_reason`, truncated `hypothesis`, `mechanism_dimension` (§5.8.3). Empty list when no rejections; block omitted in that case.
- Updated system-prompt tool description (§5.8) is detectable in `_build_conductor_system_prompt` output and references "pre-loaded".
- A near-duplicate proposed thesis (paraphrased version of a known prior) is caught by dedup, with the matched `thesis_id` and similarity surfaced to the agent.
- A thesis with a hallucinated `prior_lever_outcomes[].prior_thesis_id` is hard-rejected by the validator.
- A thesis whose chosen dimension has more attempts than every "underexplored" alternative receives a `thesis_quality_underexplored_misclassification` warn signal (not a reject).
- A cold-start run (new family, empty corpus) completes without errors and produces a thesis.
- Two consecutive runs against the same `*_backtest_runs.db` do not produce duplicate ChromaDB drawers.
- Token cost per planning round is at most ~1.5× the pre-change baseline (two-turn flow + injected blocks). Documented in the run artifact's usage block.

## 13. Levels coverage scorecard

| Level | Goal | How this spec delivers it |
|---|---|---|
| 1 — Awareness | Agent sees relevant priors | `build_prior_attempts_block` with outcome-balance floor (§5.2) |
| 2 — Pattern surfacing | Saturated vs unexplored dimensions | `build_landscape_block` + `list_dimension_summary` + adjacency-pair detection via `theme_keywords` (§5.3) |
| 3 — Cross-thesis synthesis | Killed/kept pairs + lateral-thinking turn | `build_dimension_pairs_block` (§5.4) + synthesis turn (§4.3, §5.5) |
| 4 — Latent dimension discovery | Clustering / density on embedding space | Out of scope (§2, §11) |
