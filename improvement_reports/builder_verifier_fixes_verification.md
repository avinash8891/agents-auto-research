# Verification Pass: Builder/Verifier Bug Class Fixes

Date: 2026-05-09
Method: Read-only audit of HEAD against `improvement_reports/builder_verifier_bug_class_review.md`. Tests confirmed: `pytest tests/test_compiler_pipeline_characterization.py tests/test_autoresearch_orchestration.py tests/test_autoresearch_research.py tests/test_autoresearch_experiment.py -q` → **161 passed**.

## Per-fix verdict

| # | Claim | Verified? | Evidence | Residual gap |
|---|---|---|---|---|
| 1 | Verifier rejects logger-only / comment-only diagnostic | YES (mostly) | `compiler_implementation_verify.py:267-302` (`_diagnostic_spec_emitted`) → `_payload_key_token_present` (L339-351) and `_registered_spec_key_present` (L354-372) walk AST, only count `ast.Dict` keys, `ast.Subscript` string keys, `DiagnosticRequirementSpec(key=...)` registrations. `logger.info('x')` rejected (test L1476). Comments/docstrings skipped because not matched node types. | Dead-branch literals like `if False: payload["x"] = 1` STILL pass — `ast.walk` visits unreachable nodes. Report explicitly listed dead-branch as failure scenario; no test covers it. |
| 2 | Disqualifier propagates `unparsed_disqualifiers`, verdict `inconclusive` | PARTIAL | `experiment_evaluator.py:115-138` returns `(False, True)` on no-shape-match; verdict at `:189-194` sets `inconclusive`; field plumbed through `ExperimentVerdict` (`research_types.py:169`); behavioral test `tests/test_autoresearch_experiment.py:420-448`. | **Report directed FAIL HARD; impl produces soft `inconclusive`.** Orchestration has no `inconclusive` route — `autoresearch_experiment.py:1020-1024` only short-circuits on `rejected`; `inconclusive` falls through with original `decision`. Hard-fail disqualifiers in unparseable prose still don't reject candidates; they just tag the verdict. Below CLAUDE.md error-policy bar. |
| 3 | Halted-resume preserves `required_diagnostic_specs` | YES for `try_resume_halted_thesis`; **NO for `_activate_builder_config`** | Resume: `autoresearch_orchestration.py:585-630` writes specs to `thesis.json`, builds `ExperimentContract`, writes `contract.json`, attaches to SimpleNamespace at L629. Test L194-196. **But** `_activate_builder_config` (L101-122) reads `thesis.json` into `thesis_payload` and constructs SimpleNamespace WITHOUT `required_diagnostic_specs` even though payload contains it. | Downstream `_build_thesis_for_eval` re-derives from prose so registered alias still recovers. Future spec without prose alias silently disappears at this site — bug class half-fixed. |
| 4 | Research thesis whitelists preserve specs | YES | `autoresearch_research.py:680` and `:1513` both include `"required_diagnostic_specs"`. | One round-trip behavioral test would have made this airtight; current diff just adds the key. Low risk. |
| 5 | `_baseline_metrics_from_first_result` uses tracker | PARTIAL | `autoresearch_experiment.py:920-924` consults `controller.baseline_tracker.latest()` first. | **Fallback contradicts CLAUDE.md.** When `latest()` returns `None`, silently falls back to `results[0]` — exact stale/candidate-as-baseline path the report warned about. Report directed: raise on canonical-baseline mismatch. Implementation does not raise; doesn't validate `code_commit`/`config_hash`. |
| 6 | `definition_check_` / `implementation_` analysis-only skip | YES | `diagnostic_contracts.py:19, 81-82` skips inside `build_required_diagnostic_specs`. Verifier (`compiler_implementation_verify.py:230`) and evaluator (`experiment_evaluator.py:169`) both consume specs only, so analysis-only prose never reaches missing-diagnostic check. Behavioral test L1547. | Clean. |
| 7 | VWAP no longer triggered by prose | YES (now under-triggers) | `compiler_implementation_verify.py:382-408` reads only config keys, requested_primitives, registered diagnostic spec keys/aliases. Test L1569. | **New silent-miss bug.** Strategy that uses `vwap.parquet` without declaring `vwap` in any of those four lists skips the check entirely. Report explicitly warned: "Silent miss is worse than the previous false trigger." Check still uses substring `"vwap" in token` (L400) — same prose-style match, smaller surface. |
| 8 | `missing_inputs` surface | YES (write side only) | `diagnostic_contracts.py:118-129` writes `enriched[spec.key] = {"missing_inputs": [...]}`. | **Evaluator doesn't fail on it.** `experiment_evaluator.py:175-178` checks `if not any(token in available_diagnostics ...)` — when key IS present (with `{"missing_inputs": [...]}`), missing-diagnostic check passes. Payload surfaced but does NOT push verdict to `inconclusive`/`failed`. Report directed: "let evaluator surface that as `failed`." Only writer half done. |
| 9 | `_resolve_missing_primitives` rejects legacy shape | NO | `compiler_builder.py:251-262` returns `[]` instead of raising. Synthesis-from-`config_changes` path is gone (good), but report directed `raise on the legacy shape`. Downstream guard `_validate_missing_primitives_contract` (L265-279) only fires when `compilation.status == "needs_code"`. A thesis with `requires_code_change=True` but non-`needs_code` compilation silently returns `[]` and bypasses the guard. | Legacy shape still slips through. |

## Newly introduced concerns

- **`autoresearch_orchestration.py:617`** — `except Exception as exc:` swallows pydantic/IO failures around contract refresh, only logs warning. CLAUDE.md: "Deterministic errors … propagate loud." Broad swallow; if contract construction silently fails, the SimpleNamespace at L619-630 is the only source of specs and there's no `contract.json` for downstream readers — divergent state.
- **AST-walk verifier visits unreachable code** — `_payload_key_token_present` walks entire module AST. `if False: payload["new_key"] = 1` passes. No test rules this out.
- **Builder retry classification widened** — `compiler_builder.py:_implementation_verification_failures_present` now also counts `builder_config_validation_failed` as retryable. Patches retry routing but cross-attempt failure aggregation the report recommended is not implemented; only the retry decision changed.
- **`# noqa: BLE001`** added at `autoresearch_orchestration.py:511` for activation try/except. Consistent with house rules; flagging.

## Issues NOT addressed by this pass

From original report still open: **#1 (dead-branch literals)**, **#2 (no FAIL HARD; orchestration lacks `inconclusive` routing)**, **#3 (`_activate_builder_config` SimpleNamespace still missing specs)**, **#5 (no canonical-baseline validation, silent fallback)**, **#7 (silent under-trigger)**, **#8 (evaluator doesn't fail on `missing_inputs`)**, **#9 (legacy non-needs_code shape bypass)**.

New items found here:
- **`compiler_implementation_verify.py:267`** — `_diagnostic_spec_emitted` returns `True` when `key` is empty. Unregistered prose normalizing to empty (e.g. `"_____"`) silently passes verification.

## Test quality assessment

- **`tests/test_compiler_pipeline_characterization.py`** — Mixed. New verifier tests (L1476, L1547, L1569) are behavioral: assert `result.passed` and specific `failures`, plus negation case for VWAP. Would catch obvious regressions of #1/#6/#7. None covers dead-branch / comment-only / docstring-only — the exact loopholes called out in the report's "Why tests miss it" section. Realistic strategy keys used (`per_symbol_entry_cooldown_minutes`, `pf_by_time_since_last_same_symbol_entry_bucket`) — CLAUDE.md compliant.
- **`tests/test_autoresearch_orchestration.py`** — `test_try_resume_happy_path_writes_config_and_thesis_files` (L130-199) solidly behavioral: round-trips a registered `DiagnosticRequirementSpec` through file writes and SimpleNamespace, asserts the spec key. Doesn't test unregistered-prose case where `try_resume` recovery is weakest.
- **`tests/test_autoresearch_research.py`** — single diff hunk (whitelist key); test additions check `required_diagnostic_specs` survives serialization. Adequate.
- **`tests/test_autoresearch_experiment.py`** — `test_evaluate_experiment_marks_unparsed_disqualifier_inconclusive` is behavioral: asserts `verdict.status`, unparsed list, summary string. **No test for the orchestration consequence** of `inconclusive` — does the controller actually reject the candidate? It doesn't, and no test catches that.

## Bottom line

The 161 passing tests demonstrate that each patch closes the **specific test scenario** that motivated it; they do **not** demonstrate the bug *classes* are closed. Five of nine fixes are partial: #2 (verdict surfaced but not routed), #3 (resume path patched but `_activate_builder_config` left), #5 (tracker preferred but no validation, silent `results[0]` fallback retained), #7 (prose removed but new silent-miss introduced), #8 (writer fixed, evaluator unchanged), #9 (synthesis removed but legacy shape still bypasses).

The patches lean toward symptom-level: surface the issue in a payload field, log a warning, fall back quietly — rather than the deterministic-error / fail-loud posture CLAUDE.md mandates.

## Recommended second pass

1. **#2 + #8** — make orchestration *act* on `inconclusive`. Add evaluator logic: presence of `missing_inputs` or non-empty `unparsed_disqualifiers` → push verdict to `inconclusive` AND short-circuit decision in `autoresearch_experiment.py:1020-1024`.
2. **#3** — fix `_activate_builder_config` (`autoresearch_orchestration.py:101-122`) to read `required_diagnostic_specs` out of `thesis_payload` and attach to the SimpleNamespace. Two-line change.
3. **#5** — replace silent `results[0]` fallback with `raise` when tracker has no canonical baseline. Add `code_commit`/`config_hash` validation when tracker DOES return one.
4. **#9** — raise (not return `[]`) when `compilation.status != "needs_code"` but `requires_code_change=True`.
5. **#1 + #7** — add behavioral negative tests for dead-branch / comment-only / docstring-only diagnostic literals; add behavioral negative test for VWAP-using strategy that doesn't declare it.
6. **Newly found** — guard `_diagnostic_spec_emitted` against empty `key` (return `False`, don't pass).
7. Tighten `autoresearch_orchestration.py:617` exception swallow — narrow to specific exception types and re-raise the rest.
