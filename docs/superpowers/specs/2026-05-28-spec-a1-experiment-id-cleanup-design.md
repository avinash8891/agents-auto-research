# Spec A1 — Experiment / Research-Round Terminology Cleanup

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** Spec A (`2026-05-28-spec-a-context-snapshot-design.md`) §14 Terminology unification
**Depends on:** Spec A's §14.3 Tier 2 (prompt header rename) — shipped with Spec A's PR
**Blocks:** Spec A2 (`2026-05-28-spec-a2-thesis-id-provenance-design.md`) — A2 builds on the cleaner thesis-id-vs-experiment separation A1 establishes
**Parallel with:** Spec B / C / D (does not touch their concerns)

---

## 1. Goal

Two cleanups, one spec:

1. **Eliminate "experiment" terminology in code where the concept is round-level.** Spec A §14.1 establishes that "round," "experiment," and "thesis" are *overlapping but not identical* concepts. The experiment is the backtest that runs inside a research round; round and experiment are 1:1, but `thesis_id` is a proposal-level identifier (a round may have many proposed `thesis_id`s — rejected drafts + one accepted). Code today uses "experiment" inconsistently — sometimes as a synonym for "round" (round-level operations, controller helpers), sometimes for "the backtest artifact." Replace round-level uses with `research_round` / round-level naming.

2. **Stop treating `thesis_id` as the canonical identifier for an experiment.** Several call sites look up backtest results by `thesis_id` alone, which silently breaks when:
   - The same `thesis_id` is re-proposed across rounds (same job).
   - The same `thesis_id` is reused across jobs.
   - A filesystem path collides because `experiments/{thesis_id}/` is reused.

   The correct experiment key is `research_round_id` (or `(job, round)`), which is unique per backtest by construction.

**Net effect when shipped:** code reads consistently with Spec A's terminology; round-vs-thesis-vs-experiment hierarchy is reflected in identifiers; ambiguous lookups become unambiguous.

## 2. Non-goals

- **Spec A's snapshot/prompt work** — independent; ships first.
- **DB schema migration.** The `research_thesis_attempts` table already keys by `(research_round_id, attempt_number)` — DB layer is clean. No table renames, no column renames.
- **Agent prompt rewriting beyond what's necessary for the new tool names.** The agent's system prompt is regenerated from the new names; reflexion history and saved findings remain untouched.
- **Renaming the file `autoresearch_experiment.py`.** That file holds backtest-execution helpers (`run_experiment`, `parse_benchmark_details`, etc.) — the *backtest* concept is genuinely experiment-shaped (one execution = one experiment). The module name stays. What changes: round-level helpers inside it move out or are renamed (§4).

## 2.1 No backward compatibility — hard cutover

**Old names and old filesystem paths are removed in the same commit that introduces the new ones.** No deprecation aliases, no `DeprecationWarning` wrappers, no dual-read fallback. Rationale:

- The deprecation window adds maintenance surface (two code paths, two test sets, two doc surfaces) for a benefit that doesn't apply here: there are no external consumers of these MCP tools / Python helpers outside this repo.
- "Soft" cleanups drift — wrappers stick around past their planned removal window, and the codebase ends up with both names indefinitely.
- A hard cutover surfaces every caller immediately as a failing test or import error. **The failing site is the source of truth** for what still needs to be updated.

**Implication:** every test, prompt template, fixture, and call site that references an old name MUST be updated in the same PR. The PR is not mergeable until `grep` for every old name returns zero hits (per §10 success criteria).

## 3. Background

Spec A §14.1 (now updated) defines the three concepts:

- **Research round** — the controller cycle (proposer → validator-gate-loop → backtest of the accepted thesis). The "experiment" lives here: 1 round = 1 backtest = 1 experiment. Identifier: `research_round_id`.
- **Thesis** — a *proposal within a round*. A round may produce many thesis attempts. Identifier: `thesis_id`. **`thesis_id` is not an experiment identifier** — multiple `thesis_id`s can be associated with a single round/experiment (rejected + accepted).
- **Experiment** — the backtest of the round's accepted thesis. No first-class `experiment_id` in code today; the round id serves by 1:1 mapping.

Spec A landed Tier 2 (prompt header rename) and the conceptual fix in §14.1 / §5.1. This spec is the code-side companion that fixes the remaining mis-modeling.

## 4. Audit — what changes, what stays

### 4.1 `thesis_id` used as experiment key (must fix — semantic bug)

| Location | What's broken | Fix |
|---|---|---|
| `research_memory.py:711` — `get_experiment_result(root, thesis_id, *, job_id, detail)` | Looks up the backtest result by `(thesis_id, job_id)`. If the same `thesis_id` runs twice in one job (re-attempt after a rerun), returns whichever the DB surfaces first. | **Replace** with `get_round_result(root, *, research_round_id, detail)`. Lookup by `research_round_id` (composite of `(job, round)`). Old function deleted in the same commit. |
| `research_tools_mcp.py:288` — MCP tool wrapper | Same — tool takes `thesis_id`. | **Replace** with `get_round_result(research_round_id, detail)`. Old MCP tool registration removed; old name in `_TOOL_ARG_SCHEMAS` removed. |
| `research_tools_schema.py:85` — `GetExperimentResultArgs(thesis_id: str, ...)` | Schema codifies `thesis_id` as the key. | **Replace** with `GetRoundResultArgs(research_round_id: str, ...)`. Old schema class deleted. |
| `compiler_builder.py:582` — `legacy_dir = current_root / "experiments" / thesis_id` | Filesystem path keyed by `thesis_id`. Two backtests of the same `thesis_id` collide. | **Replace** with `current_root / "experiments" / research_round_id` (e.g. `experiments/job-12-round-5/`). No fall-through read. Pre-existing on-disk artifacts under the old path must be migrated by `scripts/migrate_experiment_dirs.py` (§6) before the PR lands in any environment that has them. |
| `tests/test_compiler_pipeline_characterization.py:157` | Mirrors `compiler_builder.py:582` path. | Updates with the source — fixtures regenerated under the new layout. |
| `agent_formatters.py:157` | Instruction text: `"Call get_experiment_result(thesis_id) before relying on a specific experiment."` | Replaced with: `"Call get_round_result(research_round_id) before relying on a specific round's backtest."` |
| `research_memory.py:706, 753` | Instruction text: `"Call get_experiment_result(thesis_id)..."` | Same replacement as agent_formatters. |
| `agent_prompts.py:102` | Agent rule: `"Do NOT repeat a thesis_id that appears in PRIOR THESES or EXPERIMENT HISTORY."` | Split into two rules: (a) "Do NOT repeat a `thesis_id` that appears in PRIOR THESES" — proposal-level rule; (b) "Do NOT propose a thesis whose `research_round_id` would duplicate a prior round's backtest" — round-level rule (automatically true since `research_round_id` is monotonic per job, so mostly informational). |

### 4.2 "experiment" terminology used for round-level concepts (rename — mechanical)

| Location | Current name | New name |
|---|---|---|
| `autoresearch_experiment.py` (module file) | — | **Module stays.** Holds backtest-execution helpers; backtest = experiment is correct here. |
| `autoresearch_experiment.py:669,710,712,963` | `fallback_experiment_id` parameter | `fallback_run_id` (per §3, the experiment IS the round; the fallback is a backtest run id, not a true experiment id distinct from round id) |
| `autoresearch_experiment.py:912, autoresearch_controller.py:958, :1411` | `log_experiment_result(...)` | **Stays.** It logs the backtest's result (the experiment artifact). Genuinely experiment-shaped. |
| `autoresearch_controller.py:219, :383` | `_is_blocked_failed_experiment_resume_state`, `resume_failed_experiment` | Stays — these refer to resuming after a failed backtest execution. Backtest = experiment. |
| `autoresearch_controller.py:24-37` import aliases `_experiment_*` | aliases used inside the controller | Stays — match the source module's `autoresearch_experiment.py` naming. |
| MCP tool `list_experiment_results` (research_tools_mcp.py:40, research_conductor.py:579) | tool name | **Renamed** to `list_round_results`. List of round-level results, not experiment-level (each item is one round's backtest outcome). Old name kept as deprecated alias. |
| MCP tool `get_experiment_result` | tool name | **Renamed** to `get_round_result` (per §4.1). |
| Tests `tests/test_experiment_db_crash_consistency.py`, `tests/test_experiment_db_sqlite_runtime.py` | filenames | **Stays** — tests target backtest-execution behavior under crash/runtime conditions. Backtest = experiment. |
| `autoresearch_constants.py` (any `*EXPERIMENT*` constants) | grep-driven during implementation | Per-constant judgment: if round-level → rename; if backtest-level → keep. |

### 4.3 Agent-visible strings that flow into the prompt (must match Spec A's renaming)

| Location | Current | New |
|---|---|---|
| `research_prompts.py:54` (tool description) | `list_experiment_results / get_*        backtest outcomes` | Aligned with Spec A §5.10's reword (already shipped with Spec A). After A1: `list_round_results / get_round_result   Deep follow-up. LAST RESEARCH ROUND — RESULTS/CONFIG/DIAGNOSTICS is already in the prompt; this tool reaches prior rounds.` |
| `research_conductor.py:135` | `f"EXPERIMENT RESULTS SUMMARY:\n{experiment_results}\n\n"` (already renamed by Spec A to `PRIOR ROUNDS — RESULTS SUMMARY`) | No change — Spec A handles. |

### 4.4 Stays as-is — `experiment` is the correct term

- `autoresearch_experiment.py` module file — backtest execution = experiment.
- `run_experiment(...)`, `log_experiment_result(...)`, `parse_benchmark_details(...)` — backtest-execution helpers.
- `_is_blocked_failed_experiment_resume_state`, `resume_failed_experiment` — failed-backtest resumption.
- `tests/test_experiment_db_*.py` — backtest-execution tests.
- `latest_outcome["config_path"]`, file artifacts under `experiments/{...}/` (path stays as the directory name; the `{...}` inside changes from `thesis_id` to `research_round_id`).

## 5. New / changed function signatures

Old functions and schemas are **deleted** in the same commit that introduces the new ones — no aliases, no deprecation wrappers.

```python
# research_memory.py — replaces get_experiment_result + list_experiment_results

def get_round_result(
    root: Path,
    *,
    research_round_id: str,
    detail: bool = False,
) -> dict[str, Any]:
    """Return backtest result for a specific research round.

    `research_round_id` format: "job-{job}-round-{N}" (composite, unique
    per backtest). Raises KeyError if the round id doesn't resolve to a
    backtest record — callers must pass a valid id (no fallback resolution
    from thesis_id).
    """


def list_round_results(
    root: Path,
    *,
    order: str = "latest",
    limit: int = 10,
    job_id: int | None = None,
) -> dict[str, Any]:
    """Return a list of round results, ordered by recency / metric / etc.

    Each item is one round (= one backtest = one experiment).
    """
```

```python
# research_tools_schema.py — old GetExperimentResultArgs / ListExperimentResultsArgs deleted

class GetRoundResultArgs(BaseModel):
    research_round_id: str = Field(..., min_length=1)
    detail: bool = False


class ListRoundResultsArgs(BaseModel):
    order: Literal["latest", "best", "worst"] = "latest"
    limit: int = Field(10, ge=1, le=100)
```

```python
# research_tools_mcp.py — only new tools registered; old names removed entirely

_TOOL_ARG_SCHEMAS = {
    "list_round_results": ListRoundResultsArgs,
    "get_round_result": GetRoundResultArgs,
}
```

Any caller that imports `get_experiment_result`, `list_experiment_results`, `GetExperimentResultArgs`, or `ListExperimentResultsArgs` will fail at import time. **That failure is the signal to update the caller** — there are no silent fallbacks.

## 6. Filesystem layout change

**Old layout (collides on thesis_id reuse):**

```
runtime/jobs/job-12/research/round-5/experiments/{thesis_id}/
    backtest_output.json
    diagnostics.json
    strategy_events.jsonl
    trades.csv
```

**New layout (unique by round):**

```
runtime/jobs/job-12/research/round-5/experiments/{research_round_id}/
    backtest_output.json
    diagnostics.json
    strategy_events.jsonl
    trades.csv
```

Where `research_round_id = "job-12-round-5"`.

**No dual-read.** Readers look up the new path only. Old artifacts on disk become unreadable after this PR until they are migrated.

**Mandatory migration step.** `scripts/migrate_experiment_dirs.py` renames every existing `experiments/{thesis_id}/` directory to `experiments/{research_round_id}/` based on a DB lookup `(thesis_id, job_id) → research_round_id`. The script:

- Runs idempotently (re-running on already-migrated artifacts is a no-op).
- Aborts loudly if a thesis_id maps to multiple round_ids in the same job (the very collision this spec is fixing) — operator manually disambiguates, then re-runs.
- Must be run on every environment with pre-existing artifacts (local dev workstations, VPS, CI fixtures) before the PR is exercised there.

The PR's CI workflow runs the migration script as a pre-step against the test fixture tree.

## 7. No backward compatibility — see §2.1

This spec ships a hard cutover. There is no deprecation window, no alias layer, no dual-read. §2.1 explains the reasoning. The audit in §4 lists every site that must be updated atomically in the same PR.

**Implication for the PR:** the PR's diff includes every Python file, every test, every fixture, and every prompt template that referenced an old name or old path. The PR is not splittable across releases — a partial landing leaves the codebase in an internally-inconsistent state.

## 8. Migration plan — single PR, single commit per layer

One PR. Within the PR, commits are split per logical layer for review readability — but **all commits land together**. Partial landing is not supported (no backward compatibility means intermediate states do not run).

Commit ordering (lower-layer first so each commit's tests pass against the prior commit's state):

1. **DB-side helpers** (`backtest_run_db.py`): add `get_round_result_by_round_id(research_round_id)`. Remove any `_by_thesis_id` lookup helpers that are now dead. Update DB-helper tests.
2. **`research_memory.py`**: replace `get_experiment_result(thesis_id, ...)` with `get_round_result(research_round_id=...)`. Replace `list_experiment_results(...)` with `list_round_results(...)`. Update `tests/test_research_memory.py` in the same commit.
3. **`research_tools_schema.py`**: delete `GetExperimentResultArgs` and `ListExperimentResultsArgs`. Add `GetRoundResultArgs` and `ListRoundResultsArgs`. Update `tests/test_research_tools_schema.py` and `tests/test_legacy_recovery_schema.py` in the same commit.
4. **`research_tools_mcp.py`**: replace tool registrations (`get_experiment_result` → `get_round_result`, `list_experiment_results` → `list_round_results`). Old entries in `_TOOL_ARG_SCHEMAS` deleted. Update `tests/test_research_tools_mcp.py`.
5. **`research_conductor.py`**: replace tool registrations + the two `async def` wrappers. Trace tags updated to new names. Update `tests/test_research_conductor_characterization.py` (which asserts on prompt fixtures and tool-call payloads).
6. **`research_prompts.py`**: tool-description block now references the new names (`list_round_results / get_round_result`). Update `tests/test_research_conductor_characterization.py` fixtures if they snapshot the system prompt.
7. **`agent_formatters.py`, `research_memory.py` instruction strings**: updated to reference `get_round_result(research_round_id)`. Update tests asserting on these strings.
8. **`agent_prompts.py:102`**: rule split (thesis-id rule + round-id rule, per §4.1). Update `tests/test_agent_prompts*.py` if any assert on this rule.
9. **`compiler_builder.py:582`** + `tests/test_compiler_pipeline_characterization.py:157`: filesystem path keyed by `research_round_id`. Test fixtures regenerated under the new layout. Pre-existing on-disk fixtures migrated via `scripts/migrate_experiment_dirs.py`.
10. **`autoresearch_experiment.py`** `fallback_experiment_id` → `fallback_run_id` rename. Update `tests/test_autoresearch_experiment.py`.
11. **`scripts/migrate_experiment_dirs.py`**: new migration script + unit tests.
12. **Final sweep**: full test suite green; grep verifies:
    - `grep -rn "get_experiment_result\|list_experiment_results" --include="*.py"` returns zero hits.
    - `grep -rn "GetExperimentResultArgs\|ListExperimentResultsArgs" --include="*.py"` returns zero hits.
    - `grep -rn "fallback_experiment_id" --include="*.py"` returns zero hits.
    - `grep -rn "experiments/.*thesis_id" --include="*.py"` returns zero hits.
    - `grep -rn "experiments/.*\\{thesis_id\\}" --include="*.py"` returns zero hits.
13. **Documentation**: this spec marked Shipped; Spec A §14.1 cross-references updated. PR description includes the grep evidence.

**Test update is non-negotiable per step.** Each layer's commit includes the test updates for that layer in the same commit — not a follow-up. A green test suite at every commit boundary is the proof that the layer's rename is internally consistent.

## 9. Risk and rollback

**Risks (hard cutover):**

- **External callers break immediately.** Anything outside this repo calling `get_experiment_result(thesis_id)` fails with `AttributeError` / MCP "unknown tool" the moment this PR lands. Mitigation: confirm before merge that no external consumer exists (this is an internal autoresearch tool surface, not a public API). If an external consumer is discovered, it gets fixed in the same PR — no "we'll get to it later."
- **Pre-existing on-disk artifacts become unreadable** until `scripts/migrate_experiment_dirs.py` is run against them. Mitigation: the script is the first thing the PR's deploy/test runbook executes on every environment. CI runs it as a pre-test step. Local-dev guidance in the PR description.
- **Migration-script bug ruins artifacts.** Mitigation: script is idempotent; supports `--dry-run` (default) that prints intended renames; `--apply` is required to actually rename. Unit tests cover collision detection (same thesis_id mapping to multiple rounds — aborts loudly).
- **A test or fixture is missed in the rename sweep.** Mitigation: §10's grep gate. The PR is not mergeable until every grep returns zero hits.

**Rollback:** revert the entire PR. There is no partial-revert path because there is no backward-compatibility layer — old code paths don't exist alongside new ones. If on-disk artifacts were migrated by the script, they need to be migrated back (the script's inverse mode `--reverse` runs the rename in the other direction using the same DB lookup).

## 10. Success criteria

**Hard-cutover grep gate (PR not mergeable until all pass):**

- `grep -rn "get_experiment_result\b" --include="*.py"` returns zero hits.
- `grep -rn "list_experiment_results\b" --include="*.py"` returns zero hits.
- `grep -rn "GetExperimentResultArgs\|ListExperimentResultsArgs" --include="*.py"` returns zero hits.
- `grep -rn "fallback_experiment_id" --include="*.py"` returns zero hits.
- `grep -rn "experiments/.*thesis_id\|experiments/.*\\{thesis_id\\}" --include="*.py"` returns zero hits.
- `grep -rn "thesis_id" research_memory.py research_tools_mcp.py compiler_builder.py` returns hits only for thesis-id-as-proposal-identity (never as experiment lookup key — manual review of any remaining hits required).

**Test-suite criteria:**

- `pytest` passes end-to-end (full suite, not subset).
- Every test that previously asserted on `get_experiment_result(thesis_id)` / `list_experiment_results(...)` now asserts on the new names. Verified by per-file diff: any test file that touched the old name has a corresponding new-name assertion.
- A new test `tests/test_research_round_id_lookup.py` asserts:
  - `get_round_result(research_round_id="job-1-round-1")` returns the expected record.
  - `get_round_result(research_round_id="nonexistent")` raises `KeyError` (no silent fallback).
  - Two backtest runs of the same `thesis_id` in the same job produce distinct on-disk artifact dirs under `experiments/job-1-round-1/` and `experiments/job-1-round-2/`.

**Migration-script criteria:**

- `scripts/migrate_experiment_dirs.py --dry-run` against a fixture tree prints the expected rename list, applies nothing.
- `scripts/migrate_experiment_dirs.py --apply` renames every directory; second invocation is a no-op (idempotent).
- `scripts/migrate_experiment_dirs.py --apply` against a fixture where a `thesis_id` resolves to two `research_round_id`s aborts loudly (exit code ≠ 0, prints the offending pair).
- `scripts/migrate_experiment_dirs.py --reverse --apply` undoes a prior migration to the original layout (rollback path).

**Behavioral criterion:**

- A backtest re-run that proposes the same `thesis_id` twice produces two distinct on-disk artifact directories (`experiments/job-X-round-N1/` and `experiments/job-X-round-N2/`) — no collision. Verified by an integration test that runs the controller twice with a fixed thesis_id.

**Documentation:**

- Spec A §14 cross-reference to Spec A1 (already added).
- Spec A1 marked Shipped; PR notes link both specs.
- PR description includes the grep-gate output (zero hits for each pattern).

## 11. Out of scope (for clarity)

- Renaming the file `autoresearch_experiment.py` (justified in §2 — backtest execution = experiment is correct).
- Renaming `run_experiment()`, `log_experiment_result()` (same justification).
- Touching `research_thesis_attempts` DB table schema (already round-keyed).
- Changes to Spec B / C / D scope.
