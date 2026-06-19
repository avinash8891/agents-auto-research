# 005 — Comprehensive compounding engine (the ideal solution)

Supersedes the in-sample "for now" promotion in [004](004-baseline-promotion.md).
That shipped within-job, config-only, in-sample-gated promotion. This is the
complete design it should grow into.

## Removed: fresh-job stale-model wipe (was commit 20cfc71)
A `archive_inconsistent_model_for_fresh_job` step was added to wipe the persisted
causal model on a fresh job so the (then toolless) conductor would rediscover from
the seed instead of declining on already-harvested factors. It was **removed** once
the research tools were re-wired (the conductor now researches a *new* dimension
rather than declining on a harvested pocket — the prompt makes a harvested pocket
the signal to explore elsewhere). Wiping the model destroyed the cross-job causal
ledger the tools now consume. The causal model + promoted overlay should **carry
across fresh jobs**. (The separate plateau `accuracy_history` reset on promotion is
unrelated and stays — it is load-bearing for loop termination.)

## The core idea: a promote-and-rebaseline outer loop

Today the engine runs research rounds against a **frozen** baseline until it
plateaus, then validates out-of-sample once, then terminates. The validated edge
is never folded back in. The fix is an outer loop that ratchets the baseline up
by one out-of-sample-validated edge per turn:

```
loop:
  # inner: existing research rounds vs the CURRENT baseline's residuals
  run research rounds (propose causal rule → screen → backtest → keep/discard → harvest)
  until in-sample plateau (no new keepable edge)

  # gate: validate everything kept since the last promotion, out-of-sample
  graduated = walk_forward(kept_candidates_since_last_promotion)

  if graduated is empty:
      TERMINATE            # truly plateaued out-of-sample — principled stop
  else:
      promote(graduated)   # fold edges into the baseline (config + builder code)
      re_baseline()        # backtest new baseline → new benchmark + fresh feature table
      reset in-sample plateau
      continue             # conductor now attacks the NEXT weakness
```

Each outer turn the conductor sees the residuals of the **compounded, re-baselined**
baseline, so it attacks a new weakness instead of re-hitting a harvested pocket.
Overfit edges never promote (walk-forward gate). Termination is principled.

## Why the current state stalls (verified this session)
- Theses always compiled on the committed seed → no compounding (004 fixes this
  within a job).
- A fresh job (job 11) re-benchmarks the seed; `feature_table.latest_through`
  floors at round 0, so round-1 residuals are the seed's misses = the
  already-harvested opening-window pocket → conductor declines from round 1 →
  no kept round → promotion never fires. **In-sample promotion alone cannot
  un-stall a fresh job.**

## Status
**A + B + C shipped** (this branch). Promotion is now walk-forward-gated and the
plateau→walk-forward→terminate path became plateau→walk-forward→promote→re-baseline
→resume. The in-sample `reconcile_state` promotion from 004 was removed.
Remaining: **D** (builder-code edge promotion) and **E** (VPS end-to-end sign-off).

## Work items

### A. Walk-forward-gated promotion (replaces the in-sample trigger) — DONE
- Move the promotion call out of `reconcile_state` (in-sample) into the
  walk-forward terminal handler. Promote only candidates with a `graduated`
  verdict from `walkforward.run_walkforward_queue`.
- On graduation with survivors: promote, re-baseline, clear
  `finished_reason=model_plateau_pending_walkforward`, return the loop to
  `research`. On graduation with no survivors: terminate (real plateau).
- Keep 004's overlay write + the empty/invalid guard as the promotion primitive;
  just change *what triggers it* and *what it consumes*.

### B. Re-baseline after promotion (consistency triad)
The baseline config, the round-0 benchmark, the causal model, and the feature
table must stay mutually consistent:
- After promotion, run a baseline backtest of the **new** baseline → new
  benchmark metric + fresh feature table. Residuals then reflect the compounded
  baseline (the promoted edge's misses vanish), so the conductor moves on.
- Causal model: a harvested factor describes an edge *relative to the old
  baseline*. Once promoted, that edge is in the baseline and its residuals
  disappear naturally; the factor stays as audit history and screening dedup.
  **Invariant: never reset the baseline without the model, or the model without
  the baseline.** They are one unit of state.

### C. Fresh-job / round-0 reads the live baseline (004 TODO #4)
- `autoresearch_planning._baseline_branch` benchmarks the promoted overlay when
  present, so a fresh job continues from the latest graduated baseline rather
  than the seed. Decide explicitly: `--fresh-job` continues from the overlay
  (default) vs `--reset-baseline` clears overlay + causal model together (B's
  invariant).

### D. Builder-code edge promotion — PARTIAL (fail-loud guard done)
SHIPPED: a cross-redeploy **fail-loud guard** in `_load_base_runtime_config` — if the
promoted overlay is read on a release whose strategy can't honor it (a builder
primitive is absent), it raises a clear error with remediation instead of failing
silently or surfacing a misleading "unsupported config_changes" error against the
innocent thesis.

DELIBERATELY NOT DONE (separate capability + safety call): auto-reproducing the
generated code on a new release. The builder promotion queue is `queued_review` by
design — auto-running unreviewed AI-generated code crosses that boundary. The
responsible path is: graduation escalates the code for **merge** (auto-PR or operator
review), the merged code ships in the next release, then the overlay validates and
compounding continues. Until that capability exists, a code-dependent edge promotes
on its building release and fails loud on a release missing the code. Sub-items if we
build the capability: persist `runtime/builder-promotions/` to the **runtime root**
(today it is written to the ephemeral code root), and either auto-apply the persisted
files at startup or open a PR on graduation.

### D-orig. Builder-code edge promotion
- A graduated edge that depended on builder-generated code is only reproducible
  on the same release. Promote the builder promotion manifest
  (`runtime/builder-promotions/<family>/<thesis>/`) **with** the config — apply
  the generated files into the release (or open a PR to merge them) so the
  promoted baseline is reproducible across deploys. Until then, 004's guard
  correctly *skips* code-dependent winners (loud log) rather than corrupting.

### E. End-to-end verification (no claim without this)
- VPS run from a clean runtime root: land an edge, confirm promote → re-baseline
  → next round attacks a *different* residual pocket (not the harvested one).
- Confirm the walk-forward gate **rejects** a deliberately overfit in-sample edge
  (does not promote).
- Confirm `--reset-baseline` clears overlay + model together and reproduces the
  original seed benchmark.

## Recommended sequencing
1. **C** (small, unblocks fresh-job compounding immediately on top of 004).
2. **B** re-baseline (makes residuals refresh correctly — the actual stall fix).
3. **A** walk-forward gate (rigor: stop promoting overfit edges).
4. **D** builder-code promotion (breadth: code edges, not just config).
5. **E** verification throughout, final VPS sign-off.

## Immediate next step
Implement **A+B+C as one coherent change**: turn walk-forward graduation into
`promote → re-baseline → resume research`, and make round-0 read the overlay.
That converts 004's mechanism into the real compounding loop and fixes the
job-11 stall. D and the in-sample→walk-forward trigger move can follow.
