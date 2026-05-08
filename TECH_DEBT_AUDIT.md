# Tech Debt Audit — agents-auto-research
Generated: 2026-05-04
Last verified: 2026-05-04 (full code read, no memory)

---

## Verification legend

- 🔴 **TODO** — Reproduced. Bug is present in current code at the line cited.
- 🟡 **PARTIAL** — Partially fixed. Specific remaining work noted.
- ✅ **STALE** — Fixed. Line no longer exists or behavior was corrected.

---

## Executive summary

- 0 Critical, 4 High, 8 Medium, 8 Low findings (20 total; extended from first-pass 15 after full-repo read)
- Largest debt concentration: `experiment_db.py` + `autoresearch_orchestration.py` (packaging gap, cache hazard)
- `research_conductor.py` is the most under-tested module at **61%** (verified by running coverage) — below the project's own 70% gate
- Coverage gate frozen at 70% despite CLAUDE.md requiring 80%; the promised ratchet in pyproject.toml never landed
- Three duplicated UTC helpers → **two remain** (autoresearch_state.py still exports `iso8601_utc_now`; experiment_db.py now imports from persistence_utils)
- OAuth proxy constant (`10531`) and `_ensure_oauth_proxy()` duplicated across `agent_infra.py` and `research_paths.py`
- `write_json_atomic_strict` deleted ✅; `autoresearch_planning.py` local `_write_text_atomic` deleted ✅

---

## Architectural mental model

The system is an autonomous strategy-research loop for algorithmic trading. It runs in a tight cycle: plan the next experiment → run the backtest → evaluate results → route to more research or to the next candidate config.

**Five planes:**

1. **Strategy plugins** (`strategies/ema/`, `strategies/orb/`) — each strategy owns its contract, validation, signal generation, exits, and a research spec. Discovered via `strategies/__init__.py:STRATEGIES`. Adding a new strategy means adding a subdirectory and registering it there.

2. **Autoresearch loop** (`autoresearch_controller.py` → `autoresearch_planning` / `autoresearch_research` / `autoresearch_experiment` / `autoresearch_orchestration`) — the main loop in `execute_once()`. Orchestration is the state machine; planning decides next action; research generates hypotheses via LLMs; experiment shells out to the backtest CLI.

3. **LLM agent layer** (`research_conductor.py`, `agent_runners.py`, `agent_openai_calls.py`) — OpenAI Agents SDK (v0.14.2) routed through a local OAuth proxy at `127.0.0.1:10531`. Agents generate theses, compile configs, and analyze trade diagnostics.

4. **Persistence** (`experiment_db.py`, `autoresearch_state.py`, `persistence_utils.py`) — sqlite3 is the canonical store for experiment results; JSON files hold state, run queue, and ideas backlog. `persistence_utils.write_text_atomic` uses fsync + rename for crash safety.

5. **VPS deployment** (`vps_runner.py`) — paramiko SSH client that clones the git repo at an exact commit SHA on a remote VPS and launches the controller there.

The README contains only pre-commit setup. The system description, entry points, and environment requirements are undocumented.

---

## Findings

| Status | ID | Category | File:Line | Severity | Effort | Description | Recommendation |
|--------|----|----------|-----------|----------|--------|-------------|----------------|
| 🔴 TODO | F001 | Dep & config debt | `pyproject.toml` (py-modules list) | High | S | `autoresearch_orchestration` is on disk and imported by `autoresearch_controller.py` but absent from `py-modules`. Verified: not in the list. Package installs outside editable mode will silently fail at import time. → **FIXED** | Add `"autoresearch_orchestration"` to the `py-modules` list in `pyproject.toml`. |
| 🔴 TODO | F002 | Test debt | `pyproject.toml:fail_under = 70` | High | M | Coverage gate is 70%; project rule (CLAUDE.md) requires 80%. The comment says "Will ratchet to 80% once integration test fixtures land in PR 5" — PR 5 shipped long ago. Verified: `fail_under = 70` still in pyproject.toml. → **FIXED** | Raise `fail_under` to 80. Fix F003 first. |
| 🔴 TODO | F003 | Test debt | `research_conductor.py:41–42,66,72,84,93,132–196,226,228–237,259–274,311–386` | High | M | `research_conductor.py` is at **61%** — verified by running `python3 -m coverage run -m pytest`. Below the project's own 70% gate. Untested: `_run_coroutine_sync` threading path, agent tool injection block (lines 132–196), result-parsing / validation branches (lines 311–386). → **FIXED** | Add unit tests with mocked conductor. Start with threading fallback and `should_stop` / `validation_failed` branches. |
| 🔴 TODO | F004 | Architectural decay | `experiment_db.py:537–539` | High | M | `ExperimentDB._load()` returns `self._records` if not None without re-querying sqlite. Verified at line 538: `if self._records is not None: return self._records`. Any external write (VPS run, migration) makes the cache serve stale data for the process lifetime. → **FIXED** | Add a `reload()` method clearing `self._records = None`. Call it in `read_results()` / `all()` for cross-process callers, or add a runtime assertion enforcing single-process use. |
| 🟡 PARTIAL | F005 | Consistency rot | `autoresearch_state.py:20` | Medium | S | `experiment_db.py` now imports `utc_now_iso8601` from `persistence_utils` ✅ — that duplicate is gone. But `autoresearch_state.py:20` still defines `iso8601_utc_now()` and `autoresearch_orchestration.py:7` still imports from there. Two of three duplicates remain. → **FIXED** | Delete `iso8601_utc_now` from `autoresearch_state.py`. Update `autoresearch_orchestration.py` to import from `persistence_utils`. |
| 🟡 PARTIAL | F006 | Consistency rot | `agent_openai_calls.py:10`, `research_paths.py:11`, `agent_runners.py:92`, `agent_prompts.py:209` | Medium | S | Two module-level constants now exist: `_OPENAI_AGENT_MODEL = "gpt-5.5"` in `agent_openai_calls.py:10` and `_CONDUCTOR_MODEL = "gpt-5.5"` in `research_paths.py:11` — an improvement. But `agent_runners.py:92` still has `getattr(agent_def, "model", "gpt-5.5")` inline, and `agent_prompts.py:209` still has `model="gpt-5.5"` literal. No unified constant in `autoresearch_constants.py`. → **FIXED** | Add `DEFAULT_AGENT_MODEL = "gpt-5.5"` to `autoresearch_constants.py`. Unify `_OPENAI_AGENT_MODEL` and `_CONDUCTOR_MODEL` to import from there. Fix `agent_runners.py:92` and `agent_prompts.py:209`. |
| 🔴 TODO | F007 | Performance & resource hygiene | `agent_runners.py:91`, `agent_openai_calls.py:30,205` | Medium | M | `AsyncOpenAI` is instantiated inside each runner function on every call — verified at `agent_openai_calls.py:30` and `agent_runners.py:91`. No shared singleton in `agent_infra.py`. Each research round opens and closes connections independently. → **FIXED** | Create a `_get_client()` factory in `agent_infra.py` returning a singleton keyed by base URL. Import from there in all agent callers. |
| 🔴 TODO | F008 | Consistency rot | `agent_infra.py:54`, `agent_memory.py:8`, `agent_token_usage.py:6`, `compiler_operationalize.py:12`, `experiment_db.py:21`, `metrics.py:14`, `research_memory.py:11`, `research_paths.py:12`, `strategy_event_logger.py:26`, `thesis_validator.py:24` | Medium | S | 10 source files use `logging.getLogger(__name__)` directly instead of `autoresearch_logging.get_logger`. Verified by grep. These callers miss the project's structured UTC log format. → **FIXED** | Replace all `logging.getLogger(__name__)` in non-test source files with `from autoresearch_logging import get_logger; log = get_logger(__name__)`. |
| 🔴 TODO | F009 | Documentation drift | `README.md` | Medium | S | README contains only 4 lines of pre-commit setup. No architecture overview, no entry points, no env var reference. Verified: `cat README.md` shows only the pre-commit block. → **FIXED** | Add `## Overview` and `## Architecture` sections covering the five planes, entry points, and env vars. |
| 🔴 TODO | F010 | Dep & config debt | `pyproject.toml:[tool.coverage.run]source` | Medium | S | `autoresearch_orchestration` absent from `[tool.coverage.run] source`. Verified: coverage source list does not include it. Its state-machine logic is never measured. → **FIXED** | Add `"autoresearch_orchestration"` to the coverage source list. |
| 🔴 TODO | F011 | Error handling & observability | `agent_runners.py:170–171`, `research_conductor.py:229–231` | Low | S | Both sites have `except Exception: result_text = ""` with zero logging. Verified at both lines. If the SDK changes its output API, all agents silently return empty results with no observable signal. → **FIXED** | Replace with `except Exception as exc: log.warning("final_output_as failed: %s", exc); result_text = ""`. |
| ✅ STALE | F012 | Architectural decay | `persistence_utils.py` | Low | S | `write_json_atomic_strict` was deleted by the simplify pass. `persistence_utils.py` now has only `write_json_atomic`. Both call sites updated. Fixed. | — |
| 🔴 TODO | F013 | Architectural decay | `experiment_db.py:651` | Low | S | `if val > best_val: best = r` at line 651 — hardcoded "higher is better" regardless of `best_direction`. Verified: `best_direction()` is read in `evaluate_metric()` (line 199) but not in `best_by_metric()`. Any caller on a "lower is better" metric (drawdown) gets the worst result. → **FIXED** | `direction = self.best_direction(); if (val > best_val if direction == "higher" else val < best_val): best = r` |
| 🔴 TODO | F014 | Consistency rot | `autoresearch_orchestration.py` (filename) | Low | S | `vps_runner.py` does git-based deployment but was not renamed. Minor contributor confusion. → **FIXED** | Document in README; no code change needed. |
| 🔴 TODO | F015 | Test debt | `autoresearch_orchestration.py` | Low | M | No test file exists for `autoresearch_orchestration.py`. Verified: `ls tests/ | grep orchestration` returns nothing. The key mismatch guard in `try_resume_halted_thesis` is untested. → **FIXED** | Add `tests/test_autoresearch_orchestration.py` covering `try_resume_halted_thesis`, `apply_forced_baseline_rerun`, `resolve_next_action`. |

---

## Top 5 — if you fix nothing else, fix these

### 1. F001 — Add `autoresearch_orchestration` to `pyproject.toml` — **FIXED**
Added to both `py-modules` list and `[tool.coverage.run] source`. Module is now visible to packaging tools and measured by coverage.

### 2. F002 — Raise coverage gate to 80%
The promise was made in the comment; the work to get there is already partly done (84% total across tracked modules).

```diff
# pyproject.toml
-fail_under = 70
+fail_under = 80
```

Fix F003 first: `research_conductor.py` at 61% is what's blocking this.

### 3. F003 — Cover `research_conductor.py` critical paths
Untested lines confirmed: 41–42, 66, 72, 84, 93, 132–196, 226, 228–237, 259–274, 311–386.
The threading fallback (testable by patching `asyncio.get_running_loop`) and `should_stop` / `validation_failed` branches (testable with mock conductor responses) are the highest-value targets.

### 4. F006 — Centralize `gpt-5.5` into a constant — **FIXED**
`DEFAULT_AGENT_MODEL = "gpt-5.5"` added to `autoresearch_constants.py`. All four sites (`_OPENAI_AGENT_MODEL`, `_CONDUCTOR_MODEL`, `agent_runners.py:96` fallback, `agent_prompts.py:211`) unified to import from there.

### 5. F004 — Document or enforce `ExperimentDB` single-process constraint — **FIXED**
`ExperimentDB.reload()` added. Clears `self._records = None` so the next `_load()` re-reads from SQLite.

---

## Quick wins (updated status)

- [x] **F001**: Add `"autoresearch_orchestration"` to `py-modules` in `pyproject.toml` — 1 line — FIXED
- [x] **F005**: `iso8601_utc_now` deleted from `autoresearch_state.py`; `autoresearch_orchestration.py` imports from `persistence_utils` ✅ — FIXED
- [x] **F010**: Add `"autoresearch_orchestration"` to `[tool.coverage.run] source` — 1 line — FIXED
- [x] **F012**: `write_json_atomic_strict` deleted ✅ — DONE
- [x] **F013**: Fix `ExperimentDB.best_by_metric` direction bug at `experiment_db.py:651` — 3 lines changed — FIXED
- [x] **F011**: Add `log.warning(...)` before silencing `final_output_as` exception at `agent_runners.py:170` and `research_conductor.py:230` — 2 lines per site — FIXED
- [x] **F016**: Remove `_ensure_oauth_proxy` and `_OAUTH_PROXY_PORT` from `research_paths.py`; import from `agent_infra` — FIXED
- [x] **F017**: Local `_write_text_atomic` removed from `autoresearch_planning.py` ✅ — DONE
- [x] **F018**: Remove `_get_orb_defaults` from `compiler_pipeline.__all__` at line 31 — 2 lines — FIXED

---

## New findings (Phase 2 — full-repo read)

| Status | ID | Category | File:Line | Severity | Effort | Description | Recommendation |
|--------|----|----------|-----------|----------|--------|-------------|----------------|
| 🔴 TODO | F016 | Consistency rot | `agent_infra.py:25–39`, `research_paths.py:9–23` | Medium | S | `_OAUTH_PROXY_PORT = 10531`, `_OAUTH_PROXY_URL`, and `_ensure_oauth_proxy()` defined independently in both files. Verified: both define port 10531 and the probe loop. A port change requires two synchronized edits. → **FIXED** | Consolidate into `agent_infra.py`. Have `research_paths.py` import from there. |
| ✅ STALE | F017 | Consistency rot | `autoresearch_planning.py` | Low | S | Local `_write_text_atomic` removed by simplify pass. `autoresearch_planning.py` now imports `from persistence_utils import write_text_atomic as _write_text_atomic`. Fixed. | — |
| 🔴 TODO | F018 | Architectural decay | `compiler_pipeline.py:9,31` | Low | S | `compiler_pipeline.py` re-exports `_get_orb_defaults` in `__all__` at line 31. Verified: `"_get_orb_defaults"` still in `__all__`. ORB internals leaking through the compiler namespace. → **FIXED** | Remove `_get_orb_defaults` from `compiler_pipeline.__all__`. |
| 🔴 TODO | F019 | Dep & config debt | `backtest/runner.py:32` | Low | S | `--output-dir` defaults to `"/tmp"`. Verified at line 32: `default="/tmp"`. On VPS with tmpfs `/tmp`, large backtests could exhaust capacity. → **FIXED** | Change default to `"."` or read from `AUTORESEARCH_OUTPUT_DIR` env var. |
| 🔴 TODO | F020 | Performance & resource hygiene | `trace_sdk.py:415` | Low | M | `_initialize_tracing()` called at module level at line 415. Verified: no `PYTEST_CURRENT_TEST` guard. Mutates global OTel state for all 445 tests. → **FIXED** | Add `if os.getenv("PYTEST_CURRENT_TEST"): return` guard inside `_initialize_tracing()`. |

---

## Things that look bad but are actually fine

**The 28 `except Exception` sites.** Most are correct. The majority guard LLM agent invocations, which can fail with arbitrary SDK exceptions; the pattern is fail-open with a structured error return. The ones around stream cleanup (`agent_runners.py:119`) are also correct — stream close failures must never propagate. The two sites flagged as F011 are the only ones that silently discard results without any log line.

**`api_key="unused"` in `AsyncOpenAI` construction.** Not a security smell — auth flows through the local OAuth proxy at port 10531, and the `api_key` parameter is required by the client constructor but deliberately unused. The literal string `"unused"` is the clearest possible documentation of this.

**`from trace_sdk import ...` inside `main()` in `autoresearch_controller.py:619`.** This import-inside-function pattern looks like a mistake, but it's intentional: `trace_sdk` initializes global module-level state (the OTel tracer provider) at import time. Moving it to top-level would trigger that initialization for every test that imports `autoresearch_controller`, which is undesirable.

**`ExperimentDB._save()` calling `_load()` after `import_entries()` sets `_records`.** The flow looks like it might double-write, but the ordering is correct: `import_entries` sets `self._records = records` _before_ calling `_save()`, so `_save()` reads the already-set cache and persists it. Not a bug, just reads confusingly.

**The `_localize_remote_result_output` tempdir not cleaned on success.** This is intentional: the caller receives a RESULT_JSON path pointing _into_ the tempdir. Cleaning the dir would invalidate the path before it can be consumed by the experiment pipeline downstream. The files are small (result JSON + trade CSVs) and `/tmp` is cleaned by the OS between runs.

---

## Open questions for the maintainer

1. `autoresearch_orchestration.py` isn't in `pyproject.toml` — was this omitted deliberately (e.g., always deployed in editable mode) or is it an oversight?

2. The coverage gate comment says "ratchet to 80% once integration test fixtures land in PR 5." PR 5 was `audit PR 5/5` (commit `58cbbd0`). Should the gate be ratcheted now, or is there a known gap that still blocks 80%?

3. `backtest_5ema.py` and `backtest_orb_v2.py` appear in `pyproject.toml:py-modules` but they look like top-level scripts (not importable modules). Are they used as entry points, or are they legacy artifacts that should be moved under `strategies/ema/` and `strategies/orb/`?

4. `ExperimentDB` is used by both the local controller and (when the VPS syncs results back) potentially by local analysis tools. Is there a scenario where two processes share the same `.db` file? If so, the in-memory cache is a real hazard.

5. The `AUTORESEARCH_VPS_DIR` denylist in `vps_runner.py` contains `/srv` — but the `.env.example` recommends `/srv/autoresearch-YYYY-MM-DD` as the default. Is the denylist entry for the bare `/srv` only, or is this a sign that `/srv` subdirectories were intended to be blocked too?

6. `thesis_validator.py` uses `logging.getLogger(__name__)` directly instead of `get_logger` from `autoresearch_logging`. Is this intentional (thesis validation runs outside the autoresearch logging context) or a gap?

7. `experiment_evaluator.py` silently returns `False` (disqualifier not triggered) for conditions it can't parse mechanically. Is there a roadmap for structured disqualifier DSL, or is this "LLM checks it next round" the intended long-term design?

8. `numba_kernels.py` has no tests that exercise the fallback Python path (when `numba` is not installed). Are the ORB signal vectorized paths verified to produce identical results on both paths?
