# Spec A4b — Conductor Round Runtime Context Prompt

**Date:** 2026-05-28
**Status:** Design — split from Spec A4
**Reference:** A4a OUTPUT fields that depend on runtime context keys.
**Depends on:** A4a for field names; A4c for validation predicates consuming these keys.

---

## 1. Goal

Define only the per-round prompt blocks assembled immediately before a
conductor call. These blocks carry runtime facts the LLM needs to satisfy
conditional OUTPUT fields, but they are not part of the static OUTPUT schema.

## 2. Scope

This spec owns:

- `ROUND CONTEXT`
- `RECENT REJECTIONS`
- Runtime sources for prior theses, prior lever history, diagnostics paths,
  theme-keyword overlap signals, and citation-id conventions.

This spec does not own JSON field shapes or validator rejection codes.

## 3. ROUND CONTEXT Block

The system prompt renders a `## ROUND CONTEXT` block immediately above
OUTPUT. Conditional `Required:` lines reference its keys literally.

```
## ROUND CONTEXT (computed by conductor before LLM call)

Treat values below as ground truth. Reference them literally in conditional
fields; do not paraphrase entries or invent counts not shown here.

family_cluster_density: high | medium | low | none
  (high = the family has >=3 prior theses sharing >=2 theme_keywords each
   in the last 7 rounds; signals you must work harder on novelty)

dimensions_already_explored: (capped at 12; tail summarized)
  - signal_quality (4 attempts; 1 kept)
  - regime_conditioning (1 attempt; killed)
  (and N more)

dimensions_unexplored: (capped at 12)
  - portfolio_construction
  - alpha_decay

emergent_dimensions_in_use: (capped at 8)
  - session_microstructure (introduced job-9-round-2)

theme_keywords_in_use: (top 12 by attempt count; tail summarized)
  - stop_distance (5)
  - htf_gate (2)
  (and N more)

prior_lever_history: (top 12 by recency; structured for overlap detection)
  - config_keys: [ema_length]
    direction: tighten
    prior_thesis_id: job-11-round-2-attempt-1
    outcome: killed
  - config_keys: [rr_ratio]
    direction: widen
    prior_thesis_id: job-9-round-4-attempt-2
    outcome: kept
  (and N more)
  # Overlap check: if any key in your config_changes appears in a
  # `config_keys` entry above AND your direction (derived from your
  # value vs family-baseline) differs from the prior's `direction`,
  # populate `prior_lever_outcomes` citing that `prior_thesis_id`.

strategy_config_keys: (valid keys for config_changes)
  - ema_length (int)
  - timeframe_long (str)
  - timeframe_short (str)
  - rr_ratio (float)
  - direction_bias (str)
  - entry_cutoff_time (str)
  - max_trades_per_day (int)
  - gap_filter (bool)
  - gap_pct (float)
  - use_range_shift (bool)
  - range_shift_lookback (int)
  # Keys not listed here require requires_code_change=true +
  # requested_primitives.

prior_theses_snapshot: (top 20 by recency; for mechanism_lineage references)
  - thesis_id: job-12-round-3-attempt-1
    mechanism_dimension: regime_conditioning
    outcome: killed
  - thesis_id: job-12-round-1-attempt-2
    mechanism_dimension: regime_conditioning
    outcome: killed
  (and N more)

diagnostic_event_paths: (paths that resolve in the prior round's diagnostics JSON)
  - rejection_breakdown.trend_filter_rejected
  - rejection_breakdown.stop_hit_rejected
  - event_counts.signals_generated
  - event_counts.entries_taken
  (and N more)
  # Use only paths from this list in `expected_runtime_signal.event_path`.

theme_keywords_overlap_signal:
  # Self-check: emit your theme_keywords; if ANY token matches an entry in
  # `theme_keywords_in_use` above, `novel_connection` is REQUIRED at emit.
  # The validator runs this same check post-emit using the rendered list.

citation_id_convention:
  # The validator assigns positional ids citation_1, citation_2, ... to
  # entries of `evidence_citations` by array position. Reference these in
  # `deepest_alternative.tiebreaker.value` (and other tiebreakers). No
  # ROUND CONTEXT key carries them — they are determined by your own
  # emission order.
```

Size caps are hard. The renderer sorts each list by attempt count (or
recency, for `emergent_dimensions_in_use`) and emits a `(and N more)` tail
line so the LLM knows the view is truncated.

## 4. RECENT REJECTIONS Block

The conductor prompt also renders a `## RECENT REJECTIONS` block above OUTPUT
when prior attempts in this thesis-attempt sequence were rejected. Source:
the existing `list_rejections` MCP tool. Format:

```
## RECENT REJECTIONS (last 3 attempts in this round)

attempt 1: structural_other_alternatives_too_few
attempt 2: structural_deepest_alternative_tiebreaker_unresolved
attempt 3: thesis_quality_dimension_novelty_not_grounded
```

This is the ONLY place rejection codes appear in the LLM-facing prompt. The
LLM sees codes it has already tripped — not the full rule catalogue. Codes
the LLM has not tripped do not get surfaced; the spec's rule catalogue lives
in `prompts/conductor_output_rules.json` for the validator's use, not the
LLM's.

When the block is empty (first attempt of a round), the renderer emits no
block at all — silence beats noise.

## 5. LLM-Facing Cleanliness Rule

Runtime context blocks must contain facts the LLM can act on, not implementation
notes. Do not render Python function names, validator source line numbers,
internal derivation algorithms, filesystem source paths for config discovery,
or comments explaining validator internals. Those belong in implementation notes
or A4c.

Allowed example:

```text
prior_lever_history:
  - config_keys: [ema_length]
    direction: tighten
    prior_thesis_id: job-11-round-2-attempt-1
    outcome: killed
```

Disallowed in the LLM-facing block:

```text
# derived by validator from _direction_from_value_change in thesis_validator.py:576
```

## 6. Migration Items Owned Here

- Build the `## ROUND CONTEXT` block with size caps and deterministic ordering.
- Build the optional `## RECENT REJECTIONS` block from recent attempts in the
  current round only.
- Keep these blocks out of `prompts/conductor_output_section.md`; they are
  assembled at runtime by the prompt builder.
- Unit-test that every runtime key referenced by A4a OUTPUT fields is present
  when applicable.

## 7. Success Criteria

- Round-specific data is not embedded in the static OUTPUT schema.
- `RECENT REJECTIONS` appears only when prior attempts in the same round exist.
- Runtime blocks render only actionable facts, not implementation details.
- Every conditional OUTPUT field that depends on round state has a readable key
  in `ROUND CONTEXT`.
