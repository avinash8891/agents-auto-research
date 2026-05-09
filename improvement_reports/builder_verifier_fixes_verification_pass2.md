# Verification Pass 2: Builder/Verifier Bug Class Fixes

Date: 2026-05-09
Method: Read-only audit at HEAD against the eight claims in the second fix pass. Test confirmation: 942 passed / 0 failed across the full suite (focus suite: 170 passed).

## Per-fix verdict

| # | Claim | Verified? | File:line | Residual concern |
|---|---|---|---|---|
| 1 | `_activate_builder_config` preserves `required_diagnostic_specs` | YES | `autoresearch_orchestration.py:122` | Specs flow into SimpleNamespace and are read by `_build_thesis_for_eval` via `getattr` (`autoresearch_experiment.py:950`). Clean. |
| 2 | `try_resume_halted_thesis` no broad `except Exception` around contract rebuild | YES | `autoresearch_orchestration.py:541-646` | No try/except around contract construction or `model_dump_json`; deterministic errors propagate. Clean. |
| 3 | `_evaluate_against_thesis` discards on `inconclusive` | YES | `autoresearch_experiment.py:1027-1028` | Clean. |
| 4 | Diagnostics no longer silently fall back to `results[0]` when checkpoint missing | PARTIAL | `autoresearch_experiment.py:1000-1004` | Raises only if `any(spec.surface == "experiment_evaluation")`. `_baseline_metrics_from_first_result` still falls back to `results[0]` for non-experiment-evaluation surfaces (L920-942). Plausibly intentional for cold-start, but the broader fallback is unchanged. |
| 5 | Evaluator treats `missing_inputs` as unsatisfied | YES | `experiment_evaluator.py:182-183` | Routes through `missing_required_diagnostics` → `inconclusive` (L196-197). Clean. |
| 6 | `_diagnostic_spec_emitted` fails closed on empty key | YES | `compiler_implementation_verify.py:280-282` | Clean. |
| 7 | AST walks skip `if False:` dead branches | YES (narrow) | `compiler_implementation_verify.py:319-334` | Only matches `ast.Constant` of bool type. Misses `if 0:`, `if None:`, `if not True:`, `if 1==2:`, `if "":`. Determined adversary bypasses with one character. |
| 8 | `_verify_data_dependencies` inspects runtime modules for `vwap` | YES (narrow) | `compiler_implementation_verify.py:436-441` | Two scope gaps: (a) `_runtime_code_modules_with_failures` uses `strategy_dir.glob("*.py")` — non-recursive, top-level only; `strategies/ema/helpers/vwap_compute.py` invisible. (b) substring check `"vwap" in token` over lowercased text matches comments/docstrings — reintroduces the prose-substring class on a smaller surface. Aliases (`volume_weighted_avg`) unmatched. |
| 9 | `_validate_missing_primitives_contract` hard-fails outside `needs_code` | YES | `compiler_builder.py:265-282` | Triggers on `status == "needs_code"` OR `requires_code_change`. Called at L912, L1005. Clean. |

## New issues found (not in either prior report)

### `_contract_from_sidecar` is a third sibling SimpleNamespace constructor

- **File:line:** `autoresearch_experiment.py:413-440`
- **Pattern:** Same shape as `_activate_builder_config` and `try_resume_halted_thesis` — constructs a `SimpleNamespace` from the thesis sidecar to act as a contract.
- **Missing fields:** `required_diagnostic_specs`, `required_diagnostics`, `expected_effects`, `disqualifiers`, `strategy_family`.
- **Why it doesn't fail today:** all current callsites (L450, L514, L695) use `getattr(..., default)`.
- **Why it's a latent bug class:** if anyone wires this contract into `_evaluate_against_thesis` as a fallback (entirely plausible — there's already a similar SimpleNamespace path), evaluation silently degrades to "no required diagnostics," exactly the original bug class.
- **Suggested fix:** unify the three SimpleNamespace constructors into `ExperimentContract.from_sidecar(...)` classmethod with one source of truth.

### AST dead-branch filter trivially bypassable

- **File:line:** `compiler_implementation_verify.py:319-334`
- **Concrete failure:** `if 0:\n    cfg["vwap_gate"]` — verifier sees `vwap_gate` as consumed because `if 0:` matches `ast.Constant(value=0)`, not `ast.Constant(value=False)`. Filter only checks the bool case.
- **Suggested fix:** replace with `not bool(node.test.value)` for any literal `Constant` (covers `0`, `None`, `False`, `""`).

### vwap text scan reintroduces substring bug

- **File:line:** `compiler_implementation_verify.py:436-441`
- **Concrete failure:** strategy module containing `# TODO: consider adding vwap support later` (no implementation) plus a configured `vwap.parquet` data dependency passes the check while never consuming vwap.
- **Suggested fix:** AST-walk for `Name`/`Attribute`/`Call` nodes referencing vwap-related identifiers, not lowercased-text substring.

## Test quality

Tests inspected: `tests/test_autoresearch_experiment.py` (L1535, L1694, L1804) and `tests/test_compiler_pipeline_characterization.py` (L1081, L567, L1130). They are genuinely behavioral — assert specific failure tokens (`config_key_not_consumed_by_runtime:vwap_side_gate_enabled`), specific exception classes (`pytest.raises(ValueError, match="baseline checkpoint missing")`), specific verdict statuses. Would catch regressions.

Minor lapse: L1697 uses toy names (`t1`, `h`, `m`) — violates the project rule against synthetic test names but doesn't compromise correctness.

Coverage gap: no negative test for the AST dead-branch filter on non-bool falsy constants. The test gap mirrors the code gap.

## Cold-start / migration risk

- **Cold-start (claim 4):** safe. New raise fires only when thesis declares `experiment_evaluation`-surface diagnostics. A first-ever experiment with no diagnostics doesn't trigger; a first experiment that already declares baseline-comparison diagnostics legitimately cannot be evaluated, so failing loud is correct.
- **Inconclusive discard (claim 3):** no orchestrator path retries on `inconclusive` differently from `rejected`. `autoresearch_research.py:534-558` derives feedback uniformly from `verdict_status`. No regression.
- **In-flight halted theses (claim 9):** new hard-fail runs only at new builder calls (`compiler_builder.py:911, 1004`). Theses already on disk created without primitives still resume via `try_resume_halted_thesis`, which doesn't call `_validate_missing_primitives_contract`. Safe.

## Bottom line

Bug classes #1, #2, #3, #5, #6, #9 are genuinely closed. Class #4 is closed for `experiment_evaluation` surfaces; the broader fallback path remains but is plausibly intentional for cold-start. Classes #7 and #8 have **shifted shape, not closed** — the AST filter handles the canonical bool case but leaves trivial literal bypasses (`if 0:`, `if None:`, `if "":`), and the vwap scan reintroduces the prose-substring bug at smaller scope.

One latent sibling-caller miss remains: `_contract_from_sidecar` in `autoresearch_experiment.py` — same SimpleNamespace pattern, same missing fields, harmless today but the next caller wiring will reopen the original class.

## Recommended third pass (small)

1. **`_contract_from_sidecar`** — add the five missing fields, or unify all three constructors into `ExperimentContract.from_sidecar`.
2. **AST dead-branch filter** — generalize literal-falsy detection to all `ast.Constant` types (not just bool).
3. **vwap scan** — replace lowercased-text substring with AST walk over identifiers/attributes; add alias list (`volume_weighted_avg`, `vwap_price`) and recurse into subdirectories.
4. **Test for non-bool dead branches** — `if 0:` and `if None:` cases in `test_compiler_pipeline_characterization.py`.
