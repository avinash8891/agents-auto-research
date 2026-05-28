# Spec A — Conductor Context Snapshot v1

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** `2026-05-28-preflight-recall-design.md` (unified long-form context)
**Depends on:** none
**Blocks:** Spec C (semantic retrieval relies on the snapshot's deterministic foundation)
**Parallel with:** Spec B

---

## 1. Goal

Before each conductor LLM call, build a **deterministic, SQL-sourced context snapshot** that surfaces — in the user prompt — every piece of state about the research arc the conductor needs to reason well. No embeddings. No vector store. No LLM-side analytics. Just SQL aggregations and structured rendering of what's already in `*_backtest_runs.db`.

The user-visible win: the conductor stops being blind to landscape, parameter values, in-round rejections, and the prior thesis's structured reasoning — all without paying analyst tokens for value reads.

This is the foundation. Specs C and D layer semantic retrieval and lateral-thinking turns on top **only if telemetry from this spec shows they're needed.**

## 2. Non-goals (handled in other specs)

- **Semantic retrieval, MMR, two-pass diversity, `wing="thesis_corpus"` in ChromaDB, semantic dedup of drafted theses.** → Spec C.
- **Two-turn synthesis flow.** → Spec D.
- **OUTPUT schema refactor (`required_diagnostic_specs`, `evidence_citations`, new evidence-coverage rule).** → Spec B. This spec surfaces whatever fields the conductor currently produces; if the legacy fields are populated and the structured ones empty, this spec renders the legacy ones. Spec B fixes producer-side; rendering picks up structured automatically once populated.
- Wave 2 (research subagent), Level 4 (latent dimension discovery), prompt-variant-framework reconciliation. → reference doc §12.

## 3. Background

See reference doc §3 for full ground truth. Compressed summary:

- **System prompt** is static per family. **User prompt** is round-specific and built inline at `research_conductor.py:131–175`, fed by `_resolve_conductor_inputs` (`autoresearch_research.py:435`).
- `*_backtest_runs.db` stores `runtime_config_json`, `strategy_diagnostics_json`, full `research_thesis_attempts` (including rejected attempts with `validation_failure_reason`), and `thesis_details_json` (the full ~30-field `ResearchThesis` per attempt).
- Today the user prompt surfaces ~11 of ~30 prior-thesis fields, no inline runtime_config values, no inline diagnostics summary, no structured in-round rejections, no landscape view. The agent calls the analyst to learn these or guesses.

## 4. Architecture

```
backtest_run_db.py               ← edited
  ├─ list_dimension_summary(family)         landscape aggregations
  ├─ list_killed_kept_pairs(family)         per-dimension killed/kept pair lookup
  └─ list_round_attempts(research_round_id) structured rejected-attempt rows

research_memory.py               ← edited
  └─ latest_thesis_details expanded to surface 7 + 1 load-bearing schema fields
     (see §5.4 — uses fields already stored in thesis_details_json)

autoresearch_research.py         ← edited
  └─ _resolve_conductor_inputs (line 435): attach runtime_config,
       diagnostics_summary, this_round_rejected_attempts, expanded
       previous_thesis to latest_outcome

context_snapshot.py              ← new (small)
  ├─ build_landscape_block(family) → str
  ├─ build_dimension_pairs_block(family) → str
  └─ build_runtime_config_block / diagnostics_block / rejected_attempts_block / previous_thesis_block
     (rendering helpers — pure functions, no I/O)

research_conductor.py            ← edited
  └─ user-prompt construction (lines 131-175): append new blocks before
     the existing rejection_block / escalation_directive

research_prompts.py              ← edited (small static reword)
  └─ Tool-list block (lines 50-58): reword to mark list_past_theses /
     list_experiment_results as deep-follow-up tools

thesis_validator.py              ← edited (small)
  ├─ §6.1 prior_lever_outcomes content check: cited prior_thesis_id
  │  values must exist in the snapshot's surfaced thesis_ids set
  └─ §6.2 underexplored_dimensions_considered misclassification soft-warn
```

No new dependency. No ChromaDB use. All reads from SQLite. Pure functions for rendering.

## 5. Components

### 5.1 `build_runtime_config_block(latest_outcome) -> str`

**Today:** user prompt shows `config_path` (file path), not values.
**Source:** `latest_outcome["runtime_config"]` (newly attached by `_resolve_conductor_inputs`, populated from `_resolve_runtime_config_for_record` which is already called for resolution context).
**Render:** `LATEST EXPERIMENT CONFIG (values used):` block with key→value pairs, capped at `_last_run_config_max_keys()` (default 20). Overflow: `"+{N} more: [k1, k2, ...]"`. Long values truncated to 80 chars.
**Cost:** ~10–30 tokens.

### 5.2 `build_diagnostics_block(latest_outcome) -> str`

**Today:** user prompt shows `diagnostics_file` path; agent must call analyze_trades to read the JSON.
**Source:** `latest_outcome["diagnostics_summary"]` (newly attached). `_resolve_conductor_inputs` reads the diagnostics file (try/except, fail-open), extracts the four subfields (`event_counts`, `rejection_breakdown`, `trade_analysis`, `verdict`) — the same compact shape used in `research_memory._experiment_compact_detail`.
**Render:** `LATEST EXPERIMENT DIAGNOSTICS (summary):` block with the four subfields verbatim.
**Failure mode:** file unreadable → block omitted; `LATEST_DIAGNOSTICS_DEGRADED` logged. Round proceeds.
**Cost:** ~200–500 tokens. Removes ~1 analyst call per round on average.

### 5.3 `build_rejected_attempts_block(latest_outcome) -> str`

**Today:** in-round rejections flattened into `rejection_block` text via `rejection_artifact.render_rejection_block`. No structured access.
**Source:** new `BacktestRunDB.list_round_attempts(research_round_id)` returning rejected attempts for the most recent completed round of the current job. Attached as `latest_outcome["this_round_rejected_attempts"]`.
**Render:** `THIS ROUND'S REJECTED ATTEMPTS (structured):` block. Per attempt: `attempt_number`, `validator_status`, `validation_failure_reason`, `hypothesis` (≤180 chars), `mechanism_dimension`. Capped at `_max_round_rejected_attempts()` (default 5). Older rejections accessible via existing `list_rejections` MCP tool.
**Cost:** ~50–300 tokens depending on rejection count.

### 5.4 `build_previous_thesis_block(latest_outcome)` — expanded

**Today:** `latest_thesis_details` (`research_memory.py:465`) returns 11 of ~30 `ResearchThesis` fields. The conductor sees ~37% of the prior round's structured reasoning.
**Change:** extend `latest_thesis_details` to also return the 7 + 1 load-bearing fields below. All are already stored in `thesis_details_json` in `research_thesis_attempts`.

| Field | Why next round needs it |
|---|---|
| `mechanism_dimension` | Anchors landscape positioning |
| `theme_keywords` | Cluster-fixation rule depends on it |
| `causal_cluster` | Human-readable causal family complementing `theme_keywords` |
| `alternatives_considered` (list of `{mechanism, why_rejected ≥40 chars}`, ≥2 entries) | Pre-vetted "considered but rejected" angles. If the picked angle failed, these are the natural next candidates. Highest forward-reasoning value. |
| `prior_lever_outcomes` (list of `{prior_thesis_id, lever, direction_then, outcome, why_retry ≥40 chars}`) | Direct anti-whipsaw substrate |
| `source_code_verification` (rich string, file:function + explanation, ~100 chars) | Tells next round where prior connected to code |
| `thesis_role` (3-state enum) | Shapes what kind of next thesis fits |
| `engine_change_request` (paired render of `requires_code_change` + `requested_primitives`) | Engine-starvation rule depends on this; only meaningful as a pair |

**Deliberately not added** (redundancy / staleness):
- `dimension_novelty`, `novel_connection` — outcome supersedes these defensive texts.
- `dominant_cluster_overlap` — landscape block has the same info, fresher.
- `underexplored_dimensions_considered` — stale by definition.
- `evidence_citations` typed — empty today (Spec B populates it; this spec keeps surfacing legacy `evidence` until Spec B lands, then auto-switches).
- `required_diagnostics` (prose) — values inconsistent (Spec B refactors).

**Truncation budgets:** `mechanism_dimension` / `thesis_role` / `source_code_verification` untruncated; `alternatives_considered` up to 4 entries with `why_rejected` ≤200 chars each; `prior_lever_outcomes` up to 4 entries; `theme_keywords` full list. Missing/empty fields omitted (no empty placeholders).

**Cost:** ~200–600 tokens depending on richness.

### 5.5 `build_landscape_block(family) -> str`

Aggregations over `*_backtest_runs.db` for the family. Two SQL queries on `BacktestRunDB`:

1. **`list_dimension_summary(family)`** — groups by `mechanism_dimension`, returns `(dimension, total, kept, killed)`. Classifies as **saturated** (`total ≥ _landscape_saturated_at()`, default 8), **active** (1 ≤ total < threshold), or **unexplored** (dimension exists in `MECHANISM_DIMENSIONS` but has no attempts).

2. **Adjacency gaps:** for each dimension pair `(A, B)` where both have ≥3 attempts, count theses whose `theme_keywords` cross both. Zero count → "adjacent pair never combined: A × B".

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

### 5.6 `build_dimension_pairs_block(family) -> str`

For each `mechanism_dimension` with ≥1 KILLED and ≥1 KEPT:
1. Most-recent KILLED.
2. KEPT with the largest validation-metric improvement vs baseline (fallback: most-recent KEPT).

Capped at `_pairs_block_max_dimensions()` (default 5), sorted by total attempt count descending.

Helper: `BacktestRunDB.list_killed_kept_pairs(family)`.

**Render:**

```markdown
## Killed/kept pairs by dimension (next-round substrate)

### Dimension: trend_filters
- KILLED: ema_trend_filter_v2 (job=12, round=5) — ADX>25 entry filter; failed chop_sensitivity
- KEPT:   ema_htf_gate (job=11, round=2) — 1h-direction gate; PF 1.08 → 1.34
```

### 5.7 Tool-description reword in `research_prompts.py`

Tool-list block (lines 50–58). Replace description text only — tool signatures and MCP wiring unchanged:

```
- list_past_theses / get_past_thesis     Deep follow-up on a specific prior.
                                          Landscape + pairs + recent rejections
                                          are already pre-loaded in this round's
                                          user prompt. Use this tool only when
                                          you need a thesis NOT in those blocks
                                          or fuller detail than the summary.
- list_experiment_results / get_*        Same — for backtest outcomes.
```

## 6. Validator changes

### 6.1 `prior_lever_outcomes` content check (hard reject)

When `prior_lever_outcomes` is non-empty, every `prior_thesis_id` must exist in the **snapshot's surfaced thesis_ids set**. The snapshot defines the set of "thesis_ids the conductor saw in this round" — pre-flight block (none yet in Spec A), landscape block, dimension-pairs block, expanded previous_thesis, in-round rejected attempts.

- **Severity:** hard reject.
- **Rejection code:** `structural_prior_lever_outcomes_unknown_id`.
- **Evidence:** unknown ids; truncated set of valid ids (from the snapshot).

**Note on Spec C:** when Spec C ships and adds a semantic top-K block, the snapshot's valid_ids set expands to include those. The validator rule's contract is "must exist in the snapshot the conductor saw" — invariant across specs.

### 6.2 `underexplored_dimensions_considered` misclassification (soft warn)

When the snapshot's `list_dimension_summary` is available, emit a `BehaviorSignal` (severity="warn") when the chosen `mechanism_dimension` has **strictly more** prior attempts than **every** dimension in `underexplored_dimensions_considered`.

- **Severity:** warn (surfaces in reflexion). Not block.
- **Behavior code:** `thesis_quality_underexplored_misclassification`.
- **Rationale for soft:** legitimate cases exist (recent kept result, new variant); blocking over-fires.

### 6.3 Rules explicitly preserved

All existing rules (`_validate_process`, `_check_thesis_id_not_repeated`, theme-overlap, direction-whipsaw, `causal_cluster` requirement, `dimension_novelty` ≥30 chars, L6/L7 tool-order gates) unchanged.

## 7. Configuration

Lazy accessor functions (CLAUDE.md env-var hygiene rule).

| Function | Env var | Default | Purpose |
|---|---|---|---|
| `_landscape_saturated_at()` | `AUTORESEARCH_LANDSCAPE_SATURATED_AT` | `8` | Threshold for "saturated" dimension |
| `_pairs_block_max_dimensions()` | `AUTORESEARCH_PAIRS_BLOCK_MAX_DIMENSIONS` | `5` | Cap on pairs rendered |
| `_last_run_config_max_keys()` | `AUTORESEARCH_LAST_RUN_CONFIG_MAX_KEYS` | `20` | Max runtime_config keys |
| `_max_round_rejected_attempts()` | `AUTORESEARCH_MAX_ROUND_REJECTED_ATTEMPTS` | `5` | Max rejected-attempt entries rendered |

Each accessor validates its env var and raises with the named var on bad input.

## 8. Error handling

Fail-open for context rendering. Fail-loud for validator rules.

| Failure | Behavior |
|---|---|
| `*_backtest_runs.db` unreadable / missing | All blocks empty; structured log; round proceeds. |
| Family has no prior runs (true cold start) | Landscape/pairs blocks emit "no prior runs for this family"; other blocks render whatever's available. |
| Diagnostics file unreadable | `diagnostics_summary` block omitted; `LATEST_DIAGNOSTICS_DEGRADED` logged. |
| `runtime_config` unavailable | Block omitted. |
| `latest_thesis_details` returns empty (e.g., legacy attempt with no `thesis_details_json`) | Previous-thesis block falls back to whatever subset is available. No exception. |
| `mechanism_dimension` missing on a prior | Bucketed as `unknown_dimension` in landscape. |

## 9. Testing

Real data from `*_backtest_runs.db`. No toy thesis names. No mocked internals. CLAUDE.md rules **G** (real tests), I (quarantine bad data).

### 9.1 Unit

- `BacktestRunDB.list_dimension_summary`: hand-computed expectation matches against a fixture DB.
- `BacktestRunDB.list_killed_kept_pairs`: most-recent-killed + best-improvement-kept selection verified per dimension.
- `BacktestRunDB.list_round_attempts`: returns rejected attempts only; orders by `attempt_number`.
- `latest_thesis_details` expansion: returns all 7 + 1 fields when populated; omits cleanly when missing.
- `build_landscape_block`: saturated/active/unexplored classification correct; adjacency detection via `theme_keywords` intersection correct.
- `build_dimension_pairs_block`: cap honored; sorting by total attempts descending.
- `build_runtime_config_block`: cap honored; long values truncated; overflow rendering correct.
- `build_diagnostics_block`: missing file → empty; degraded log emitted.
- `build_rejected_attempts_block`: empty list → block omitted entirely.
- Each lazy accessor reads env at call time, not at import.

### 9.2 Integration

- End-to-end conductor round with a populated EMA fixture DB → user prompt contains all six block types (runtime_config, diagnostics, rejected_attempts, previous_thesis expanded, landscape, pairs). Assertions check content (`assert "ema_pullback_v3" in user_prompt`), not just non-null.
- Validator §6.1: thesis with `prior_lever_outcomes[].prior_thesis_id="ghost_id_not_in_snapshot"` → hard reject.
- Validator §6.2: chosen dimension has more attempts than every "underexplored" alternative → warn signal (not reject).
- Tool-description reword: `_build_conductor_system_prompt` output contains the new wording and mentions "pre-loaded".
- Cold start (new family, empty DB): round runs cleanly, all blocks omitted or "no prior runs" placeholder.

### 9.3 Rerun & state-transition

- Two rounds in sequence: second round sees the first round's outcome in the previous_thesis block.
- Manual deletion of a row from `*_backtest_runs.db` → blocks recompute correctly (no caching bug).

## 10. Migration plan

One PR, in order:

1. `BacktestRunDB.list_dimension_summary` + `list_killed_kept_pairs` + `list_round_attempts` helpers with unit tests.
2. `research_memory.latest_thesis_details` expansion with unit tests.
3. `context_snapshot.py` new module with the 6 render helpers and unit tests.
4. `autoresearch_research.py` `_resolve_conductor_inputs` enrichments (attach the new fields to `latest_outcome`).
5. `research_conductor.py` user-prompt augmentation (append blocks before existing `rejection_block` / `escalation_directive`).
6. `research_prompts.py` tool-description reword + unit test.
7. `thesis_validator.py` §6.1 + §6.2.
8. End-to-end integration test against a real fixture DB; commit per CLAUDE.md verification rules.

No staged rollout flag.

## 11. Telemetry contract (drives Spec C decision)

Spec A ships without semantic retrieval. To know whether Spec C is justified, we measure:

1. **Per-round repeat-rate:** % of new thesis drafts where the `hypothesis + mechanism` text has high lexical overlap (Jaccard ≥ 0.6 on token sets, or shared 5-gram count ≥ 5) with any prior thesis in the same family. Logged as `THESIS_REPEAT_LEXICAL_HIT`. Computed by a one-off post-thesis lexical comparator (no embeddings).
2. **Cross-dimension proposal rate:** % of new theses whose `mechanism_dimension` differs from `latest_outcome.mechanism_dimension`. Logged as `THESIS_CROSS_DIMENSION`. Direct measurement of whether the agent breaks out of just-failed direction.
3. **Validator-rejection rate from `prior_lever_outcomes` content check (§6.1):** % of rounds where the agent cited a `prior_thesis_id` that didn't exist in the snapshot. Logged via the existing rejection-code path.

**Decision rule for Spec C:**
- If `THESIS_REPEAT_LEXICAL_HIT` rate < 10% AND `THESIS_CROSS_DIMENSION` rate ≥ 50% over a rolling 30-round window → Spec C is **not justified**; the deterministic snapshot is sufficient.
- If `THESIS_REPEAT_LEXICAL_HIT` rate ≥ 10% OR `THESIS_CROSS_DIMENSION` rate < 50% → Spec C is **justified**; ship semantic retrieval + dedup.

These telemetry counters are added in this spec, written via `trace_sdk`, audited via existing `scripts/token_audit.py`-style tools.

## 12. Success criteria

- User prompt for a populated `ema_backtest_runs.db` round contains: `LATEST EXPERIMENT CONFIG`, `LATEST EXPERIMENT DIAGNOSTICS` (when readable), `THIS ROUND'S REJECTED ATTEMPTS` (when non-empty), expanded `previous_thesis` (with `mechanism_dimension`, `theme_keywords`, `alternatives_considered`, etc.), landscape block, dimension-pairs block.
- A specific `runtime_config` value (e.g. `"ema_period": 5`) is present in the rendered block for a fixture run that used it.
- A `why_rejected` substring from a fixture prior's `alternatives_considered` is present in the rendered previous_thesis block.
- Validator §6.1: ghost-ID thesis is hard-rejected with `structural_prior_lever_outcomes_unknown_id`.
- Validator §6.2: under-explored-mismatch thesis produces a warn signal, not a reject.
- Tool description in `_build_conductor_system_prompt` output contains "pre-loaded".
- Cold-start round runs cleanly.
- `THESIS_REPEAT_LEXICAL_HIT` and `THESIS_CROSS_DIMENSION` counters are emitted for every accepted thesis.

## 13. Out of scope (for clarity)

- Any embedding computation, vector store query, MMR, cosine similarity, dedup gate, override field. → Spec C.
- Any second LLM turn / synthesis turn. → Spec D.
- Any change to the OUTPUT schema (`required_diagnostic_specs`, `evidence_citations`). → Spec B.
- Any change to validator rules beyond §6.1 and §6.2. → Spec B handles diagnostics + evidence; Spec C handles dedup override well-formedness.
