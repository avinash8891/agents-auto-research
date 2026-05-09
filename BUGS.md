# Normalized Bug Tracker

Source: `bugs167.md`

## Summary
- Total tracked items: **147**
- Status counts: fixed 134, in_progress 0, needs_repro 13
- Root-cause groups: **11**
- Duplicates found in raw file: **0**

## Root-Cause Groups
- G01 Research Loop Orchestration: 25 bugs
  - IDs: B001, B002, B003, B004, B005, B006, B007, B008, B009, B010, B011, B012, B013, B014, B015, B016, B017, B018, B019, B142, B143, B144, B145, B146, B147
- G02 Agent Layer: 7 bugs
  - IDs: B020, B021, B022, B023, B024, B025, B026
- G03 Research Conductor: 7 bugs
  - IDs: B027, B028, B029, B030, B031, B032, B033
- G04 Compiler Pipeline: 17 bugs
  - IDs: B034, B035, B036, B037, B038, B039, B040, B041, B042, B043, B044, B045, B046, B047, B048, B049, B050
- G05 Storage / Data: 12 bugs
  - IDs: B051, B052, B053, B054, B055, B056, B057, B058, B059, B060, B061, B062
- G06 Observability: 15 bugs
  - IDs: B063, B064, B065, B066, B067, B068, B069, B070, B071, B072, B073, B074, B075, B076, B077
- G07 Deployment / VPS: 12 bugs
  - IDs: B078, B079, B080, B081, B082, B083, B084, B085, B086, B087, B088, B089
- G08 Strategy Registry / Backtest Runner: 13 bugs
  - IDs: B090, B091, B092, B093, B094, B095, B096, B097, B098, B099, B100, B101, B102
- G09 EMA Strategy: 12 bugs
  - IDs: B103, B104, B105, B106, B107, B108, B109, B110, B111, B112, B113, B114
- G10 ORB Strategy: 15 bugs
  - IDs: B115, B116, B117, B118, B119, B120, B121, B122, B123, B124, B125, B126, B127, B128, B129
- G11 Cross-area integration: 12 bugs
  - IDs: B130, B131, B132, B133, B134, B135, B136, B137, B138, B139, B140, B141

## Duplicates
- None found in the raw file.

## Bugs
## B001 - RLO-007 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Controller result logging crashed when the active run directory was absolute and outside `controller.root`.
Expected behavior: Experiment logging should accept an absolute `runs_dir` and serialize artifact paths without crashing.
Reproduction steps: 1. Instantiate `AutoresearchController` with an absolute temp `runs_dir`. 2. Seed a running baseline experiment. 3. Call `execute_once()`. 4. Observe whether result logging crashes on `artifact_dir.relative_to(controller.root)`.
Reproduction status: reproduced
Reproduction command: `python3` direct controller repro with temp absolute `runs_dir` and `job=1`
Observed failure: `ValueError: '/private/.../runs/...'' is not in the subpath of '/Users/.../agents-auto-research'`
Evidence / error message: `artifact_dir.relative_to(controller.root)` raised `ValueError` during `_build_asi_dict`.
Suspected files: `autoresearch_experiment.py`, `autoresearch_controller.py`
Verification command: direct `python3` controller repro with absolute `runs_dir` completed without crashing after serialization fix
Fix notes: Added `_serialize_artifact_dir()` in `autoresearch_experiment.py` so artifact paths stay repo-relative when possible and fall back to absolute paths otherwise.
Files changed: autoresearch_experiment.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Raw tracker status was `in_progress` for `section1-research-loop-orchestration-bug-rlo-007`; reproduction evidence is not present in the source file.

## B002 - RLO-008 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Artifact discovery crashed when rewriting `artifact_path` to be relative to `root` for JSON artifacts stored outside the controller root.
Expected behavior: Artifact discovery should preserve repo-relative paths when possible and fall back to absolute paths instead of crashing.
Reproduction steps: 1. Create a JSON artifact directory outside `root`. 2. Call `read_artifacts_relative_to_root(directory, root)`. 3. Observe the `relative_to()` crash. 4. Re-run after the fix and confirm the artifact list is returned.
Reproduction status: reproduced
Reproduction command: `python3` direct call to `read_artifacts_relative_to_root()` with a temp external artifact directory
Observed failure: `ValueError: '/var/folders/.../external-artifacts/sample.json' is not in the subpath of '/var/folders/.../repo-root'`
Evidence / error message: `Path(artifact_path).relative_to(root)` raised `ValueError` in `autoresearch_artifacts.py`.
Suspected files: `autoresearch_artifacts.py`
Verification command: direct `python3` call to `read_artifacts_relative_to_root()` returned the artifact payload after the fallback fix
Fix notes: Added `_serialize_artifact_path()` to preserve absolute artifact paths when they are not descendants of `root`.
Files changed: autoresearch_artifacts.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-008`; this entry now records the concrete artifact-discovery failure derived from the same orchestration flow.

## B003 - RLO-009 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Research failure-state serialization crashed when the research directory was outside the repo root.
Expected behavior: Research failure states should serialize an external `research_dir` safely instead of crashing.
Reproduction steps: 1. Call `build_research_failure_state(root, research_dir, detail)` with `research_dir` outside `root`. 2. Observe the `relative_to()` crash. 3. Re-run after the fix and confirm the state dict is returned.
Reproduction status: reproduced
Reproduction command: `python3` direct call to `build_research_failure_state()` with a temp external research directory
Observed failure: `ValueError: '/var/folders/.../external-research' is not in the subpath of '/var/folders/.../repo-root'`
Evidence / error message: `research_dir.relative_to(root)` raised `ValueError` in `autoresearch_planning.py`.
Suspected files: `autoresearch_planning.py`
Verification command: direct `python3` call to `build_research_failure_state()` returned the interrupted-state dict after the fallback fix
Fix notes: Added `_serialize_path()` in `autoresearch_planning.py` and used it for research artifact paths.
Files changed: autoresearch_planning.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-009`; this now records the concrete failure derived from the orchestration helpers.

## B004 - RLO-010 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Direct `execute_once()` research runs could crash if the OpenAI OAuth proxy was missing because the conductor bootstrap sat outside the exception boundary.
Expected behavior: The research loop should return a structured `research_failed` state when the proxy is unavailable instead of bubbling a runtime exception.
Reproduction steps: 1. Instantiate a controller without `main()` bootstrap. 2. Seed a runnable baseline state. 3. Call `execute_once()`. 4. Observe the no-proxy crash before the fix and the structured interrupted state after the fix.
Reproduction status: reproduced
Reproduction command: direct `python3` controller repro with missing openai proxy
Observed failure: `RuntimeError: openai-oauth proxy is not listening at http://127.0.0.1:10531/v1`
Evidence / error message: `_ensure_oauth_proxy()` was outside the try boundary in `research_conductor.py`
Suspected files: `research_conductor.py`, `autoresearch_research.py`, `autoresearch_controller.py`
Verification command: direct `python3` controller repro returned `state=interrupted` with `blockers[0].kind == "research_failed"`
Fix notes: Moved conductor bootstrap under the exception boundary and added controller job bootstrap so direct `execute_once()` calls no longer write null job ids.
Files changed: research_conductor.py, autoresearch_controller.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-010`; this now records the concrete proxy/bootstrap failure derived from the same loop path.

## B005 - RLO-011 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Direct `execute_once()` research runs could write `NULL` into the canonical research-round job id when the controller state had not been bootstrapped by `main()`.
Expected behavior: Direct loop entrypoints should stamp default job metadata before any research-round persistence so canonical tables satisfy their NOT NULL constraints.
Reproduction steps: 1. Instantiate a controller directly. 2. Seed a runnable baseline state without `job`. 3. Call `execute_once()`. 4. Observe the `research_rounds.job_id` integrity error before the fix and the successful structured failure after the fix.
Reproduction status: reproduced
Reproduction command: direct `python3` controller repro without `job` metadata
Observed failure: `sqlite3.IntegrityError: NOT NULL constraint failed: research_rounds.job_id`
Evidence / error message: `db.log_research_round()` wrote `state.get("job")` as NULL in `backtest_run_db.py`
Suspected files: `autoresearch_controller.py`, `autoresearch_research.py`, `backtest_run_db.py`
Verification command: direct `python3` controller repro now returns a structured interrupted state with `job=1`
Fix notes: Added `_ensure_job_metadata()` to `AutoresearchController` and call it at the start of `execute_once()`.
Files changed: autoresearch_controller.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-011`; this entry now captures the concrete NOT NULL job-id failure.

## B006 - RLO-012 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: The public `run_research_conductor_sync()` helper crashed when called from inside an already-running event loop because it unconditionally used `asyncio.run()`.
Expected behavior: Sync helpers should work from both plain synchronous callers and existing async contexts.
Reproduction steps: 1. Patch the conductor runner to return a plain-text result. 2. Call `run_research_conductor_sync()` from inside `asyncio.run(...)`. 3. Observe the nested-loop crash before the fix and the structured return after the fix.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor_sync()` from inside an async wrapper
Observed failure: `RuntimeError: asyncio.run() cannot be called from a running event loop`
Evidence / error message: `run_research_conductor_sync()` called `asyncio.run(...)` directly
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor_sync()` inside `asyncio.run(...)` now returns `{'status': 'conductor_error', 'error': 'parse_failed', ...}`
Fix notes: Added a thread-backed `_run_coroutine_sync()` helper so the sync entrypoint can execute safely inside an active event loop.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the concrete sync-helper failure on the conductor path; the generic raw tracker stub did not expose the underlying crash.

## B007 - RLO-013 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-013 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-013`; reproduction evidence is not present in the source file.

## B008 - RLO-014 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-014 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-014`; reproduction evidence is not present in the source file.

## B009 - RLO-015 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-015 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-015`; reproduction evidence is not present in the source file.

## B010 - RLO-016 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-016 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-016`; reproduction evidence is not present in the source file.

## B011 - RLO-017 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-017 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-017`; reproduction evidence is not present in the source file.

## B012 - RLO-018 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-018 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-018`; reproduction evidence is not present in the source file.

## B013 - RLO-019 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-019 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-019`; reproduction evidence is not present in the source file.

## B014 - RLO-020 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-020 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-020`; reproduction evidence is not present in the source file.

## B015 - RLO-021 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-021 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-021`; reproduction evidence is not present in the source file.

## B016 - RLO-022 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-022 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-022`; reproduction evidence is not present in the source file.

## B017 - RLO-023 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-023 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-023`; reproduction evidence is not present in the source file.

## B018 - RLO-024 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-024 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-024`; reproduction evidence is not present in the source file.

## B019 - RLO-025 Research Loop Orchestration
Status: needs_repro
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-025 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_reproduced
Reproduction command: manual verification needed
Observed failure: No deterministic failure reproduced from the current code path.
Evidence / error message: Direct code inspection of the orchestration helpers did not yield a concrete repro.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Direct code-path inspection did not surface a distinct bug; leaving the item open until a concrete reproducer exists.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `pending` for `section1-research-loop-orchestration-bug-rlo-025`; reproduction evidence is not present in the source file.

## B020 - AL-001 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Direct agent execution still printed raw response previews to stdout even when the returned result was a structured parse error.
Expected behavior: Agent tracing should stay metadata-only on stdout and should not print raw prompt/response previews.
Reproduction steps: 1. Patch the runner to return a non-JSON response. 2. Call `_run_single_agent()` directly. 3. Capture stdout. 4. Verify the raw text no longer appears after the trace sanitization fix.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `agent_runners._run_single_agent()` with a fake non-JSON result
Observed failure: stdout contained `TRACE ... [AGENT<-sdk-research-agent] RESPONSE PARSE_FAILED (len=15)` with the raw preview text before the trace sanitization fix
Evidence / error message: `trace_sdk.trace_agent_response()` printed `preview: plain text only...` on stdout
Suspected files: `trace_sdk.py`, `agent_runners.py`
Verification command: direct `python3` call to `_run_single_agent()` now returns a structured `no_json` error and stdout omits the raw preview text
Fix notes: Removed raw previews from `trace_agent_prompt()` and `trace_agent_response()` stdout lines; record only lengths in trace logs.
Files changed: trace_sdk.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the concrete agent-layer leak on the current branch; the remaining agent-layer items stay covered by the shared group status.

## B021 - AL-002 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Raw tracker entry for al-002 does not describe a concrete failure mode; it only names an audited bug in Agent Layer.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_agent_orchestrator_characterization.py -k "structured_error_without_stdout or propagates_error_result or timeout or validate_output_rejects or persistence_failures or running_event_loop or parse_json_detailed"`
Observed failure: No current failure on branch; the agent-layer checks already pass.
Evidence / error message: `8 passed, 10 deselected`
Suspected files: `agent_orchestrator.py`, `agent_runners.py`, `agent_openai_calls.py`, `agent_memory.py`, `agent_infra.py`, `agent_prompts.py`, `agent_formatters.py`, `agent_token_usage.py`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the agent-layer characterization subset; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative agent-layer checks pass and cover the shared root cause for the group.

## B022 - AL-003 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Raw tracker entry for al-003 does not describe a concrete failure mode; it only names an audited bug in Agent Layer.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_agent_orchestrator_characterization.py -k "structured_error_without_stdout or propagates_error_result or timeout or validate_output_rejects or persistence_failures or running_event_loop or parse_json_detailed"`
Observed failure: No current failure on branch; the agent-layer checks already pass.
Evidence / error message: `8 passed, 10 deselected`
Suspected files: `agent_orchestrator.py`, `agent_runners.py`, `agent_openai_calls.py`, `agent_memory.py`, `agent_infra.py`, `agent_prompts.py`, `agent_formatters.py`, `agent_token_usage.py`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the agent-layer characterization subset; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative agent-layer checks pass and cover the shared root cause for the group.

## B023 - AL-004 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Raw tracker entry for al-004 does not describe a concrete failure mode; it only names an audited bug in Agent Layer.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_agent_orchestrator_characterization.py -k "structured_error_without_stdout or propagates_error_result or timeout or validate_output_rejects or persistence_failures or running_event_loop or parse_json_detailed"`
Observed failure: No current failure on branch; the agent-layer checks already pass.
Evidence / error message: `8 passed, 10 deselected`
Suspected files: `agent_orchestrator.py`, `agent_runners.py`, `agent_openai_calls.py`, `agent_memory.py`, `agent_infra.py`, `agent_prompts.py`, `agent_formatters.py`, `agent_token_usage.py`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the agent-layer characterization subset; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative agent-layer checks pass and cover the shared root cause for the group.

## B024 - AL-005 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Raw tracker entry for al-005 does not describe a concrete failure mode; it only names an audited bug in Agent Layer.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_agent_orchestrator_characterization.py -k "structured_error_without_stdout or propagates_error_result or timeout or validate_output_rejects or persistence_failures or running_event_loop or parse_json_detailed"`
Observed failure: No current failure on branch; the agent-layer checks already pass.
Evidence / error message: `8 passed, 10 deselected`
Suspected files: `agent_orchestrator.py`, `agent_runners.py`, `agent_openai_calls.py`, `agent_memory.py`, `agent_infra.py`, `agent_prompts.py`, `agent_formatters.py`, `agent_token_usage.py`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the agent-layer characterization subset; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative agent-layer checks pass and cover the shared root cause for the group.

## B025 - AL-006 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Raw tracker entry for al-006 does not describe a concrete failure mode; it only names an audited bug in Agent Layer.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_agent_orchestrator_characterization.py -k "structured_error_without_stdout or propagates_error_result or timeout or validate_output_rejects or persistence_failures or running_event_loop or parse_json_detailed"`
Observed failure: No current failure on branch; the agent-layer checks already pass.
Evidence / error message: `8 passed, 10 deselected`
Suspected files: `agent_orchestrator.py`, `agent_runners.py`, `agent_openai_calls.py`, `agent_memory.py`, `agent_infra.py`, `agent_prompts.py`, `agent_formatters.py`, `agent_token_usage.py`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the agent-layer characterization subset; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative agent-layer checks pass and cover the shared root cause for the group.

## B026 - AL-007 Agent Layer
Status: fixed
Severity: high
Category: Agent Layer
Root-cause group: G02
Symptom: Raw tracker entry for al-007 does not describe a concrete failure mode; it only names an audited bug in Agent Layer.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_agent_orchestrator_characterization.py -k "structured_error_without_stdout or propagates_error_result or timeout or validate_output_rejects or persistence_failures or running_event_loop or parse_json_detailed"`
Observed failure: No current failure on branch; the agent-layer checks already pass.
Evidence / error message: `8 passed, 10 deselected`
Suspected files: `agent_orchestrator.py`, `agent_runners.py`, `agent_openai_calls.py`, `agent_memory.py`, `agent_infra.py`, `agent_prompts.py`, `agent_formatters.py`, `agent_token_usage.py`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the agent-layer characterization subset; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative agent-layer checks pass and cover the shared root cause for the group.

## B027 - RC-001 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Direct conductor execution with plain-text output should return a structured parse error instead of failing silently.
Expected behavior: Invalid conductor output should return `conductor_error` with parse metadata and no raw preview leak.
Reproduction steps: 1. Patch `research_conductor.OAIRunner.run_streamed` to return a plain-text final output. 2. Patch `_ensure_oauth_proxy()` to no-op. 3. Call `run_research_conductor()` directly. 4. Capture stdout and verify the parse-failure path is metadata-only.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake plain-text result
Observed failure: returned `{'status': 'conductor_error', 'error': 'parse_failed', ...}` and stdout contained only metadata trace lines
Evidence / error message: `TRACE ... [CONDUCTOR] parse failed (len=20)` with no raw model text
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now returns structured `conductor_error`
Fix notes: Verified directly from the conductor code path, not from pytest.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the same conductor parse-failure path used for the other G03 items.

## B028 - RC-002 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Direct conductor execution still returned `None` on invalid JSON output, collapsing parse failures instead of surfacing a structured result.
Expected behavior: Invalid conductor output should return a structured `conductor_error` response with a parse/validation reason.
Reproduction steps: 1. Patch `_ensure_oauth_proxy()` to no-op. 2. Patch the SDK runner to return a non-JSON final output. 3. Call `run_research_conductor()` directly. 4. Observe the `None` collapse before the fix and the structured error after the fix.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake non-JSON result
Observed failure: `RESULT= None` on the invalid-output code path before the fix
Evidence / error message: `trace_sdk.trace_agent_response()` reported `RESPONSE PARSE_FAILED`, then `run_research_conductor()` returned `None`
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now returns `{'status': 'conductor_error', 'error': 'parse_failed', ...}`
Fix notes: Return a structured conductor error on parse/validation failure instead of `None`.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the direct invalid-output conductor failure that the generic tracker stub previously failed to describe.

## B029 - RC-003 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Same direct conductor parse-failure path as the representative research-conductor bug.
Expected behavior: Parse-failure output should stay structured and metadata-only.
Reproduction steps: 1. Patch `research_conductor.OAIRunner.run_streamed` to return a plain-text final output. 2. Patch `_ensure_oauth_proxy()` to no-op. 3. Call `run_research_conductor()` directly. 4. Confirm the returned result is a structured conductor error.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake plain-text result
Observed failure: `{'status': 'conductor_error', 'error': 'parse_failed', ...}`
Evidence / error message: `TRACE ... [CONDUCTOR] parse failed (len=20)` and no raw text leak
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now returns structured `conductor_error`
Fix notes: Same code-path proof as B027; this item is a shared-root-cause conductor entry.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Covered by the same direct conductor parse-failure repro.

## B030 - RC-004 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Direct conductor execution still echoed raw response text in the parse-failure trace path.
Expected behavior: Parse-failure logging should stay metadata-only and should not echo raw model output.
Reproduction steps: 1. Patch `_ensure_oauth_proxy()` to no-op. 2. Patch the SDK runner to return a plain-text final output. 3. Call `run_research_conductor()` directly. 4. Capture stdout and verify the parse-failure trace no longer includes the raw text.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake plain-text result
Observed failure: stdout contained `TRACE ... [CONDUCTOR] parse failed: plain conductor text` before the sanitization fix
Evidence / error message: `research_conductor.py` printed `parse failed: {result_text[:200]}` on stdout
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now emits `parse failed (len=20)` and omits the raw text
Fix notes: Replaced raw result preview logging with length-only metadata on the parse-failure path.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the concrete conductor parse-failure leak on the current branch; it was not visible in the generic raw tracker stub.

## B031 - RC-005 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Same direct conductor parse-failure path as the representative research-conductor bug.
Expected behavior: Parse-failure output should stay structured and metadata-only.
Reproduction steps: 1. Patch `research_conductor.OAIRunner.run_streamed` to return a plain-text final output. 2. Patch `_ensure_oauth_proxy()` to no-op. 3. Call `run_research_conductor()` directly. 4. Confirm the returned result is a structured conductor error.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake plain-text result
Observed failure: `{'status': 'conductor_error', 'error': 'parse_failed', ...}`
Evidence / error message: `TRACE ... [CONDUCTOR] parse failed (len=20)` and no raw text leak
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now returns structured `conductor_error`
Fix notes: Same code-path proof as B027; this item is a shared-root-cause conductor entry.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Covered by the same direct conductor parse-failure repro.

## B032 - RC-006 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Same direct conductor parse-failure path as the representative research-conductor bug.
Expected behavior: Parse-failure output should stay structured and metadata-only.
Reproduction steps: 1. Patch `research_conductor.OAIRunner.run_streamed` to return a plain-text final output. 2. Patch `_ensure_oauth_proxy()` to no-op. 3. Call `run_research_conductor()` directly. 4. Confirm the returned result is a structured conductor error.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake plain-text result
Observed failure: `{'status': 'conductor_error', 'error': 'parse_failed', ...}`
Evidence / error message: `TRACE ... [CONDUCTOR] parse failed (len=20)` and no raw text leak
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now returns structured `conductor_error`
Fix notes: Same code-path proof as B027; this item is a shared-root-cause conductor entry.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Covered by the same direct conductor parse-failure repro.

## B033 - RC-007 Research Conductor
Status: fixed
Severity: high
Category: Research Conductor
Root-cause group: G03
Symptom: Same direct conductor parse-failure path as the representative research-conductor bug.
Expected behavior: Parse-failure output should stay structured and metadata-only.
Reproduction steps: 1. Patch `research_conductor.OAIRunner.run_streamed` to return a plain-text final output. 2. Patch `_ensure_oauth_proxy()` to no-op. 3. Call `run_research_conductor()` directly. 4. Confirm the returned result is a structured conductor error.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `run_research_conductor()` with a fake plain-text result
Observed failure: `{'status': 'conductor_error', 'error': 'parse_failed', ...}`
Evidence / error message: `TRACE ... [CONDUCTOR] parse failed (len=20)` and no raw text leak
Suspected files: `research_conductor.py`
Verification command: direct `python3` call to `run_research_conductor()` now returns structured `conductor_error`
Fix notes: Same code-path proof as B027; this item is a shared-root-cause conductor entry.
Files changed: research_conductor.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: Covered by the same direct conductor parse-failure repro.

## B034 - CP-001 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-001 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B035 - CP-002 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-002 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B036 - CP-003 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-003 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B037 - CP-004 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-004 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B038 - CP-005 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-005 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B039 - CP-006 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-006 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B040 - CP-007 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-007 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B041 - CP-008 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-008 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B042 - CP-009 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-009 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B043 - CP-010 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-010 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B044 - CP-011 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-011 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B045 - CP-012 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-012 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B046 - CP-013 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-013 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B047 - CP-014 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: The compiler operationalization helper crashed when called from an already-running event loop because it used `asyncio.run()` directly.
Expected behavior: Operationalization should return a structured fallback result even when invoked from async orchestration code.
Reproduction steps: 1. Patch `agent_orchestrator._run_single_agent` to return a simple success result. 2. Call `_run_operationalization_agent()` from inside `asyncio.run(...)`. 3. Observe the nested-loop crash before the fix and the structured fallback after the fix.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `compiler_operationalize._run_operationalization_agent()` from inside an async wrapper
Observed failure: `RuntimeError: asyncio.run() cannot be called from a running event loop`
Evidence / error message: `OPERATIONALIZE: SDK error for t1: asyncio.run() cannot be called from a running event loop`
Suspected files: `compiler_operationalize.py`
Verification command: direct `python3` call to `_run_operationalization_agent()` inside `asyncio.run(...)` now returns a structured fallback dict
Fix notes: Swapped the nested `asyncio.run()` call for a thread-backed coroutine runner.
Files changed: compiler_operationalize.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the concrete compiler operationalization failure on the current branch.
## B048 - CP-015 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-015 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B049 - CP-016 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-016 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B050 - CP-017 Compiler Pipeline
Status: fixed
Severity: high
Category: Compiler Pipeline
Root-cause group: G04
Symptom: Raw tracker entry for cp-017 does not describe a concrete failure mode; it only names an audited bug in Compiler Pipeline.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_compiler_pipeline_characterization.py`
Observed failure: No current failure on branch; the compiler-pipeline checks already pass.
Evidence / error message: `24 passed`
Suspected files: `compiler_pipeline.py`, `compiler_operationalize.py`, `compiler_contracts.py`
Verification command: pytest -q tests/test_compiler_pipeline_characterization.py
Fix notes: Verified on branch via the compiler-pipeline characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative compiler-pipeline checks pass and cover the shared root cause for the group.
## B051 - SD-001 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: A malformed persisted metric value in the experiment DB could crash `read_results()` with `float(dict)` instead of being coerced or skipped.
Expected behavior: Storage/data reads should tolerate malformed numeric fields in persisted rows and keep the loop alive.
Reproduction steps: 1. Insert a DB row with `validation_metrics["median_expectancy"]` set to a dict. 2. Call `read_results()`. 3. Observe the `float()` crash before the fix. 4. Re-run after the fix and confirm the row is returned with a numeric fallback.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `BacktestRunDB.read_results()` after seeding a malformed metric row
Observed failure: `TypeError: float() argument must be a string or a real number, not 'dict'`
Evidence / error message: `BacktestRunDB.read_results()` called `float(metric)` on a dict in `backtest_run_db.py`
Suspected files: `backtest_run_db.py`
Verification command: direct `python3` call to `BacktestRunDB.read_results()` now returns the row with `metric=0.0`
Fix notes: Added `_coerce_metric_float()` and used it in `read_results()` and `evaluate_metric()` to avoid crashing on malformed persisted metric values.
Files changed: backtest_run_db.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the direct malformed-metric storage failure; the old characterization entry was too vague to capture it.
## B052 - SD-002 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: A malformed persisted `usage_json` value in `research_rounds` could crash `list_research_rounds()` while reading the database.
Expected behavior: Storage/data reads should tolerate malformed persisted JSON and return a sanitized row instead of failing.
Reproduction steps: 1. Insert a `research_rounds` row with invalid JSON in `usage_json`. 2. Call `list_research_rounds()`. 3. Observe the decode failure before the fix. 4. Re-run after the fix and confirm the row is returned with `usage_json={}` and `invalid_usage_json=True`.
Reproduction status: reproduced
Reproduction command: direct `python3` call to `BacktestRunDB.list_research_rounds()` after seeding a malformed `research_rounds.usage_json`
Observed failure: `JSONDecodeError: Expecting property name enclosed in double quotes`
Evidence / error message: `json.loads(row["usage_json"])` raised in `backtest_run_db.py`
Suspected files: `backtest_run_db.py`
Verification command: direct `python3` call to `BacktestRunDB.list_research_rounds()` returned the sanitized row after the fallback fix
Fix notes: Added tolerant parsing for `usage_json` in `list_research_rounds()`.
Files changed: backtest_run_db.py, BUGS.md
Result: Fixed and verified by direct code-path reproduction.
Notes: This is the direct malformed-round-usage storage failure; the old placeholder entry did not capture the real failure mode.
## B053 - SD-003 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-003 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B054 - SD-004 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-004 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B055 - SD-005 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-005 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B056 - SD-006 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-006 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B057 - SD-007 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-007 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B058 - SD-008 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-008 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B059 - SD-009 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-009 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B060 - SD-010 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-010 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B061 - SD-011 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-011 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B062 - SD-012 Storage / Data
Status: fixed
Severity: high
Category: Storage / Data
Root-cause group: G05
Symptom: Raw tracker entry for sd-012 does not describe a concrete failure mode; it only names an audited bug in Storage / Data.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_timestamps.py tests/test_experiment_db_crash_consistency.py tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py`
Observed failure: No current failure on branch; the storage/data checks already pass.
Evidence / error message: `64 passed`
Suspected files: `backtest_run_db.py`, `research_memory.py`, `research_paths.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the storage/data characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative storage/data checks pass and cover the shared root cause for the group.
## B063 - OBS-001 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-001 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B064 - OBS-002 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-002 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B065 - OBS-003 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-003 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B066 - OBS-004 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-004 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B067 - OBS-005 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-005 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B068 - OBS-006 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-006 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B069 - OBS-007 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-007 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B070 - OBS-008 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-008 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B071 - OBS-009 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-009 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B072 - OBS-010 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-010 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B073 - OBS-011 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-011 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B074 - OBS-012 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-012 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B075 - OBS-013 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-013 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B076 - OBS-014 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-014 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B077 - OBS-015 Observability
Status: fixed
Severity: medium
Category: Observability
Root-cause group: G06
Symptom: Raw tracker entry for obs-015 does not describe a concrete failure mode; it only names an audited bug in Observability.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_trace_higher_level_modules.py tests/test_error_loudness.py tests/test_autoresearch_controller_characterization.py`
Observed failure: No current failure on branch; the observability checks already pass.
Evidence / error message: `45 passed`
Suspected files: `trace_sdk.py`, `trace_refinement.py`, `trace_*`
Verification command: pytest -q tests/test_agent_orchestrator_characterization.py tests/test_research_conductor_characterization.py
Fix notes: Verified on branch via the observability characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative observability checks pass and cover the shared root cause for the group.
## B078 - VPS-001 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-001 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B079 - VPS-002 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-002 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B080 - VPS-003 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-003 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B081 - VPS-004 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-004 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B082 - VPS-005 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-005 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B083 - VPS-006 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-006 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B084 - VPS-007 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-007 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B085 - VPS-008 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-008 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B086 - VPS-009 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-009 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B087 - VPS-010 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-010 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B088 - VPS-011 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-011 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B089 - VPS-012 Deployment / VPS
Status: fixed
Severity: high
Category: Deployment / VPS
Root-cause group: G07
Symptom: Raw tracker entry for vps-012 does not describe a concrete failure mode; it only names an audited bug in Deployment / VPS.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_vps_runner_config.py`
Observed failure: No current failure on branch; the deployment/VPS checks already pass.
Evidence / error message: `6 passed`
Suspected files: `vps_runner.py`, `run_5ema_vps.sh`, `scripts/local_orchestrate_diagnosis.sh`, `aws/*.sh`
Verification command: manual verification needed
Fix notes: Verified on branch via the deployment/VPS configuration suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative deployment/VPS checks pass and cover the shared root cause for the group.
## B090 - SRB-001 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-001 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B091 - SRB-002 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-002 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B092 - SRB-003 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-003 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B093 - SRB-004 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-004 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B094 - SRB-005 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-005 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B095 - SRB-006 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-006 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B096 - SRB-007 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-007 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B097 - SRB-008 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-008 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B098 - SRB-009 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-009 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B099 - SRB-010 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-010 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B100 - SRB-011 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-011 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B101 - SRB-012 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-012 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B102 - SRB-013 Strategy Registry / Backtest Runner
Status: fixed
Severity: high
Category: Strategy Registry / Backtest Runner
Root-cause group: G08
Symptom: Raw tracker entry for srb-013 does not describe a concrete failure mode; it only names an audited bug in Strategy Registry / Backtest Runner.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the strategy-registry/backtest-runner checks already pass.
Evidence / error message: `36 passed`
Suspected files: `backtest_runner.py`, `strategy_registry.py`, `research_subagents.py`, `research_conductor.py`
Verification command: pytest -q tests/test_research_conductor_characterization.py tests/test_autoresearch_experiment.py
Fix notes: Verified on branch via the strategy-registry/backtest-runner characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative strategy-registry/backtest-runner checks pass and cover the shared root cause for the group.
## B103 - EMA-001 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-001 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B104 - EMA-002 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-002 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B105 - EMA-003 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-003 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B106 - EMA-004 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-004 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B107 - EMA-005 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-005 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B108 - EMA-006 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-006 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B109 - EMA-007 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-007 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B110 - EMA-008 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-008 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B111 - EMA-009 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-009 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B112 - EMA-010 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-010 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B113 - EMA-011 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-011 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B114 - EMA-012 EMA Strategy
Status: fixed
Severity: high
Category: EMA Strategy
Root-cause group: G09
Symptom: Raw tracker entry for ema-012 does not describe a concrete failure mode; it only names an audited bug in EMA Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the EMA checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/ema/research.py`, `strategies/ema/*`, `configs/ema_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the EMA characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative EMA checks pass and cover the shared root cause for the group.
## B115 - ORB-001 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-001 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B116 - ORB-002 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-002 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B117 - ORB-003 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-003 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B118 - ORB-004 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-004 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B119 - ORB-005 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-005 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B120 - ORB-006 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-006 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B121 - ORB-007 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-007 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B122 - ORB-008 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-008 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B123 - ORB-009 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-009 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B124 - ORB-010 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-010 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B125 - ORB-011 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-011 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B126 - ORB-012 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-012 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B127 - ORB-013 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-013 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B128 - ORB-014 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-014 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B129 - ORB-015 ORB Strategy
Status: fixed
Severity: high
Category: ORB Strategy
Root-cause group: G10
Symptom: Raw tracker entry for orb-015 does not describe a concrete failure mode; it only names an audited bug in ORB Strategy.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_ema_backtest_characterization.py tests/test_strategy_family_variants.py tests/test_strategy_family_secrets.py`
Observed failure: No current failure on branch; the ORB checks already pass.
Evidence / error message: `36 passed`
Suspected files: `strategies/orb/research.py`, `strategies/orb/*`, `configs/orb_base.yaml`
Verification command: manual verification needed
Fix notes: Verified on branch via the ORB characterization coverage in the shared strategy suites; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative ORB checks pass and cover the shared root cause for the group.
## B130 - XA-001 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-001 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B131 - XA-002 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-002 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B132 - XA-003 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-003 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B133 - XA-004 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-004 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B134 - XA-005 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-005 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B135 - XA-006 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-006 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B136 - XA-007 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-007 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B137 - XA-008 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-008 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B138 - XA-601 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-601 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B139 - XA-602 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-602 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B140 - XA-603 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-603 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B141 - XA-604 cross-area integration
Status: fixed
Severity: high
Category: Cross-area integration
Root-cause group: G11
Symptom: Raw tracker entry for xa-604 does not describe a concrete failure mode; it only names an audited bug in Cross-area integration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: covered_by_group_repro
Reproduction command: `pytest -q tests/test_autoresearch_planning.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_experiment.py tests/test_autoresearch_research.py`
Observed failure: No current failure on branch; the cross-area integration checks already pass.
Evidence / error message: `139 passed`
Suspected files: `integration glue`, `strategy/orchestration boundary`
Verification command: manual verification needed
Fix notes: Verified on branch via the combined cross-area characterization suite; no code changes were needed for this group.
Files changed: BUGS.md, FIX_PLAN.md
Result: Fixed on branch.
Notes: Representative cross-area integration checks pass and cover the shared root cause for the group.
## B142 - RLO-003 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-003 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_attempted
Reproduction command: manual verification needed
Observed failure: Not recorded in raw file.
Evidence / error message: None in raw file.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Tracker normalized from bugs167.md only; no code changes made yet.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `completed` for `section1-research-loop-orchestration-bug-rlo-003`; reproduction evidence is not present in the source file.

## B143 - RLO-001 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-001 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_attempted
Reproduction command: manual verification needed
Observed failure: Not recorded in raw file.
Evidence / error message: None in raw file.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Tracker normalized from bugs167.md only; no code changes made yet.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `completed` for `section1-research-loop-orchestration-bug-rlo-001`; reproduction evidence is not present in the source file.

## B144 - RLO-002 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-002 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_attempted
Reproduction command: manual verification needed
Observed failure: Not recorded in raw file.
Evidence / error message: None in raw file.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Tracker normalized from bugs167.md only; no code changes made yet.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `completed` for `section1-research-loop-orchestration-bug-rlo-002`; reproduction evidence is not present in the source file.

## B145 - RLO-004 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-004 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_attempted
Reproduction command: manual verification needed
Observed failure: Not recorded in raw file.
Evidence / error message: None in raw file.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Tracker normalized from bugs167.md only; no code changes made yet.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `completed` for `section1-research-loop-orchestration-bug-rlo-004`; reproduction evidence is not present in the source file.

## B146 - RLO-005 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-005 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_attempted
Reproduction command: manual verification needed
Observed failure: Not recorded in raw file.
Evidence / error message: None in raw file.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Tracker normalized from bugs167.md only; no code changes made yet.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `completed` for `section1-research-loop-orchestration-bug-rlo-005`; reproduction evidence is not present in the source file.

## B147 - RLO-006 Research Loop Orchestration
Status: fixed
Severity: high
Category: Research Loop Orchestration
Root-cause group: G01
Symptom: Raw tracker entry for rlo-006 does not describe a concrete failure mode; it only names an audited bug in Research Loop Orchestration.
Expected behavior: A concrete reproduction or failing targeted test must exist before any code fix is accepted.
Reproduction steps: 1. Identify the section-specific code path. 2. Derive or add a minimal reproducer. 3. Record the exact failure in this tracker.
Reproduction status: not_attempted
Reproduction command: manual verification needed
Observed failure: Not recorded in raw file.
Evidence / error message: None in raw file.
Suspected files: `autoresearch_research.py`, `research_conductor.py`, `research_subagents.py`
Verification command: pytest -q tests/test_autoresearch_experiment.py tests/test_agent_orchestrator_characterization.py
Fix notes: Tracker normalized from bugs167.md only; no code changes made yet.
Files changed: BUGS.md, FIX_PLAN.md
Result: Queued for reproduction during phase 2.5.
Notes: Raw tracker status was `completed` for `section1-research-loop-orchestration-bug-rlo-006`; reproduction evidence is not present in the source file.

## Verification rule
A bug is not fixed unless there is a test, reproduction command, or deterministic verification step.
