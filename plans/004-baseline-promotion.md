# 004 — Baseline promotion (close the compounding loop)

## Problem
Research theses always compiled their `proposed_change` on top of the **frozen
committed baseline** (`configs/<family>_base.yaml`). A validated improvement was
catalogued (causal model) and selected (`current_best`) but never became the base
the next thesis built on. Combined with the residual-driven conductor, this
starved the engine: the conductor's residual signal is computed against a baseline
that never absorbs the harvested edge, so once the dominant residual pocket is
harvested every later round correctly declines (job 11, rounds 1–10).

## Shipped (this branch — in-sample-gated promotion)
`reconcile_state` now calls `promote_baseline_if_improved` after `current_best` is
set and before `plan_next_action`. When `current_best` is a non-baseline `keep`
result (by construction a validated improvement), its runtime config is written to
a runtime-root overlay `promoted_baselines/<family>_base.yaml`. The base-config
reads (`compiler_research._load_base_runtime_config`) prefer the overlay over the
committed seed, so the next thesis compounds on it.

- Overlay lives at a path that does **not** exist in the read-only code root, so
  `resolve_config_path`'s runtime-root fallback finds it without clobbering the
  committed seed on local runs.
- Fail-soft: a failed promotion logs loud and continues; it never aborts the run.

## Deferred — TODO (do these next)

1. **Walk-forward gate (the main one).** Promotion currently fires on the
   in-sample `keep` verdict. It should instead fire only after the candidate
   **graduates walk-forward** validation (`walkforward.run_walkforward_queue` →
   `graduated`). Today walk-forward runs once at model plateau over the whole
   candidate queue; promotion should consume its graduated verdict rather than the
   per-round in-sample metric. Wiring: gate `promote_baseline_if_improved` on the
   graduated candidate, or move the promotion write into the walk-forward terminal
   handler.

2. **Builder-code-dependent edges.** A winning config whose edge came from
   builder-generated code (`requested_primitive`) is only reproducible on the same
   release. Promoting just its config across a redeploy would reference primitives
   the released code may not have. Decide: promote the config + the builder
   promotion manifest together, or skip code-dependent winners until their code is
   merged.

3. **Fresh-job / redeploy reset semantics.** The overlay persists in the runtime
   root across `--fresh-job` and across deploys (runtime root is stable). Decide
   whether a truly fresh job should reset to the committed seed or keep compounding
   from the overlay, and whether a new committed baseline (new sha) should win over
   a stale overlay.

4. **Round-0 benchmark on the promoted baseline.** `autoresearch_planning._baseline_branch`
   still benchmarks the committed seed for a brand-new job. Once (3) is decided, point
   round-0 at the overlay when present so a fresh job's benchmark reflects the live
   baseline.
