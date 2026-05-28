# Pre-Flight Recall, Corpus Synthesis, Semantic Dedup, and Schema Refactor

**Date:** 2026-05-28
**Status:** Design — grounded against actual code (`research_prompts.py`, `research_conductor.py`, `thesis_validator.py`, `research_memory.py`, `research_types.py`, `backtest_run_db.py`, `autoresearch_research.py`, `strategies/ema/prompt.py`, `diagnostic_contracts.py`).
**Scope:** Conductor's thesis-creation path only. Research subagent integration deferred.

---

## 1. Goal

When the conductor enters thesis creation, the agent must already have — in its context, every round, without on-demand tool calls — the structured information it needs to:

1. **See what just happened.** Last-experiment parameters, diagnostics summary, in-round rejected attempts, and the prior thesis's structured reasoning.
2. **See the arc.** Where each dimension stands (saturated / active / unexplored) and what killed/kept pairs exist per dimension.
3. **Diverge before drafting.** A retrieval layer that surfaces both relevance-similar priors and cross-dimension priors, then a synthesis LLM turn that explicitly looks for combinations and gaps.
4. **Avoid re-litigation.** Semantic dedup of the drafted thesis against the prior corpus, with a soft-gate override path.

The user-visible problem this solves: re-litigation ("EMA crossover 9/21 proposed again three jobs after being killed"), unstructured awareness of the search landscape, and the agent paying analyst tokens for value reads that already exist in the DB.

A secondary, coupled fix: the OUTPUT schema currently asks for free-form fields (`required_diagnostics`, `evidence`) while their structured siblings (`required_diagnostic_specs`, `evidence_citations`) exist in the schema and were designed to be canonical. The schema-author comments call this out explicitly. This spec finishes that migration, because consumer-side surfacing (§5.7) only delivers value when the producer-side populates the structured fields.

---

## 2. Non-goals

- Unified prompt assembler / `ContextAssembler` covering all agent roles. We touch exactly two prompt-construction sites (the user-prompt path in `research_conductor.py:131–175` and the OUTPUT block in `research_prompts.py`). A full assembly refactor is a separate spec and conflicts with the in-flight `docs/superpowers/plans/2026-05-04-prompt-variant-framework.md`.
- Per-role token budgets / working-memory window enforcement.
- Wave 2: applying pre-flight to the research subagent's hypothesis-suggestion path. Same primitive, different injection point.
- Latent dimension discovery via clustering / density estimation over the embedding space (Level 4).
- Periodic insight curation in the `research_findings` MemPalace wing (QuantEvolve every-50-generations pattern). Deferred to future work.
- A new vector store. ChromaDB is already in use via MemPalace; a second store violates rule **B** (One home per concept).
- Retiring `_validate_process`'s `_REQUIRED_PROCESS_TOOLS` (`("list_experiment_results", "web_search")`). `list_past_theses` was never in it — nothing to retire.
- A "selection turn" between synthesis and drafting (Phase 4 converge — see §4). Deliberately deferred until telemetry shows synthesis outputs need explicit ranking.

---

## 3. Background — ground truth from code

### 3.1 System prompt vs user prompt today

**System prompt** is static per family. Built by `research_prompts._build_conductor_system_prompt(strategy_description)`. Contains identity, strategy mechanics, tool list, doctrine, validator guardrails, OUTPUT schema. **Not** updated per round.

**User prompt** is round-specific. Built inline at `research_conductor.py:131–175`. Composed from `research_round`, `_render_resolution_context`, `LATEST EXPERIMENT OUTCOME` (full `latest_outcome` JSON dump), `EXPERIMENT RESULTS SUMMARY`, three file paths (trades / events / diagnostics), optional `rejection_feedback`, and `rejection_block` + `escalation_directive`.

**Implication:** pre-flight injection point is the **user prompt**.

### 3.2 What's stored about an experiment today

**`backtest_runs` table** (per-run): `run_id`, `thesis_id`, `config_path`, `runtime_config_json` (full params used), `code_commit`, `data_hash`, `train_metrics_json`, `validation_metrics_json`, `trade_count`, `trades_file`, `strategy_events_file`, `diagnostics_file`, `strategy_diagnostics_json` (event counts, rejection breakdown, trade analysis, verdict), `accepted`, `rejection_reason`, `verdict_status`, `verdict_summary`, `parent_backtest_run_id`, `timestamp`, `family`, `hypothesis`, `mechanism`, `job`, `usage_json`, `asi_json`.

**`research_thesis_attempts` table** (per attempt, including rejected): `research_round_id`, `attempt_number`, `thesis_id`, `strategy_family`, `config_changes_json` (delta from baseline), `validator_status` (accepted/rejected), `mechanism_dimension`, `hypothesis`, `mechanism`, `thesis_details_json` (full thesis structured reasoning), `validation_failure_reason`, `selected_for_execution`, `created_at_utc`.

**`research_rounds` table** (per round): `research_round_id`, `job_id`, `round_number`, `run_id`, `hypothesis_id`, `selected_thesis_id`, `outcome`, `created_at_utc`, `usage_json`.

### 3.3 What's surfaced today vs stored (gap table)

| Stored | In today's user prompt | This spec |
|---|---|---|
| Last run's metrics (PF, drawdown, etc.) | ✓ | unchanged |
| Decision / verdict | ✓ | unchanged |
| Prior thesis's hypothesis, mechanism, config_changes, expected_effects, evidence, disqualifiers, evidence_strength, closest_prior_theses_considered, orthogonality_defense, falsification_or_alternative, why_not_overfit (~11 of ~30 fields) | ✓ | unchanged; **expanded** (§5.7.4) |
| 3 file paths (trades / events / diagnostics) | ✓ paths only | unchanged |
| **Full `runtime_config` values** | ✗ | ✓ inlined (§5.7.1) |
| **`strategy_diagnostics_json` summary** | ✗ (path only) | ✓ inlined (§5.7.2) |
| **In-round rejected attempts as structured data** | ✗ (flattened text only) | ✓ structured (§5.7.3) |
| **`mechanism_dimension`, `theme_keywords`, `causal_cluster`, `alternatives_considered`, `prior_lever_outcomes`, `source_code_verification`, `thesis_role`, `requires_code_change`+`requested_primitives` from prior thesis** | ✗ | ✓ expanded (§5.7.4) |
| Cross-job validator-rejected attempts | ✗ | ✓ via pre-flight block (§5.2) |
| Landscape / dimension counts | ✗ | ✓ landscape block (§5.3) |
| Killed/kept pairs by dimension | ✗ | ✓ pairs block (§5.4) |

### 3.4 Existing retrieval primitives (we compose, not replace)

`research_memory.py` already exposes:
- `list_past_theses` / `get_past_thesis` — paginated and detail views over `*_backtest_runs.db`
- `list_experiment_results` / `get_experiment_result` — outcome views
- `search_research_findings` / `save_research_finding` — vector search over MemPalace ChromaDB `wing="research_findings"` with `research_findings.jsonl` fallback
- `latest_thesis_details` — compact dict for the most recent attempt of one thesis_id

These are wired into `research_tools_mcp.py` as MCP tools.

### 3.5 Existing validator rules around prior theses and novelty

From `thesis_validator.py`:
- `_validate_process` (line 340) — required tools `("list_experiment_results", "web_search")`.
- `_check_thesis_id_not_repeated` (line 668) — exact ID-collision rejection.
- `_validate_underexplored_dimensions` (line 1417) — when priors exist: list non-empty, valid dimensions, chosen-dim-not-in-list.
- Direction-whipsaw (line 615) — when a prior tested the same `theme_keywords` in the opposite direction, new thesis must cite it in `prior_lever_outcomes`.
- Theme-overlap → `novel_connection` ≥40 chars (line 1634).
- `_validate_dimension_novelty` — `dimension_novelty` ≥30 chars.
- `causal_cluster` required when priors exist (line 1619).

These rules are **structural** — they check field shapes and computed theme intersections. None can verify that **content** values (`prior_thesis_id` strings, "underexplored" dimension choices) actually correspond to the real corpus. Pre-flight is what makes those content checks tractable.

### 3.6 Why "tools-list + process validator" isn't enough

The current setup tells the agent what tools exist and asserts post-hoc that two required tools were called. This does not address:

- **Query quality.** `search_research_findings("EMA crossover")` passes the validator but misses a prior phrased "fast/slow MA cross 9-21" — keyword overlap is low while semantic similarity is high.
- **Engagement.** The agent may call a tool, read the result, and propose the same idea anyway.
- **Dedup.** Eyeballing 25 hypotheses for "is mine ~90% the same?" is unreliable for humans and LLMs alike.
- **Landscape awareness.** Tools surface instances; they don't surface the shape of the search space.
- **Synthesis.** No tool nudges the agent to *combine* a killed prior with a kept one.
- **Content checks on novelty fields.** The validator can't assert `prior_lever_outcomes[].prior_thesis_id` resolves to a real prior without a canonical "what was in front of the planner" set.

---

## 4. The first-principles flow this spec serves

Six phases. Each is either covered by what's in the codebase today, covered by this spec, or explicitly deferred.

| Phase | Today | After this spec |
|---|---|---|
| **1a. Look back — last run** | `LATEST EXPERIMENT OUTCOME` + analyst on demand for params/diagnostics/prior reasoning | + inline `runtime_config` values, diagnostics summary, in-round rejected attempts, expanded previous_thesis (§5.7) |
| **1b. Look back — the arc** | analyst on demand only | landscape + pairs blocks every round (§5.3, §5.4) |
| **2. Orient** | agent guesses | landscape + pairs blocks (same source, positioning role) |
| **3. Diverge** | single-turn, anchors on most-recent | synthesis turn (§5.5) + two-pass MMR retrieval (§5.2) |
| **4. Converge** | implicit | still implicit — deferred (see §11) |
| **5. Substantiate** | analyst + web_search on demand | same tools; far fewer calls because §5.7 already inlines routine reads |
| **6. Draft + self-check** | partial (validator post-hoc) | pre-loaded priors with concrete `config_changes` values, semantic dedup with override, content-check validator rules (§6) |

Mapping to the literature: best practices from QuantEvolve, FunSearch/AlphaEvolve, AI Scientist v1's failure mode, NoveltyAgent, MMR/RAG-diversity research, and the 94%-architecture/6%-hyperparam finding all map cleanly to these phases. The mapping table appears in §11.

---

## 5. Architecture

### 5.1 Module layout

```
preflight_recall.py              ← new
  ├─ PreflightIntent             dataclass
  ├─ build_prior_attempts_block  → str  (Level 1: top-K, two-pass MMR + diversity)
  ├─ build_landscape_block       → str  (Level 2: counts + adjacency)
  ├─ build_dimension_pairs_block → str  (Level 3: killed/kept pairs)
  ├─ dedup_check                 → DedupResult  (post-draft, soft-gate)
  └─ _thesis_corpus_index        internal: lazy ChromaDB collection accessor

preflight_synthesis_turn.py      ← new
  └─ build_synthesis_user_prompt → str  (Level 3: lateral-thinking turn instructions)

backtest_run_db.py               ← edited
  ├─ list_dimension_summary(family)         landscape aggregations
  ├─ list_killed_kept_pairs(family)         per-dimension pair lookup
  └─ list_round_attempts(research_round_id) structured rejected-attempts (§5.7.3)

research_memory.py               ← edited
  └─ latest_thesis_details expanded to surface the 7 + 1 load-bearing
     schema fields (§5.7.4)

autoresearch_research.py         ← edited
  └─ _resolve_conductor_inputs (line 435): enrich latest_outcome with
       runtime_config, diagnostics_summary, this_round_rejected_attempts,
       expanded previous_thesis (§5.7.1–.4)

research_prompts.py              ← edited (small, but real)
  ├─ Tool-description block (lines 50–58): rewording per §5.6
  └─ OUTPUT schema instructions: refactor per §5.8
       - required_diagnostics → required_diagnostic_specs (structured)
       - evidence (legacy) → evidence_citations (typed)

research_conductor.py            ← edited
  ├─ user-prompt construction (lines 131–175): append pre-flight blocks
  │  AND new last-experiment enrichment blocks
  ├─ two-turn flow: synthesis turn (Turn 1) → drafting turn (Turn 2)
  └─ post-draft dedup_check; on trigger, return to agent with match for revise/override

research_types.py                ← edited (small)
  ├─ Add ResearchThesis.dedup_override_justification: DedupOverride | None
  └─ Add DedupOverride dataclass

diagnostic_contracts.py          ← edited
  └─ build_required_diagnostic_specs: prefer structured input from the
     conductor; legacy prose normalization retained as fallback for
     DB-loaded historical attempts (§5.8.A)

thesis_validator.py              ← edited
  ├─ §6.1 prior_lever_outcomes content check (hard reject on unknown id)
  ├─ §6.2 underexplored_dimensions_considered misclassification (soft warn)
  ├─ §6.3 dedup-override well-formedness
  ├─ §6.4 migrate diagnostics-related rules to read required_diagnostic_specs
  └─ §6.5 new evidence_citations source-coverage rule with cold-start waiver
```

No new dependency. ChromaDB and MemPalace are already in the import graph via `research_memory.py`.

### 5.2 Data flow

```
[conductor builds user prompt for round N]
        │
        ▼
   intent = PreflightIntent(family, latest_outcome, rejection_feedback)
   latest_outcome enriched (§5.7): runtime_config, diagnostics_summary,
                                   this_round_rejected_attempts,
                                   expanded previous_thesis
        │
        ▼
   prior_attempts_block (two-pass: MMR-relevance + cross-dim diversity) ──┐
   landscape_block                                                         │── appended
   dimension_pairs_block                                                   │── to user_prompt
        │
        ▼
[Turn 1: synthesis] ─────► synthesis_observations (JSON list)
   user_prompt + synthesis_instruction
        │
        ▼
[Turn 2: drafting]  ─────► draft thesis (with structured required_diagnostic_specs
   user_prompt + synthesis_observations + drafting_instruction          and evidence_citations per §5.8)
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
   thesis_validator (existing rules + §6.1–6.5)
        │
        ▼
   [thesis enters run queue]
```

---

## 6. Components

### 6.1 `PreflightIntent`

```python
@dataclass(frozen=True)
class PreflightIntent:
    family: str
    latest_outcome: dict          # already in scope in run_conductor()
    rejection_feedback: str = ""  # already in scope
    draft_hypothesis: str = ""    # filled only for dedup
    draft_mechanism: str = ""     # same
```

All upstream data is already passed to the conductor. No changes required to `autoresearch_controller.py` or `autoresearch_orchestration.py`.

Query strings are built inside `preflight_recall`:

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

On round 0 (no `latest_outcome`), the query is just `f"family={intent.family}"`.

### 6.2 `build_prior_attempts_block(intent, *, k=None) -> str`

Two-pass retrieval (relevance + diversity), MMR re-ranking on the relevance half, outcome-balance floor on the union.

**Why two-pass:** pure cosine top-K concentrates near the just-failed direction, which inadvertently nudges the agent toward more-of-the-same proposals that dedup then rejects. MMR within the relevance pass + an explicit cross-dimension pass solves this. Both techniques are production-default in RAG (LangChain, LlamaIndex, Azure AI Search, Elastic).

**Algorithm:**

1. `k = _preflight_k()` (default 8). `relevance_share = k * _preflight_relevance_share()` (default 0.5).

2. **Relevance half — with MMR:**
   - ChromaDB `query` with `where={"strategy_family": intent.family}`, `n_results = relevance_share * 3` (over-fetch to give MMR room).
   - Greedy MMR selection: first pick = argmax cosine to query. For subsequent picks `c`, score is `lambda_mult * cos(c, query) - (1 - lambda_mult) * max(cos(c, s) for s in selected)`. `lambda_mult = _preflight_mmr_lambda()` (default 0.5).
   - Stop at `relevance_share` selections.

3. **Diversity half — cross-dimension:**
   - When `latest_outcome.mechanism_dimension` is set: ChromaDB query with `where={"strategy_family": intent.family, "mechanism_dimension": {"$ne": just_failed_dim}}`, ranked by cosine. Query text appends `theme_keywords` of latest_outcome.
   - When `just_failed_dim` is unset (round 0): random non-overlapping picks from the family corpus.
   - No MMR here — `where_not` already enforces diversity.

4. **Union + outcome-balance floor:**
   - Deduplicate by `(thesis_id, attempt_number)`.
   - Enforce ≥2 KEPT and ≥2 KILLED when both exist in the family corpus. Swap lowest-scoring entries to meet the floor.
   - Floors from `_kept_floor()` / `_killed_floor()` (default 2 each).

5. **Cold-path logging:**
   - Diversity half returned 0 entries → log `PREFLIGHT_DIVERSITY_DEGRADED`, fill relevance half up to `k`.
   - Relevance candidates < `relevance_share` → log `PREFLIGHT_MMR_DEGRADED`.

6. **Render.** Sections grouped: "## Closest priors" (relevance half) and "## Cross-dimension priors (for synthesis)" (diversity half). Per entry:
   - `thesis_id`, outcome, `mechanism_dimension`
   - `hypothesis` ≤180 chars, `mechanism` ≤180 chars
   - `validation_failure_reason` ≤160 chars
   - `job_id`, `round_number`
   - **`config_changes` key→value pairs** (up to `_preflight_config_changes_max_keys()`, default 5). Long string values truncated to 60 chars. Overflow: `"+{N} more keys: [...]"`. Surfacing values (not just keys) is how the agent can spot that a specific value like `ema_period=8` was already tried.

### 6.3 `build_landscape_block(family) -> str`

Aggregations over `*_backtest_runs.db` for the family. Two SQL queries on `BacktestRunDB`:

1. **`list_dimension_summary(family)`** → groups by `mechanism_dimension`, returns `(dimension, total, kept, killed)`. Classifies each as **saturated** (total ≥ `_landscape_saturated_at()`, default 8), **active** (1 ≤ total < threshold), or **unexplored** (total = 0 — only listed when the dimension exists in `MECHANISM_DIMENSIONS` but has no attempts).

2. **Adjacency gaps:** for every dimension pair `(A, B)` where each individually has ≥3 attempts, count theses whose `theme_keywords` cross both dimensions (any prior in A whose theme_keywords intersect priors in B). Zero count → surface as "adjacent pair never combined: A × B".

**Render:**

```markdown
## Mechanism landscape (family=ema)

Dimensions explored:
- trend_filters         → 12 attempts, 2 kept, 10 killed   (saturated)
- vol_regime_filter     →  4 attempts, 1 kept,  3 killed   (active)
- exit_management       →  3 attempts, 0 kept,  3 killed   (active)

Adjacent pairs never combined:
- trend_filters × vol_regime_filter
- exit_management × regime_conditioning

Unexplored dimensions (zero attempts):
- universe_selection, alternative_data
```

### 6.4 `build_dimension_pairs_block(family) -> str`

For each `mechanism_dimension` with ≥1 KILLED and ≥1 KEPT in the corpus:

1. Most-recent KILLED.
2. KEPT with the largest validation-metric improvement vs baseline (fallback: most-recent KEPT).
3. Render as a pair.

Capped at `_pairs_block_max_dimensions()` (default 5), sorted by total attempt count descending.

**Render:**

```markdown
## Killed/kept pairs by dimension (synthesis substrate)

### Dimension: trend_filters
- KILLED: ema_trend_filter_v2 (job=12, round=5) — ADX>25 entry filter; failed chop_sensitivity
- KEPT:   ema_htf_gate (job=11, round=2) — 1h-direction gate; PF 1.08 → 1.34

### Dimension: vol_regime_filter
- KILLED: ema_vol_quantile (job=10, round=3) — top-decile vol skip; PF unchanged
- KEPT:   ema_vol_regime_v1 (job=11, round=6) — overnight-ATR multiple skip; PF 1.10 → 1.41
```

Helper: `BacktestRunDB.list_killed_kept_pairs(family)`.

### 6.5 Synthesis turn

The conductor runs **two LLM turns** instead of one.

**Turn 1 — synthesis.** User prompt = round context + pre-flight blocks + this instruction:

> "Given the priors, landscape, and dimension pairs above, identify 2–3 unexploited combinations, contradictions, or gaps you notice in the corpus. Output a JSON array of `{observation, supporting_thesis_ids[]}`. Do not draft a thesis yet."

Output schema: `{"synthesis_observations": [{"observation": ≥80 chars, "supporting_thesis_ids": [...]}]}`. Persisted to the round artifact.

**Turn 2 — drafting.** Same context + synthesis output appended + existing drafting instruction.

The split is the well-documented "deliberation before decision" pattern. Single-turn prompts anchor agents on the most-recent prior; two turns split the anchor.

Kill switch: `_synthesis_enabled()` (default `true`). When `false`, the blocks still get injected but only one turn runs.

### 6.6 `dedup_check(intent)` and `DedupOverride`

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

@dataclass
class DedupOverride:
    matched_thesis_id: str
    similarity: float
    load_bearing_difference: str   # ≥60 chars, validator-enforced
```

- Query: `f"{intent.draft_hypothesis}\n\n{intent.draft_mechanism}"`.
- `n_results=1`. Threshold from `_dedup_threshold()` (default 0.88).
- On trigger, conductor returns the result to the agent. Agent must revise OR set `ResearchThesis.dedup_override_justification`.
- Cap: 1 override per round. Second override attempt → hard reject (`structural_dedup_override_invalid`).

This is the AI Scientist v1 lesson operationalized: soft-gate with explicit override, not hard novelty rejection. False positives are handled at the agent + validator boundary, not by relaxing the threshold.

### 6.7 Last-experiment context enrichment in the user prompt

The current user prompt has gaps in "what just happened." All four items below close them. All data is already stored in `*_backtest_runs.db` and `research_thesis_attempts`; we only surface it.

#### 6.7.1 `runtime_config` values inline

**Today:** user prompt shows `config_path` (file path), not values.
**Change:** `_resolve_conductor_inputs` (line 435) attaches `latest_outcome["runtime_config"] = _resolve_runtime_config_for_record(...)` (the function already exists and is already called for resolution context — its return is currently used for one thing).
**Render:** `LATEST EXPERIMENT CONFIG (values used):` block printing key→value pairs from `runtime_config`, capped at `_last_run_config_max_keys()` (default 20). Overflow → `"+{N} more: [...]"`. Long values truncated to 80 chars.
**Cost:** ~10–30 tokens. Eliminates a class of analyst calls.

#### 6.7.2 `strategy_diagnostics_json` summary inline

**Today:** user prompt shows `diagnostics_file` path. The agent must call analyze_trades to read the JSON.
**Change:** `_resolve_conductor_inputs` reads the diagnostics file (try/except + fail-open) and extracts the same summary shape used in `research_memory._experiment_compact_detail`: `event_counts`, `rejection_breakdown`, `trade_analysis`, `verdict` subfields. Stored as `latest_outcome["diagnostics_summary"]`.
**Render:** `LATEST EXPERIMENT DIAGNOSTICS (summary):` block showing the four subfields verbatim. Full diagnostics file path remains available for deep dives.
**Failure mode:** file unreadable → block omitted, `LATEST_DIAGNOSTICS_DEGRADED` logged.
**Cost:** ~200–500 tokens. Removes ~1 analyst call per round on average.

#### 6.7.3 In-round rejected thesis attempts as structured data

**Today:** rejections are flattened into `rejection_block` text. The agent sees rejection text but not the structured `(attempt_number, validator_status, validation_failure_reason, hypothesis, mechanism)` per attempt.
**Change:** new helper `BacktestRunDB.list_round_attempts(research_round_id)` returning rejected attempts for the most recent completed round of the current job. Called inside `_resolve_conductor_inputs`; attached as `latest_outcome["this_round_rejected_attempts"]`.
**Render:** `THIS ROUND'S REJECTED ATTEMPTS (structured):` block: `attempt_number`, `validator_status`, `validation_failure_reason`, `hypothesis` (≤180 chars), `mechanism_dimension` per attempt. Capped at `_max_round_rejected_attempts()` (default 5).
**Cost:** ~50–300 tokens.

#### 6.7.4 Expanded `previous_thesis` — surface the load-bearing schema fields

**Today:** `latest_thesis_details` returns 11 of the ~30 `ResearchThesis` fields — about 37% of the prior round's structured reasoning.
**Change:** extend `latest_thesis_details` to also return the 7 + 1 load-bearing fields below. They're already stored (in `thesis_details_json` in `research_thesis_attempts`). Render in the existing `previous_thesis` block.

| Field | Value shape (from real fixtures) | Why next round needs it |
|---|---|---|
| `mechanism_dimension` | one-token enum (`"signal_quality"`) | Anchors landscape positioning |
| `theme_keywords` | 2–3 short noun phrases | Cluster-fixation rule depends on this |
| `causal_cluster` | human-phrased family (`"opening-session adverse selection"`) | Human framing complementary to `theme_keywords` |
| `alternatives_considered` | list of `{mechanism, why_rejected ≥40 chars}`, ≥2 entries | Highest forward-reasoning value of the lot — the prior's pre-vetted rejected angles are natural next candidates |
| `prior_lever_outcomes` | list of `{prior_thesis_id, lever, direction_then, outcome, why_retry ≥40 chars}` | Direct anti-whipsaw substrate |
| `source_code_verification` | rich string with file:function + explanation, ~100 chars | Tells next round where the prior connected to code |
| `thesis_role` | 3-state enum (`orthogonal_discovery` / `implementation_unlock` / `cleanup_validation_follow_up`) | Shapes the kind of next thesis that fits |
| `engine_change_request` (paired render of `requires_code_change` + `requested_primitives`) | `{requires: bool, primitives: ["volatility_regime_filter", ...]}` | Engine-starvation rule depends on this; only meaningful as a pair |

**Deliberately not added** (verified against real fixtures + redundancy check):
- `dimension_novelty`, `novel_connection` — defensive text from the prior; outcome supersedes.
- `dominant_cluster_overlap` — implied by landscape block counts.
- `underexplored_dimensions_considered` — stale by definition; landscape block has fresher data.
- `evidence_citations` (typed) — empty today because OUTPUT prompt doesn't ask for it. After §5.8 fixes that, this will start populating; surface it then instead of legacy `evidence`.
- `required_diagnostics` (legacy prose) — values are inconsistent; §5.8 refactor produces structured `required_diagnostic_specs` going forward.

**Truncation budgets:** mechanism_dimension / thesis_role / source_code_verification untruncated; alternatives_considered up to 4 entries with `why_rejected` ≤200 chars each; prior_lever_outcomes up to 4 entries; `theme_keywords` full list. Missing/empty fields omitted from rendering (no empty placeholders).

**Cost:** ~200–600 tokens depending on richness.

### 6.8 Tool-description edit in `research_prompts.py`

The system prompt's tool-list block (lines 50–58) advertises `list_past_theses` / `get_past_thesis` / `list_experiment_results` / `get_experiment_result` as if they were the primary path to prior context. Once pre-flight pre-loads the top-K, they become **follow-up tools**. Without rewording, the agent will sometimes call them redundantly for context already in front of it.

**Change.** Replace the description text only:

```
- list_past_theses / get_past_thesis     Deep follow-up on a specific prior.
                                          Top-K relevant priors are already
                                          pre-loaded in this round's user prompt.
                                          Use this tool only when you need
                                          details on a thesis NOT in that block,
                                          or full attempt detail beyond the
                                          summary you were shown.
- list_experiment_results / get_*        Same — for backtest outcomes.
```

Tool signatures and MCP wiring unchanged.

### 6.9 OUTPUT schema refactor — structured fields

The OUTPUT block in `research_prompts.py` asks for two **legacy free-form** fields whose **structured** siblings exist in the schema and were explicitly designed as canonical. The schema-author comments in `research_types.py` say so directly. The structured fields sit empty in real outputs because the prompt doesn't ask for them.

#### 6.9.A Refactor — `required_diagnostics` → `required_diagnostic_specs`

**Today:** OUTPUT asks for `required_diagnostics: list[str]`. Real values vary from descriptive sentences ("Max_drawdown and pct_profitable_windows vs base") to terse keys ("regime_breakdown"). The helper `diagnostic_contracts.build_required_diagnostic_specs` normalizes each into a `DiagnosticRequirementSpec` — for prose strings, the result is a mangled key with the prose as `description`.

**Change:**
- **OUTPUT prompt:** ask for `required_diagnostic_specs: list[{key, surface, description, payload_fields?}]`. `key` must be snake_case (registered or stable identifier the agent commits to). `surface ∈ {metrics, strategy_diagnostics, experiment_evaluation, any}`.
- **`diagnostic_contracts.build_required_diagnostic_specs`:** when structured input is provided, use it as-is. Prose-to-specs derivation retained as legacy fallback for DB-loaded historical attempts.
- **Validator (§6.4):** existing rules that read `required_diagnostics` (prose) migrate to read `[spec.key for spec in required_diagnostic_specs]`. Full enumeration during writing-plans via grep.

#### 6.9.B Refactor — `evidence: list[str]` → `evidence_citations`

**Today:** OUTPUT asks for `evidence: list[str]`. The typed `evidence_citations` (with `source ∈ {web_search, analyst, source_code, experiment_result, memory}`) exists in the schema but is empty because the prompt doesn't ask for it. The schema comment says the validator was *intended* to require ≥1 `web_search` + ≥1 `analyst` — that rule is **aspirational, not enforced today** because the field is empty.

**Change:**
- **OUTPUT prompt:** ask for `evidence_citations: list[{source, citation}]`. List source enum values explicitly. Drop legacy `evidence` from instructions (field remains in `ResearchThesis` for backward-compat on DB reads).
- **New validator rule (§6.5):** activate the aspirational coverage rule. ≥1 `web_search` AND ≥1 `analyst`, with cold-start waiver (no trades file → analyst requirement waived; matches `research_conductor.py:162–168` `no_trades_instruction` path).

**Coupling:** consumer-side surfacing in §6.7.4 (`evidence_citations` mention) only delivers value once these refactors land. Bundled in this spec rather than split, so producer + consumer align from day one.

---

## 7. Validator changes

No retires. Extensions and one new rule, plus the schema-refactor-driven migrations.

### 7.1 `prior_lever_outcomes` content check

When `prior_lever_outcomes` is non-empty, every `prior_thesis_id` must exist in the corpus snapshot the pre-flight block was built from.

- **Severity:** hard reject.
- **Rejection code:** `structural_prior_lever_outcomes_unknown_id`.
- **Evidence:** unknown ids; truncated set of valid ids.

### 7.2 `underexplored_dimensions_considered` misclassification (soft-warn)

When corpus stats exist, emit a `BehaviorSignal` (`severity="warn"`) when the chosen `mechanism_dimension` has **strictly more** prior attempts than **every** dimension in `underexplored_dimensions_considered`. The agent labeled its own choice as underexplored without warrant.

- **Severity:** warn (not block). Surfaces in reflexion.
- **Behavior code:** `thesis_quality_underexplored_misclassification`.
- **Rationale for soft:** legitimate cases exist (recent kept result, new variant). Blocking would over-fire.

### 7.3 Dedup-override well-formedness

Validator rejects (`structural_dedup_override_invalid`) when:
- `dedup_override_justification.load_bearing_difference` missing or <60 chars.
- `matched_thesis_id` doesn't resolve to a real prior.
- More than 1 override attempt in the same round.

### 7.4 Migrate diagnostic-spec consumers

Rides §6.9.A. Any rule in `thesis_validator.py` reading `required_diagnostics` (prose) migrates to read from `required_diagnostic_specs`. The legacy field remains for backward-compat on DB-loaded attempts; validator rules see a consistent shape via the fallback normalization.

- **Affected rules:** enumerated during writing-plans by grep.
- **Behavior change:** none for new outputs. Rejection codes unchanged.

### 7.5 New rule: `evidence_citations` source coverage

Activates the aspirational rule referenced in `research_types.py`. Rides §6.9.B.

- **Check:** when `evidence_citations` is non-empty, require ≥1 `source="web_search"` AND ≥1 `source="analyst"`.
- **Cold-start waiver:** `analyst` requirement waived when `latest_outcome` indicates no trades file (matches `no_trades_instruction` path).
- **Severity:** hard reject.
- **Rejection code:** `structural_evidence_citations_coverage_insufficient`.
- **Evidence:** present sources, required sources, missing sources, waiver flag.

### 7.6 Rules explicitly preserved unchanged

- `_validate_process` and `_REQUIRED_PROCESS_TOOLS`.
- `_check_thesis_id_not_repeated`.
- Theme-overlap / `novel_connection`.
- Direction-whipsaw structural check.
- `causal_cluster` requirement when priors exist.
- `dimension_novelty` ≥30 chars.
- L6/L7 tool-order gates.
- All other rules.

---

## 8. Configuration

Lazy accessor functions — **not** module-level constants (CLAUDE.md hygiene rule).

| Function | Env var | Default | Purpose |
|---|---|---|---|
| `_preflight_k()` | `AUTORESEARCH_PREFLIGHT_K` | `8` | Total top-K (union of halves) |
| `_preflight_relevance_share()` | `AUTORESEARCH_PREFLIGHT_RELEVANCE_SHARE` | `0.5` | Fraction of K spent on relevance half |
| `_preflight_mmr_lambda()` | `AUTORESEARCH_PREFLIGHT_MMR_LAMBDA` | `0.5` | MMR `lambda_mult` |
| `_dedup_threshold()` | `AUTORESEARCH_DEDUP_THRESHOLD` | `0.88` | Cosine cutoff for dedup |
| `_cold_start_threshold()` | `AUTORESEARCH_PREFLIGHT_COLD_START_THRESHOLD` | `5` | Min per-family corpus size to enable pre-flight |
| `_landscape_saturated_at()` | `AUTORESEARCH_LANDSCAPE_SATURATED_AT` | `8` | Threshold for "saturated" |
| `_pairs_block_max_dimensions()` | `AUTORESEARCH_PAIRS_BLOCK_MAX_DIMENSIONS` | `5` | Cap on pairs rendered |
| `_synthesis_enabled()` | `AUTORESEARCH_SYNTHESIS_TURN_ENABLED` | `true` | Kill switch for the synthesis turn |
| `_kept_floor()`, `_killed_floor()` | `AUTORESEARCH_PREFLIGHT_KEPT_FLOOR`, `..._KILLED_FLOOR` | `2`, `2` | Outcome-balance floors |
| `_preflight_config_changes_max_keys()` | `AUTORESEARCH_PREFLIGHT_CONFIG_CHANGES_MAX_KEYS` | `5` | Max config_changes key→value pairs per prior |
| `_last_run_config_max_keys()` | `AUTORESEARCH_LAST_RUN_CONFIG_MAX_KEYS` | `20` | Max runtime_config keys for the last experiment |
| `_max_round_rejected_attempts()` | `AUTORESEARCH_MAX_ROUND_REJECTED_ATTEMPTS` | `5` | Max rejected-attempt entries rendered |

Each accessor validates its env var (int/float parse, range check) and raises with the named env var on bad input.

### 8.1 Thesis corpus indexing

On first access per process, `preflight_recall._thesis_corpus_index()`:

1. Reads all rows from `*_backtest_runs.db` via `BacktestRunDB.list_research_thesis_attempts`.
2. For each row, upserts a drawer into ChromaDB `wing="thesis_corpus"` with id `f"thesis_{thesis_id}_attempt_{attempt_number}"`. Document text: `f"{hypothesis}\n\n{mechanism}"`. Metadata: `thesis_id, attempt_number, job_id, strategy_family, validator_status, mechanism_dimension, dimension_novelty, theme_keywords, created_at_utc, run_id, validation_failure_reason`.
3. ChromaDB's default embedder (sentence-transformers all-MiniLM-L6-v2). No new dependency.

Subsequent calls within a process: count delta vs `thesis_corpus` drawer count — if equal, skip; if delta, upsert only new rows (ids are deterministic).

### 8.2 Cold start

When `thesis_corpus` filtered by `strategy_family` has fewer than `_cold_start_threshold()` (default 5) entries:

- `build_prior_attempts_block`, `build_landscape_block`, `build_dimension_pairs_block` return empty blocks.
- `dedup_check` returns `DedupResult.skipped(reason="cold_start")`.
- `PREFLIGHT_COLD_START` logged.
- No errors propagated.

---

## 9. Error handling

Fail-open for retrieval; fail-loud for validator rules.

| Failure | Behavior |
|---|---|
| ChromaDB unavailable / palace path fails | All blocks empty; `dedup_check` skipped; structured log; round proceeds. |
| Corpus empty for family | Cold-start path (§8.2). |
| Embedding call fails | Same as ChromaDB-unavailable. |
| Recursive override (agent keeps overriding) | Capped at 1/round. Second → hard-reject. |
| Synthesis turn produces malformed JSON | One retry. Second failure → skip synthesis output, proceed to drafting. `SYNTHESIS_TURN_DEGRADED` logged. |
| `mechanism_dimension` missing on a prior | Bucketed as `unknown_dimension` in landscape; not dropped. |
| Diagnostics file unreadable | `diagnostics_summary` block omitted; `LATEST_DIAGNOSTICS_DEGRADED` logged. |
| Corpus snapshot not passed to validator | Soft-skip `prior_lever_outcomes` content check; structural check still runs. |
| OUTPUT schema field missing on a new thesis (e.g. empty `required_diagnostic_specs`) | Standard schema-validation rejection; consistent with existing schema validation. |

---

## 10. Testing strategy

Real data from `*_backtest_runs.db`. No toy thesis names. No mocked internals.

### 10.1 Unit

- `preflight_recall`:
  - `_query_text_for_recall` builds expected string for cold-start, normal, and rejection-feedback cases.
  - `build_prior_attempts_block` respects the outcome-balance floor; honors MMR demotion (synthetic 5-clones + 5-spread test fixture at `lambda_mult=0.5` → ≤1 clone in first 3).
  - Two-pass union: when corpus has theses in multiple dimensions, returned K contains entries from both halves; when single-dim corpus, diversity half empty and relevance half fills (no exception).
  - `build_landscape_block` aggregations match fixture-DB hand-computed table; adjacency detection via `theme_keywords` correct.
  - `build_dimension_pairs_block` picks most-recent killed + best-improvement kept per dimension; honors cap.
  - `dedup_check` triggers on a re-embedded paraphrase of a known prior.
  - Cold start: empty blocks + skipped result + structured log.
  - Each lazy accessor reads env at call time, not at import.

- `preflight_synthesis_turn`: prompt assembly stable; malformed-output retry + degradation.

- `BacktestRunDB.list_dimension_summary`, `list_killed_kept_pairs`, `list_round_attempts`: aggregations match hand-computed expectations.

- `latest_thesis_details` expansion: returns all 7 + 1 fields when populated; omits missing fields cleanly.

### 10.2 Integration

- End-to-end conductor round with a populated corpus → user prompt contains all rendered blocks (closest priors, cross-dimension priors, landscape, pairs, synthesis instruction, all four §6.7 enrichments).
- Dedup trigger → override path → validator accepts override only when `load_bearing_difference ≥ 60` and `matched_thesis_id` resolves.
- Dedup trigger → agent revises → second draft passes.
- Validator §7.1: thesis with `prior_lever_outcomes[].prior_thesis_id="ghost_id"` → hard reject.
- Validator §7.2: chosen dimension has more attempts than every "underexplored" alternative → warn signal (not reject).
- Validator §7.5: `evidence_citations` missing `web_search` → hard reject; cold-start path → only `analyst` waived; `web_search` still required.
- Schema refactor (§6.9): fresh conductor run produces non-empty `required_diagnostic_specs` and `evidence_citations`; legacy fields empty on new outputs.
- Validator §7.4 migration: existing diagnostics-related rules still produce the same rejection codes after sourcing from `required_diagnostic_specs`.
- Cold start: new family, empty DB → round runs cleanly, empty blocks, no errors.
- Tool-description (§6.8): `_build_conductor_system_prompt` output contains the new wording, mentions "pre-loaded".

### 10.3 Rerun & state-transition

- Second run after first populates corpus: incremental upsert; no duplicate drawers.
- Manual deletion of a row from `*_backtest_runs.db` → drawer stays (documented limitation).

### 10.4 Behavior assertions, not structural

All test assertions check counts, scores, and content (`assert "ema_pullback_v3" in user_prompt`), never just non-null (CLAUDE.md rule **G**).

---

## 11. Migration plan

One PR, one deliverable, in this order:

1. `preflight_recall.py` (MMR + two-pass) + `preflight_synthesis_turn.py` modules with full test coverage.
2. `BacktestRunDB.list_dimension_summary` + `list_killed_kept_pairs` + `list_round_attempts` helpers.
3. `research_types.ResearchThesis.dedup_override_justification` field (Pydantic optional).
4. `research_memory.latest_thesis_details` expansion (§6.7.4).
5. `research_prompts.py`: tool-description reword (§6.8) + OUTPUT schema refactor (§6.9.A + §6.9.B). One file, two textual changes, one PR step.
6. `diagnostic_contracts.build_required_diagnostic_specs` update: structured input preferred; prose normalization is legacy fallback.
7. `autoresearch_research.py` `_resolve_conductor_inputs` enrichments (§6.7.1–.4): attach `runtime_config`, `diagnostics_summary`, `this_round_rejected_attempts`, expanded `previous_thesis` to `latest_outcome`.
8. `research_conductor.py` user-prompt augmentation (pre-flight blocks + last-experiment blocks + expanded previous_thesis render) + two-turn flow (synthesis → drafting) + post-draft dedup call site.
9. `thesis_validator.py` §7.1, §7.2, §7.3, §7.4 migration, §7.5 new rule.
10. End-to-end test against a real fixture DB; commit per CLAUDE.md verification rules.

No staged rollout flag. Behavior change is contained to thesis-creation rounds; cold-start path covers new families.

---

## 12. Open considerations (deliberately out of scope) and related work

### 12.1 Future work

- **Model routing within the synthesis turn.** Synthesis is meta-reasoning that doesn't need Opus tokens. Route to cheaper tier when a model router exists.
- **Wave 2: research subagent integration.** Same `preflight_recall` module; second injection point in the subagent prompt path.
- **Level 4: latent dimension discovery.** Clustering / density estimation over embedding space. Earn the right by measuring whether Levels 1–3 hit a ceiling.
- **Cross-family pre-flight.** Currently `where={"strategy_family": ...}`. A future spec could add controlled cross-family recall for genuinely orthogonal mechanisms.
- **Reconciliation with `prompt-variant-framework`.** The default variant being edited here should be respected by the variant-framework work when it lands. Worth a heads-up in that plan's next revision.
- **Periodic insight curation (QuantEvolve every-50-generations pattern).** Our `research_findings` MemPalace wing grows continuously without re-curation; over many jobs it will drift toward redundancy.
- **Field-wise / point-wise novelty (NoveltyAgent pattern).** Current dedup embeds `hypothesis + mechanism` as one document. Separate embeddings per field with dedup firing only on multi-field matches is more expressive.
- **MAP-Elites grid maintenance.** QuantEvolve / FunSearch / AlphaEvolve enforce "one elite per niche." Whether to add `(family, mechanism_dimension, theme_cluster)` grid constraints is a paradigm choice.
- **Selection turn between synthesis and drafting (Phase 4 converge).** Ranks synthesis-turn angles on disconfirmer strength × evidence availability × novelty. ~+30% tokens; deferred until telemetry justifies.
- **Deprecation of `list_past_theses` MCP tool.** Pre-flight + the §6.8 description edit make the tool largely redundant for primary context. Revisit deprecation after one quarter of telemetry.

### 12.2 Related work

| Source | What we adopted | What we deliberately did not |
|---|---|---|
| [QuantEvolve (arxiv:2510.18569)](https://arxiv.org/html/2510.18569v1) | Dimension-axis structured exploration; landscape view | Full MAP-Elites grid + island model + α exploit/explore parameter |
| [FunSearch / AlphaEvolve / OpenEvolve](https://github.com/codelion/openevolve) | Past attempts (kept + killed) injected into LLM context | Behavioral-hash dedup |
| [MMR / LangChain / Azure / Elastic](https://www.elastic.co/search-labs/blog/maximum-marginal-relevance-diversify-results) | `lambda_mult`-based MMR in relevance half | Hybrid BM25+vec (corpus small + homogeneous) |
| [AI Scientist v1 critical eval (arxiv:2502.14297)](https://arxiv.org/abs/2502.14297) | Cautionary tale → embeddings over keyword search; soft-gate with override | Keyword-only novelty |
| [NoveltyAgent (arxiv:2603.20884)](https://arxiv.org/pdf/2603.20884) | Self-validation pattern: dedup result back to agent for revise-or-override | Point-wise per-field novelty (deferred) |
| [Auto Researching convergence (arxiv:2603.15916)](https://arxiv.org/pdf/2603.15916) | Empirical justification for dimension-switching over hyperparameter tuning (landscape block) | Cross-architecture meta-search |
| [Memory Management Impact (arxiv:2505.16067)](https://arxiv.org/pdf/2505.16067) | Selective addition: validator gates what enters the corpus | Two-tier constant-size memory |

---

## 13. Success criteria

- A planning round on a populated `ema_backtest_runs.db` shows: prior-attempts block (with both "Closest priors" and "Cross-dimension priors" sub-sections), landscape block, dimension-pairs block, and synthesis observations in its user prompt and round artifact.
- The top-K block contains ≥2 KEPT and ≥2 KILLED when both exist in the corpus.
- When the corpus has theses in multiple dimensions, ≥1 entry in the returned K is from a dimension other than `latest_outcome.mechanism_dimension` (two-pass working).
- MMR re-ranking demoted ≥1 near-clone in the synthetic 5-clones + 5-spread test fixture at `lambda_mult=0.5`.
- Rendered prior-attempts entries show `config_changes` key→value pairs (up to the cap), not just key names — verified by asserting a specific value (e.g. `"ema_period": 8`) appears in the block.
- User prompt contains `LATEST EXPERIMENT CONFIG (values used):` block (§6.7.1).
- User prompt contains `LATEST EXPERIMENT DIAGNOSTICS (summary):` block when readable; omitted with `LATEST_DIAGNOSTICS_DEGRADED` log when not (§6.7.2).
- User prompt contains `THIS ROUND'S REJECTED ATTEMPTS (structured):` block when present; omitted when empty (§6.7.3).
- The rendered `previous_thesis` block contains the 7 + 1 expanded fields when populated (§6.7.4), verified by asserting a fixture prior's `alternatives_considered` `why_rejected` substring appears.
- A fresh conductor run produces non-empty structured `required_diagnostic_specs` and `evidence_citations`; legacy `required_diagnostics` and `evidence` are empty on new outputs (§6.9).
- A thesis with `evidence_citations` missing `web_search` is rejected with `structural_evidence_citations_coverage_insufficient` (§7.5).
- A cold-start `web_search`-only round (no trades file) accepts the thesis when `analyst` is missing but `web_search` is present (§7.5 waiver).
- Validator rules that previously sourced from `required_diagnostics` produce the same rejection codes after migration (§7.4).
- A near-duplicate proposed thesis (paraphrased version of a known prior) is caught by dedup; matched `thesis_id` + similarity surfaced.
- A thesis with hallucinated `prior_lever_outcomes[].prior_thesis_id` is hard-rejected (§7.1).
- A thesis whose chosen dimension has more attempts than every "underexplored" alternative receives a `thesis_quality_underexplored_misclassification` warn (not reject) (§7.2).
- Cold-start run (new family, empty corpus) completes cleanly with empty blocks.
- Two consecutive runs against the same `*_backtest_runs.db` do not produce duplicate ChromaDB drawers.
- Token cost per planning round at most ~1.5× the pre-change baseline (synthesis turn + injected blocks). Documented in the run artifact's usage block.

---

## 14. Levels coverage scorecard

| Level | Goal | How this spec delivers it |
|---|---|---|
| 1 — Awareness | Agent sees relevant priors | `build_prior_attempts_block` with two-pass + MMR + outcome-balance floor (§6.2); enriched `previous_thesis` (§6.7.4) |
| 2 — Pattern surfacing | Saturated vs unexplored dimensions | `build_landscape_block` + `list_dimension_summary` + adjacency via `theme_keywords` (§6.3) |
| 3 — Cross-thesis synthesis | Killed/kept pairs + lateral-thinking turn | `build_dimension_pairs_block` (§6.4) + synthesis turn (§6.5) + two-pass retrieval (§6.2) |
| 4 — Latent dimension discovery | Clustering / density on embedding space | Out of scope (§2, §12.1) |
