# Thesis Duplicate Detection Plan

## Problem

The research loop needs to reject duplicated theses so agents do not repeatedly backtest the same idea. The current validation boundary also must not reject genuinely new theses that require new strategy code.

A recent failure showed the old duplicate check was too coarse:

- Two different engine-change theses both used the top-level config key `new_config_keys_needed`.
- The validator compared only top-level config keys.
- Because both theses had the same top-level key, the validator reported 100% overlap.
- The research loop rejected a valid new idea before the builder could create the required code.

This is not a web-research problem. It is a thesis novelty-classification problem.

## Current Fix

The current change flattens nested config-change keys before comparing overlap.

Example:

```text
new_config_keys_needed.entry_confirmation_mode
new_config_keys_needed.momentum_activation_enabled
```

These are now treated as different proposed changes instead of both being collapsed into `new_config_keys_needed`.

This is the correct immediate fix because it restores the validation boundary:

- Same nested config path can still be rejected as duplicate.
- Different nested config paths are allowed through.
- New theses that require builder-created code are no longer blocked just because they use the common `new_config_keys_needed` envelope.

## Why This Is Not Enough Long Term

Flattened config-path overlap catches structural duplicates, but it does not fully understand research novelty.

It can miss cases where:

- Two theses use different config names but express the same trading mechanism.
- The same idea is rewritten with different wording.
- A prior failed thesis returns with slightly renamed parameters.

It can also be too strict if used alone for future engine-change work, because new research ideas often share scaffolding keys while differing in mechanism.

## Proposed Long-Term Solution

Use layered duplicate detection instead of one hard Jaccard check.

### Layer 1: Structural Hard Reject

Keep flattened config-path comparison as the hard duplicate gate.

Reject only when there is strong structural evidence:

- Same strategy family.
- Same or highly overlapping normalized config paths.
- Same mechanism category if available.
- Same entry/exit/risk surface being modified.

This layer should be deterministic and explainable.

### Layer 2: Semantic Similarity Warning

Add embeddings over a normalized thesis representation:

```text
strategy_family
mechanism_summary
hypothesis
expected_edge
entry_logic
exit_logic
risk_logic
flattened_config_paths
builder_code_surface
```

Use cosine similarity against prior theses to find near-duplicates.

This should usually be a soft signal, not an automatic hard reject, because similar language does not always mean the same testable trading idea.

### Layer 3: LLM Novelty Judge For Borderline Cases

For borderline candidates, retrieve the top similar prior theses and ask an LLM judge whether the new thesis is materially novel.

The judge should answer with structured output:

```json
{
  "decision": "novel | duplicate | revise",
  "closest_prior_thesis_id": "...",
  "reason": "...",
  "required_difference": "..."
}
```

Use this for retry feedback to the research agent. Only hard-stop if the judge and structural signals both indicate a true duplicate.

### Layer 4: Persistence

Persist enough metadata per thesis so novelty checks are stable and cheap:

- thesis id
- strategy family
- normalized mechanism text
- flattened config paths
- code surface touched or requested
- embedding vector
- validation decision
- closest prior thesis ids
- rejection reason

SQLite is sufficient for this stage. Embeddings can be stored as JSON or a compact binary blob unless vector search volume becomes large enough to justify a vector index.

## Decision Policy

Recommended policy:

| Case | Action |
| --- | --- |
| Same family and same flattened config paths | Hard reject |
| Same family and high structural overlap | Hard reject or require LLM judge |
| High embedding similarity but different config/code surface | Retry feedback, not hard reject |
| Similar wording but distinct mechanism | Allow |
| New builder-required config/code path | Allow unless clearly duplicate |

## Implementation Order

1. Keep the current flattened config-path fix.
2. Persist normalized thesis metadata for every accepted and rejected thesis.
3. Add embedding generation for the normalized thesis representation.
4. Add top-k prior thesis retrieval by cosine similarity.
5. Add LLM novelty judge for borderline cases.
6. Convert semantic duplicate signals into research-agent retry feedback.
7. Add metrics for rejected duplicates, allowed near-duplicates, and later backtest outcomes.

## Success Criteria

The system is working as intended when:

- Builder-required theses are not rejected merely because they share `new_config_keys_needed`.
- Exact or near-exact repeated theses are rejected before backtesting.
- Borderline cases produce actionable retry feedback instead of opaque validation failure.
- Duplicate decisions include the prior thesis id and a human-readable reason.
- The research loop keeps progressing without repeatedly creating new jobs due to avoidable validation blocks.
