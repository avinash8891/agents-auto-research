---
title: V2 Integrity Path Fixes
type: fix
date: 2026-06-11
---

# V2 Integrity Path Fixes

## Summary

Repair the V2 research engine paths that can contaminate model learning or let mechanism proposals bypass the intended causal loop. This plan targets the highest-priority review findings from `.context/attachments/dEJnzO/pasted_text_2026-06-11_19-45-17.txt`: holdout residual leakage, non-actionable mechanism proposals producing no screening knowledge, legacy force-discard ownership of mechanism verdicts, and harvest prediction evaluation bugs.

---

## Problem Frame

The current V2 implementation added the named causal-model, screening, harvest, and walkforward components, but several control paths still behave like the legacy thesis-to-backtest loop. The most serious issue is objective contamination: `evidence_pack.py` renders residual trades from the same holdout slice later used by `score_on_holdout`, exposing holdout outcomes to the conductor before it proposes rules.

The second problem is that rules marked `actionable=false` complete before screening and model update, so zero-backtest rounds produce no knowledge. The prompt tells the conductor to use this path for rules worth adding only to the model, but the code does not persist them into the model.

The third problem is ownership ambiguity after a mechanism backtest. Mechanism proposals are converted into legacy `ResearchThesis` objects with `expected_effects`, which triggers `_evaluate_against_thesis` before registered prediction evaluation. A supported registered verdict should decide mechanism acceptance; the legacy force-discard chain should not be able to discard a supported mechanism.

The fourth problem is harvest correctness. `evaluate_predictions` treats an empty prediction set as supported, and `causal_harvest._flatten_metrics` lets train metrics override validation metrics. Both can produce false harvest support.

---

## Requirements

- R1. The conductor evidence pack must not expose holdout-slice outcomes in residual summaries used for hypothesis generation.
- R2. Non-actionable mechanism proposals must still screen, persist screening results, update or reject the causal model, and record accuracy without dispatching a backtest.
- R3. Mechanism backtest verdict ownership must come from registered predictions, not from the legacy expected-effects force-discard path.
- R4. Harvest prediction evaluation must reject empty registered-prediction lists as degenerate rather than supported.
- R5. Harvest metric flattening must prefer validation metrics over train metrics when both contain the same observable metric.
- R6. The implementation must preserve the existing V2 artifact formats and runtime roots; no new dependency is allowed.
- R7. Targeted tests must fail before the fix where practical, then pass after implementation.

---

## Key Technical Decisions

- KTD1. Use training residuals for conductor evidence: `causal_model.residual_map` should support an explicit slice or a dedicated training-residual helper, and `evidence_pack._residual_summary` should use pre-holdout rows. `score_on_holdout` remains holdout-only because that is the objective measurement path.
- KTD2. Treat non-actionable mechanisms as model-only candidates: reuse the existing screening and causal-model update functions in `autoresearch_research.py` rather than creating a parallel persistence path. The path should return `completed` only after a screening verdict and accuracy point are written.
- KTD3. Remove mechanism `expected_effects` backfill: `_mechanism_proposal_to_research_thesis` should not synthesize legacy expected effects for mechanism proposals. This keeps `_evaluate_against_thesis` out of mechanism rounds and lets `causal_harvest.evaluate_harvest` own the decision.
- KTD4. Fail harvest validation loud for vacuous predictions: `experiment_evaluator.evaluate_predictions` should classify empty predictions as `degenerate` with a direct reason.
- KTD5. Validation metrics are the authoritative harvest comparison surface: `_flatten_metrics` should merge train metrics first and validation metrics last, or otherwise choose validation values on key collision.

---

## Implementation Units

### U1. Holdout-Safe Residual Evidence

- **Goal:** Ensure residual summaries shown to the conductor are derived from pre-holdout rows.
- **Files:** `causal_model.py`, `evidence_pack.py`, `tests/test_causal_model.py`, `tests/test_evidence_pack.py`.
- **Patterns:** Follow existing `holdout_mask`, `score_on_holdout`, and `residual_map` tests in `tests/test_causal_model.py`.
- **Test Scenarios:** Add a test where holdout rows contain distinctive `trade_id` and outcome values; `build_corpus` must exclude those holdout IDs from `corpus.residual_summary`. Add a lower-level residual test proving the training residual helper excludes holdout rows and still ranks by unexplained absolute P&L.
- **Verification:** Run `pytest tests/test_causal_model.py tests/test_evidence_pack.py -v`.

### U2. Model-Only Mechanism Screening

- **Goal:** Make `actionable=false` mechanism proposals produce screening and model knowledge without spending a backtest.
- **Files:** `autoresearch_research.py`, `tests/test_autoresearch_research.py`.
- **Patterns:** Reuse `_screen_mechanism_proposal` and the accuracy append path already used for actionable mechanisms before conversion to `ResearchThesis`.
- **Test Scenarios:** Update or add a test where a non-actionable mechanism with a rule and predictions returns `completed`, writes a screening result, updates causal-model accuracy, and does not call the experiment dispatch path. Add a rejected-screening variant proving it records the rejection rather than silently completing.
- **Verification:** Run targeted tests for non-actionable mechanism handling in `tests/test_autoresearch_research.py`.

### U3. Mechanism Verdict Ownership

- **Goal:** Prevent legacy expected-effects evaluation from force-discarding mechanism proposals after registered predictions support them.
- **Files:** `autoresearch_research.py`, `autoresearch_experiment.py`, `tests/test_autoresearch_research.py`, `tests/test_autoresearch_experiment.py`.
- **Patterns:** Keep legacy thesis evaluation unchanged for true legacy `ResearchThesis` inputs; narrow the change to mechanism-generated theses or remove synthesized `expected_effects` from `_mechanism_proposal_to_research_thesis`.
- **Test Scenarios:** Add a conversion test proving mechanism proposals no longer create `expected_effects`. Add an experiment test where a mechanism harvest verdict is supported while a legacy expected-effect check would discard; final decision must not be `discard`.
- **Verification:** Run the targeted mechanism conversion and experiment decision tests.

### U4. Harvest Prediction Correctness

- **Goal:** Remove false supported verdicts from empty prediction lists and train-metric comparisons.
- **Files:** `experiment_evaluator.py`, `causal_harvest.py`, `tests/test_experiment_evaluator.py`, `tests/test_causal_harvest.py`.
- **Patterns:** Follow the existing degenerate and inconclusive prediction tests in `tests/test_experiment_evaluator.py`.
- **Test Scenarios:** Add an empty-predictions test expecting `degenerate`. Add a flattening test where `train_metrics` and `validation_metrics` share a metric with conflicting values; harvest evaluation must compare against the validation value.
- **Verification:** Run `pytest tests/test_experiment_evaluator.py tests/test_causal_harvest.py -v`.

---

## Scope Boundaries

- Deferred: regime-label lagging, external regime convention confirmation, feature-table vectorization, corpus prompt capping, walkforward graduation redesign, legacy-mode removal, validator residue cleanup, and harvest LLM lesson generation.
- In scope: only fixes needed to restore integrity of the V2 causal learning loop and avoid false harvest support.
- Out of scope: dependency changes, schema migrations, new services, and broad rewrites of the research conductor prompt stack.

---

## System-Wide Impact

The changes affect the path from conductor evidence generation through model screening and experiment harvest. They must preserve existing runtime artifact locations under `runtime/jobs/*/research/round-*`, existing causal-model JSON shape, and existing public function names unless a test proves the old behavior is unsafe.

---

## Risks & Dependencies

- The non-actionable mechanism path may need careful state transitions because existing tests expect it to complete without interruption. The fix should keep the terminal state but add the missing model side effects.
- Removing synthesized `expected_effects` may affect old tests that asserted the legacy conversion shape. Those tests should be updated to assert the new ownership boundary rather than keep the legacy behavior.
- Holdout-safe residual summaries reduce prompt detail. This is intended; the conductor should learn from training residuals and the holdout should remain reserved for scoring.

---

## Sources / Research

- `causal_model.py:137` keeps `score_on_holdout` as holdout-only objective scoring.
- `causal_model.py:160` currently builds residuals from the holdout mask.
- `evidence_pack.py:146` renders the residual summary into the conductor corpus.
- `autoresearch_research.py:1140` currently completes non-actionable mechanisms before screening knowledge is persisted.
- `autoresearch_research.py:873` currently converts mechanism proposals into legacy research theses with synthesized `expected_effects`.
- `autoresearch_experiment.py:1390` runs legacy expected-effects evaluation before harvest evaluation when expected effects exist.
- `experiment_evaluator.py:34` evaluates registered predictions and currently has no empty-list guard.
- `causal_harvest.py:345` flattens candidate metrics and currently lets train metrics win on collisions.
