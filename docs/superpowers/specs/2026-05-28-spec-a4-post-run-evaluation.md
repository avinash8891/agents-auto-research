# Spec A4d - Post-Run Thesis Evaluation

**Date:** 2026-05-28
**Status:** Design - split from Spec A4c
**Reference:** A4a OUTPUT fields, A4c pre-run validation.
**Depends on:** A4c for a validated thesis before execution; backtest artifacts
for observed metrics and diagnostics after execution.

---

## 1. Goal

Define how an accepted, compiled, executed thesis is evaluated after a backtest
run. This spec owns observed outcome checks: whether `expected_effects`,
`disqualifiers`, required diagnostics, and `expected_runtime_signal` actually
occurred.

## 2. Non-goals

- LLM-facing OUTPUT field wording and examples - see A4a.
- Pre-run thesis shape, requiredness, reference resolution, and validator drift
  - see A4c.
- Per-round runtime prompt content - see A4b.
- Compiler-contract validation before execution - see A4c Stage 2.

## 3. Evaluation Boundary

A4c decides whether a thesis is well-formed, grounded, and executable before a
run. A4d starts only after a compiled thesis has run and produced metrics and
diagnostics.

The evaluator must not reject a thesis before runtime for an outcome that
cannot exist yet. Pre-run validators may check that predictions are shaped and
referenceable; post-run evaluators check whether those predictions happened.

## 4. Post-Run Evaluation Gates

| Gate | Scope | Owner | Result field(s) | Applicability | Logic | Feedback to research conductor |
|---|---|---|---|---|---|---|
| Expected effects pass/fail against backtest metrics. | evaluator | evaluator / `BacktestVerdict` | `passed_effects`, `failed_effects` | `post_run_only` | After a run, compare candidate metrics against baseline metrics for each `expected_effects` entry. `increase`/`decrease` must move in the declared direction and, when A4 `magnitude_range` is present, the measured delta must fall inside that range after converting by `unit`: `ratio` = raw delta for ratio metrics, `pct` = percent relative change, `bps` = basis-point delta, `sharpe_points` = raw Sharpe delta, `count`/`trades` = absolute count delta, `dollars` = absolute currency delta. `increase_or_same`, `decrease_or_same`, and `not_worse_than` are guardrail checks. Missing or failed effects produce `failed_effects` and make the verdict `inconclusive`, not accepted. | "Post-run result: expected effect {metric} {direction} {range} was {passed_or_failed}; baseline={baseline_value}, candidate={candidate_value}." |
| Disqualifiers triggered by run results. | evaluator | evaluator / `BacktestVerdict` | `triggered_disqualifiers` | `post_run_only` | Parse mechanically evaluable disqualifier conditions against run metrics/diagnostics. Hard-fail disqualifiers make the verdict `rejected`; soft-fail disqualifiers make it `inconclusive`. | "Post-run disqualifier {name} triggered: {condition} matched actual diagnostics. Treat the thesis as killed for hard_fail, or inconclusive for soft_fail." |
| Disqualifiers that cannot be parsed mechanically. | evaluator | evaluator / `BacktestVerdict` | `unparsed_disqualifiers` | `post_run_only` | When a disqualifier condition cannot be mechanically parsed, record its name in `unparsed_disqualifiers` and return an `inconclusive` verdict. Do not silently accept the run; the next prompt/spec iteration should make the disqualifier condition machine-checkable. | "Post-run disqualifier {name} could not be parsed mechanically. Rewrite future disqualifiers as metric/diagnostic comparisons the evaluator can check." |
| Required diagnostics missing after run. | evaluator | evaluator / `BacktestVerdict` | `missing_required_diagnostics` | `post_run_only` | After execution, ensure diagnostics required by predictions and `required_diagnostic_specs` appear in the run artifact. | "Post-run diagnostics are missing: {diagnostic_keys}. The run cannot evaluate the thesis claims until these diagnostics are emitted." |
| `expected_runtime_signal` actually occurred. | evaluator | future runtime-signal evaluator | TBD verdict field | `post_run_only` | After execution, resolve each `expected_runtime_signal.event_path` in diagnostics and evaluate relation/bounds under its condition when condition data is available. | "Post-run runtime signal {event_path} did not satisfy {relation}/{bounds} under condition {condition}." |

## 5. Verdict Semantics

The evaluator returns one thesis verdict:

- `accepted` - all required expected effects pass, no hard-fail disqualifier
  triggers, and required diagnostics are present.
- `rejected` - a hard-fail disqualifier triggers, or a required post-run check
  definitively disproves the thesis.
- `inconclusive` - required diagnostics are missing, expected effects fail or
  cannot be evaluated, disqualifiers are unparsed, or runtime-signal data is
  unavailable.

Status integrity rule: a run is not accepted unless every required post-run
sub-check succeeds. Silent skips are inconclusive, not accepted.

## 6. Persistence

Post-run evaluation results must be persisted separately from A4c pre-run
validator decisions. The persisted record must include:

- thesis/run identifiers;
- verdict status;
- passed and failed expected effects;
- triggered, unparsed, and missing disqualifier/diagnostic entries;
- expected-runtime-signal results when implemented;
- conductor-facing feedback strings for the next research round.

If the same gate-audit vocabulary is reused (`pass`, `warn`, `reject`,
`skipped_not_applicable`, `not_evaluated`), post-run rows must be clearly
distinguishable from A4c pre-run validator rows by stage/scope.

## 7. Success Criteria

- A run with all expected effects passing and no disqualifiers yields
  `accepted`.
- A run with a hard-fail disqualifier yields `rejected`.
- Missing diagnostics, unparsed disqualifiers, or unavailable runtime-signal
  data yield `inconclusive`, not accepted.
- Unit conversion for `magnitude_range` is tested for `ratio`, `pct`, `bps`,
  `sharpe_points`, `count`/`trades`, and `dollars`.
- A4c contains no post-run evaluator gate table; it only points to this spec.
