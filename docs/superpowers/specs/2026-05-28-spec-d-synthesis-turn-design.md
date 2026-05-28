# Spec D — Synthesis Turn

**Date:** 2026-05-28
**Status:** Design — **conditional** on Spec C telemetry
**Reference:** `2026-05-28-preflight-recall-design.md` (unified long-form context)
**Depends on:** Spec C (synthesis turn operates on the pre-flight top-K block Spec C adds)
**Trigger condition:** Spec C telemetry shows `THESIS_ANCHORS_ON_LATEST` rate ≥ 30% over a rolling 30-round window. Meaning: even with full deterministic snapshot + semantic retrieval + dedup gate, the agent still proposes theses whose `mechanism_dimension` AND ≥2 `theme_keywords` overlap with the most-recent prior — anchoring on what just happened rather than considering the broader landscape and cross-dimension priors.

If `THESIS_ANCHORS_ON_LATEST` rate < 30% after ≥30 rounds of Spec C in production, Spec D is **not justified** and is dropped.

---

## 1. Goal

Split the conductor's thesis-creation step into **two LLM turns** instead of one:

1. **Synthesis turn (new).** Given the full pre-flight context (Spec A's landscape + pairs + previous_thesis + Spec C's top-K block), the LLM's task is **only** to identify 2–3 unexploited combinations, contradictions, or gaps it notices in the corpus. **Does not draft a thesis.**
2. **Drafting turn (existing, with synthesis output appended).** Same context + the synthesis-turn output is appended. The LLM picks the most promising angle and drafts one thesis.

The two-turn split forces lateral consideration before commitment. Single-turn prompting anchors the agent on the most-recent prior; splitting the turn breaks the anchor.

## 2. Non-goals

- Anything not related to the two-turn flow.
- A formal "selection turn" between synthesis and drafting (ranking the synthesis angles on disconfirmer strength × evidence availability × novelty). This is a Phase-4-converge feature; deferred until separate telemetry justifies.
- Heterogeneous-agent diversity (multiple sub-agents per round). Different paradigm.

## 3. Background

See reference doc §3 for ground truth and Spec A §3 / Spec C §3 for compressed background.

**Why two turns, specifically when Spec C alone isn't enough:**

LLMs in a single-turn prompt anchor on the most-recent prior in their context — the recency effect is well-documented. Even with cross-dimension priors visible in Spec C's diversity-half block, the agent's first instinct (when asked to draft a thesis) is "propose a variant of what just happened." A two-turn split asks for **divergent observations first** ("what do you notice across these priors that nobody has tried?") then **convergent drafting second**. The literature on "deliberation before decision" patterns supports this — separating the divergent thinking from the convergent decision is what consistently produces more novel outputs.

This is the closest analog in our setup to QuantEvolve's "cousin sampling" pattern, which surfaces diverse parent candidates and asks the LLM to combine. We don't have an evolutionary population, but we have the corpus and Spec C's two-pass retrieval; this spec turns those into an explicit divergence step.

**Cost:** ~1.3× the round's planning-LLM tokens (one extra LLM call). Synthesis-turn output is small (a JSON list), so output-token cost is bounded.

## 4. Architecture

```
preflight_synthesis_turn.py      ← new
  └─ build_synthesis_user_prompt → str  (Turn 1 user-prompt wrapper)

research_conductor.py            ← edited
  ├─ Thesis-creation flow becomes two LLM turns instead of one:
  │    Turn 1: synthesis (user prompt = round context + Spec A blocks +
  │            Spec C top-K block + synthesis instruction)
  │    Turn 2: drafting (Turn 1 user prompt + synthesis_observations + drafting instruction)
  └─ Synthesis-turn output (synthesis_observations) is persisted to the round artifact
     and forwarded into Turn 2 verbatim.
```

No new external dependency. No validator change (existing rules apply to the Turn 2 draft only).

## 5. Components

### 5.1 Synthesis turn

**Trigger:** thesis-creation stage of the conductor. Only when `_synthesis_enabled()` is `true` (default).

**Turn 1 user prompt:** existing round context + Spec A blocks (landscape, pairs, runtime_config, diagnostics, rejected_attempts, previous_thesis) + Spec C top-K block + this synthesis instruction:

> "Given the priors, landscape, and dimension pairs above, identify 2–3 unexploited combinations, contradictions, or gaps you notice in the corpus. Output a JSON object matching the schema below. Do not draft a thesis yet."

**Output schema for Turn 1:**

```json
{
  "synthesis_observations": [
    {
      "observation": "string, ≥80 chars — what you noticed",
      "supporting_thesis_ids": ["..."],
      "type": "combination" | "contradiction" | "gap"
    }
  ]
}
```

**Validation of Turn 1 output:**
- ≥1 observation required.
- `observation` ≥80 chars (forces specificity).
- `supporting_thesis_ids` must be a subset of the snapshot's surfaced ids (re-use of Spec A's snapshot-ids check, no new code).
- Malformed JSON → one retry. Second failure → skip synthesis output and proceed to Turn 2 with pure context blocks. Log `SYNTHESIS_TURN_DEGRADED`.

**Turn 1 output persisted to:** the round artifact (`runtime/jobs/job-N/research/round-M/synthesis.json`). Visible to the operator + future audits.

### 5.2 Drafting turn

**Turn 2 user prompt:** same as Turn 1 user prompt, with `synthesis_observations` appended verbatim before the existing drafting instruction:

> "## Synthesis observations from Turn 1
> {synthesis_observations JSON}
>
> Pick the most promising angle from your synthesis above and draft a single thesis. If you claim novelty, cite the priors you contrasted against in `prior_lever_outcomes` (and optionally reference your synthesis observations in your hypothesis text)."

Turn 2's output goes through the existing thesis-validation pipeline unchanged.

### 5.3 `build_synthesis_user_prompt()`

Stateless string returning the Turn 1 instruction (the bit appended to round context + blocks). One home for the exact wording (CLAUDE.md rule **B** — one home per concept).

Returns:

```
## Synthesis turn

You are looking at:
- The mechanism landscape (above)
- Killed/kept pairs by dimension (above)
- The closest priors to what was just tried (above)
- Cross-dimension priors selected for diversity (above)
- The previous thesis's full structured reasoning (above)

Your task in this turn:
Identify 2–3 unexploited combinations, contradictions, or gaps you notice in the corpus.

Do NOT draft a thesis yet. The next turn will be drafting.

Output one JSON object only, matching this schema:
{
  "synthesis_observations": [
    { "observation": "...", "supporting_thesis_ids": [...], "type": "combination|contradiction|gap" }
  ]
}

Each observation must be ≥80 characters. Each supporting_thesis_id must be one of the
thesis_ids visible in the priors above.
```

## 6. Validator changes

None new. Existing thesis-validation rules apply to Turn 2's draft. Turn 1's `synthesis_observations` validation is local to `preflight_synthesis_turn.py` (schema check + supporting_thesis_ids set membership), not a `thesis_validator.py` rule.

## 7. Configuration

| Function | Env var | Default | Purpose |
|---|---|---|---|
| `_synthesis_enabled()` | `AUTORESEARCH_SYNTHESIS_TURN_ENABLED` | `true` | Kill switch — when `false`, Spec C blocks still inject but only one LLM turn runs |
| `_synthesis_min_observation_chars()` | `AUTORESEARCH_SYNTHESIS_MIN_OBSERVATION_CHARS` | `80` | Minimum observation length for Turn 1 validation |

Each accessor validates and raises with the named env var on bad input.

## 8. Error handling

| Failure | Behavior |
|---|---|
| Turn 1 LLM call fails (network, timeout) | One retry. Second failure → skip synthesis, proceed to Turn 2 with no `synthesis_observations`. Log `SYNTHESIS_TURN_DEGRADED`. |
| Turn 1 produces malformed JSON | Same — one retry, then degrade. |
| Turn 1 produces 0 observations or all observations < 80 chars | Same — retry, then degrade. |
| `supporting_thesis_ids` contains unknown ids | The output is still passed to Turn 2 (the agent's reasoning is preserved), but a `SYNTHESIS_HALLUCINATED_IDS` warning is logged for telemetry. Not a hard fail. |
| Synthesis turn disabled (`_synthesis_enabled()=false`) | Skip Turn 1 entirely. Turn 2 runs with no `synthesis_observations` appended. |

Fail-open posture: if synthesis fails, the conductor still produces a thesis from Turn 2 (using Spec A + Spec C context as it would have today).

## 9. Testing

### 9.1 Unit

- `build_synthesis_user_prompt`: output is stable; contains required schema fields; mentions "Do NOT draft a thesis yet".
- Turn 1 output validation: malformed JSON triggers retry; second failure triggers degradation log; well-formed output passes through.
- `supporting_thesis_ids` membership check works against a fixture snapshot.

### 9.2 Integration

- End-to-end conductor round with `_synthesis_enabled()=true`:
  - Two LLM calls observed (verified via trace/usage logs).
  - `synthesis.json` artifact written to the round directory.
  - `synthesis_observations` appears in Turn 2's user prompt.
  - Final thesis draft includes a citation to ≥1 thesis_id from the synthesis observations (when the agent uses one).
- End-to-end conductor round with `_synthesis_enabled()=false`:
  - One LLM call observed.
  - No `synthesis.json` written.
  - Behavior matches Spec C-only baseline.
- Synthesis-turn degradation: simulate malformed Turn 1 output → conductor proceeds to Turn 2 cleanly; `SYNTHESIS_TURN_DEGRADED` logged.

## 10. Migration plan

One PR:

1. `preflight_synthesis_turn.py` new module + unit tests.
2. `research_conductor.py`: split thesis-creation into Turn 1 + Turn 2; persist `synthesis.json`; forward observations into Turn 2 prompt.
3. Integration test (both `_synthesis_enabled` settings).
4. End-to-end test + commit per CLAUDE.md.

## 11. Telemetry contract

- `SYNTHESIS_TURN_TOKENS` — input/output tokens for Turn 1 per round.
- `SYNTHESIS_OBSERVATIONS_COUNT` — number of observations per round.
- `SYNTHESIS_OBSERVATION_TYPES` — distribution across {combination, contradiction, gap}.
- `THESIS_CITES_SYNTHESIS_OBSERVATION` — % of Turn 2 drafts that reference a synthesis observation's `supporting_thesis_ids`.
- `THESIS_ANCHORS_ON_LATEST` (continuing from Spec C) — did this spec actually reduce the anchoring rate?
- `SYNTHESIS_TURN_DEGRADED` — count of fall-back-to-Turn-2-only rounds.

**Post-Spec-D evaluation:** after 30 rounds in production:
- If `THESIS_ANCHORS_ON_LATEST` dropped by ≥10 percentage points vs the Spec-C-only baseline → Spec D earned its tokens.
- If unchanged or increased → consider disabling via `_synthesis_enabled()=false` and revisit the design.

## 12. Success criteria

- Two LLM calls observed per thesis-creation round when `_synthesis_enabled()=true`.
- `synthesis.json` written per round.
- `synthesis_observations` appears in Turn 2's user prompt.
- `synthesis_observations` validates against the schema (≥1 entry, ≥80 chars each, valid `type` enum).
- Malformed Turn 1 output triggers one retry then degrades cleanly with logged signal.
- Kill switch (`_synthesis_enabled()=false`) cleanly bypasses Turn 1.
- All Spec D telemetry counters emit.
- Post-30-round measurement: `THESIS_ANCHORS_ON_LATEST` rate change is documented (drop justifies keeping the synthesis turn; no drop justifies disabling).

## 13. Coupling notes

- **Spec C must ship first.** Synthesis turn consumes Spec C's pre-flight top-K block in its context. Without that block, the synthesis turn has only Spec A's landscape + pairs to work with, which is weaker context.
- **Independent of Spec B.** OUTPUT schema doesn't affect the two-turn flow.
- **Spec A obviously must be in place** (Spec D inherits all of Spec A's context via Spec C's dependency on Spec A).

## 14. Open considerations (out of scope, recorded)

- **Model routing for Turn 1.** Synthesis is meta-reasoning that doesn't need Opus tokens. When a model router exists, route Turn 1 to a cheaper tier. Future work.
- **Selection turn between Turn 1 and Turn 2.** Adds a third LLM call that ranks Turn 1's observations. Further deferred — earn the right by showing Spec D's two-turn flow isn't enough.
- **Synthesis-observation corpus.** Over time `synthesis.json` artifacts accumulate. Could become a second-order corpus ("what the planner *noticed* in past rounds"), feeding a future meta-pre-flight. Far-future work.
