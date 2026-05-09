# Bug-Class Code Review — Builder/Verifier/Diagnostic Pipeline

Date: 2026-05-09
Scope: compiler_builder.py, compiler_implementation_verify.py, compiler_research.py, compiler_operationalize.py, autoresearch_orchestration.py, autoresearch_research.py, autoresearch_experiment.py, research_types.py, strategies/ema/*, related tests.

Premise: builder is treated as an imperfect codegen agent that may satisfy verifier heuristics without producing real artifacts. Recent fix addressed one prose→contract normalization bug for a baseline-comparison diagnostic; this review searches for sibling bugs of the same class.

---

## 1. Findings (ordered by severity)

### CRITICAL — Verifier accepts ANY string literal anywhere as proof of diagnostic emission

- **Refs:** `compiler_implementation_verify.py:259-287`; exercised by test `tests/test_compiler_pipeline_characterization.py:1434` (`logger.info('new_builder_metric')` passes verification).
- **Failure scenario:** Builder writes `logger.info('new_builder_metric')` — or a comment, a docstring containing `"new_builder_metric"`, or `_DIAG_KEYS = ['new_builder_metric']` in a dead branch. Verifier sees the quoted token in any of `strategies/<family>/*.py`, `metrics.py`, `strategy_event_logger.py`, `backtest/runner.py`, `autoresearch_experiment.py`, or `experiment_evaluator.py` and returns `passed=True`. Runtime emits no diagnostic. Evaluator silently runs without the field.
- **Why tests miss it:** `test_builder_implementation_verifier_accepts_custom_pf_diagnostic_from_metrics` is itself the false-positive: a `metrics.py` containing `diag['pf_by_time_since_last_same_symbol_entry_bucket'] = {}` with no caller and no schema check is asserted to *pass*.
- **Fix direction:** Verify against the *result payload* — load `experiments/<id>/result.json` (or run a sample backtest in the verifier subprocess) and assert the registered `payload_fields` exist for `surface=metrics`/`strategy_diagnostics` specs. For `surface="any"`, require both a string literal AND a write site (`= ` or `dict.update` / `[key] =` on a payload variable).

### CRITICAL — Disqualifier evaluation parses prose with regex; unparseable conditions silently return "not triggered"

- **Refs:** `experiment_evaluator.py:62-116` (`return False  # Can't parse mechanically` at L116); call site `:138-140`.
- **Failure scenario:** Builder/researcher emits `Disqualifier(condition="profit factor falls under 1.0", severity="hard_fail")`. Regex looks for `decreases by more than N percent` shape. None matches → `False` → `triggered=[]` → verdict `accepted` despite a clear hard-fail. No `failed_to_parse` channel, no logging, no quarantine — exactly the failure mode the diagnostics fix was meant to eliminate.
- **Why tests miss it:** No test asserts behavior for unparseable disqualifier conditions; `evaluate_disqualifier` is not unit-tested with conditions outside the two regex shapes.
- **Fix direction:** Mirror the `DiagnosticRequirementSpec` pattern — introduce `DisqualifierSpec(metric, operator, threshold, severity)` as the canonical machine contract; keep `condition` as prose for humans. If only prose is supplied, build a registered spec via a dispatcher; FAIL HARD when no spec matches, not silently return `False`.

### HIGH — Resumed/halted thesis path drops `required_diagnostic_specs`

- **Refs:** `autoresearch_orchestration.py:78-88` (builder activation), `autoresearch_orchestration.py:489-515` (halted-thesis sidecar + ctx contract); compare with `_build_thesis_for_eval` at `autoresearch_experiment.py:940-957`.
- **Failure scenario:** Halted thesis (requires_code_change), builder later succeeds, controller resumes. Reconstructed `SimpleNamespace` contract has no `required_diagnostic_specs`. `_build_thesis_for_eval` falls back to `getattr(..., "required_diagnostic_specs", [])` → empty → `build_required_diagnostic_specs` re-derives from prose. For the registered baseline-comparison key this happens to recover via aliases; for any future registered spec without a prose alias, evaluation runs without the spec, and `enrich_required_diagnostics` silently skips enrichment.
- **Why tests miss it:** No test loads the SimpleNamespace path through `_evaluate_against_thesis`. `test_evaluate_against_thesis_enriches_registered_baseline_comparison_diagnostic` only validates the happy path with prose that maps via the registered alias.
- **Fix direction:** Persist `required_diagnostic_specs` into `experiments/<id>/contract.json` (already done by `compiler_research.py`) and read it back into the SimpleNamespace at `autoresearch_orchestration.py:78-88` and `:505-514`. Also add the field to the `thesis_sidecar` write at `:490-500`.

### HIGH — Research thesis serialization strips `required_diagnostic_specs`

- **Refs:** `autoresearch_research.py:681`, `autoresearch_research.py:1513` (both whitelists missing `required_diagnostic_specs`).
- **Failure scenario:** Research sub-agent sets `required_diagnostic_specs` on a `ResearchThesis`. When the rejected/accepted record is serialized through these whitelists, the structured spec drops. Downstream consumers (rejection rules, MemPalace, reflexion) only ever see prose, defeating the canonicalization at `compiler_research.py:75-78` / `:115-118`.
- **Why tests miss it:** `tests/test_autoresearch_research.py` doesn't assert presence of `required_diagnostic_specs` in serialized records.
- **Fix direction:** Add `"required_diagnostic_specs"` to both whitelists.

### HIGH — `_baseline_metrics_from_first_result` uses results[0] unconditionally

- **Refs:** `autoresearch_experiment.py:920-937`, used by `_evaluate_against_thesis` at `:990`.
- **Failure scenario:** A forced baseline rerun (`apply_forced_baseline_rerun`, orchestration:536) appends a new baseline result; `results[0]` may now be a stale baseline whose code/data hash drifted. In resumed flows where the first result is a candidate (state file partial), the "baseline" comparison compares candidate-vs-candidate and reports zero deltas.
- **Why tests miss it:** Only the single-result happy path is tested.
- **Fix direction:** Pick baseline by `BaselineCheckpoint`/`baseline_tracker` lookup (already imported), not list index. Validate that the picked result's `code_commit`/`config_hash` matches the canonical baseline; if not, raise (deterministic error per CLAUDE.md error policy).

### HIGH — Retry artifact overwrite hides first attempt's failure evidence

- **Refs:** `compiler_builder.py:163-167` (`_builder_artifact_dir` returns one dir per `thesis_id`); `:170-204` (`_write_builder_attempt_artifacts` writes by name into a fixed dir); retry loop at `:722-808`.
- **Failure scenario:** Attempt 1 fails verification with diagnostic-missing failure; attempt 2 satisfies the heuristic with `logger.info('x')` and overwrites. Operator triaging only sees the success artifacts, no record of why attempt 1 failed.
- **Why tests miss it:** `test_compiler_pipeline_characterization.py:1455-1462` only asserts the final `prompt.txt` and `stdout.log` content; never asserts both attempts' artifacts coexist.
- **Fix direction:** Suffix `attempt_dir` per attempt (`attempt-001/`, `attempt-002/`). Aggregate into an `attempts.json` index.

### MEDIUM — Verifier `surface="any"` defaults to substring match across all texts

- **Refs:** `compiler_implementation_verify.py:280-282`; `diagnostic_contracts.py:85-91`.
- **Failure scenario:** Unregistered prose specs default to `surface="any"`. The verifier joins all texts (strategy code + metrics + evaluator + experiment runner) and accepts a hit anywhere — even in analysis-only files. Since the registry has only one entry today, every prose-only diagnostic falls through to `"any"`.
- **Why tests miss it:** Tests assume registered specs; no negative test for unregistered prose hitting analysis-only files.
- **Fix direction:** Default unregistered specs to `surface="strategy_diagnostics"` (the most common real intent), or require explicit `surface` and reject `"any"` at registration time.

### MEDIUM — Removal of `ANALYSIS_ONLY_DIAGNOSTIC_PREFIXES` regressed `definition_check:` skip semantics

- **Refs:** Diff hunk `compiler_implementation_verify.py` (deletion of `ANALYSIS_ONLY_DIAGNOSTIC_PREFIXES`); `diagnostic_contracts.py:10-15` (`normalize_diagnostic_requirement` does NOT skip those prefixes).
- **Failure scenario:** A thesis with `required_diagnostics=["definition_check: VWAP semantics"]` previously skipped runtime verification. Post-fix, it normalizes to `definition_check_vwap_semantics` and fails `required_diagnostic_not_emitted:` — builder will be looped or marked manual_review for an analysis-only check it can't satisfy in code.
- **Why tests miss it:** No test asserts the prefixes still skip runtime verification.
- **Fix direction:** Reintroduce the analysis-only filter inside `build_required_diagnostic_specs` — skip prose whose normalized form starts with `definition_check_` / `implementation_`.

### MEDIUM — `_verify_data_dependencies` uses prose-substring `"vwap"` lookup

- **Refs:** `compiler_implementation_verify.py:290-302`.
- **Failure scenario:** A thesis discussing "anti-VWAP" or with `"do NOT use vwap"` triggers the VWAP data check; builder fails for missing `vwap.parquet` although the strategy never reads it. Conversely, a thesis using `volume_weighted_average_price` without the literal token skips the check.
- **Fix direction:** Drive data-dependency checks off `runtime_config["data_universe"]` + a declared `data_inputs` list in the contract, not prose.

### MEDIUM — `_verify_config_key_consumption` is a substring check

- **Refs:** `compiler_implementation_verify.py:166-172` (`if key not in runtime_text: ...`).
- **Failure scenario:** Builder writes `# TODO: read new_builder_key` or assigns it to a docstring; verifier passes; runtime never reads the key.
- **Fix direction:** Require an actual call shape (`config.get("<key>")` or attribute access) via a tiny AST walk, or `re.search(rf"(config|cfg|self\.config)\.[a-zA-Z_]*\(?[\"']{re.escape(key)}[\"']")`.

### MEDIUM — Builder retry routing diverges between verifier-failure and config-validation-failure

- **Refs:** `compiler_builder.py:354-356` (only retry on verifier failures); `:806` (break on non-retryable error). Orchestration routes `builder_config_validation_failed` to research-retry vs. `builder_implementation_contract_failed` → manual_review (orchestration:155 / :200).
- **Failure scenario:** Attempt 1 passes validation but fails verifier; attempt 2 fails fresh-config validation. Surfaced error reflects only attempt 2; attempt 1's verifier failures are dropped. Final outcome path diverges based on which retry slot fails.
- **Fix direction:** Aggregate all attempts' failures into `out["attempt_failures"]` and route on the worst attempt's classification, not the last one.

### LOW — `enrich_required_diagnostics` silently skips when any of 4 fields is None

- **Refs:** `diagnostic_contracts.py:115-116` (`if None in {b_md, c_md, b_ppw, c_ppw}: continue`).
- **Failure scenario:** Baseline result missing `pct_profitable_windows` (older runs predate the field). Diagnostic is silently absent from `strategy_diagnostics`; verdict claims diagnostic was satisfied.
- **Fix direction:** Emit `enriched[spec.key] = {"missing_inputs": [...]}` instead of skipping; let evaluator surface that as `failed`.

### LOW — `_resolve_missing_primitives` synthesizes from config_changes when proposal lists are empty

- **Refs:** `compiler_builder.py:54-68`.
- **Failure scenario:** A thesis with non-empty `config_changes` but no `requested_primitives`/`missing_primitives` (legacy artifact, schema drift) ends up with synthesized primitives equal to its config keys. `_validate_missing_primitives_contract` passes; builder is told to "implement" already-supported keys; verifier passes (key already consumed); no real change happens.
- **Fix direction:** Synthesize only when `compilation.status == "needs_code"`; raise on the legacy shape.

---

## 2. Missing test matrix

| Scenario | Currently tested? | Suggested test name |
|---|---|---|
| Verifier rejects diagnostic present only in a comment | NO | `test_verifier_rejects_diagnostic_in_comment_only` |
| Verifier rejects diagnostic present only in a docstring | NO | `test_verifier_rejects_diagnostic_in_docstring_only` |
| Verifier rejects diagnostic literal in dead `if False:` branch | NO | `test_verifier_rejects_diagnostic_in_dead_branch` |
| Disqualifier with unparseable condition raises (not silently False) | NO | `test_unparseable_disqualifier_fails_loud` |
| Disqualifier registered spec round-trip | NO | `test_disqualifier_spec_round_trip` |
| Resumed halted thesis preserves `required_diagnostic_specs` | NO | `test_resume_halted_thesis_preserves_diagnostic_specs` |
| `_activate_builder_config` SimpleNamespace exposes specs | NO | `test_activate_builder_config_exposes_diagnostic_specs` |
| Research record serialization preserves specs | NO | `test_research_record_preserves_diagnostic_specs` |
| `_baseline_metrics_from_first_result` chooses canonical baseline after forced rerun | NO | `test_baseline_metrics_uses_canonical_after_rerun` |
| Builder retry preserves attempt-1 artifacts | NO | `test_builder_retry_preserves_per_attempt_artifacts` |
| `definition_check:` prose still skipped | NO | `test_analysis_only_prefix_skipped_post_refactor` |
| `_verify_data_dependencies` does not trigger on `"anti-vwap"` prose | NO | `test_data_dependency_uses_runtime_config_not_prose` |
| `_verify_config_key_consumption` rejects comment-only mention | NO | `test_config_key_consumption_rejects_comment_only` |
| `enrich_required_diagnostics` surfaces missing-input case | NO | `test_enrich_diagnostic_surfaces_missing_inputs` |
| `_resolve_missing_primitives` rejects legacy synthesis | NO | `test_resolve_missing_primitives_rejects_legacy_synthesis` |

---

## 3. Edge cases explicitly checked and ruled out

- `BUILDER_SENTINEL_CONFIG_KEYS`/`THESIS_METADATA_CONFIG_KEYS` filtering at `compiler_builder.py:25-26` and `_normalize_proposal_config_changes` correctly strips metadata before runtime-config writes.
- `_load_baseline_config` defaults-fallback path (`compiler_implementation_verify.py:135-142`) correctly distinguishes `STRATEGIES[family].get_defaults()` from missing-baseline error.
- `compile_research_thesis` no-op detection (`compiler_research.py:188-189`) correctly raises when generated config equals base.
- `DiagnosticRequirementSpec` pydantic model and `_register` deduplication on aliases (`diagnostic_contracts.py:21-24`) are sound.
- `_runtime_config_for_registered_strategy` correctly handles invalid keys (returns `{}`) and routes to `_needs_code_contract`.

## 4. Residual risks if no changes are made

- Future builder satisfies verifier with a single `logger.info('<key>')` line and ships no real instrumentation; evaluator runs blind, accepting candidates that don't actually emit demanded diagnostics. (CRITICAL #1 + LOW enrichment skip compound.)
- Disqualifier hard-fails written outside the two regex shapes never trigger — silent acceptance of strategies that should have been rejected. (CRITICAL #2.)
- Resume/halted flow corrupts the diagnostic contract on every recovery; drift accumulates as more registered specs are added. (HIGH #3.)
- Forced baseline reruns quietly compute zero-delta diagnostics, masking real regressions. (HIGH #5.)
- Operator triage on builder failures sees only the latest attempt's artifacts; root-cause analysis across attempts is impossible. (HIGH #6.)

---

## Recommended fix order

1. **#1 + #2** — same bug class as the recently-shipped fix; closes the source-text-as-proof loophole and parallel disqualifier loophole.
2. **#3 + #4** — specs propagation; cheap; prevents the recent fix from regressing on resume/serialization.
3. **#5 + #6** — correctness/observability; needed before the next builder retry incident.
4. Remaining MEDIUM/LOW as opportunistic cleanup.
