# Spec-vs-Code Review — autoresearch backtesting platform

**Date:** 2026-06-10
**Branch:** `avinash8891/spec-review` (HEAD `965521f`)
**Reviewer:** multi-agent workflow — 8 requirement extractors → 7 dimensional reviewers (spec compliance, API/contract, data model/schema, edge case, regression, test coverage, simplicity) → adversarial verification per finding (refute-by-default) → main-loop independent re-verification of every confirmed citation against source.
**Supersedes:** the 2026-05-31 version of this report.

## Scope and method

There is **no `docs/spec.md`** in this repository. Per prior direction (recorded in the 2026-05-31 version of this report), the "spec" is the **markdown documentation corpus**, grouped as:

| Group | Documents | Role |
|---|---|---|
| CORE | `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/data-universes.md` | Binding operating rules + architecture contracts |
| SCHEMA | `docs/persistence-schema/01..03` | Target canonical SQLite schema + migration definition |
| VALIDATOR | `docs/superpowers/plans/2026-05-27-*` (4 validator plans) | Thesis-validator refactor (landed in #57/#62) |
| BEHAVIOR | behavior-signals-phase-abc, prompt-variant-framework | Behavior signals + prompt-variant A/B |
| EXPMCP | experiment-round-cleanup, mcp-tool-arg-validation (plan + design) | Terminology cutover + MCP arg validation (landed in #61–#65) |
| DESIGNS | pluggable-agent-backends, preflight-recall | **Proposals** — absence of implementation is not a defect |
| MISC | fix-code-review-issues, thesis-duplicate-detection, tracing, vps-provenance plans, `FIX_PLAN.md` | Assorted plans |
| AUDIT | `TECH_DEBT_AUDIT.md` | Regression baseline (documented-issue claims, not authored requirements) |

**373 requirements/invariants/acceptance criteria/non-goals** were extracted. Reviewers produced 41 findings; adversarial verification **refuted 13** (e.g. claims resting on non-binding plan sketches like the `le=100` limit, unreachable preconditions, or misattributed commits). The 28 survivors were deduplicated to **16 distinct findings**, and every citation below was independently re-confirmed by reading the source (rule L).

---

## 1. Traceability matrix (summary)

Status per requirement group (357 traceability entries across reviewers):

| Group | implemented | partial | missing | violated | untested | ambiguous | n/a |
|---|---|---|---|---|---|---|---|
| CORE (35) | 15 | 4 | 1 | 7 | — | — | 1 |
| SCHEMA (~80) | 46 | 20 | 9 | 2 | — | — | 3 |
| VALIDATOR (~50) | 48 | — | — | — | 2 | — | 1 |
| BEHAVIOR (16) | 11 | — | 4 | — | 1 | — | — |
| EXPMCP (~110) | 90 | 19 | — | 2 | — | — | 1 |
| DESIGNS (7) | — | — | — | — | — | — | 7 (proposals) |
| MISC (14) | 12 | 1 | — | — | — | 1 | — |
| AUDIT (49) | 34 | 5 | — | 5 | 2 | 1 | 2 |

Headline reading:

- **Validator refactor (#57/#62) and MCP arg validation (#63): fully landed and tested.** No confirmed defects in either.
- **Experiment→round terminology cutover (#64/#65): substantially landed** — the partials are two deliberate-but-undocumented compat shims and one duplicated helper (F7, F11, F12).
- **Persistence-schema migration: half done.** `research_rounds` + `research_thesis_attempts` are canonical; `backtest_runs` keeps the legacy column shape and `baseline_checkpoints` was never created (F1, F2).
- **Prompt-variant framework plan: zero implementation** despite its Definition of Done checkboxes being marked `[x]` (see §3).
- **Designs (pluggable backends, preflight recall): unscheduled proposals** — tracked as not-applicable, no defects asserted.

---

## 2. Bugs and gaps, ranked by severity

Severities reflect adversarial-verifier corrections (several initial HIGHs were downgraded with reasons noted).

### HIGH

**F1 — `baseline_checkpoints` table (required table 4 of 4) was never created; checkpoints persist to a shadow JSON file**
- *Spec:* SCHEMA-31 — "Implement these tables only: 1. `backtest_runs` 2. `research_rounds` 3. `research_thesis_attempts` 4. `baseline_checkpoints`" (`docs/persistence-schema/03`, §"Implement these tables"); SCHEMA-14 requires `checkpoint_id TEXT PRIMARY KEY` + `strategy_family` + two indexes, flagged `NEW_WRITE_PATH_REQUIRED`; SCHEMA-01 "SQLite is the only canonical durable store."
- *Code:* `backtest_run_db.py:173-296` `_init_db` creates only `session_meta`, `backtest_runs`, `research_rounds`, `research_thesis_attempts` — zero `baseline_checkpoints` DDL anywhere in the repo. Instead `autoresearch_controller.py:629` wires `BaselineTracker` to `{family}_baseline_checkpoints.json` (`backtest_run_db.py:1041-1095`, JSON read/write).
- *Why violated:* durable cross-round checkpoint history lives outside SQLite — exactly the dual durable persistence the migration was defined to eliminate (also CLAUDE.md rule 8, single source of truth). Neither required NEW field (`checkpoint_id`, `strategy_family`) exists in the JSON shape.
- *Confidence:* high (2 reviewers + verifier + independent re-check).
- *Minimal fix:* add `baseline_checkpoints` DDL (checkpoint_id PK, strategy_family, code_commit, data_hash, config_hash, metrics_json, created_at_utc) + its two indexes to `_init_db`; point `BaselineTracker` writes at it; demote the JSON to operational state or delete it.
- *Test:* `test_baseline_checkpoint_persists_to_sqlite` — `BaselineTracker.record()` on a fresh ema DB writes one row with non-empty `checkpoint_id`, `strategy_family='ema'`.

**F2 — `backtest_runs` still has the pre-migration column shape; all six required indexes missing**
- *Spec:* SCHEMA-07 (`docs/persistence-schema/01`:18-49) — canonical columns incl. `trace_run_id`, `decision_status`, `created_at_utc`, `strategy_family`, `job_id`, `primary_metric_name/value`, merged `metrics_json`, `trade_analysis_json`; SCHEMA-09 — six `idx_backtest_runs_*` indexes; doc 02:33-50 defines the exact mappings (`accepted→decision_status keep/discard`, `timestamp→created_at_utc`, …).
- *Code:* `backtest_run_db.py:181-213` DDL: `accepted INTEGER`, `timestamp`, `family`, `job`, split `train_metrics_json`/`validation_metrics_json`; none of the canonical columns. `backtest_run_db.py:262-281` creates indexes only for `research_rounds`/`research_thesis_attempts`; repo-wide grep for `idx_backtest_runs` hits only the spec doc.
- *Why violated:* the canonical cutover is half-finished — the two new tables migrated (#61–#65), the central one didn't. Four of the six indexes can't even be created until the columns exist.
- *Confidence:* high.
- *Minimal fix:* next migration increment via the existing `_ensure_column` idempotent pattern: add canonical columns (backfilled from legacy ones), then the six `CREATE INDEX IF NOT EXISTS`.
- *Test:* migration test per SCHEMA-39 — open a pre-migration DB, assert `PRAGMA table_info(backtest_runs)` contains the doc-01 columns, `decision_status` backfilled keep/discard from `accepted`, and `PRAGMA index_list` shows the six indexes.

**F3 — `data_loader` silently drops requested symbols; backtest proceeds on a subset and reports ok**
- *Spec:* CORE-13 (AGENTS.md error policy) — "a run or step is 'ok' only if every required sub-step succeeded. Partial success with a silent skip = failed run, reported as failed."
- *Code:* `data_loader.py:74-76` wide path: `cols = [s for s in symbols if s in df.columns]` — missing requested symbols silently filtered. `data_loader.py:96-104` per-symbol path: a symbol dir with no parquets is skipped via `if parquets:` with no log; only the all-symbols-missing case raises.
- *Why violated:* concrete scenario — universe manifest lists 8 symbols, one parquet missing/corrupt on the VPS → backtest loads 7, completes, and the run is recorded keep/discard against baselines computed on 8. No warning, no quarantine, no failure. Verifier confirmed the production caller chain (`backtest/data_universe.py:44` → strategies) has no compensating check.
- *Confidence:* high.
- *Minimal fix:* in both `_load_wide` and `_load_per_symbol`, compute `missing = set(symbols) - set(loaded)` and raise `DataLoadError` when non-empty.
- *Test:* `load_data(symbols=['AAPL','MSFT'])` against a fixture universe containing only AAPL → assert `DataLoadError`, not a silent 1-symbol batch.

### MEDIUM

**F4 — Coverage gate is `fail_under = 45`, contradicting the 80% rule and TECH_DEBT_AUDIT's "FIXED" claim** *(found independently by 3 reviewers; downgraded from HIGH because commit `65f8590` lowered it deliberately with a ratchet comment)*
- *Spec:* AUDIT-01 / CLAUDE.md testing rules (80% minimum); `TECH_DEBT_AUDIT.md:52,74-80` marks F002 **FIXED** with a 70→80 diff.
- *Code:* `pyproject.toml:142-144` — `fail_under = 45` ("Current baseline … ~45.7% … then ratchet upward"). CI enforces it via `pytest -v --cov` (`.github/workflows/ci.yml:28`). Git history: `ab2691d` raised 70→80 (matching the FIXED claim), then `65f8590` (#53) lowered 80→45.
- *Why violated:* doc and config directly contradict; the gate is below even the 70% the audit originally complained about, and no doc records a waiver. Whole-module gaps pass CI silently.
- *Minimal fix:* either ratchet `fail_under` back toward 70/80 with focused coverage work, or correct `TECH_DEBT_AUDIT.md` F002 from FIXED to a dated, explicit 45%-baseline decision. Doc and config must agree.
- *Test:* CI check parsing `fail_under` against the documented gate.

**F5 — HALO/Claude timeout tunables are module-level env-read constants, violating the lazy-accessor invariant**
- *Spec:* CORE-18 (AGENTS.md Hygiene) — "Env-var-backed tunables use lazy accessor functions, not module-level constants… Validation lives inside the accessor and raises with a named env-var."
- *Code:* `improvement_halo.py:27` `HALO_TIMEOUT_SECONDS = parse_positive_int_env(…)` (used at `:127`); `improvement_halo_apply.py:35` `CLAUDE_TIMEOUT_SECONDS = …` (used at `:122`). The tests prove the failure mode: they must `importlib.reload()` to make `monkeypatch.setenv` stick (`tests/test_improvement_halo.py:221-254`, `tests/test_improvement_halo_apply.py:329-361`). Bonus deviation: `parse_positive_int_env` falls back to the default on invalid input instead of raising with the named env var.
- *Minimal fix:* convert to `halo_timeout_seconds()` / `claude_timeout_seconds()` accessors called at the `subprocess.run` sites; raise `ValueError` naming the env var on bad input; delete the reload-based tests.
- *Test:* `monkeypatch.setenv` after plain import changes the timeout used (no reload); `'not-a-number'` raises naming the env var.

**F6 — Runtime artifacts not gitignored: `.gitignore` still carries the pre-rename DB name; 37 run artifacts committed**
- *Spec:* CORE-32 (AGENTS.md architecture) — `trace_exports/` and `ema_backtest_runs.db` documented "(gitignored)".
- *Code:* `.gitignore:235` ignores only `ema_experiments.db` (pre-#64 name). `git check-ignore trace_exports ema_backtest_runs.db` exits 1. `git ls-files` shows 37 tracked artifacts under `runtime/` (e.g. `runtime/jobs/job-25/research/round-1/trace_exports/...`), committed in `900c51e`.
- *Why violated:* commit `965521f` renamed the DB but the ignore entry wasn't updated; a local run now leaves committable runtime state, and real job-25/job-7 artifacts are already in history.
- *Minimal fix:* replace the entry with `*_backtest_runs.db`, add `/trace_exports/` and `/runtime/` (or precise globs); `git rm -r --cached` the 37 artifacts unless they are deliberate fixtures (if so, document that).
- *Test:* CI check — `git check-ignore` succeeds for both; `git ls-files runtime/ trace_exports/` empty or allowlisted.

**F7 — Second public `research_round_id()` with a divergent (lenient) contract in `backtest_run_db`, used by production callers** *(found by 3 reviewers)*
- *Spec:* EXPMCP-02/-03 (`docs/superpowers/plans/2026-05-28-experiment-round-cleanup.md`) — one canonical helper in `autoresearch_runtime_paths.py` (raises on `job<1`/`round<0`), created precisely because "the literal string is duplicated across modules today"; AGENTS.md rule B (one home per concept).
- *Code:* canonical helper at `autoresearch_runtime_paths.py:22-32`. Duplicate at `backtest_run_db.py:28-39` — same name, `f"job-{int(job_id)}-round-{int(round_number)}"`, docstring admits it "does NOT raise on out-of-range values." Production consumers of the lenient copy: `research_conductor.py:19,985`, `autoresearch_research.py:47,256,825,1184,1225`.
- *Why violated:* two callers now get different contracts for the same primary-key-generating concept; the lenient path can mint `"job-0-round-3"` ids that the strict DB write boundary (`add_from_sqlite_fields`, `backtest_run_db.py:430-444`) later rejects as a mismatch. Format drift between the two copies would silently break the 1:1 `get_by_research_round_id` lookups.
- *Minimal fix:* delete `backtest_run_db.research_round_id`; repoint callers at the strict helper, or at the existing `research_round_id_or_empty` where partial state is legitimate.
- *Test:* conductor round-id assignment with `job=0` yields `""` or raises — never a fabricated `job-0-round-N`.

**F8 — Halt-handler enrichment guard is narrower than "any code that can raise": a non-`ValueError` skips `_close_run`**
- *Spec:* CORE-14 (AGENTS.md error policy) — terminal-state mutation + operator notification "must run *before* any code that can raise"; enrichment is best-effort with its own try/except.
- *Code:* `autoresearch_research.py:1674-1680` writes halt fields first (compliant), but the enrichment guard at `:1691` is `except (ValidationError, ValueError)`. The enrichment chain reaches `resolve_contract_support` (`compiler_operationalize.py:74`) → `strategies/orb/contract.py:51-55`, which can raise other types (e.g. `TypeError`/`KeyError` on malformed agent payloads). Halt fields are mutated only in the in-memory dict; persistence + Discord live in `_close_run`.
- *Why violated:* an unexpected exception type propagates out of `_handle_needs_code` before `_close_run`, so the halted state is never persisted and the operator is never notified — the exact partial-state failure the rule was written against (the in-code comment at `:1670-1673` even states the intent).
- *Minimal fix:* widen the enrichment guard to `except Exception` (logging the class); optionally wrap `write_current_md` inside `_close_run` so `notify_discord` always runs after `write_state`.
- *Test:* monkeypatch `_prepare_thesis_for_validation` to raise `TypeError`; assert state file has `state='halted'` and Discord notify was invoked.

**F9 — Unparsable metric values coerced to `0.0` in best-run ranking instead of propagating loud**
- *Spec:* CORE-11 (AGENTS.md error policy) — deterministic errors (schema/data) propagate loud, never swallowed.
- *Code:* `backtest_run_db.py:56-60` `_coerce_metric_float` returns `0.0` on `TypeError`/`ValueError`; used by `best_by_metric` at `:988-993` to rank records. No log, no quarantine.
- *Why violated:* under `direction='lower'` (a real path — drawdown-style metrics), a record with a corrupt non-numeric metric coerces to 0.0 and **wins** `best_by_metric` outright, silently steering the conductor toward a garbage "best" run.
- *Minimal fix:* in `best_by_metric`, skip records whose metric fails coercion or is non-finite (`continue` + `log.warning` with run id) instead of ranking them at 0.0.
- *Test:* DB with `direction='lower'`, records `metric='corrupt'` and `metric=2.5` → returns the 2.5 record, warns on the corrupt one. (Pairs with U1 below — this branch currently has zero coverage.)

### LOW

**F10 — `compiler_thesis_io.py:75` calls `ResearchThesis.model_validate` without `normalize_thesis_payload`** *(downgraded from HIGH: verifier found no production caller — reachable only via `compiler_pipeline.py:16-17` re-export and a characterization test)*
- *Spec:* CORE-05 (AGENTS.md rule B) — the normalizer "must be … invoked at *every* `model_validate` call site."
- *Code:* `compiler_thesis_io.py:75` — no normalizer import anywhere in the file; the upstream chain (`validate_family_config_changes`, `operationalize_thesis`) doesn't normalize either. The other three sites do (`autoresearch_research.py:141`, `agent_runners.py:59`, `thesis_validator.py:2416`).
- *Minimal fix:* wrap with `normalize_thesis_payload(dict(thesis))` — or, if the entry point is confirmed dead, delete it per CORE-19. Fixing this is also the precondition for removing the F11 alias.
- *Test:* pass a thesis with legacy `source="experiment_result"` evidence into `create_executable_artifact`; assert it compiles.

**F11 — Legacy `"experiment_result"` alias kept in the `Literal` enum: two migration mechanisms for one legacy value**
- *Spec:* EXPMCP-01 — "Hard cutover, no deprecation shims… Every old name removed in the same PR"; EXPMCP-14 grep gates require zero matches.
- *Code:* `research_types.py:67-78` Literal includes both `"round_result"` and `"experiment_result"` (annotated "Deployment-migration alias"); `thesis_validator.py:1003-1012` *also* rewrites it in `normalize_thesis_payload`. If normalization ran at every site (F10), the Literal alias would be dead code.
- *Why flagged:* accept-in-schema + normalize-on-load is redundant machinery for the same value, and contradicts the written invariant without a recorded deviation. (Arguably the safer engineering call — but undocumented.)
- *Minimal fix:* fix F10, then delete `"experiment_result"` from the Literal; or amend the plan to document the sanctioned alias + removal condition.
- *Test:* raw `source="experiment_result"` → `ValidationError`; normalize-then-validate → accepted as `"round_result"`.

**F12 — `vps_runner` keeps a `'run_experiment'` compat branch with an open-ended TODO**
- *Spec:* EXPMCP-01 (no shims); EXPMCP-13 names the `vps_runner.py` heredoc sites for atomic replacement.
- *Code:* `vps_runner.py:454-470` — "Deploy migration compat: accept both… TODO: remove the 'run_experiment' branch once all VPS instances are post-refactor"; tuple membership at `:462,:469`. Shim is test-locked at `tests/test_vps_runner_config.py:472-474`.
- *Minimal fix:* remove now (deploys are git-ref pinned; redeploying is the cutover), or file the follow-up the TODO implies with a pinned removal condition.
- *Test:* heredoc test asserting the remote script contains `run_round` and not `run_experiment` (post-removal).

**F13 — TECH_DEBT_AUDIT F019 marked FIXED, but `backtest/runner.py --output-dir` still defaults to `/tmp`**
- *Spec:* AUDIT-16 — tmpfs-exhaustion risk on VPS; `TECH_DEBT_AUDIT.md:118` appends "→ **FIXED**".
- *Code:* `backtest/runner.py:31-33` — `default="/tmp"` unchanged; git history shows no commit touching it.
- *Minimal fix:* env-backed default (`AUTORESEARCH_OUTPUT_DIR`, falling back to `"."`) per CORE-18, or revert F019 to TODO. Same doc-integrity class as F4.
- *Test:* parser default ≠ `/tmp`; honors the env var via monkeypatch.

**F14 — F020's `PYTEST_CURRENT_TEST` guard in `trace_sdk` is dead code per the project's own hygiene rule**
- *Spec:* CORE-16 (AGENTS.md Hygiene) — `PYTEST_CURRENT_TEST` "is NOT set during pytest module import/collection… Module-level code cannot use it as an import guard."
- *Code:* `trace_sdk.py:530` runs `_initialize_tracing()` at import; inside, `_PROVIDER = _build_provider()` (`:510`) executes **before** the `PYTEST_CURRENT_TEST` check (`:511-513`). Test modules import production modules at collection time, so the guard never fires and global OTel state mutates for the whole suite — the very thing F020 claimed to fix.
- *Minimal fix:* use a mechanism that works at import time (dedicated env var set in `conftest.py`, or `'pytest' in sys.modules`), and move provider construction behind the guard.
- *Test:* fresh-subprocess import with the disable var set → assert no `Traceloop.init` / provider replacement.

**F15 — `research_rounds` row is written only at first outcome, not at round start; crashed zero-attempt rounds leave no row**
- *Spec:* SCHEMA-32 (doc 03 write behavior) — "When a research round starts: 1. create a `research_rounds` row."
- *Code:* only write path is `log_research_round` (`backtest_run_db.py:526-567`, `outcome` NOT NULL), called per rejected attempt (`autoresearch_research.py:713`) and at finalize (`:1842`). No round-start write; no `in_progress` outcome value.
- *Why flagged (low):* the gap is only rounds that die before producing any logged attempt — invisible in canonical history. Could equally be resolved by documenting the deviation in doc 03.
- *Minimal fix:* write the row with `outcome='in_progress'` at round start (the existing `INSERT OR REPLACE` upsert already supports finalization), **or** amend doc 03.
- *Test:* kill a round before any attempt → assert an `in_progress` row exists for `job-N-round-M`.

**F16 — Prompt-variant framework plan: Definition of Done checked `[x]` with zero implementation** *(doc-integrity finding, not a code defect — the plan is plausibly deferred)*
- *Spec:* BEHAVIOR-26..43 (`docs/superpowers/plans/2026-05-04-prompt-variant-framework.md`).
- *Code:* no `prompt_registry.py`; zero production hits for `prompt_variant`/`prompt_variant_hash`; no DB column. Yet the plan's Definition of Done (`:1300-1309`) is checked `[x]` while all 63 task-step checkboxes are unchecked.
- *Minimal fix:* uncheck the DoD and mark the plan deferred/superseded (or land it). Same stale-status class as F4/F13.

---

## 3. Untested requirements

Spec behaviors with implementation but **no test exercising them** (deleting the fix would leave the suite green):

| # | Requirement | Code | Gap |
|---|---|---|---|
| U1 | AUDIT-12 / F013 fix — `best_by_metric` direction-aware ranking | `backtest_run_db.py:990-993` | Only test (`tests/test_experiment_db_sqlite_runtime.py:356-371`) uses `direction="higher"`; the `elif direction != "higher"` branch — the entire point of the F013 fix — has zero coverage. Deleting it reverts the bug silently. |
| U2 | AUDIT-06 / F004 fix — `BacktestRunDB.reload()` stale-cache invalidation | `backtest_run_db.py:847-853` | Zero `.reload()` callers in tests or production. Also violates the "test what happens when a second run uses the same directory" rule. |
| U3 | SCHEMA-14 — `baseline_checkpoints` table | not implemented (F1) | No test can exist until F1 lands; add `test_baseline_checkpoint_persists_to_sqlite` with it. |
| U4 | SCHEMA-09 — six `backtest_runs` indexes | not implemented (F2) | Add a `sqlite_master` assertion test with the migration. |
| U5 | CORE-14 — halt persistence under non-ValueError enrichment failure | `autoresearch_research.py:1674-1691` | No test injects an unexpected exception type into the enrichment path (F8's test). |

Note: U1/U2 are the sharpest — both are *documented past bug fixes* whose regression tests were never written.

---

## 4. Ambiguous requirements

Flagged per the "mark ambiguity clearly" rule; **no defect asserted** for these:

| ID | Ambiguity |
|---|---|
| SCHEMA-21 | Canonical `metrics_json` shape for merged train/validation metrics is unspecified — the F2 migration cannot be completed without deciding it. |
| SCHEMA-24/40 | Doc 01 has no failure-summary / rejection-reason column on `research_thesis_attempts`; where that data lives canonically is undefined. |
| EXPMCP-28 | Plan itself flags the proposal-level vs round-level contract as an implementer decision. |
| CORE-29 | `manifest.json` "should be a small JSON object" — "should," not "must"; field optionality undefined. |
| VALIDATOR-10 | `tools_called=None` vs empty-set gate semantics — wording suggests gate-exempt, code defaults None→empty set. |
| EXPMCP `le=50` vs `le=100` | The cleanup plan sketches `limit le=100` for `ListRoundResultsArgs` but code has `le=50` (`research_tools_schema.py:82`, test-pinned at `tests/test_research_tools_schema.py:222-225`). The verifier ruled the plan sketch non-normative — **deliberately not listed as a defect**; pick one and align the doc. |
| DESIGNS (both) | Pluggable-backends and preflight-recall specs have no stated schedule/commitment — treated as proposals throughout. |
| MISC-37 | VPS provenance plan's example path omits the SHA segment its own text implies. |
| AUDIT-28/29/30 | Open questions in the audit, not requirements. |

---

## 5. Suggested implementation order

Ordered by risk-reduction per unit of work; each item is one commit/PR per rule E.

1. **F3** — `data_loader` missing-symbol guard (small diff, kills a silent-wrong-results path on the live VPS flow).
2. **F8** — widen the halt-handler enrichment guard (few lines; protects terminal-state integrity + operator notification).
3. **F9 + U1** — `best_by_metric`: skip uncoercible metrics, and add the missing `direction='lower'` test in the same PR.
4. **F6** — `.gitignore` fix + untrack the 37 runtime artifacts (hygiene; prevents accidental data commits now).
5. **F7** — delete the duplicate `research_round_id`, repoint callers (closes the id-contract split before more callers accrete).
6. **F2** — `backtest_runs` canonical-column migration + six indexes (requires resolving SCHEMA-21 merge-shape ambiguity first).
7. **F1 + U3** — `baseline_checkpoints` table + write-path cutover from JSON.
8. **F10 → F11** — normalize at `compiler_thesis_io` (or delete the dead entry point), then remove the `"experiment_result"` Literal alias.
9. **F5** — lazy accessors for HALO/Claude timeouts; delete reload-based tests.
10. **F14** — replace the dead `PYTEST_CURRENT_TEST` guard in `trace_sdk`.
11. **U2** — `reload()` regression test.
12. **Doc-integrity sweep (F4, F13, F16, F12, F15)** — one PR correcting stale FIXED/DoD claims in `TECH_DEBT_AUDIT.md` and the prompt-variant plan, recording the 45% coverage baseline decision (or ratcheting), pinning a removal condition for the `run_experiment` shim, and documenting the round-row-at-first-outcome deviation (or fixing it).

---

## Appendix: refuted findings (13)

Recorded so they are not re-raised; each was killed by adversarial verification with a concrete reason:

- `le=50` limit cap (×3, three reviewers) — plan code block is a non-normative rename sketch; bound is test-pinned.
- Duplicate `research_round_id` as a *spec-compliance* violation — the plan only mandated the canonical helper exist (the duplication itself survives as F7 under rule B instead).
- `get_logger` bypass in `autoresearch_orchestration.py` — F008's scope is exactly 10 named files; this file isn't one, and the raw `getLogger` predates #64 (misattributed commit).
- Empty-string `research_round_id` PK collision / FK-not-enforced / unguarded `int(state.get("job"))` — preconditions unreachable via the only production callers.
- Quarantine >1% stop rule "unimplemented" — quoted text is an operating-discipline rule for data-ingestion work, with no current ingestion surface it binds to.
- Drift-checker acceptance criteria — a manual verification step for the plan executor, not a runtime requirement.
- "experiment_result enum retained" as *API-contract* violation — duplicate of F11 with a non-binding framing.

---

## Methodology integrity

- **Workflow:** 56 agents, 8 extraction agents (373 requirements) → 7 dimensional reviewers (41 raw findings) → 41 adversarial verifiers (refute-by-default; 13 refuted, several severities corrected downward) → main-loop dedup to 16 findings.
- **Independent re-verification (rule L):** F1, F2, F4, F6, F7, F8, F9, F10, F13 citations re-confirmed by direct read/grep in the main loop after the workflow completed.
- **No code was modified.** This report is the sole artifact.
- **Convergence signal:** the coverage-gate contradiction (F4) and the duplicate `research_round_id` (F7) were each flagged by three independent reviewer lenses; `baseline_checkpoints` (F1) by two — the highest-confidence findings.
