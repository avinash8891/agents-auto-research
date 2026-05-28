# Spec C — Semantic Retrieval + Dedup

**Date:** 2026-05-28
**Status:** Design — **conditional** on Spec A telemetry
**Reference:** `2026-05-28-preflight-recall-design.md` (unified long-form context)
**Depends on:** Spec A (Conductor Context Snapshot — must ship and stabilize first)
**Blocks:** Spec D (synthesis turn ships after this, again conditionally)
**Trigger condition:** Spec A telemetry shows either:
- `THESIS_REPEAT_LEXICAL_HIT` rate ≥ 10% over a rolling 30-round window, OR
- `THESIS_CROSS_DIMENSION` rate < 50% over a rolling 30-round window.

If neither condition holds after ≥30 rounds of Spec A in production, Spec C is **not justified** and is dropped.

---

## 1. Goal

Add the semantic layer on top of Spec A's deterministic snapshot:

1. A `thesis_corpus` wing in the existing MemPalace ChromaDB, indexed from `*_backtest_runs.db`.
2. A **two-pass top-K retrieval block** (relevance half with MMR + diversity half via cross-dimension filter) inserted into the user prompt alongside Spec A's landscape and pairs blocks.
3. A **post-draft semantic dedup gate** that catches near-duplicate proposals lexical comparison missed, with an explicit soft-gate override path.

This spec specifically attacks failure modes that Spec A's deterministic snapshot **can't see**: "EMA crossover 9/21 proposed under different phrasing than the prior thesis it duplicates" — same idea, different words, lexical comparator misses.

## 2. Non-goals

- **Two-turn synthesis flow.** → Spec D.
- Replacing Spec A's blocks. Spec A's landscape/pairs/runtime_config/diagnostics/rejected-attempts/previous_thesis blocks remain unchanged. Spec C **adds** a top-K block (with two sub-sections: "Closest priors" and "Cross-dimension priors") and the dedup gate.
- Creating a new vector store. ChromaDB is already in use via MemPalace.
- Field-wise (NoveltyAgent-style) point-wise dedup. Future work.

## 3. Background

See reference doc §3 for full ground truth, and Spec A §3 for compressed background.

**Why the semantic layer needs to exist (when telemetry says so):**

- Spec A's `THESIS_REPEAT_LEXICAL_HIT` counter uses Jaccard / 5-gram overlap on raw text. Two theses can express the same mechanism with low lexical overlap (e.g. "ADX > 25 entry filter" vs "trend-strength gate via directional movement index"). Embedding cosine catches that; lexical does not.
- Spec A's landscape block tells the agent *which dimensions are saturated*. It does not surface the **specific 8 most-similar priors** to what's about to be proposed.
- AI Scientist v1's documented failure mode (arxiv:2502.14297) is the cautionary tale: keyword-based novelty checks misclassify well-known concepts as novel. Embeddings + soft-gate-with-override is the established fix.

**Why two-pass retrieval, not pure top-K:**

Pure cosine top-K against a query built from `latest_outcome` (which describes what just failed) concentrates returns near the just-failed direction. The agent then proposes more-of-the-same; dedup fires; cycle. MMR within the relevance half + an explicit cross-dimension diversity half is the standard production fix (LangChain, Azure AI Search, Elastic).

## 4. Architecture

```
preflight_recall.py              ← new
  ├─ PreflightIntent             dataclass (family, latest_outcome,
  │                               rejection_feedback, draft fields for dedup)
  ├─ build_prior_attempts_block  → str  (two-pass: MMR-relevance + cross-dim diversity)
  ├─ dedup_check                 → DedupResult  (post-draft, cosine ≥ θ)
  └─ _thesis_corpus_index        lazy ChromaDB collection accessor (wing="thesis_corpus")

research_types.py                ← edited (small)
  └─ Add DedupOverride dataclass + ResearchThesis.dedup_override_justification field

research_conductor.py            ← edited
  ├─ user-prompt construction: append pre-flight top-K block AFTER Spec A's blocks
  └─ post-draft: call dedup_check; on trigger, return to agent with match details

thesis_validator.py              ← edited (small)
  ├─ §6.1 (Spec C extension): the snapshot's valid_ids set expands to include
  │  pre-flight top-K thesis_ids (Spec A's §6.1 rule body stays the same;
  │  only the set computation grows)
  └─ §6.2 dedup-override well-formedness

context_snapshot.py              ← edited (small)
  └─ Renders the new pre-flight top-K block alongside existing Spec A blocks.
     "## Closest priors" + "## Cross-dimension priors (for synthesis)" sub-sections.
```

No new dependency. ChromaDB and MemPalace are already in the import graph via `research_memory.py`.

## 5. Components

### 5.1 `PreflightIntent`

```python
@dataclass(frozen=True)
class PreflightIntent:
    family: str
    latest_outcome: dict          # already in scope (from Spec A enrichment)
    rejection_feedback: str = ""
    draft_hypothesis: str = ""    # filled only for dedup
    draft_mechanism: str = ""     # filled only for dedup
```

Query construction inside `preflight_recall`:

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

### 5.2 Thesis corpus indexing

On first access per process:

1. Read all rows from `*_backtest_runs.db` via `BacktestRunDB.list_research_thesis_attempts`.
2. Upsert into ChromaDB `wing="thesis_corpus"` with id `f"thesis_{thesis_id}_attempt_{attempt_number}"`. Document text: `f"{hypothesis}\n\n{mechanism}"`. Metadata: `thesis_id, attempt_number, job_id, strategy_family, validator_status, mechanism_dimension, theme_keywords, created_at_utc, run_id, validation_failure_reason`.
3. Embedder: ChromaDB default (`sentence-transformers all-MiniLM-L6-v2`). No API cost, no new dep.
4. Subsequent calls: row-count delta vs drawer count — if equal, skip; if delta, upsert only new (ids are deterministic).

**Cold start:** if `thesis_corpus` filtered by `strategy_family` has fewer than `_cold_start_threshold()` (default 5) entries, the pre-flight block, dedup, and override all skip cleanly. `PREFLIGHT_COLD_START` logged.

### 5.3 `build_prior_attempts_block(intent, *, k=None) -> str`

Two-pass retrieval + MMR.

1. `k = _preflight_k()` (default 8). `relevance_share = k * _preflight_relevance_share()` (default 0.5).

2. **Relevance half (MMR):**
   - Chroma `query` with `where={"strategy_family": intent.family}`, `n_results = relevance_share * 3` (over-fetch).
   - Greedy MMR: first pick = argmax cosine to query. For subsequent picks `c`, score `lambda_mult * cos(c, query) - (1 - lambda_mult) * max(cos(c, s) for s in selected)`. `lambda_mult = _preflight_mmr_lambda()` (default 0.5).
   - Stop at `relevance_share` selections.

3. **Diversity half (cross-dimension):**
   - When `latest_outcome.mechanism_dimension` is set: Chroma query with `where={"strategy_family": intent.family, "mechanism_dimension": {"$ne": just_failed_dim}}`. Query text: `f"family={family}; explore mechanisms different from {just_failed_dim}"` (append `theme_keywords` if present).
   - When unset (round 0): random non-overlapping picks from family corpus.
   - No MMR here — `where_not` already enforces diversity.

4. **Union + outcome-balance floor:**
   - Dedup by `(thesis_id, attempt_number)`.
   - Enforce ≥2 KEPT and ≥2 KILLED when both exist in the family corpus. Swap lowest-MMR-score entries to meet the floor. Floors from `_kept_floor()` / `_killed_floor()` (default 2 each).

5. **Cold-path logging:**
   - Diversity half empty → `PREFLIGHT_DIVERSITY_DEGRADED`, relevance half fills up to `k`.
   - Relevance candidates < `relevance_share` → `PREFLIGHT_MMR_DEGRADED`.

6. **Render:** two markdown sub-sections — `## Closest priors` (relevance half) and `## Cross-dimension priors (for synthesis)` (diversity half). Per entry:
   - `thesis_id`, outcome, `mechanism_dimension`
   - `hypothesis` ≤180 chars, `mechanism` ≤180 chars, `validation_failure_reason` ≤160 chars
   - `job_id`, `round_number`
   - `config_changes` key→value pairs (up to `_preflight_config_changes_max_keys()`, default 5; long values truncated to 60 chars; overflow `"+{N} more keys: [...]"`).

### 5.4 `dedup_check(intent)` and `DedupOverride`

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
- On trigger: conductor returns the result to the agent. Agent must revise OR set `ResearchThesis.dedup_override_justification`.
- Cap: 1 override per round. Second override attempt → hard reject (`structural_dedup_override_invalid`).

This is the AI Scientist v1 lesson operationalized — soft-gate with explicit override is the established fix for embedding-novelty false positives.

## 6. Validator changes

### 6.1 Extend Spec A §6.1 `prior_lever_outcomes` content check

Spec A's rule already validates `prior_thesis_id` against the snapshot's surfaced ids. Once Spec C ships, the snapshot's valid_ids set **expands** to include the pre-flight top-K thesis_ids (which weren't there before). The rule body is unchanged; only the set-computation function grows.

- **Rejection code:** `structural_prior_lever_outcomes_unknown_id` (unchanged).
- **Behavior:** stricter coverage — ids the agent saw via pre-flight retrieval are now considered "seen" and can be cited.

### 6.2 New rule: dedup-override well-formedness

Validator rejects (`structural_dedup_override_invalid`) when:
- `dedup_override_justification.load_bearing_difference` missing or < 60 chars.
- `matched_thesis_id` doesn't resolve to a real prior in the snapshot's valid_ids set.
- A round has more than 1 override attempt (cap enforced at this rule).

## 7. Configuration

| Function | Env var | Default | Purpose |
|---|---|---|---|
| `_preflight_k()` | `AUTORESEARCH_PREFLIGHT_K` | `8` | Total top-K (union of halves) |
| `_preflight_relevance_share()` | `AUTORESEARCH_PREFLIGHT_RELEVANCE_SHARE` | `0.5` | Fraction of K spent on relevance half |
| `_preflight_mmr_lambda()` | `AUTORESEARCH_PREFLIGHT_MMR_LAMBDA` | `0.5` | MMR `lambda_mult` |
| `_dedup_threshold()` | `AUTORESEARCH_DEDUP_THRESHOLD` | `0.88` | Cosine cutoff for dedup |
| `_cold_start_threshold()` | `AUTORESEARCH_PREFLIGHT_COLD_START_THRESHOLD` | `5` | Min per-family corpus size to enable pre-flight |
| `_kept_floor()`, `_killed_floor()` | `AUTORESEARCH_PREFLIGHT_KEPT_FLOOR`, `..._KILLED_FLOOR` | `2`, `2` | Outcome-balance floors |
| `_preflight_config_changes_max_keys()` | `AUTORESEARCH_PREFLIGHT_CONFIG_CHANGES_MAX_KEYS` | `5` | Max config_changes key→value pairs per prior |

Each accessor validates and raises with the named env var on bad input.

## 8. Error handling

Fail-open for retrieval. Fail-loud for validator rules.

| Failure | Behavior |
|---|---|
| ChromaDB unavailable / palace path fails | Pre-flight block omitted; dedup skipped; structured log; round proceeds. Spec A's blocks still appear. |
| Corpus empty for family | Cold-start path — block omitted, dedup skipped. |
| Embedding call fails | Same as ChromaDB-unavailable. |
| Recursive override attempt | Capped at 1/round. Second → hard-reject. |
| `mechanism_dimension` missing on a prior | Diversity-half `where_not` ignores; entry can appear in relevance half. |

## 9. Testing

Real data from `*_backtest_runs.db`. No toy thesis names. No mocked internals.

### 9.1 Unit

- `_query_text_for_recall`: builds expected string for cold-start, normal, rejection-feedback cases.
- `build_prior_attempts_block`:
  - Respects outcome-balance floor.
  - MMR demotion verified: synthetic 5-clones + 5-spread fixture at `lambda_mult=0.5` → ≤1 clone in first 3.
  - Two-pass union: multi-dim corpus → entries from both halves; single-dim corpus → diversity half empty + relevance half fills (no exception).
- `dedup_check`: triggers on re-embedded paraphrase of a known prior at the default threshold.
- Cold start: empty block + skipped result + structured log.
- Each lazy accessor reads env at call time.

### 9.2 Integration

- End-to-end conductor round with a populated corpus → user prompt contains pre-flight top-K block with both sub-sections, alongside Spec A's existing blocks.
- Dedup trigger → override path → validator accepts override only when `load_bearing_difference ≥ 60` and `matched_thesis_id` resolves.
- Dedup trigger → agent revises → second draft passes.
- Validator §6.2: malformed override → hard reject.
- Validator §6.1 (extension): thesis citing a `prior_thesis_id` only present in pre-flight top-K (not in Spec A's snapshot) → accepted (set expanded).

### 9.3 Rerun

- Second run after first populates corpus: incremental Chroma upsert; no duplicate drawers.

## 10. Migration plan

One PR:

1. `preflight_recall.py` new module (intent, query, two-pass retrieval, MMR, dedup) with full unit tests.
2. `research_types.py` add `DedupOverride` + `ResearchThesis.dedup_override_justification`.
3. `context_snapshot.py` render of the new top-K block (with two sub-sections), called from `research_conductor.py`.
4. `research_conductor.py`: append pre-flight block; post-draft `dedup_check` call site; revise-or-override loop.
5. `thesis_validator.py`: §6.1 extension (expand valid_ids set), §6.2 new dedup-override rule.
6. Captured-fixture integration test against a real `ema_backtest_runs.db`.
7. End-to-end test + commit per CLAUDE.md.

## 11. Telemetry contract (drives Spec D decision)

Spec C ships with pre-flight + dedup. To know whether Spec D (synthesis turn) is justified, we measure:

1. **`PREFLIGHT_DEDUP_TRIGGERED` rate:** % of rounds where the dedup gate fired pre-validator.
2. **`PREFLIGHT_DEDUP_OVERRIDDEN` rate:** % of triggered dedups where the agent overrode rather than revised.
3. **`THESIS_REPEAT_LEXICAL_HIT` rate (continuing from Spec A):** does adding semantic retrieval bring this below 5%?
4. **`THESIS_ANCHORS_ON_LATEST` rate (new):** % of new theses whose `mechanism_dimension` AND ≥2 of `theme_keywords` overlap with `latest_outcome`'s. Measures whether the agent still anchors on the most-recent prior despite having cross-dimension priors in context.

**Decision rule for Spec D:**
- If `THESIS_ANCHORS_ON_LATEST` rate < 30% over a rolling 30-round window → Spec D is **not justified**.
- If ≥ 30% → Spec D is **justified**; ship synthesis turn.

## 12. Success criteria

- Pre-flight top-K block appears in the user prompt with both `## Closest priors` and `## Cross-dimension priors` sub-sections.
- The returned K contains ≥2 KEPT and ≥2 KILLED when both exist in the family corpus.
- ≥1 entry in the returned K is from a dimension other than `latest_outcome.mechanism_dimension` (when both exist).
- MMR demoted ≥1 near-clone in the synthetic 5-clones + 5-spread test fixture at `lambda_mult=0.5`.
- A near-duplicate proposed thesis (paraphrased version of a known prior) triggers dedup; matched `thesis_id` + similarity surfaced to the agent.
- Validator rejects a malformed dedup-override; accepts a well-formed one.
- Cold-start round (new family) completes cleanly with pre-flight block omitted.
- Two consecutive runs against the same `*_backtest_runs.db` do not produce duplicate Chroma drawers.
- All Spec C telemetry counters are emitted.

## 13. Coupling notes

- **Spec A must ship and stabilize first.** Spec A's `context_snapshot.py` rendering is where the new top-K block gets composed alongside the existing blocks. Spec A must be in production at least 30 rounds before Spec C ships, to generate the telemetry that justifies Spec C in the first place.
- **Spec B is independent.** `evidence_citations` and `required_diagnostic_specs` are not used by Spec C's retrieval or dedup paths.
- **Spec D depends on this spec** (synthesis turn operates on the pre-flight block this spec adds).
