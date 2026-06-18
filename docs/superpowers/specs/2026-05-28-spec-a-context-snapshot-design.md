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
     (the 5 promoted to §5.1 WHAT WAS TESTED, the 1 to §5.2 CONFIG, the 13
      remaining in §5.6 PROPOSER REASONING — uses fields already stored in
      thesis_details_json)

autoresearch_research.py         ← edited
  └─ _resolve_conductor_inputs (line 435): assemble last_research_round dict
       (renamed from latest_outcome, see §14) carrying runtime_config,
       diagnostics_summary, last_round_rejected_attempts, proposer_reasoning,
       plus identity + metrics + verdict fields

last_research_round_snapshot.py  ← new
  └─ build_blocks(last_research_round) → LastRoundBlocks
     ├─ build_what_was_tested_block(last_research_round)       §5.1
     ├─ build_config_block(last_research_round)                §5.2
     ├─ build_results_block(last_research_round)               §5.3
     ├─ build_diagnostics_block(last_research_round)           §5.4
     ├─ build_rejected_attempts_block(last_research_round)     §5.5
     ├─ build_proposer_reasoning_block(last_research_round)    §5.6
     └─ build_evidence_files_block(last_research_round)        §5.7
     (single-round scope — keyed by (job, round). Pure functions, no I/O
      beyond reading what _resolve_conductor_inputs already attached.)

family_history_snapshot.py       ← new
  └─ build_blocks(family) → FamilyHistoryBlocks
     ├─ build_landscape_block(family)                     §5.8
     └─ build_dimension_pairs_block(family)               §5.9
     (cross-history aggregations — keyed by family. Reads via BacktestRunDB
      helpers; SQL aggregations only.)

context_snapshot.py              ← new (thin orchestrator)
  └─ build_snapshot(last_research_round, family) → SnapshotResult
     ├─ calls last_research_round_snapshot.build_blocks(...)
     ├─ calls family_history_snapshot.build_blocks(...)
     └─ unions block texts + thesis_ids into SnapshotResult
        (SnapshotResult carries rendered_blocks: dict[str,str] and
         thesis_ids: set[str] — the set §6.1 binds against.)

research_conductor.py            ← edited
  └─ user-prompt construction (lines 131-175): render the seven last-round
     blocks (§5.1–§5.7) followed by family-history blocks (§5.8–§5.9), then
     escalation_directive. The legacy render_rejection_block call at line 194
     is deleted — §5.5 replaces it.

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

**Module layout.** Components §5.1–§5.7 live in `last_research_round_snapshot.py` (single-round scope, keyed by `(job, round)`) and render the seven last-research-round blocks. Components §5.8–§5.9 live in `family_history_snapshot.py` (cross-history aggregations, keyed by family) and render the two family-history blocks. §5.10 is a tiny static reword in `research_prompts.py`. The thin orchestrator `context_snapshot.build_snapshot(...)` calls both modules' `build_blocks(...)` and unions the result into a `SnapshotResult` — the object the conductor consumes and the validator (§6.1) binds against.

**Why split.** Different query shapes (single-row vs `GROUP BY`), different invalidation (last-round data fixed once round ends; family-history recomputed every round), different test fixtures, different concepts. Rule **B** — one home per concept.

**Section ordering matches prompt-render order.** §5.0 documents the prompt anatomy; §5.1–§5.7 then describe the seven last-research-round blocks **in the order they appear in the user prompt** — a narrative arc from *what was tested* → *how* → *what happened* → *what diagnostics showed* → *what got rejected first* → *the proposer's reasoning* → *deep-dive evidence file paths*. §5.8–§5.9 cover the two family-history blocks that follow. §5.10 covers the system-prompt reword.

### 5.0 Last-research-round portion — anatomy + payload mapping + cold-start

The controller hands the conductor a six-item payload describing the last completed research round. The user prompt renders that payload (plus two non-payload artifacts: diagnostics summary and file paths) as **seven blocks in narrative order** so the conductor reads them as a story: *what was tested → how it was configured → what resulted → what diagnostics showed → what got rejected first → what the proposer was thinking → where to dig deeper*.

#### 5.0.1 Round-id header

The very first line of the user prompt is `Research round: {round_number}` where `round_number` is a positive integer. The composite id format `"job-{job}-round-{round_number}"` is used only for internal DB queries (e.g. `list_round_attempts`); never rendered in the prompt.

#### 5.0.2 Block order (matches user-prompt render order)

| # | Block (spec section) | Header in prompt | Purpose |
|---|---|---|---|
| 1 | §5.1 WHAT WAS TESTED | `LAST RESEARCH ROUND — WHAT WAS TESTED:` | The identity + description of the thesis that ran — leads so the conductor immediately knows what it's looking at. |
| 2 | §5.2 CONFIG | `LAST RESEARCH ROUND — CONFIG (values used):` | The deltas the conductor proposed + the full resolved values the strategy actually ran with. |
| 3 | §5.3 RESULTS | `LAST RESEARCH ROUND — RESULTS:` | Decision, headline metrics, verdict, derived research feedback. |
| 4 | §5.4 DIAGNOSTICS | `LAST RESEARCH ROUND — DIAGNOSTICS (summary):` | Event counts + per-filter rejection breakdown — the mechanism-level "why" behind the results. |
| 5 | §5.5 REJECTED ATTEMPTS | `LAST RESEARCH ROUND — REJECTED ATTEMPTS (structured):` | Validator rejections that preceded the accepted thesis in the same round. |
| 6 | §5.6 PROPOSER REASONING | `LAST RESEARCH ROUND — PROPOSER REASONING:` | The 13 remaining metadata fields explaining why the proposer picked the thesis (predictions, evidence, alternatives, anti-whipsaw substrate). |
| 7 | §5.7 EVIDENCE FILES | `LAST RESEARCH ROUND — EVIDENCE FILES:` | Three file paths for deep forensic dives via the analyst tool. |

After these seven, the **family-history blocks** follow: §5.8 landscape, §5.9 dimension-pairs.

#### 5.0.3 Controller payload → block mapping

| # | Controller payload item | Rendered in |
|---|---|---|
| 1 | `research_round_id` | §5.0.1 header (round number only; composite id is DB-only) |
| 2 | backtested `thesis_id` | §5.1 WHAT WAS TESTED |
| 3 | backtested thesis description (`hypothesis` + `mechanism`) | §5.1 WHAT WAS TESTED |
| 4 | backtested thesis config | §5.2 CONFIG (both views: `config_changes` deltas + full resolved `runtime_config`) |
| 5 | backtested thesis metadata (other conductor-generated fields) | §5.1 takes 3 (`thesis_role`, `mechanism_dimension`, `theme_keywords`); §5.6 PROPOSER REASONING takes the remaining 13 |
| 6 | list of theses rejected by validator in that round + reasons | §5.5 REJECTED ATTEMPTS |

Plus two non-payload artifacts: diagnostics → §5.4; file paths → §5.7.

#### 5.0.4 Source dict — `last_research_round`

All seven last-research-round block builders read from a single dict, `last_research_round`, assembled by `_resolve_conductor_inputs` (the renamed `latest_outcome` per §14.4). Its keys:

| Key | Type | Source | Consumed by |
|---|---|---|---|
| `thesis_id` | string | `record.asi.thesis_id` (fallback: parent dir name of `record.config`) | §5.1 |
| `metric` | float \| null | `record.metric` (primary validation metric) | §5.3 |
| `decision` | string | `record.status` | §5.3 |
| `config_path` | string | `record.config` | §5.3 (informational, audit) |
| `resolution_context` | object | `resolve_research_resolution_context(family, runtime_config)` | §5.3 |
| `trade_count` | int | `trade_analysis.trade_count` | §5.3 |
| `profit_factor` | float | `trade_analysis.profit_factor` | §5.3 |
| `max_drawdown` | float | `trade_analysis.max_drawdown` | §5.3 |
| `pct_profitable_windows` | float | `trade_analysis.pct_profitable_windows` | §5.3 |
| `avg_sharpe_across_windows` | float | `trade_analysis.avg_sharpe_across_windows` | §5.3 |
| `verdict_status` | string | `trade_analysis.verdict.status` | §5.3 |
| `verdict_summary` | string | `trade_analysis.verdict.summary` | §5.3 |
| `research_feedback` | string | derived: `"Previous candidate was {verdict_status}: {verdict_summary}."` (with a special-case nudge if status is `invalid_noop_config`) | §5.3 |
| `runtime_config` | object | full resolved config dict | §5.2 |
| `diagnostics_summary` | object | `event_counts` + `rejection_breakdown` extracted from diagnostics JSON (fail-open) | §5.4 |
| `last_round_rejected_attempts` | list | output of `BacktestRunDB.list_round_attempts(research_round_id)` for the last completed round | §5.5 |
| `proposer_reasoning` | object | output of `latest_thesis_details(root, thesis_id, *, job_id)` — full `ResearchThesis` payload (~30 fields per `research_types.py:139–211`; see §5.0.6 for per-field surfacing decision) | §5.1 (5 fields: `hypothesis`, `mechanism`, `thesis_role`, `mechanism_dimension`, `theme_keywords`); §5.2 (`config_changes`); §5.6 (15 fields + 3 conditionally rendered for emergent dimension) |
| `trades_file`, `strategy_events_file`, `diagnostics_file` | strings (file paths) | resolved against `record.asi` + on-disk artifacts | §5.7 |

#### 5.0.5 Cold-start behavior

When no backtest has run yet for the current job (true cold start), `last_research_round` is empty `{}`. **All seven last-research-round blocks are omitted** — there is nothing to report yet. The user prompt instead carries:

```
Research round: 1

(No prior research round has completed yet for this job.
 No prior-round results, no rejected attempts, no proposer reasoning available.
 Proceed using family history (below) and your own research.)
```

The conductor then takes the no-trades branch (web research, source-code reading) and proposes the first thesis. Family-history blocks (§5.8–§5.9) still render — they're scoped to the family across all jobs, not just the current job.

#### 5.0.6 Master `ResearchThesis` field inventory

The conductor's `proposer_reasoning` payload (stored as `thesis_details_json` on `research_thesis_attempts`) contains **every field of the `ResearchThesis` schema** defined in `research_types.py:139–211`. That schema has ~30 fields. This inventory exists so a reader can audit, per field: (a) is it required by the validator, (b) is it useful for the next round's conductor, (c) which block surfaces it.

**Decision criteria:**

| Mark | Meaning |
|---|---|
| **Required** | Validator hard-rejects the thesis if this field is missing, empty, or malformed. Must be surfaced — the conductor will always have a value to read. |
| **Useful** | Validator does not require it, but reading it helps the conductor pick the next thesis (e.g. it carries forward-reasoning value, anti-whipsaw substrate, or rationale that informs the next proposal). Surface when populated; omit cleanly when absent. |
| **Drop** | Neither required nor useful for next-round reasoning (legacy compat, redundant with other blocks, stale by next round, or pure metadata). Not surfaced in the last-research-round portion of the prompt. |

**Full inventory:**

| # | Field | Type | Required | Useful | Surfaced in |
|---|---|---|---|---|---|
| 1 | `thesis_id` | str | ✅ (line 1584) | ✅ identity | §5.1 |
| 2 | `strategy_family` | str | ✅ (set by caller) | — known from job context, redundant in prompt | **Drop** |
| 3 | `hypothesis` | str | ✅ (line 1591) | ✅ thesis claim | §5.1 |
| 4 | `mechanism` | str | ✅ (line 1596) | ✅ causal story | §5.1 |
| 5 | `mechanism_dimension` | str | ✅ (line 1480) | ✅ landscape position | §5.1 |
| 6 | `dimension_novelty` | str | — | ✅ explains why this is structurally novel; useful for the next round to avoid claiming the same novelty for a near-duplicate | §5.6 (promoted from "Deliberately not added") |
| 7 | `causal_cluster` | str | ✅ conditional (line 1619) | ✅ cluster identity | §5.6 |
| 8 | `dominant_cluster_overlap` | Literal | — | redundant with `theme_keywords` (§5.1) + landscape (§5.8) | **Drop** |
| 9 | `underexplored_dimensions_considered` | list[str] | — | stale by next round — §5.8 landscape carries fresh data | **Drop** |
| 10 | `novel_connection` | str (≥ N chars validated) | ✅ length check (line 1634) | ✅ explains the new evidence connection; useful when next round wants to extend or contrast | §5.6 (promoted from "Deliberately not added") |
| 11 | `closest_prior_theses_considered` | list[str] | — | ✅ nearby priors the proposer compared against | §5.6 |
| 12 | `orthogonality_defense` | str | — | ✅ why this is mechanism-distinct, not adjacent | §5.6 |
| 13 | `evidence_strength` | Literal | — | ✅ self-graded confidence calibration | §5.6 |
| 14 | `thesis_role` | Literal | — | ✅ categorical role tag, shapes next-thesis kind | §5.1 |
| 15 | `falsification_or_alternative` | str | — | ✅ alternative explanation that would invalidate the thesis | §5.6 |
| 16 | `new_dimension_name` | str | ✅ conditional (only when `mechanism_dimension == "emergent"`) | ✅ conditional — only meaningful when emergent | §5.6 (conditionally rendered) |
| 17 | `why_existing_dimensions_do_not_fit` | str | ✅ conditional (emergent only) | ✅ conditional | §5.6 (conditionally rendered) |
| 18 | `mechanism_family_definition` | str | ✅ conditional (emergent only) | ✅ conditional | §5.6 (conditionally rendered) |
| 19 | `expected_reuse_across_future_theses` | str | — | speculative forward-looking text; low signal for the *next* round | **Drop** |
| 20 | `evidence` | list[str] | — | ✅ legacy evidence list (Spec B refactors to typed `evidence_citations` — until that lands, this is the populated field) | §5.6 |
| 21 | `base_contract_id` | str | — (legacy compat) | legacy field, must stay empty per schema doc | **Drop** |
| 22 | `base_config_path` | str | — (legacy compat) | legacy field, must stay empty per schema doc | **Drop** |
| 23 | `config_changes` | dict | ✅ (or `requires_code_change=True`, line 1510) | ✅ the proposer-specified values | §5.2 |
| 24 | `expected_effects` | list[ExpectedEffect] | ✅ non-empty (line 1533) | ✅ predictions to compare against §5.3 actuals | §5.6 |
| 25 | `disqualifiers` | list[Disqualifier] | ✅ non-empty (line 823, 1681) | ✅ stated falsification conditions | §5.6 |
| 26 | `required_diagnostics` | list[str] | — | values inconsistent today (Spec B refactors); low signal until then | **Drop** until Spec B |
| 27 | `required_diagnostic_specs` | list[DiagnosticRequirementSpec] | — | empty today (Spec B populates) | **Drop** until Spec B |
| 28 | `requires_code_change` | bool | ✅ pair-required with `requested_primitives` | ✅ engine-starvation rule input | §5.6 (paired as `engine_change_request`) |
| 29 | `requested_primitives` | list[str] | ✅ pair (when `requires_code_change=True`) | ✅ engine-starvation rule input | §5.6 (paired as `engine_change_request`) |
| 30 | `why_not_overfit` | str | — | ✅ overfit self-defense — helpful next round when reading anti-overfit reasoning | §5.6 |
| 31 | `theme_keywords` | list[str] | — (validated indirectly by cluster-fixation/whipsaw rules) | ✅ lever-theme tokens for landscape positioning | §5.1 |
| 32 | `prior_lever_outcomes` | list[PriorLeverOutcome] | ✅ structural (when non-empty, ids must match snapshot per §6.1) | ✅ anti-whipsaw substrate | §5.6 |
| 33 | `alternatives_considered` | list[Alternative] | ✅ ≥2 entries | ✅ pre-vetted next-candidate angles — **highest forward-reasoning value** | §5.6 |
| 34 | `evidence_citations` | list[EvidenceCitation] | ✅ ≥1 web_search + ≥1 analyst | ✅ typed evidence (replaces legacy `evidence` once Spec B lands) | §5.6 |
| 35 | `source_code_verification` | str (≥40 chars) | ✅ length check | ✅ tells next round where prior connected to code | §5.6 |

**Field count summary:**

| Bucket | Count |
|---|---|
| Required (validator hard-rejects without it, fully or conditionally) | 15 |
| Useful (surfaced because next-round reasoning benefits) | total surfaced = 22 |
| Drop (not surfaced — see rationale per row) | 8 |

**Sanity check against the schema:** the schema in `research_types.py:139–211` defines `ResearchThesis` with the fields listed above. If the schema changes (Spec B refactors `evidence_citations`, etc.), this table is the audit surface to re-evaluate — any new field defaults to **Drop** until explicitly promoted via this decision matrix.

---

### 5.1 `build_what_was_tested_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** Lead the prompt with what the previous round actually tested — the accepted thesis's identity, description, and landscape positioning — so the conductor grounds itself before reading metrics, diagnostics, or reasoning. This is the first answer to "what did we just do?"

**Round vs thesis — terminology note.** The **round** is the controller cycle and is identified by `research_round_id` (rendered in the §5.0.1 header, not here). The **thesis** is a *proposal within a round*. A round may produce multiple thesis attempts: zero or more are rejected by the validator (rendered in §5.5), and exactly one is accepted and backtested. `thesis_id` in this block identifies that single **accepted** thesis. The round's experiment is the backtest of that accepted thesis — round and experiment are 1:1, but round and thesis are 1:many.

**Fields rendered (6 total):**

| Field | Shape | Source | Why in this block |
|---|---|---|---|
| `thesis_id` | string | `last_research_round["thesis_id"]` | Identifier of the **accepted** thesis whose backtest produced this round's results. Pinning it first lets the conductor cross-reference any other block back to this thesis. (The round itself is identified by `research_round_id` in the §5.0.1 header — don't conflate the two.) |
| `hypothesis` | string (full, untruncated) | `last_research_round["proposer_reasoning"]["hypothesis"]` | The thesis's core claim. |
| `mechanism` | string (full, untruncated) | `last_research_round["proposer_reasoning"]["mechanism"]` | The causal story. |
| `thesis_role` | enum string (`extension` \| `replacement` \| `orthogonal`) | `proposer_reasoning["thesis_role"]` | Categorical role label — shapes what kind of next thesis fits. Belongs with the description, not buried in reasoning. |
| `mechanism_dimension` | enum string | `proposer_reasoning["mechanism_dimension"]` | Anchors the thesis on the landscape axis (§5.8). Without this in §5.1, the conductor has to scroll to §5.6 to know where the prior sits. |
| `theme_keywords` | list of strings | `proposer_reasoning["theme_keywords"]` | The lever-theme tokens. Read alongside `mechanism_dimension` so the conductor immediately sees the lever family. |

**Render shape:**

```
LAST RESEARCH ROUND — WHAT WAS TESTED:
  thesis_id:           ema_pullback_v3
  thesis_role:         extension
  mechanism_dimension: trend_filters
  theme_keywords:      [ema, trend, htf_gate]
  hypothesis:          Adding a 1-hour direction gate filters out counter-trend
                       5-min pullbacks that historically drove the strategy's
                       drawdown floor.
  mechanism:           HTF direction acts as a regime overlay — when the 1h
                       trend is up, only long 5-min pullbacks fire; when down,
                       only shorts. Reduces signal count by ~40% but the
                       surviving signals have better edge.
```

`hypothesis` and `mechanism` get hanging-indent wrapping at ~80 cols for readability (not truncation — they render in full).

**Cold-start behavior:** block omitted entirely if `last_research_round` is empty or `thesis_id` is missing.

**Edge cases:**

| Condition | Behavior |
|---|---|
| `proposer_reasoning` missing or `{}` | Render `thesis_id` only; other fields show `(unavailable)` placeholder. |
| `thesis_role` missing | Field omitted from the block (no empty line). |
| `mechanism_dimension` missing | Render `mechanism_dimension: unknown_dimension` (matches §5.8 landscape's missing-dim bucket). |
| `theme_keywords` empty list | Render `theme_keywords: []`. |

**Cost:** ~80–250 tokens depending on hypothesis/mechanism length.

### 5.2 `build_config_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** Tell the conductor what configuration the strategy ran with — both *what the proposer chose to set* (intent) and *the full state that actually ran* (state). Two flat lists, no diff arrows, no references to the baseline config. The conductor doesn't need to know the family's default values to read this block.

**Design note — why no diff arrows.** A previous design draft rendered changes as `key: baseline → new` (e.g. `min_stop_distance_pct: null → 0.0035`). That required the conductor to (a) know what "baseline" meant as a concept and (b) treat the rendered baseline values as ground truth — coupling the prompt to base-config knowledge the LLM doesn't otherwise need. The intent-vs-state framing here drops both requirements: each sub-block is just `key: value` lines the LLM can read directly. The "is this a proposer-chosen value or a default?" distinction is conveyed structurally by the two sub-block headers, not by inline annotation.

**Fields rendered:**

| Sub-block | Field | Shape | Source |
|---|---|---|---|
| Proposer-specified values | `config_changes` | dict (e.g. `{"min_stop_distance_pct": 0.0035, "gap_filter": true}`) | `last_research_round["proposer_reasoning"]["config_changes"]` |
| Resolved runtime_config | `runtime_config` | dict (~22 keys for EMA today) | `last_research_round["runtime_config"]` |

**Why both sub-blocks (CLAUDE.md rule B exception with explicit rationale):**
- **Proposer-specified values** alone — hides defaults like `ema_length=5` that the strategy depends on; the conductor would not know the full state that ran.
- **Resolved runtime_config** alone — visible values, but the conductor can't tell which keys were *intentionally set by the proposer* vs which were inherited defaults. The proposer's choices carry signal ("they decided this knob mattered"); inherited defaults don't.

Together: the proposer's intent + the strategy's full state, both as flat `key: value` lists, no diffing required.

**Render shape:**

```
LAST RESEARCH ROUND — CONFIG:

  Proposer-specified values (the keys the proposer chose to set this round):
    min_stop_distance_pct: 0.0035
    gap_filter:            true
    trail_after_r:         3.0

  Resolved runtime_config (full state the strategy ran with):
    family:                ema
    data_universe:         nasdaq8
    symbols:               null
    validation_start:      2020-01-01
    validation_end:        2023-12-31
    ema_length:            5
    timeframe_short:       5
    timeframe_long:        15
    rr_ratio:              3.0
    direction_bias:        short_only
    entry_cutoff_time:     10:00
    max_trades_per_day:    3
    min_stop_distance_pct: 0.0035
    max_stop_distance_pct: null
    gap_filter:            true
    gap_pct:               0.01
    gap_exclude:           false
    gap_exclude_pct:       0.005
    trail_after_r:         3.0
    max_hold_bars:         78
    use_range_shift:       false
    range_shift_lookback:  20
```

**Render rules:**
- Proposer-specified sub-block: one `key: value` line per entry in `config_changes`. Capped at `_proposer_specified_max_keys()` (default 30 — almost all theses set ≤5 keys; the cap protects against pathological proposals).
- Resolved sub-block: `key: value` lines, capped at `_last_run_config_max_keys()` (default 100). Overflow line: `"+{N} more: [k1, k2, ...]"`.
- **Values rendered in full** — no per-value truncation. Truncating values silently drops the very thing the conductor needs to reason about the next thesis.
- Values that appear in both sub-blocks are rendered identically (no formatting drift between the two views).

**Cold-start behavior:** entire block omitted (no last round, no config to show).

**Edge cases:**

| Condition | Behavior |
|---|---|
| `config_changes` missing or `{}` | Proposer-specified sub-block renders as `"Proposer-specified values: (none — proposer chose to test the baseline as-is)"`. Common for `thesis_role=cleanup_validation_follow_up` proposals. |
| `runtime_config` missing | Resolved sub-block omitted; proposer-specified sub-block still renders if available. |
| Both missing | Entire block omitted. |
| Key present in `config_changes` but missing from `runtime_config` | Render the key in proposer-specified sub-block with a trailing `(not in resolved config — possible compiler noop)` flag. Useful tell for compiler bugs. |

**Cost:** ~50–200 tokens (proposer-specified ~10–60, resolved ~30–180). Drops the ~20–40 tokens the old diff-arrow rendering carried.

### 5.3 `build_results_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** Tell the conductor what happened — the decision, headline metrics, verdict, and the derived research-feedback nudge — without the proposer-reasoning noise. This is the "outcome" view of the round, narrowed to result fields only (no identity, no config, no nested objects).

**Fields rendered:**

| Field | Type | Source | Role |
|---|---|---|---|
| `decision` | string | `last_research_round["decision"]` | Controller's verdict state (e.g. `kept`, `killed`, `invalid_noop_config`, `inferior_to_baseline`). |
| `metric` | float \| null | `last_research_round["metric"]` | The primary validation metric (e.g. profit factor for EMA). |
| `trade_count` | int | `last_research_round["trade_count"]` | Number of trades executed in validation. |
| `profit_factor` | float | `last_research_round["profit_factor"]` | Gross profit / gross loss. |
| `max_drawdown` | float | `last_research_round["max_drawdown"]` | Worst peak-to-trough equity drop. |
| `pct_profitable_windows` | float | `last_research_round["pct_profitable_windows"]` | Walk-forward stability indicator. |
| `avg_sharpe_across_windows` | float | `last_research_round["avg_sharpe_across_windows"]` | Walk-forward risk-adjusted return. |
| `verdict_status` | string | `last_research_round["verdict_status"]` | Analyst's structured verdict (e.g. `improvement`, `regression`, `flat`, `invalid_noop_config`). |
| `verdict_summary` | string | `last_research_round["verdict_summary"]` | One-sentence prose summary from the analyst. |
| `research_feedback` | string | derived | Sentence-form feedback the analyst surfaces to the conductor (e.g. `"Previous candidate was improvement: PF rose from 1.08 → 1.34."`). Special case: when `verdict_status == "invalid_noop_config"`, an additional nudge is appended directing the proposer to revise the threshold or abandon the mechanism. |
| `config_path` | string | `last_research_round["config_path"]` | Path to the resolved config file. Render as an audit footer line, not a primary field. |
| `resolution_context` | object | `last_research_round["resolution_context"]` | Family-resolution metadata (e.g. which base config variant was used). Render as audit footer. |

**Deliberately excluded from this block:** `thesis_id` (in §5.1), `hypothesis`/`mechanism` (in §5.1), `runtime_config` (in §5.2), `diagnostics_summary` (in §5.4), `last_round_rejected_attempts` (in §5.5), `proposer_reasoning` (in §5.6). This block is **results only**.

**Render shape:**

```
LAST RESEARCH ROUND — RESULTS:
  decision:                  killed
  metric:                    0.91
  verdict_status:            regression
  verdict_summary:           PF dropped from baseline 1.08 → 0.91; max drawdown
                             widened from 4.2% → 6.8%.
  research_feedback:         Previous candidate was regression: PF dropped from
                             baseline 1.08 → 0.91; max drawdown widened from
                             4.2% → 6.8%.

  Trade metrics:
    trade_count:              198
    profit_factor:            0.91
    max_drawdown:             6.8%
    pct_profitable_windows:   42%
    avg_sharpe_across_windows: 0.31

  Audit:
    config_path:               /…/job-12-round-5/resolved_config.yaml
    resolution_context:        {variant: ema_base, overrides: 3}
```

`verdict_summary` and `research_feedback` get hanging-indent wrap. Trade metrics get their own sub-block (visually separated). Audit fields go last under their own sub-header so the conductor doesn't conflate them with the actual results.

**Cold-start behavior:** block omitted entirely.

**Edge cases:**

| Condition | Behavior |
|---|---|
| `metric` is null and `verdict_status == "invalid_noop_config"` | Render `metric: null (invalid noop config — no backtest produced)`. |
| `trade_count == 0` | Render trade metrics sub-block with all fields, but prepend `"⚠ zero trades — strategy did not fire under this config"`. |
| `verdict_summary` missing | Field omitted; `research_feedback` falls back to `"Previous candidate was {verdict_status}."`. |
| `resolution_context` missing | Audit sub-block renders `config_path` only. |

**Cost:** ~120–280 tokens.

### 5.4 `build_diagnostics_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** Surface the two diagnostics subfields that explain *why* the results came out the way they did at the mechanism level — which filter killed how many signals.

**Source:** `last_research_round["diagnostics_summary"]`. `_resolve_conductor_inputs` reads the diagnostics file (try/except, fail-open) and extracts **only `event_counts` + `rejection_breakdown`** — the two subfields not already surfaced elsewhere in the prompt.

**Deliberately excluded** (duplication — already rendered in §5.3 RESULTS):
- `trade_analysis` — `trade_count`, `profit_factor`, `max_drawdown`, `pct_profitable_windows`, `avg_sharpe_across_windows` are already in §5.3.
- `verdict` — `verdict_status`, `verdict_summary`, and the derived `research_feedback` are already in §5.3.

Re-rendering them here would (a) waste ~150–350 tokens per round, (b) violate CLAUDE.md rule **B** (one home per concept), and (c) create a drift/debugging hazard when format or truncation diverges between the two render sites.

**Why these two:** `rejection_breakdown` is the only high-signal novel field — it names *which* filter killed *what fraction* of signals, the mechanism-level "why" the conductor needs to pick the next thesis. `event_counts` is the required denominator: a 1,200-signal rejection means very different things at 2,400 total events vs 1,210 total events.

**Render shape:**

```
LAST RESEARCH ROUND — DIAGNOSTICS (summary):
  event_counts:
    signals_generated:    2401
    signals_filtered:     2203
    trades_taken:          198
  rejection_breakdown:
    trend_filter_rejected:     1200
    vol_filter_rejected:        803
    cutoff_time_rejected:       180
    other:                       20
```

**Failure mode:** file unreadable → block omitted; `LATEST_DIAGNOSTICS_DEGRADED` logged. Round proceeds.

**Cost:** ~80–150 tokens. Removes ~1 analyst call per round on average.

**Note on `_experiment_compact_detail`:** that helper renders all four diagnostics subfields because it's used for arbitrary `get_past_thesis` lookups where the caller does *not* have `last_research_round` already populated. The pre-flight context is a different surface — `last_research_round` is always present — so the de-duplicated shape applies here, not in the MCP tool's response.

### 5.5 `build_rejected_attempts_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** Show the conductor every thesis draft that the validator rejected in the last round *before* the accepted thesis was reached. These are the proposals the conductor itself made in retry attempts inside the prior round — the most direct "don't do this again" signal.

**Scope clarification — what this block is and isn't:**
- **Is:** structured access to the **last completed round's** validator rejections — item 6 of the controller's between-round payload to the conductor (research_round_id, backtested thesis id/description/config/metadata, **list of theses rejected by validator in that round + reasons**).
- **Is not:** in-process rejections from the current round. Those flow validator → conductor *directly* inside a single conductor invocation (the retry loop inside `run_research_conductor`), never reach the controller, and never appear in the snapshot. By construction, anything about rejections in the user prompt is about a prior round.

**Source:** new `BacktestRunDB.list_round_attempts(research_round_id)` returning rejected attempts for the **last completed round** of the current job — the round the controller is now telling the conductor about. Attached as `last_research_round["last_round_rejected_attempts"]`.

**Positioning — this is a replacement, not an addition:** the existing `render_rejection_block` call at `research_conductor.py:194` is **deleted**. Same data (item 6), better shape (structured rows with consistent fields and a token cap, instead of a flat text blob). One home per concept (CLAUDE.md rule **B**).

**Render shape:**

```
LAST RESEARCH ROUND — REJECTED ATTEMPTS (structured):

  Attempt #1
    validator_status:           rejected
    validation_failure_reason:  structural_alternatives_considered_too_short
    mechanism_dimension:        trend_filters
    hypothesis:                 Tighten the entry filter to ADX > 30 to filter
                                noise during chop regimes.

  Attempt #2
    validator_status:           rejected
    validation_failure_reason:  thesis_quality_underexplored_misclassification
    mechanism_dimension:        trend_filters
    hypothesis:                 Add a 4-hour trend confirmation requirement to
                                the existing 1h gate.
```

Per attempt: `attempt_number`, `validator_status`, `validation_failure_reason`, `mechanism_dimension`, `hypothesis` (≤180 chars). Capped at `_max_round_rejected_attempts()` (default 5). Older rejections (rounds before last) accessible via existing `list_rejections` MCP tool.

**Cold-start behavior:** block omitted (no last round, no rejections).
**Block omission when zero rejections:** if the last round's accepted thesis was the proposer's first attempt (no retries), `last_round_rejected_attempts` is empty and the block is omitted entirely (no `"(none)"` placeholder).

**Cost:** ~50–300 tokens depending on rejection count. Net change vs today: roughly neutral — same data, just structured. The win is reasoning quality (the conductor can index/filter by `mechanism_dimension` and `validation_failure_reason`), not token count.

### 5.6 `build_proposer_reasoning_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** Show the conductor the prior proposer's reasoning surface — predictions, evidence, alternatives, anti-whipsaw substrate. This is the "why we chose this" view that complements §5.1 ("what we chose") and §5.3 ("what resulted"). **17 schema fields** live here, rendered as 15 entries (the last entry pairs `requires_code_change` + `requested_primitives` as one `engine_change_request`). Plus 3 more conditionally rendered when `mechanism_dimension == "emergent"`. §5.1 takes 5 (`hypothesis`, `mechanism`, `thesis_role`, `mechanism_dimension`, `theme_keywords`); §5.2 takes 1 (`config_changes`). See §5.0.6 for the master inventory of every `ResearchThesis` field and its surfacing decision.

**Source:** `last_research_round["proposer_reasoning"]` — output of `latest_thesis_details(root, thesis_id, *, job_id)`.

**Source contract:** `latest_thesis_details` returns `{}` for empty `thesis_id` or when no attempt records exist. When records exist, returns the most-recent attempt's metadata. `job_id` scoping is required to prevent surfacing unrelated priors when the same thesis_id appears across jobs.

**Fields rendered (17 schema fields, rendered as 15 entries — `engine_change_request` pairs 2 schema fields into 1 render entry).** Grouped into 7 named sub-blocks for readability.

**Predictions (2 fields).**

| Field | Shape | Role |
|---|---|---|
| `expected_effects` | list[ExpectedEffect] | Predicted directional impact per metric. Lets the conductor compare prediction vs §5.3 actuals. |
| `evidence_strength` | Literal (`direct`/`proxy`/`mixed`/`speculative`) | Self-graded confidence calibration. |

**Evidence & defense (5 fields).**

| Field | Shape | Role |
|---|---|---|
| `evidence` | list[str] | The proposer's evidence list (legacy shape — Spec B refactors to typed `evidence_citations`). |
| `evidence_citations` | list[EvidenceCitation] | Typed evidence — populated post-Spec-B. Surfaces alongside `evidence` until then. |
| `disqualifiers` | list[Disqualifier] | Stated falsification conditions ("this thesis is wrong if X"). |
| `falsification_or_alternative` | str | Alternative explanation that would invalidate the proposal. |
| `why_not_overfit` | str | Self-defense against the overfitting accusation. |

**Anti-whipsaw substrate (1 field).**

| Field | Shape | Role |
|---|---|---|
| `prior_lever_outcomes` | list[PriorLeverOutcome] (`{prior_thesis_id, lever, direction_then, outcome, why_retry ≥40 chars}`) | Direct anti-whipsaw substrate; the validator's §6.1 rule binds against the `prior_thesis_id` values cited here. |

**Considered but rejected (1 field).**

| Field | Shape | Role |
|---|---|---|
| `alternatives_considered` | list[Alternative] (`{mechanism, why_rejected ≥40 chars}`, ≥2 entries) | Pre-vetted "considered but rejected" angles. If the picked angle failed, these are the natural next candidates — **highest forward-reasoning value** of any reasoning field. |

**Mechanism novelty (2 fields — promoted from "Deliberately not added" per §5.0.6 audit).**

| Field | Shape | Role |
|---|---|---|
| `dimension_novelty` | str (≥30 chars) | Why the chosen `mechanism_dimension` is structurally novel relative to prior attempts (not a parameter variation). Useful for the next round to avoid claiming the same novelty for a near-duplicate. |
| `novel_connection` | str (≥ N chars, length-validated) | Why this proposal connects evidence in a materially new way. Useful for the next round when extending or contrasting. |

**Landscape + wiring + closest priors (4 fields).**

| Field | Shape | Role |
|---|---|---|
| `causal_cluster` | str | Human-readable causal family label complementing `theme_keywords` (which lives in §5.1). |
| `orthogonality_defense` | str | Why this proposal is mechanism-distinct from the nearest priors (not adjacent). |
| `closest_prior_theses_considered` | list[str] (thesis_ids) | Priors the proposer felt this proposal was nearest to. |
| `source_code_verification` | str (~100 chars, format `file:function — explanation`, ≥40 chars validated) | Tells next round where the prior connected to code. |

**Engine wiring (paired field — 2 schema fields rendered as one).**

| Rendered as | Source fields | Role |
|---|---|---|
| `engine_change_request` | `requires_code_change: bool` + `requested_primitives: list[str]` | Engine-starvation rule input; only meaningful as a pair (a `True` flag without primitives = malformed). |

**Conditionally rendered (emergent-dimension only):** if `mechanism_dimension == "emergent"`, three additional fields render under a sub-block "Emergent-dimension justification": `new_dimension_name`, `why_existing_dimensions_do_not_fit`, `mechanism_family_definition`. These are validator-required only when the dimension is emergent; for non-emergent theses they're omitted (and typically empty).

**Deliberately not surfaced** (per §5.0.6 audit — listed here for §5.6-scope completeness):
- `dominant_cluster_overlap` — §5.8 landscape block has the same info, fresher.
- `underexplored_dimensions_considered` — stale by next round; §5.8 landscape carries fresh data.
- `expected_reuse_across_future_theses` — speculative forward-looking text; low signal for the *next* round.
- `required_diagnostics` / `required_diagnostic_specs` — values inconsistent today; Spec B refactors.
- `base_contract_id` / `base_config_path` — legacy compat, must stay empty.
- `strategy_family` — known from job context.

**Render shape (sketch):**

```
LAST RESEARCH ROUND — PROPOSER REASONING:

  Predictions:
    expected_effects:           {profit_factor: +0.15, max_drawdown: -1.2%}
    evidence_strength:          moderate

  Evidence & defense:
    evidence:                   - 1h gate reduces 5-min chop in transcripts
                                - HTF regime alignment improved PF in T4
    disqualifiers:              - If 1h trend flips intra-day frequently…
    orthogonality_defense:      Distinct from trend_filter_v2 because…
    falsification_or_alternative: Could be that HTF gate just filters volume…
    why_not_overfit:            Tested across 8 symbols, not just AAPL.

  Anti-whipsaw substrate:
    prior_lever_outcomes:
      - prior_thesis_id: ema_trend_filter_v2
        lever:           trend_filter
        direction_then:  tighten
        outcome:         killed
        why_retry:       v2 was an entry filter; v3 is a regime gate — different semantic.

  Considered but rejected:
    alternatives_considered:
      - mechanism:    ADX>30 entry filter
        why_rejected: Too strict in low-vol regimes per fixture analysis.
      - mechanism:    Volume confirmation requirement
        why_rejected: Doesn't address the 5-min counter-trend chop directly.

  Mechanism novelty:
    dimension_novelty:          This is a regime-overlay gate, not a parameter
                                variation of the existing trend filter — entirely
                                different lever family.
    novel_connection:           Connects 5-min counter-trend chop evidence
                                (from prior diagnostics) with 1h regime overlay
                                via the htf_gate primitive.

  Landscape + wiring + closest priors:
    causal_cluster:             regime_overlay
    orthogonality_defense:      Distinct from trend_filter_v2 because v2 is a
                                signal-quality filter; this is a regime overlay.
    closest_prior_theses_considered: [ema_trend_filter_v2, ema_htf_gate]
    source_code_verification:   strategies/ema/signals.py:apply_htf_gate — gate
                                evaluated before stop_distance check.

  Engine wiring:
    engine_change_request:      {requires_code_change: false, requested_primitives: []}
```

Fields render in named sub-groups (Predictions / Evidence & defense / Anti-whipsaw / Considered but rejected / Mechanism novelty / Landscape + wiring + closest priors / Engine wiring) for readability. Empty / missing fields are omitted (no empty placeholders). Emergent-dimension fields render under an additional "Emergent-dimension justification" sub-block only when `mechanism_dimension == "emergent"`.

**Truncation budgets:** `source_code_verification` untruncated; `alternatives_considered` up to 4 entries with `why_rejected` ≤200 chars each; `prior_lever_outcomes` up to 4 entries; `dimension_novelty` and `novel_connection` rendered in full (each ≤500 chars by validator constraint); lists like `closest_prior_theses_considered` full.

**Cold-start behavior:** block omitted entirely.

**Cost:** ~250–650 tokens depending on richness (slightly higher than the pre-promotion 13-field design because `dimension_novelty` and `novel_connection` add ~50–100 tokens of typically-populated prose; offset by §5.1 still rendering only 6 fields).

### 5.7 `build_evidence_files_block(last_research_round) -> str` — `last_research_round_snapshot.py`

**Purpose.** At the end of the last-research-round portion, give the conductor (or the analyst tool it calls) three file paths it can read for deep forensic analysis. Positioned last because these are deep-dive references, used after the conductor has read the summarized blocks — not a starting point.

**Source:** `last_research_round["trades_file"]`, `["strategy_events_file"]`, `["diagnostics_file"]`.

**Fields rendered:**

| Path | What it contains |
|---|---|
| `trades_file` | per-trade CSV: entry/exit, P&L, MAE/MFE, holding bars |
| `strategy_events_file` | every setup considered, **accepted AND rejected**, with reason — used to understand *why* signals were filtered |
| `diagnostics_file` | the full diagnostics JSON (§5.4 extracts a 2-field summary; this file has everything) |

**Render shape:**

```
LAST RESEARCH ROUND — EVIDENCE FILES:
  Trades file for analysis: /…/job-12-round-5/trades.csv
  Strategy events file:     /…/job-12-round-5/strategy_events.jsonl
    (Contains EVERY setup the strategy considered — accepted AND rejected.
     Use this to understand WHY signals were filtered out.)
  Diagnostics file:         /…/job-12-round-5/diagnostics.json
    (Full diagnostics JSON — §5.4 already surfaces event_counts +
     rejection_breakdown inline.)

  Analyst capabilities:
    - default anchor: baseline artifacts for mechanism discovery when available
    - can fetch latest/current/best or specific round artifacts for comparison
```

**Why paths and not contents:** trades CSVs are routinely megabytes. The `analyze_trades` tool (and the analyst sub-agent behind it) reads these files on demand. The framework rule from §1's prompt-vs-tool decision is: read rate <30% OR size >1k tokens → tool. Trade files satisfy both.

**Cold-start behavior:** block omitted entirely (no last-round artifacts exist).

**Edge cases:**

| Condition | Behavior |
|---|---|
| `trades_file` missing but others present | Render the available paths; missing one omitted. |
| All three missing despite a completed round | Render `"LAST RESEARCH ROUND — EVIDENCE FILES:\n  (artifacts not found — possible disk cleanup or run interruption)"`. Conductor takes the no-trades branch for analyst calls. |

**Cost:** ~80–120 tokens.

### 5.8 `build_landscape_block(family) -> str` — `family_history_snapshot.py`

**Purpose.** Give the conductor a one-glance map of where the family's research effort has actually gone — which mechanism dimensions are saturated, which are active, which are untouched, and which adjacent pairs have never been combined. This lets the conductor pick a thesis from an underexplored region instead of re-mining a saturated one.

**Source contract (`BacktestRunDB.list_dimension_summary(family) -> list[DimensionRow]`):**

Reads every `research_thesis_attempts` row for the family across all jobs. Per attempt, the relevant fields are `mechanism_dimension` (string, from `thesis_details_json`), `validator_status` (string, e.g. `accepted`, `rejected_*`), and `runtime_status` (string, the post-backtest verdict mapped to `kept` or `killed`).

Returns one `DimensionRow` per distinct `mechanism_dimension` observed in the family, with `(dimension, total_attempts, kept, killed)` where:

- `total_attempts` = count of all accepted-by-validator attempts (rejections don't count toward landscape — they had no chance to be kept or killed).
- `kept` = count of those whose runtime verdict was `kept` / `accepted` / `promoted`.
- `killed` = count of those whose runtime verdict was `killed` / `rejected` / `inferior_to_baseline`.
- Attempts with `mechanism_dimension` missing or NULL are bucketed under the synthetic dimension name `unknown_dimension` (see §8).

**Classification rule** (applied at render time, not in SQL):

| Bucket | Condition |
|---|---|
| **saturated** | `total_attempts ≥ _landscape_saturated_at()` (default 8) |
| **active**    | `1 ≤ total_attempts < threshold` |
| **unexplored**| dimension name exists in the family's `MECHANISM_DIMENSIONS` enum but has no rows |

The `MECHANISM_DIMENSIONS` enum is the family's authoritative list of valid mechanism dimensions (one per family). The block renders every member: rows from the DB get bucketed into saturated/active, members with no rows get bucketed into unexplored. There are no other buckets — every enum member appears exactly once across the three.

**Adjacency-gaps algorithm:**

For each unordered pair of dimensions `(A, B)` from `MECHANISM_DIMENSIONS` where **both** dimensions have `total_attempts ≥ 3` (the activity threshold — pairs with sparse coverage on either side aren't meaningful gaps yet):

1. Collect the set of `theme_keywords` lists from accepted attempts where `mechanism_dimension == A`.
2. Collect the same from attempts where `mechanism_dimension == B`.
3. Count attempts (across the whole family, any dimension) whose `theme_keywords` contain at least one keyword from set A AND at least one from set B.
4. If that count is zero → the pair has never been combined; emit `"{A} × {B}"` in the "Adjacent pairs never combined" subsection.

Result is sorted alphabetically by the pair's first dimension name for deterministic rendering (so the same prompt is reproducible across runs given the same DB state).

**Render shape (canonical example, family=ema):**

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

**Edge cases:**

| Condition | Behavior |
|---|---|
| Family has zero attempts (true cold start) | Block renders header + single line `"No prior runs for family={family} yet."` All three subsections omitted. |
| Family has attempts but every dimension is in one bucket only | Empty subsections are omitted (no `"(none)"` placeholders). |
| `MECHANISM_DIMENSIONS` enum is empty for the family | "Unexplored dimensions" subsection omitted entirely. Adjacency subsection requires ≥2 enum members, so it's also omitted. |
| All adjacent pairs have ≥1 combined thesis | "Adjacent pairs never combined" subsection omitted. |
| `unknown_dimension` bucket has attempts | Rendered like any other dimension, but flagged with trailing `(missing dimension on prior)` so the conductor doesn't treat it as a real research area. |

**Cost:** ~80–250 tokens depending on family activity.

### 5.9 `build_dimension_pairs_block(family) -> str` — `family_history_snapshot.py`

**Purpose.** For each mechanism dimension that has produced both a KILLED and a KEPT thesis, render one concrete killed/kept pair. This gives the conductor next-round substrate: a concrete contrast showing what's been tried in this dimension, what failed, and what succeeded. Especially useful for deciding whether the dimension is worth re-mining (kept exists → there's a working angle) or pivoting away from (only killed → the dimension may be exhausted).

**Source contract (`BacktestRunDB.list_killed_kept_pairs(family) -> list[DimensionPair]`):**

Inputs: every accepted-by-validator attempt for the family across all jobs. Per attempt, the fields read are `thesis_id`, `job`, `round_number`, `mechanism_dimension`, `runtime_status`, `validation_metric` (the family's headline metric on the validation window, e.g. profit factor for EMA), `hypothesis` (≤120 chars surfaced), and `validator_status_reason` or `runtime_status_reason` (≤80 chars surfaced).

Returns a list of `DimensionPair` rows, one per dimension that satisfies the selection rules below, sorted by `total_attempts` descending.

**KILLED vs KEPT semantics:**

| Bucket | Includes runtime_status values |
|---|---|
| KEPT  | `kept`, `accepted`, `promoted`, `inferior_to_baseline_but_kept` (any verdict that survived) |
| KILLED| `killed`, `rejected`, `inferior_to_baseline`, `invalid_noop_config` |

If the runtime_status doesn't map to either bucket (e.g. `running`, `pending`, missing), the attempt is excluded from this block — only terminally-decided attempts qualify.

**Selection rule per dimension:**

A dimension is rendered iff it has **≥1 KEPT and ≥1 KILLED** attempt.

For each qualifying dimension, two attempts are picked:

1. **KILLED slot** → the **most-recent** killed attempt for that dimension (max `(job, round_number)` lexicographic). Most-recent because it best reflects the current code state — older killed attempts may have died for reasons that are no longer true.
2. **KEPT slot** → the kept attempt with the **largest validation-metric improvement vs the family baseline**, where `improvement = (attempt.validation_metric − baseline.validation_metric)` and `baseline.validation_metric` is the family's frozen baseline metric (the result of running the unmodified base config). If two kept attempts tie on improvement → tiebreaker is most-recent. If no baseline metric is recorded → fallback to **most-recent KEPT**.

**Cap and sort:**

Capped at `_pairs_block_max_dimensions()` (default 5). When more than 5 dimensions qualify, the top 5 by `total_attempts` (across both buckets) are rendered. Sort within the block is also by `total_attempts` descending, so the conductor sees the most-explored dimensions first.

**Render shape (canonical example):**

```markdown
## Killed/kept pairs by dimension (next-round substrate)

### Dimension: trend_filters
- KILLED: ema_trend_filter_v2 (job=12, round=5) — ADX>25 entry filter; failed chop_sensitivity
- KEPT:   ema_htf_gate (job=11, round=2) — 1h-direction gate; PF 1.08 → 1.34
```

Per pair, each line carries: `bucket`, `thesis_id`, `(job, round)`, dash, truncated `hypothesis` (≤120 chars), semicolon, truncated reason (≤80 chars) — for KEPT, the reason is "metric_before → metric_after" of the validation metric.

**Edge cases:**

| Condition | Behavior |
|---|---|
| No dimension has both KEPT and KILLED | Block renders header + `"No killed/kept pairs available yet for family={family}."` Useful tell that the family is too early in research to have substrate. |
| Family baseline metric missing | KEPT slot falls back to most-recent KEPT; KEPT-line reason renders as `"PF kept; baseline metric N/A"` so the conductor knows the improvement comparison was skipped. |
| Dimension has multiple killed entries with same `(job, round_number)` | Tiebreaker is `thesis_id` lexicographic — deterministic for reproducible prompts. |
| Dimension has only KEPT (no KILLED) | Excluded — selection rule requires both. Such dimensions show up in §5.8's landscape but not here. |
| `mechanism_dimension` missing on a prior | Excluded from pair selection (not bucketed under `unknown_dimension` here — that bucket is meaningful in landscape but useless as a "pair substrate"). |

**Cost:** ~150–400 tokens (≤5 dimensions × ~30–80 tokens per pair).

### 5.10 Tool-description reword in `research_prompts.py`

**Purpose.** With the snapshot pre-loading landscape, pairs, expanded previous_thesis, runtime_config, diagnostics, and last-round rejections, the conductor no longer needs to call `list_past_theses` / `list_experiment_results` to learn the baseline state of the family. The original tool descriptions told the agent "this tool gives you full thesis history" — which now mostly duplicates what's in the prompt and encourages wasted tool calls. The reword redirects these tools to their actual remaining purpose: **deep follow-up on something the snapshot didn't carry**.

**Scope.** Description text only. Tool signatures, MCP wiring, argument shapes, return shapes — all unchanged.

**Tool-list block — before (full block, system prompt):**

```
TOOLS
- analyze_trades(focus_question)         analyst — interpret evidence (REQUIRED >= 1 / round)
- web_search(query, context)             external mechanism evidence
- list_past_theses / get_past_thesis     full thesis history
- list_experiment_results / get_*        backtest outcomes
- save_finding / search_findings         persistent research notes across rounds
- list_rejections / get_rejection        prior validator rejections (this job)
- rejection_pattern_summary              grouped rejection counts (last 10 rounds)

Tool descriptions specify when to use each. Use them.
```

**Tool-list block — after:**

```
TOOLS
- analyze_trades(focus_question)         analyst — interpret evidence (REQUIRED >= 1 / round)
- web_search(query, context)             external mechanism evidence
- list_past_theses / get_past_thesis     Deep follow-up on a specific prior.
                                          Landscape + pairs + recent rejections
                                          are already pre-loaded in this round's
                                          user prompt. Use this tool only when
                                          you need a thesis NOT in those blocks
                                          or fuller detail than the summary.
- list_experiment_results / get_*        Same — for prior-round results (the prompt
                                          already carries LAST RESEARCH ROUND —
                                          RESULTS, CONFIG, and DIAGNOSTICS).
- save_finding / search_findings         persistent research notes across rounds
- list_rejections / get_rejection        Deep follow-up. LAST ROUND'S REJECTED
                                          ATTEMPTS is already in the prompt;
                                          this tool reaches rounds before that.
- rejection_pattern_summary              grouped rejection counts (last 10 rounds)

Tool descriptions specify when to use each. Use them.
```

**Diff summary** (for reviewer convenience):

| Line | Change |
|---|---|
| `list_past_theses / get_past_thesis` | Replaced one-line `"full thesis history"` with five-line block referencing pre-loaded snapshot. |
| `list_experiment_results / get_*` | Replaced `"backtest outcomes"` with two-line block referencing LAST RESEARCH ROUND — RESULTS/CONFIG/DIAGNOSTICS. |
| `list_rejections / get_rejection` | Replaced `"prior validator rejections (this job)"` with three-line block referencing the LAST RESEARCH ROUND — REJECTED ATTEMPTS block. |
| `rejection_pattern_summary` | **Unchanged** — operates on a 10-round rolling window, complementary to the snapshot's last-round-only scope. |
| All other tools | Unchanged. |

**Cost:** ~60 additional tokens in the system prompt (one-time, amortized across all rounds — the system prompt is cached per family).

## 6. Validator changes

The validator gets two new rules and preserves all existing rules. Both new rules bind against the snapshot's `thesis_ids` set — the union of `thesis_id` values that appeared in any rendered block in this round's user prompt. The set is constructed by `context_snapshot.build_snapshot(...)` (§4 architecture) which collects ids from each block builder's return value and unions them into the resulting `SnapshotResult.thesis_ids: set[str]`. The validator entry point accepts this set as an explicit parameter — `validate_thesis_dict(raw, *, prior_theses, snapshot_thesis_ids, tools_called)` — so the rule's "valid id" definition is bound to what the conductor actually saw, not to "anything in history."

### 6.1 `prior_lever_outcomes` content check (hard reject)

**Rule.** When the proposed thesis's `prior_lever_outcomes` list is non-empty, every `prior_thesis_id` cited in any of its entries must be present in `snapshot_thesis_ids`. The set contributors are: §5.3 last-round rejected attempts (each rejected attempt's `thesis_id`), §5.4 expanded previous_thesis (its own `thesis_id`), §5.6 dimension-pairs block (`thesis_id` of every KILLED and KEPT slot rendered), and the §5.0.2 `LAST RESEARCH ROUND — RESULTS` block's `thesis_id` field. The §5.5 landscape block contributes no ids (it renders only aggregate counts). When Spec C lands, the semantic top-K block will also contribute, and the rule's contract continues to hold without change.

**Why bind to the snapshot, not to "all history":** the rule's purpose is to prevent the conductor from citing prior_thesis_ids it never actually saw — pure hallucination, training-memory recall, or leakage from another job's context. Binding to `prior_theses` (the full DB load) would let those slip through because they happen to exist in history.

| Attribute | Value |
|---|---|
| Severity | hard reject (Stage 1) |
| Rejection code | `structural_prior_lever_outcomes_unknown_id` |
| Evidence payload | `{"unknown_ids": [list of cited ids not in the set], "valid_ids_sample": [first 20 ids from snapshot_thesis_ids, sorted lexicographically], "snapshot_thesis_ids_count": int}` |
| Remediation message | `"Cite only prior_thesis_id values that appear in this round's snapshot (landscape, dimension-pairs, previous_thesis, last-round rejections, or LAST RESEARCH ROUND — RESULTS). Cited unknown ids: [unknown_ids]. Drop them, or replace with one of the valid ids."` |

**Example failing thesis fragment:**

```json
{
  "thesis_id": "ema_trend_filter_v3",
  "prior_lever_outcomes": [
    {
      "prior_thesis_id": "ema_invented_id_not_in_snapshot",
      "lever": "trend_filter",
      "direction_then": "tighten",
      "outcome": "killed",
      "why_retry": "..."
    }
  ]
}
```
→ rejected with `structural_prior_lever_outcomes_unknown_id`.

**Pass condition:** every cited `prior_thesis_id` is in `snapshot_thesis_ids`. Empty `prior_lever_outcomes` trivially passes (the rule guards a non-empty list).

### 6.2 `underexplored_dimensions_considered` misclassification (soft warn)

**Rule.** When `last_research_round["family_landscape"]` (the structured form of §5.8's data, attached by `_resolve_conductor_inputs`) is available, emit a `BehaviorSignal` with severity `warn` if the proposed thesis's `mechanism_dimension` has **strictly more** prior attempts than **every** dimension listed in `underexplored_dimensions_considered`. In other words: the agent is claiming "these other dimensions are underexplored compared to mine" while the snapshot shows the opposite.

**Why soft, not hard:** legitimate cases exist — e.g. the chosen dimension has many attempts but only one recent KEPT (so it's worth more), or the agent has a strong directional thesis in a sat-dim that genuinely supersedes underexplored alternatives. A hard reject would over-fire. A `warn` surfaces in reflexion so the agent self-corrects on subsequent rounds without aborting the current one.

| Attribute | Value |
|---|---|
| Severity | `warn` (BehaviorSignal — surfaces in reflexion, does not block validation) |
| Behavior code | `thesis_quality_underexplored_misclassification` |
| Evidence payload | `{"chosen_dimension": str, "chosen_dimension_attempts": int, "claimed_underexplored": [{"dimension": str, "attempts": int}, ...], "rationale": "chosen has strictly more attempts than every claimed-underexplored alternative"}` |
| Surfacing | written to the reflexion trace, visible to the agent in subsequent rounds via standard BehaviorSignal channel |

**Pass condition:** at least one dimension in `underexplored_dimensions_considered` has `total_attempts ≥ chosen_dimension.total_attempts`. Empty `underexplored_dimensions_considered` trivially passes (nothing to misclassify).

### 6.3 Rules explicitly preserved

All existing validator rules continue to apply. Spec A adds no other modifications. Quick reference for what's preserved:

| Rule | What it checks |
|---|---|
| `_validate_process` (process gate) | Required tools were called this round (e.g. `web_search`, `list_experiment_results`). Hard reject on missing required tool. Rejection code: `process_required_tools_not_called`. |
| `_check_thesis_id_not_repeated` | The proposed `thesis_id` is not identical to any prior thesis_id in the same job. Prevents trivial duplicates. |
| theme-overlap (cluster-fixation) | At most 3 of the last 7 accepted theses may share `theme_keywords`. Prevents the agent from circling one mechanism cluster. |
| direction-whipsaw | If a prior thesis tested a lever in one direction (e.g. `tighten`) and the new thesis flips to the other direction (e.g. `loosen`) on the same theme, the new thesis must cite the prior in `prior_lever_outcomes` with `direction_then`, `outcome`, and `why_retry`. Hard reject otherwise. (This rule + §6.1 are the reason `prior_lever_outcomes` needs to be well-formed.) |
| `causal_cluster` required field | Non-empty string required. Hard reject if missing. |
| `dimension_novelty` ≥ 30 chars | Free-text justification for why the chosen `mechanism_dimension` is novel relative to prior attempts. Hard reject if shorter. |
| L6/L7 tool-order gates | Certain tool calls must precede certain others within the round (e.g. `analyze_trades` before `web_search` in some configurations). Hard reject on violation. |

Spec A does **not** modify any of these. Spec B/C/D may revisit; out of scope here.

## 7. Configuration

Lazy accessor functions (CLAUDE.md env-var hygiene rule).

| Function | Env var | Default | Purpose |
|---|---|---|---|
| `_landscape_saturated_at()` | `AUTORESEARCH_LANDSCAPE_SATURATED_AT` | `8` | Threshold for "saturated" dimension |
| `_pairs_block_max_dimensions()` | `AUTORESEARCH_PAIRS_BLOCK_MAX_DIMENSIONS` | `5` | Cap on pairs rendered |
| `_last_run_config_max_keys()` | `AUTORESEARCH_LAST_RUN_CONFIG_MAX_KEYS` | `100` | Max keys rendered in §5.2 resolved-runtime_config sub-block |
| `_proposer_specified_max_keys()` | `AUTORESEARCH_PROPOSER_SPECIFIED_MAX_KEYS` | `30` | Max keys rendered in §5.2 proposer-specified sub-block |
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

- `BacktestRunDB.list_dimension_summary`: hand-computed expectation matches against a fixture DB (§5.8 source contract).
- `BacktestRunDB.list_killed_kept_pairs`: most-recent-killed + best-improvement-kept selection verified per dimension (§5.9 source contract).
- `BacktestRunDB.list_round_attempts`: returns rejected attempts only; orders by `attempt_number` (§5.5 source contract).
- `latest_thesis_details` expansion: returns all surfaced fields from the §5.0.6 inventory when populated (5 in §5.1, 1 in §5.2 as `config_changes`, 15 in §5.6, plus the 3 emergent-conditional fields when `mechanism_dimension == "emergent"`); omits cleanly when missing.
- `build_what_was_tested_block` (§5.1): all 6 fields render; missing `thesis_role` omits the line; `mechanism_dimension` missing renders as `unknown_dimension`; cold-start (`thesis_id` absent) omits the entire block.
- `build_config_block` (§5.2): proposer-specified sub-block renders `(none — proposer chose to test the baseline as-is)` when empty; resolved sub-block key cap honored; **values rendered in full** (test asserts a long value passes through untruncated); key in proposer-specified but missing from resolved is flagged with `(not in resolved config — possible compiler noop)`; **no diff arrows** in either sub-block (test asserts `" → "` does not appear in the rendered block).
- `build_results_block` (§5.3): renders only result fields (no `thesis_id`, no `hypothesis`, no `runtime_config`); `metric=null` + `verdict_status="invalid_noop_config"` renders the noop label; `trade_count==0` prepends the warn line.
- `build_diagnostics_block` (§5.4): missing file → block omitted; degraded log emitted; renders only `event_counts` + `rejection_breakdown` (no `trade_analysis` / `verdict` duplication).
- `build_rejected_attempts_block` (§5.5): empty list → block omitted entirely; rejection-count cap honored; structured fields all present per attempt.
- `build_proposer_reasoning_block` (§5.6): renders the 15 fields per §5.6 (none of the 5 promoted to §5.1, no `config_changes`); empty fields omitted (no placeholders); truncation budgets honored; the two newly-promoted fields (`dimension_novelty`, `novel_connection`) appear in their "Mechanism novelty" sub-block.
- `build_evidence_files_block` (§5.7): all three paths render with their captions; missing paths gracefully omitted; all-missing case renders the cleanup-warning placeholder.
- `build_landscape_block` (§5.8): saturated/active/unexplored classification correct; adjacency detection via `theme_keywords` intersection correct.
- `build_dimension_pairs_block` (§5.9): cap honored; sorting by total attempts descending; KEPT/KILLED bucket mapping correct against `runtime_status` values.
- Each lazy accessor reads env at call time, not at import.

### 9.2 Integration

- End-to-end conductor round with a populated EMA fixture DB → user prompt contains all nine block types in the §5.0.2 order: WHAT WAS TESTED, CONFIG, RESULTS, DIAGNOSTICS, REJECTED ATTEMPTS, PROPOSER REASONING, EVIDENCE FILES, LANDSCAPE, DIMENSION PAIRS. Assertions check content (`assert "ema_pullback_v3" in user_prompt`), not just non-null.
- Render-order assertion: regex confirms blocks appear in the spec's documented order (`WHAT WAS TESTED` index < `CONFIG` index < `RESULTS` index < … < `EVIDENCE FILES` index < `Mechanism landscape` index < `Killed/kept pairs` index).
- Validator §6.1: thesis with `prior_lever_outcomes[].prior_thesis_id="ghost_id_not_in_snapshot"` → hard reject.
- Validator §6.2: chosen dimension has more attempts than every "underexplored" alternative → warn signal (not reject).
- Tool-description reword (§5.10): `_build_conductor_system_prompt` output contains the new wording and mentions "pre-loaded".
- Cold start (new family, empty DB): round runs cleanly; §5.1–§5.7 all omitted with the single cold-start placeholder from §5.0.5; §5.8–§5.9 render with the no-prior-runs placeholders.

### 9.3 Rerun & state-transition

- Two rounds in sequence: second round sees the first round's accepted thesis in the §5.1 WHAT WAS TESTED + §5.6 PROPOSER REASONING blocks, and its rejection siblings (if any) in §5.5.
- Manual deletion of a row from `*_backtest_runs.db` → blocks recompute correctly (no caching bug).

## 10. Migration plan

One PR, in order:

1. `BacktestRunDB.list_dimension_summary` + `list_killed_kept_pairs` + `list_round_attempts` helpers with unit tests.
2. `research_memory.latest_thesis_details` expansion with unit tests.
3. `last_research_round_snapshot.py` (7 builders for §5.1–§5.7), `family_history_snapshot.py` (2 builders for §5.8–§5.9), and `context_snapshot.py` orchestrator (`build_snapshot → SnapshotResult`) — new modules with unit tests.
4. `autoresearch_research.py` `_resolve_conductor_inputs` enrichments (attach the new fields to `latest_outcome`).
5. `research_conductor.py` user-prompt augmentation: render the seven last-research-round blocks (§5.1–§5.7) followed by family-history blocks (§5.8–§5.9) before `escalation_directive`, **and delete the existing `render_rejection_block` call at line 194** — §5.5's new structured `LAST RESEARCH ROUND — REJECTED ATTEMPTS` block replaces it (same data, better shape).
6. `research_prompts.py` tool-description reword + unit test.
7. `thesis_validator.py` §6.1 + §6.2.
8. End-to-end integration test against a real fixture DB; commit per CLAUDE.md verification rules.

No staged rollout flag.

## 11. Telemetry contract (drives Spec C decision)

Spec A ships without semantic retrieval. To know whether Spec C is justified, we measure:

1. **Per-round repeat-rate:** % of new thesis drafts where the `hypothesis + mechanism` text has high lexical overlap (Jaccard ≥ 0.6 on token sets, or shared 5-gram count ≥ 5) with any prior thesis in the same family. Logged as `THESIS_REPEAT_LEXICAL_HIT`. Computed by a post-thesis lexical comparator that lives in its **own module**: `thesis_similarity.py`, function `lexical_overlap_hit(new_text: str, prior_texts: list[str]) -> bool`.

   **Placement rationale:** this is a similarity check, not a prompt-rendering helper (so not `context_snapshot.py`) and not a validation rule (so not `thesis_validator.py` — adding non-blocking telemetry analytics there pollutes the validator's concern). Its own module keeps "thesis similarity" as one concept with one home (CLAUDE.md rule **B**).

   **Spec C reuse:** Spec C's semantic dedup is the same shape of question with a different distance function. It will add a sibling `semantic_overlap_hit(...)` (cosine on embeddings) in the same `thesis_similarity.py` module. Landing the lexical comparator in its own module today avoids a forced extraction PR when Spec C arrives.
2. **Cross-dimension proposal rate:** % of new theses whose `mechanism_dimension` differs from `latest_outcome.mechanism_dimension`. Logged as `THESIS_CROSS_DIMENSION`. Direct measurement of whether the agent breaks out of just-failed direction.
3. **Validator-rejection rate from `prior_lever_outcomes` content check (§6.1):** % of rounds where the agent cited a `prior_thesis_id` that didn't exist in the snapshot. Logged via the existing rejection-code path.

**Decision rule for Spec C:**
- If `THESIS_REPEAT_LEXICAL_HIT` rate < 10% AND `THESIS_CROSS_DIMENSION` rate ≥ 50% over a rolling 30-round window → Spec C is **not justified**; the deterministic snapshot is sufficient.
- If `THESIS_REPEAT_LEXICAL_HIT` rate ≥ 10% OR `THESIS_CROSS_DIMENSION` rate < 50% → Spec C is **justified**; ship semantic retrieval + dedup.

These telemetry counters are added in this spec, written via `trace_sdk`, audited via existing `scripts/token_audit.py`-style tools.

## 12. Success criteria

**Prompt-shape criteria (all six payload items from §5.0 must be rendered):**

- The prompt for a populated `ema_backtest_runs.db` round contains, in order:
  1. `Research round: {N}` header line (§5.0.1) — payload item 1.
  2. `LAST RESEARCH ROUND — WHAT WAS TESTED` block (§5.1) with 6 fields — payload items 2 (thesis id), 3 (description via `hypothesis` + `mechanism`), and 3 of item 5 (`thesis_role`, `mechanism_dimension`, `theme_keywords`).
  3. `LAST RESEARCH ROUND — CONFIG (values used)` block (§5.2) with two sub-views — payload item 4 (deltas + full resolved values).
  4. `LAST RESEARCH ROUND — RESULTS` block (§5.3) with decision, headline metrics, verdict, derived research_feedback, plus audit footer.
  5. `LAST RESEARCH ROUND — DIAGNOSTICS (summary)` block with `event_counts` + `rejection_breakdown` (§5.4) when the diagnostics file is readable.
  6. `LAST RESEARCH ROUND — REJECTED ATTEMPTS (structured)` block (§5.5) when the last round had ≥1 rejection — payload item 6.
  7. `LAST RESEARCH ROUND — PROPOSER REASONING` block (§5.6) with the 13 remaining metadata fields — the rest of payload item 5.
  8. `LAST RESEARCH ROUND — EVIDENCE FILES` block (§5.7) with three file paths and the analyst-capabilities footer.
  9. `Mechanism landscape (family={family})` block (§5.8) with saturated/active/unexplored sections and adjacency gaps.
  10. `Killed/kept pairs by dimension (next-round substrate)` block (§5.9) when at least one dimension qualifies.

**Content-grounded criteria (asserts on real values, not structural presence):**

- A specific `runtime_config` value (e.g. `"ema_length": 5`) is present in the rendered §5.2 block for a fixture run that used it (no truncation, full value).
- A `why_rejected` substring from a fixture prior's `alternatives_considered` is present in the rendered §5.6 block.
- A `rejection_breakdown` key (e.g. `"trend_filter_rejected": 1200`) from the fixture diagnostics file is present in the rendered §5.4 block.
- A `mechanism_dimension` from a fixture KILLED/KEPT pair is present in §5.9's dimension header.
- A fixture thesis's `hypothesis` (full text) is present in the rendered §5.1 block — verifying the block leads with the description.
- A fixture thesis's `config_changes` delta is present in the rendered §5.2 deltas sub-block (e.g. `"min_stop_distance_pct: null → 0.0035"`).

**Deletion criterion (verifies the §5.5 replacement landed):**

- Old `rejection_artifact.render_rejection_block` call at `research_conductor.py:194` is removed; no flat-text rejection block appears in the user prompt for any fixture round.

**Validator criteria (§6.1 / §6.2):**

- §6.1 hard reject: a thesis with `prior_lever_outcomes[0].prior_thesis_id = "ghost_id_not_in_snapshot"` raises `ThesisValidationError` with rejection code `structural_prior_lever_outcomes_unknown_id` and evidence payload containing `unknown_ids: ["ghost_id_not_in_snapshot"]`.
- §6.2 soft warn: a thesis with `mechanism_dimension` having more attempts than every dimension in `underexplored_dimensions_considered` does NOT raise — instead, a `BehaviorSignal` with code `thesis_quality_underexplored_misclassification` is emitted.
- Preserved rules (§6.3): a control thesis that previously passed validation still passes (no false positives from new rules).

**System-prompt criterion (§5.10):**

- The output of `_build_conductor_system_prompt(strategy_desc)` contains the string `"pre-loaded"` (verifying the tool-description reword landed) and still contains all five preserved tool entries (`analyze_trades`, `web_search`, `save_finding`, `rejection_pattern_summary`, plus the rewritten three).

**Cold-start criterion:**

- A round against an empty `ema_backtest_runs.db`:
  - Renders the `Research round: 1` header.
  - Renders the cold-start placeholder block per §5.0.5 ("No prior research round has completed yet for this job..."), and **omits all of §5.1–§5.7** (no last research round to render).
  - Renders §5.8 landscape with `"No prior runs for family=ema yet."` + the unexplored-dimensions list.
  - Renders §5.9 with `"No killed/kept pairs available yet for family=ema."`.
  - Round completes without exception.

**Telemetry criteria (§11):**

- `THESIS_REPEAT_LEXICAL_HIT` counter is emitted for every accepted thesis (boolean: did the new draft lexically overlap any prior?).
- `THESIS_CROSS_DIMENSION` counter is emitted for every accepted thesis (boolean: does the new `mechanism_dimension` differ from `last_research_round["mechanism_dimension"]`?).
- Both counters travel through `trace_sdk` and are queryable via the existing token-audit tooling.

**Architectural criterion:**

- `context_snapshot.build_snapshot(last_research_round, family) → SnapshotResult` returns an object with both `rendered_blocks: dict[str, str]` (keyed by block name from §5.1–§5.9) and `thesis_ids: set[str]` (the union from every contributing block — verified by asserting the set contains all rendered thesis_ids for a fixture round).
- The validator entry point signature is `validate_thesis_dict(raw, *, prior_theses=None, snapshot_thesis_ids=None, tools_called=None)`. When `snapshot_thesis_ids` is None the §6.1 rule no-ops (backward compatibility for callers not yet plumbed through).

## 13. Out of scope (for clarity)

- Any embedding computation, vector store query, MMR, cosine similarity, dedup gate, override field. → Spec C.
- Any second LLM turn / synthesis turn. → Spec D.
- Any change to the OUTPUT schema (`required_diagnostic_specs`, `evidence_citations`). → Spec B.
- Any change to validator rules beyond §6.1 and §6.2. → Spec B handles diagnostics + evidence; Spec C handles dedup override well-formedness.

## 14. Terminology unification

> **Companion specs:** the code-level cleanup of `thesis_id`-as-experiment-key misuse and round-vs-experiment rename in non-prompt code lives in **Spec A1** (`2026-05-28-spec-a1-experiment-id-cleanup-design.md`). Spec A handles spec-level + prompt-string terminology (Tier 1/2/3 here); Spec A1 handles MCP tool surface, filesystem paths, and code identifiers. The architectural fix to move `thesis_id` assignment from the LLM to the system lives in **Spec A2** (`2026-05-28-spec-a2-thesis-id-provenance-design.md`). The `ResearchThesis` schema cleanup (dropping weak / redundant fields based on the three-goal audit) lives in **Spec A3** (`2026-05-28-spec-a3-thesis-schema-cleanup-design.md`). The conductor OUTPUT-section overhaul (per-field typed contracts, worked example, drift detection — addresses the root-cause class for production rejections) lives in **Spec A4** (`2026-05-28-spec-a4-conductor-output-schema-overhaul-design.md`). The duplicate-field consolidation analysis that drives A3's drop list and A4's surviving 23-field set lives in **Spec A4a** (`2026-05-28-spec-a4a-field-consolidation-analysis.md`). All six are read together; ordering is A → A4a (analysis input) → A1 → A2 → A3 → A4. A4a is referenced by A3 (its drop list) and A4 (its surviving field set), so reading A4a first frames the field-count decisions in both.

### 14.1 Motivation

The codebase today uses three terms for the same operational unit:

- `research_round` — the round number / cycle identifier (the dominant term, used across 43 files including the DB schema, traces, controller state, runner scripts).
- `experiment` — used in prompt block headers (`LATEST EXPERIMENT *`) and a few parameter names (`experiment_results`).
- `previous_thesis` — used as a `latest_outcome` dict key and in helper names (`latest_thesis_details`).

These terms refer to overlapping but **not identical** concepts, and the mixed usage in code blurs the hierarchy:

- **Research round** — the controller cycle (proposer → validator-gate-loop → backtest of the accepted thesis). The "experiment" lives here: one round = one backtest = one experiment. Identifier: `research_round_id`.
- **Thesis** — a *proposal within a round*. A round may produce many thesis attempts (rejected drafts + one accepted). Identifier: `thesis_id`. **`thesis_id` is not an experiment identifier** — multiple `thesis_id`s can be associated with a single round/experiment via the rejected-attempts list.
- **Experiment** — the backtest of the round's accepted thesis. There's no first-class `experiment_id` in code today; the round id serves as the experiment identifier by 1:1 mapping.

Mixed terminology hurts in three ways: (a) the conductor sees inconsistent labels in its own prompt and the system prompt, (b) reviewers reading code can't tell at a glance whether `latest_outcome["previous_thesis"]` and `_latest_experiment_for_job(...)` describe the same data, (c) new contributors must learn three terms whose relationships aren't documented.

**Canonical term going forward: `last_research_round`** (and its variants `research_round`, `prior_research_rounds`, etc.). Reasons: (1) `research_round` is already dominant in code (43 vs 6 file counts), (2) it matches the controller's between-round payload framing, (3) "round" is the operational unit the conductor reasons in, not "experiment" or "thesis."

### 14.2 Tier 1 — concept already canonical (no change needed)

`research_round` as the integer round number, `research_round_id` as the composite `"job-{job}-round-{N}"` identifier, `log_research_round(...)` writer, DB schema columns. All untouched.

### 14.3 Tier 2 — user-facing strings (shipped with this spec)

Block header renames in the user prompt and in this spec's §5.1–§5.9 already use the unified term. Implementation work:

| Location | Old string | New string | Touch count |
|---|---|---|---|
| `research_conductor.py:134` (user prompt f-string) | `"LATEST EXPERIMENT OUTCOME:\n..."` | `"LAST RESEARCH ROUND — RESULTS:\n..."` | 1 |
| `research_conductor.py:135` (user prompt f-string) | `"EXPERIMENT RESULTS SUMMARY:\n..."` | `"PRIOR ROUNDS — RESULTS SUMMARY:\n..."` | 1 |
| Block headers added by this spec (§5.1–§5.7 builders) | `LATEST EXPERIMENT *` / `PREVIOUS THESIS` / `LAST ROUND'S *` | `LAST RESEARCH ROUND — *` | new code; ships with §5 builders |
| Tests asserting on prompt strings (`tests/test_research_conductor_characterization.py` and a small set of siblings) | as above | as above | ~6 assertion updates |

**Verification:** `grep -r "LATEST EXPERIMENT" .` and `grep -r "PREVIOUS THESIS" .` (excluding migration notes in this spec) must return zero hits after the change.

### 14.4 Tier 3 — Python internal data names (shipped with this spec)

Rename three internal identifiers so the data-plumbing names match the canonical concept. This is pure cosmetic refactoring — no behavior change, no schema change, no DB migration.

#### Rename map

| Old name | New name | Where defined | Touch count |
|---|---|---|---|
| `latest_outcome` (dict variable, parameter, key) | `last_research_round` | `_resolve_conductor_inputs` (autoresearch_research.py:435), `run_research_conductor` parameter (research_conductor.py:113), test assertions (~3 test files) | ~15 references across 3 files |
| `latest_outcome["previous_thesis"]` (nested key) | `last_research_round["proposer_reasoning"]` | written at autoresearch_research.py:520; consumed in research_conductor.py and §5.1 / §5.2 / §5.6 block builders | ~6 references |
| `experiment_results` (parameter name) | `prior_rounds_summary` | `run_research_conductor` signature + analyst call sites | ~5 references |
| `_latest_thesis_for_job`, `_latest_experiment_*` helpers (if any in private scope) | `_last_round_thesis_for_job`, `_last_round_*` | research_memory.py + adjacent helpers | grep-driven |

**Renames intentionally NOT in scope (would expand blast radius without semantic gain):**

| Identifier | Why kept |
|---|---|
| `latest_thesis_details(root, thesis_id, *, job_id)` | Public-ish helper; renaming would ripple through `research_memory.py`'s callers and the Spec B/C/D specs that already reference it. Adds churn without changing meaning. |
| `BacktestResultRecord`, `record.asi`, `record.metric` | DB-shape names, not narrative names. Renaming the data class would touch the migration layer. |
| `trade_analysis`, `verdict`, `diagnostics_summary` | Sub-field keys inside the payload — these describe the *kind* of data, not the round. No conflict. |
| `research_round` itself | Already canonical (Tier 1). |

#### Affected files (Tier 3)

**Producer + consumer (must change together):**
- `autoresearch_research.py` — `_resolve_conductor_inputs` and its return tuple element name; ~12 references.
- `research_conductor.py` — `run_research_conductor` parameter rename + every internal use; ~8 references.

**Tests asserting on the renamed keys:**
- `tests/test_autoresearch_research.py` — `assert latest_outcome["thesis_id"] == ...` style assertions; ~10 lines.
- `tests/test_research_conductor_characterization.py` — prompt fixture assertions; ~3 lines.
- `tests/test_research_conductor_paths.py` — parameter passing in test setup; ~2 lines.

**Internal callers worth re-checking** (no rename, just verify they don't import the old name):
- `agent_runners.py`, `agent_orchestrator.py`, `research_subagents.py` — grep for `latest_outcome` after the rename; should return zero hits outside the renamed call sites.

**No-touch zones (verified by grep before/after):**
- Trace adapters (`trace_adapters/*.py`) — read by tag name, not by attribute name.
- VPS runner, deploy scripts — don't reach into `latest_outcome`.
- Spec docs B/C/D — currently reference `latest_outcome` in design narrative; will be updated when those specs ship. For now, Spec A's §14.5 lists the global rename so siblings can adopt incrementally.

### 14.5 Migration ordering

Tier 3 rename is added to §10's migration plan as a new step inserted after step 5 (`research_conductor.py` user-prompt augmentation):

> **Step 5a (Tier 3 rename):** Rename `latest_outcome` → `last_research_round`, `previous_thesis` key → `proposer_reasoning`, `experiment_results` parameter → `prior_rounds_summary` across `autoresearch_research.py`, `research_conductor.py`, and affected tests. Single commit, `refactor:` prefix. Verification: full test suite green; `grep -r "latest_outcome" .` returns only spec-doc references (which are then updated in the same commit).

Done as one atomic commit — partial rename would leave the codebase in an internally-inconsistent state (some sites using new name, others using old).

### 14.6 Risk and rollback

**Risks:**

- Test fixture drift — any external test file outside the audited set that asserts on `latest_outcome[...]` keys will break. Mitigation: pre-flight `grep -r "latest_outcome\["` to catch all sites before the commit.
- Subagent reflexion may have stored prompts with old block headers in MemPalace. These don't need renaming (historical record), but a search for new-header strings against historical reflexions will miss. Acceptable — reflexions are advisory, not load-bearing.
- Trace exports written before the rename use old keys. The trace_sdk reads by event-type tag, not by dict-key name, so old exports remain queryable; only the human-readable surface changes.

**Rollback:** revert the single rename commit. No data migration needed because nothing on disk uses these names — they're in-memory Python identifiers only.

### 14.7 Success criteria additions

Append to §12:

- After the rename commit: `grep -rn "latest_outcome\|previous_thesis\|experiment_results" autoresearch_research.py research_conductor.py tests/` returns zero hits.
- After the rename commit: `grep -rn "last_research_round\|proposer_reasoning\|prior_rounds_summary" autoresearch_research.py research_conductor.py` returns at least the expected counts (15, 6, 5 respectively).
- User prompt contains no occurrence of the strings `"LATEST EXPERIMENT"`, `"PREVIOUS THESIS"`, `"LAST ROUND'S"` — only `"LAST RESEARCH ROUND —"` variants.
