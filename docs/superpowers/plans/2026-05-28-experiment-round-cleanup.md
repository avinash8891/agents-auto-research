# Experiment/Round Terminology Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hard-cutover rename of round-level "experiment" terminology to round-level naming, and replace `thesis_id` as a backtest lookup key with `research_round_id`. Backed by Spec A1 (`.context/attachments/.../pasted_text_2026-05-28-18-27-48.txt`) plus three audit reports that expand the spec's scope.

**Architecture:**
- Hard cutover, no deprecation shims (per Spec A1 §2.1). Every old name removed in the same PR.
- Backtest rows in `backtest_runs` table **already carry `research_round_id`** (`backtest_run_db.py:167`, populated at `autoresearch_experiment.py:705-712`). The refactor is mostly reader-side and surface-naming.
- New canonical helper `research_round_id(job, round_number)` lives in `autoresearch_runtime_paths.py` (4 production inline duplicates today; consolidate first so later tasks reuse it).
- MCP tool names and schemas rename atomically with their characterization-test snapshots, validator allowlist, and evidence-source enums.

**Tech Stack:** Python 3.11+, pytest, pydantic v2, sqlite3, MCP server.

---

## Scope expansion (audit findings beyond Spec A1)

Surfaced by the three audit subagents — these MUST be touched in the same PR:

1. **`backtest_run_db.BacktestRunDB.get_by_thesis()`** (`:896-897`) is the 1:N "lookup by proposal" helper. Replace with `get_by_research_round_id()` (1:1) and migrate callers.
2. **Canonical `research_round_id` format helper** does not exist. Add to `autoresearch_runtime_paths.py`; replace 4 production inline `f"job-{job}-round-{N}"` sites: `autoresearch_research.py:228`, `autoresearch_experiment.py:706`, `:881`, `backtest_run_db.py:517`.
3. **MCP tool name `list_experiment_results`** is hardcoded in 14 test files as `_VALID_PROCESS_TOOLS` literal + validator allowlist `thesis_validator.py:347` + L6/L7 hard-gate error string. Single source of truth needed.
4. **Evidence-source enum `"experiment_result"`** at `research_types.py:71` + `thesis_validator.py:126`. Agent-visible — rename to `"round_result"`.
5. **State `next_action.type` `"run_experiment"` / activity `"experiment"`** appears in 8 production sites + `vps_runner.py` SSH heredoc string. Atomic move required.
6. **`autoresearch_cli.py` & `eval_cli.py`** are round-level "experiment session" CLIs with extensive user-facing string surface (description/help/JSON keys/log lines).
7. **`format_experiment_results_summary` in `agent_formatters.py:124`** plus `experiment_results` parameter passed through `research_conductor.py:1041,1054`.
8. **Controller method `log_experiment_result`** at `autoresearch_controller.py:931-958` persists row with `thesis_id` only — should also carry round identification via `record_run_id`. Already does (line 711) but parameter naming is misleading.
9. **`reflexio_agent_reflections.py:84`** agent reflection prompt, **`agent_token_usage.py:225`** docstring, **`agent_orchestrator_helpers.py:156`** prompt string.
10. **A new test `tests/test_research_round_id_lookup.py`** is required by Spec §10.
11. **A regression test** asserting "same thesis_id proposed twice → distinct directories" is missing.

**Stays as `experiment`** (correctly backtest-execution-level):
- File `autoresearch_experiment.py` (the module name).
- File `experiment_evaluator.py` and the diagnostic surface `"experiment_evaluation"` (registered in `diagnostic_contracts.py:31`, `compiler_implementation_verify.py:267-315`, `compiler_builder.py:389-392`).
- Tests `tests/test_experiment_db_*.py` (target backtest-execution behavior).
- `run_experiment()`, `log_experiment_result()` (backtest helpers).

---

## File map

**Create:**
- `scripts/migrate_experiment_dirs.py` — migration tool (idempotent, --dry-run/--apply/--reverse)
- `tests/test_research_round_id_lookup.py` — new tests per Spec §10
- `tests/test_migrate_experiment_dirs.py` — migration script unit tests

**Modify (production):**
- `autoresearch_runtime_paths.py` — add `research_round_id()` helper
- `backtest_run_db.py` — add `get_by_research_round_id()`, remove `get_by_thesis`, add round-id params to `add_from_sqlite_fields`, use helper at `:517`
- `research_memory.py` — replace `get_experiment_result`/`list_experiment_results` + internal helpers (`_iter_experiment_records`, `_experiment_index_entry`, `_experiment_detail`, `_experiment_compact_detail`, `_sort_experiment_records`) + instruction strings (`:152`, `:706`, `:753`)
- `research_tools_schema.py` — replace `GetExperimentResultArgs`/`ListExperimentResultsArgs`
- `research_tools_mcp.py` — replace tool dispatch, callable params (`list_experiment_results_for_root`, `get_experiment_result_for_root`)
- `research_conductor.py` — rename inner tool funcs, tool registrations, trace tags, prompt strings/headers, `experiment_results` parameter at `:1041,1054`
- `research_prompts.py` — tool descriptions (`:23,54,93,150`)
- `research_types.py` — evidence enum `"experiment_result"` → `"round_result"` at `:71`
- `thesis_validator.py` — allowlist (`:347`), evidence enum mirror (`:126`), legacy-path error codes (`:177,194,311`)
- `agent_formatters.py` — `format_experiment_results_summary` → `format_round_results_summary`, instruction strings (`:124,125,150,154,155,157`)
- `agent_prompts.py` — `:102` rule split, prompt strings (`:85,118`)
- `agent_orchestrator_helpers.py` — `:156` prompt string
- `agent_token_usage.py` — `:225` docstring
- `reflexio_agent_reflections.py` — `:84` reflection prompt
- `compiler_builder.py` — `:582` rekey to `research_round_id`, error msg (`:583-586`)
- `autoresearch_experiment.py` — `fallback_experiment_id` → `fallback_run_id`, helpers `_finalize_experiment` → `_finalize_round`, docstrings, use round-id helper at `:706,881`
- `autoresearch_controller.py` — `_experiment_*` import aliases → `_round_*`, controller methods `_is_blocked_failed_experiment_resume_state`/`resume_failed_experiment`/`_run_experiment`/`log_experiment_result`, `experiments_dir` param, docstrings
- `autoresearch_orchestration.py` — `"type": "experiment"`/`"run_experiment"` (`:120,140,708`)
- `autoresearch_planning.py` — same activity strings (`:164,372`)
- `autoresearch_research.py` — activity strings (`:1656,1673`), variable `experiment_results` → `round_results`, `format_experiment_results_summary` call sites, docstrings
- `autoresearch_state.py` — `non_experiment` variable, docstrings (`:87,89,90,200-218,311`)
- `autoresearch_cli.py` — description/help text, JSON keys, log lines, docstrings (~10 sites)
- `eval_cli.py` — `:9` docstring
- `autoresearch_constants.py` — `:3` docstring
- `vps_runner.py` — heredoc strings (`:457,464,465`) atomically with state schema rename
- `improvement_recursive_improve.py` — `:253-260` round-id helper substitution

**Modify (tests):** 20 files per test audit:
- `tests/test_research_tools_mcp.py` (extensive)
- `tests/test_research_tools_schema.py` (full rename)
- `tests/test_research_conductor_characterization.py` (~15 sites)
- `tests/test_research_memory.py` (review under new API)
- `tests/test_autoresearch_experiment.py` (`fallback_experiment_id` kwargs at `:445,511,633`)
- `tests/test_compiler_pipeline_characterization.py:155-157` (path constructions)
- `tests/test_autoresearch_research.py:204,679` (`tools_called` sets)
- `tests/test_thesis_validator.py:13` (`_VALID_PROCESS_TOOLS`) and 14 more validator test files with the same literal duplicated
- `tests/test_l6_l7_tool_order_gates.py:30` (hard-gate error string)

**Audit-confirmed safe (no change):**
- `tests/test_legacy_recovery_schema.py` — proposal-level thesis_id only
- `tests/test_experiment_db_crash_consistency.py` — backtest-level DB tests
- `tests/test_experiment_db_sqlite_runtime.py` — uses `research_round_id` for lookups already
- `rejection_artifact.py` — `runtime/.../theses/<thesis_id>/rejection.json` paths are proposal-level (correct)
- `experiment_evaluator.py` (filename + surface name stays)

---

## Task 1: Add canonical `research_round_id` helper

**Files:**
- Modify: `autoresearch_runtime_paths.py`
- Test: `tests/test_autoresearch_runtime_paths.py` (create if missing)

- [ ] **Step 1: Inspect current file**

Read all of `autoresearch_runtime_paths.py` so the helper sits next to `research_round_root()`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_autoresearch_runtime_paths.py
from autoresearch_runtime_paths import research_round_id

import pytest


def test_research_round_id_format():
    assert research_round_id(12, 5) == "job-12-round-5"


def test_research_round_id_baseline():
    assert research_round_id(1, 0) == "job-1-round-0"


def test_research_round_id_rejects_bad_job():
    with pytest.raises(ValueError):
        research_round_id(0, 1)


def test_research_round_id_rejects_negative_round():
    with pytest.raises(ValueError):
        research_round_id(1, -1)
```

- [ ] **Step 3: Run test (expect fail — function missing)**

`pytest tests/test_autoresearch_runtime_paths.py -v`

- [ ] **Step 4: Implement**

Add to `autoresearch_runtime_paths.py`:

```python
def research_round_id(job: int, round_number: int) -> str:
    """Canonical id for a research round (= one backtest = one experiment).

    Format: "job-{job}-round-{round_number}". Use this everywhere a round id
    is constructed — the literal string is duplicated across modules today.
    """
    if job < 1:
        raise ValueError(f"job id must be >= 1; got {job}")
    if round_number < 0:
        raise ValueError(f"round number must be >= 0; got {round_number}")
    return f"job-{job}-round-{round_number}"
```

- [ ] **Step 5: Tests pass**

`pytest tests/test_autoresearch_runtime_paths.py -v`

- [ ] **Step 6: Replace 4 production inline duplicates**

Edit each so they import and call `research_round_id(job, round_number)`:
- `autoresearch_research.py:228`
- `autoresearch_experiment.py:706`
- `autoresearch_experiment.py:881`
- `backtest_run_db.py:517`

- [ ] **Step 7: Targeted pytest for affected modules**

`pytest tests/test_autoresearch_research.py tests/test_autoresearch_experiment.py tests/test_experiment_db_sqlite_runtime.py -x`

- [ ] **Step 8: Commit**

```
refactor: centralize research_round_id format in runtime_paths

Reason: literal f"job-{j}-round-{n}" appeared in 4 production
sites + 4 test sites with no canonical helper. Three-strikes —
consolidate before further refactor.
```

---

## Task 2: DB layer — `get_by_research_round_id`, drop `get_by_thesis`

**Files:**
- Modify: `backtest_run_db.py`
- Test: `tests/test_experiment_db_sqlite_runtime.py` (add coverage)

- [ ] **Step 1: Confirm `get_by_thesis` callers**

`grep -rn "get_by_thesis\b" --include="*.py"` — verify zero production callers. Audit says none found; verify before deletion (Rule L).

- [ ] **Step 2: Failing test for new helper**

```python
# tests/test_experiment_db_sqlite_runtime.py — add test
def test_get_by_research_round_id_returns_single_record(tmp_path):
    db = BacktestRunDB(tmp_path / "runs.db")
    db.init_session(name="ema", metric_name="sharpe", direction="max")
    # seed a row via record_backtest_run with research_round_id="job-1-round-1"
    ...
    record = db.get_by_research_round_id("job-1-round-1")
    assert record is not None
    assert record.research_round_id == "job-1-round-1"


def test_get_by_research_round_id_returns_none_for_unknown(tmp_path):
    db = BacktestRunDB(tmp_path / "runs.db")
    assert db.get_by_research_round_id("nonexistent") is None
```

- [ ] **Step 3: Implement `get_by_research_round_id`**

```python
def get_by_research_round_id(self, research_round_id: str) -> BacktestRunRecord | None:
    """1:1 lookup of the backtest for a research round.

    Returns None if no row matches. Use this in preference to thesis-id
    based lookups, which are 1:N (same thesis can be proposed across rounds).
    """
    for record in self._load():
        if record.research_round_id == research_round_id:
            return record
    return None
```

- [ ] **Step 4: Delete `get_by_thesis` at `:896-897` if confirmed dead.**

- [ ] **Step 5: Pass `research_round_id`/`research_round_number`/`is_baseline` through `add_from_sqlite_fields` (`:390-439`)**

Callers (chiefly `autoresearch_experiment.py`) already compute these — wire them through so newly written rows have populated columns rather than relying on defaults.

- [ ] **Step 6: Tests pass**

`pytest tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_crash_consistency.py -x`

- [ ] **Step 7: Commit**

```
refactor(db): add get_by_research_round_id, remove get_by_thesis

Reason: get_by_thesis returns list[] because thesis_id is 1:N
across backtest_runs. Backtest-for-round lookup is 1:1 and belongs
on research_round_id.
```

---

## Task 3: `research_memory.py` rename

**Files:**
- Modify: `research_memory.py`
- Test: `tests/test_research_memory.py`

- [ ] **Step 1: Failing test for new API**

```python
# tests/test_research_memory.py — add
def test_get_round_result_by_round_id(tmp_path):
    # seed a backtest record with research_round_id="job-1-round-1"
    ...
    result = get_round_result(tmp_path, research_round_id="job-1-round-1")
    assert result["research_round_id"] == "job-1-round-1"


def test_get_round_result_raises_keyerror_on_unknown(tmp_path):
    with pytest.raises(KeyError):
        get_round_result(tmp_path, research_round_id="nonexistent")
```

- [ ] **Step 2: Implement `get_round_result` + `list_round_results`**

Per Spec §5. Internally call `BacktestRunDB.get_by_research_round_id`. Delete `get_experiment_result` and `list_experiment_results` in the same edit.

- [ ] **Step 3: Rename internal helpers**

- `_iter_experiment_records` → `_iter_round_records`
- `_experiment_index_entry` → `_round_index_entry`
- `_experiment_detail` → `_round_detail`
- `_experiment_compact_detail` → `_round_compact_detail`
- `_sort_experiment_records` → `_sort_round_records`

- [ ] **Step 4: Update instruction strings at `:152`, `:706`, `:753`**

Replace `"Call get_experiment_result(thesis_id)..."` with `"Call get_round_result(research_round_id)..."`.

- [ ] **Step 5: Tests pass**

`pytest tests/test_research_memory.py -x`

- [ ] **Step 6: Commit**

```
refactor: research_memory get_round_result(research_round_id)

Reason: get_experiment_result(thesis_id) silently returned the wrong
record when same thesis_id was re-proposed; research_round_id is the
unique key by construction.
```

---

## Task 4: Schemas + MCP tool registration

**Files:**
- Modify: `research_tools_schema.py`, `research_tools_mcp.py`
- Test: `tests/test_research_tools_schema.py`, `tests/test_research_tools_mcp.py`

- [ ] **Step 1: Schema rewrite**

In `research_tools_schema.py`, delete `GetExperimentResultArgs` and `ListExperimentResultsArgs`. Add per Spec §5:

```python
class GetRoundResultArgs(BaseModel):
    research_round_id: NonEmptyStr
    detail: bool = False


class ListRoundResultsArgs(BaseModel):
    order: Literal["latest", "best", "worst"] = "latest"
    limit: int = Field(10, ge=1, le=100)
    job_id: int | None = None
```

- [ ] **Step 2: MCP wiring**

In `research_tools_mcp.py`:
- Imports → new names.
- Dispatch map keys → `"list_round_results"`, `"get_round_result"`.
- Constructor params: `list_round_results_for_root`, `get_round_result_for_root`.
- Tool wrapper signature: `get_round_result(research_round_id, detail=False)`.

- [ ] **Step 3: Update tests**

- `tests/test_research_tools_schema.py` — full file rename (~8 test functions, imports, instantiations).
- `tests/test_research_tools_mcp.py` — `EXPECTED_TOOL_NAMES` set (`:217-218`), monkeypatched callable params (`:188-189`), test fn names (`:371-388`).

- [ ] **Step 4: Tests pass**

`pytest tests/test_research_tools_schema.py tests/test_research_tools_mcp.py -x`

- [ ] **Step 5: Commit**

```
refactor: rename MCP tools list_round_results/get_round_result

Reason: tool names mirrored a round-vs-experiment confusion; the
list returns rounds and the lookup is by research_round_id.
```

---

## Task 5: research_conductor + prompts + formatters

**Files:**
- Modify: `research_conductor.py`, `research_prompts.py`, `agent_formatters.py`, `agent_orchestrator_helpers.py`, `agent_token_usage.py`, `reflexio_agent_reflections.py`
- Test: `tests/test_research_conductor_characterization.py`

- [ ] **Step 1: research_conductor.py inner tool wrappers**

- `async def list_experiment_results(...)` → `list_round_results` (`:587-616`)
- `async def get_experiment_result(thesis_id, detail)` → `get_round_result(research_round_id, detail)` (`:625-662`)
- Trace tags + `tools_called_this_round.add(...)` strings
- Imports at `:20-22`
- Parameter `experiment_results` at `:123,1041,1054` → `round_results`
- Prompt strings `"LATEST EXPERIMENT OUTCOME:"`, `"EXPERIMENT RESULTS SUMMARY:"`, `"No trades file is available for the latest/current experiment..."` at `:142-172` → round wording
- Docstrings at `:336-347` → round wording
- Tool registration list at `:830-831`

- [ ] **Step 2: research_prompts.py**

- `:23` — `"next experiment"` → `"next round"`
- `:54` — tool desc `"list_experiment_results / get_*  backtest outcomes"` → `"list_round_results / get_round_result   prior round outcomes"`
- `:93`, `:150` — `"experiment_result"` evidence-source mentions → `"round_result"`

- [ ] **Step 3: agent_formatters.py**

- `format_experiment_results_summary` → `format_round_results_summary` (`:124`)
- Docstring `:125`
- `"total_experiments="` → `"total_rounds="` (`:150`)
- Instructions `"list_experiment_results"` / `"get_experiment_result(thesis_id)"` → new names (`:154,155,157`)

- [ ] **Step 4: agent_orchestrator_helpers.py**

`:156` — `"FULL EXPERIMENT HISTORY:\n..."` → `"FULL ROUND HISTORY:\n..."`.

- [ ] **Step 5: agent_token_usage.py**

`:225` docstring — `"per-experiment"` → `"per-round"`.

- [ ] **Step 6: reflexio_agent_reflections.py**

`:84` — `"Use prior theses, experiment results, web evidence"` → `"Use prior theses, round results, web evidence"`.

- [ ] **Step 7: Characterization snapshot update**

`tests/test_research_conductor_characterization.py` has ~15 sites: tool name list (`:130`), monkeypatch targets (`:308,313,405,478`), `tool_calls` fixtures with `("list_experiment_results", {...})` / `("get_experiment_result", {"thesis_id": ...})` → `(..., {"research_round_id": ...})`, output assertions (`:370,371,446,517`). The thesis_id payloads must convert to research_round_id payloads keyed on the round id format from Task 1.

- [ ] **Step 8: Tests pass**

`pytest tests/test_research_conductor_characterization.py -x`

- [ ] **Step 9: Commit**

```
refactor: rename round-level surface across conductor/prompts/formatters

Reason: terminology unification per Spec A1 §4.2 / §4.3.
```

---

## Task 6: Validator allowlist + evidence enum

**Files:**
- Modify: `thesis_validator.py`, `research_types.py`, `agent_prompts.py`
- Test: 14 validator test files

- [ ] **Step 1: Replace allowlist literal**

If `thesis_validator.py:347` declares `_VALID_PROCESS_TOOLS = {"list_experiment_results", "web_search"}` and 14 test files mirror it, **first** consolidate: export the constant from `thesis_validator.py` and import it in tests.

`grep -n "_VALID_PROCESS_TOOLS" tests/ --include="*.py"` to enumerate.

If consolidation is too large for this PR, at minimum update every occurrence to `"list_round_results"`.

- [ ] **Step 2: Evidence-source enum**

- `research_types.py:71` — `"experiment_result"` → `"round_result"`
- `thesis_validator.py:126` — mirror update
- Update legacy-path error code at `:177` (`config_validity_base_config_path_legacy_experiments` likely keeps name as it refers to legacy path)
- Update comments at `:194,311` (cosmetic — legacy reference text)

- [ ] **Step 3: agent_prompts.py:102 rule split**

Replace single rule with two per Spec §4.1:

```
- Do NOT repeat a `thesis_id` that appears in PRIOR THESES.
- (Informational) Each research round has a unique `research_round_id`
  by construction — backtests of the same `thesis_id` across rounds
  produce distinct rounds.
```

Other agent_prompts strings:
- `:85` — `"experiment history"` → `"round history"`
- `:118` — `"experiment history"` → `"round history"`

- [ ] **Step 4: L6/L7 hard-gate error string**

`tests/test_l6_l7_tool_order_gates.py:30` mirrors the producing-code string. Locate the producing site (likely `thesis_validator.py`) and rename to `list_round_results` together.

- [ ] **Step 5: Update 14 validator test files**

Per audit list. All references to `"list_experiment_results"` → `"list_round_results"`. Evidence-source enum mentions of `"experiment_result"` → `"round_result"`.

- [ ] **Step 6: Tests pass**

`pytest tests/test_thesis_validator.py tests/test_validator_*.py tests/test_stage1_rules*.py tests/test_l5_neighboring_threshold.py tests/test_l6_l7_tool_order_gates.py tests/test_schema_additions.py -x`

- [ ] **Step 7: Commit**

```
refactor: validator allowlist + evidence enum to round_*

Reason: process-tool name and evidence-source enum are the LLM-visible
surface; must move with tool rename to stay coherent.
```

---

## Task 7: compiler_builder filesystem rekey

**Files:**
- Modify: `compiler_builder.py`
- Test: `tests/test_compiler_pipeline_characterization.py`

- [ ] **Step 1: Update guardrail at `:582-586`**

```python
legacy_dir = current_root / "experiments" / research_round_id
```

…and adapt the surrounding `_load_structured_thesis_artifacts` signature to take `research_round_id` instead of `thesis_id` if needed (read the function — it currently receives `thesis_id` via caller chain; trace and update).

Error message update:
`"legacy builder experiment directory is not supported (use research/round-N/)"` — concrete and matches new layout.

- [ ] **Step 2: Update test fixture path**

`tests/test_compiler_pipeline_characterization.py:155-157` builds the fixture with `thesis_id = "legacy"` as the dir component. Replace with `research_round_id = "job-1-round-5"`.

- [ ] **Step 3: Tests pass**

`pytest tests/test_compiler_pipeline_characterization.py -x`

- [ ] **Step 4: Commit**

```
refactor: compiler_builder legacy guardrail keys on research_round_id

Reason: `experiments/{thesis_id}/` legacy paths collide on thesis
re-attempts; refuse legacy layout by research_round_id.
```

---

## Task 8: `fallback_experiment_id` → `fallback_run_id`

**Files:**
- Modify: `autoresearch_experiment.py`
- Test: `tests/test_autoresearch_experiment.py`

- [ ] **Step 1: Rename param + helpers**

- `fallback_experiment_id` → `fallback_run_id` at `:669,710,712,963`
- Helpers: `_finalize_experiment` → `_finalize_round` (`:1427,1437`)
- Local docstrings/comments

- [ ] **Step 2: Update tests**

`tests/test_autoresearch_experiment.py` lines `:445, :511, :633` — kwarg `fallback_experiment_id="fallback"` → `fallback_run_id="fallback"`.

- [ ] **Step 3: Tests pass**

`pytest tests/test_autoresearch_experiment.py -x`

- [ ] **Step 4: Commit**

```
refactor: fallback_experiment_id → fallback_run_id

Reason: the param is a backtest run id, not a true experiment id
distinct from research_round_id.
```

---

## Task 9: State `next_action.type` atomic rename

**Files:**
- Modify: `autoresearch_orchestration.py`, `autoresearch_planning.py`, `autoresearch_research.py`, `autoresearch_experiment.py`, `vps_runner.py`
- Test: any test that asserts on these strings

- [ ] **Step 1: Replace strings**

Atomically replace `"type": "experiment"` and `"type": "run_experiment"` with `"type": "round"` and `"type": "run_round"` at:
- `autoresearch_orchestration.py:120,140,708`
- `autoresearch_planning.py:164,372`
- `autoresearch_research.py:1656,1673`
- `autoresearch_experiment.py:1335`
- `vps_runner.py:457,464,465` (heredoc shell strings)

- [ ] **Step 2: grep verifies zero stragglers**

`grep -rn '"type": "experiment"' --include="*.py"` → 0 hits
`grep -rn '"type": "run_experiment"' --include="*.py"` → 0 hits

- [ ] **Step 3: Tests pass**

`pytest tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py tests/test_autoresearch_experiment.py -x`

- [ ] **Step 4: Commit**

```
refactor: state next_action.type "run_experiment" → "run_round"

Reason: next_action.type is the round-level activity dispatcher;
"experiment" wording conflated round step with backtest artifact.
```

---

## Task 10: Controller round-level renames

**Files:**
- Modify: `autoresearch_controller.py`

- [ ] **Step 1: Rename helpers and aliases**

- Import aliases `_experiment_*` → `_round_*` at `:24-37`
- `_is_blocked_failed_experiment_resume_state` → `_is_blocked_failed_round_resume_state` (`:219`)
- `resume_failed_experiment` → `resume_failed_round` (`:383`)
- `_run_experiment` → `_run_round` (`:1062-1063`)
- `log_experiment_result` controller method **stays** (per Spec §4.2 — backtest result logging is genuinely experiment-shaped). Confirm reading the method body.
- `experiments_dir=` kwarg → `builder_requests_dir=` at `:1030`
- Docstrings/comments at `:12, :404, :415, :673, :684, :1071, :1100`

- [ ] **Step 2: Update tests**

`tests/test_autoresearch_controller*.py` — adapt any references.

- [ ] **Step 3: Tests pass**

`pytest tests/test_autoresearch_controller*.py -x`

- [ ] **Step 4: Commit**

```
refactor: controller round-level helpers + aliases renamed
```

---

## Task 11: CLI text rename

**Files:**
- Modify: `autoresearch_cli.py`, `eval_cli.py`, `autoresearch_constants.py`

- [ ] **Step 1: autoresearch_cli.py**

- Module docstring `:2`
- Docstrings `:150,190,308,342,367`
- JSON output key `"totalExperiments"` → `"totalRounds"` at `:419`
- argparse description `:434`, subparser help text `:438,446,469`

- [ ] **Step 2: eval_cli.py:9**

Docstring `"SQLite experiment-tracker CLI"` → `"SQLite round-tracker CLI"`.

- [ ] **Step 3: autoresearch_constants.py:3**

Module docstring mentions "experiment modules" — update wording.

- [ ] **Step 4: Tests pass**

`pytest tests/test_autoresearch_cli*.py tests/test_eval_cli*.py -x` (if exists; otherwise skip).

- [ ] **Step 5: Commit**

```
refactor: CLI user-facing text now says "round" not "experiment"
```

---

## Task 12: Misc rename sweep

**Files:**
- Modify: `autoresearch_state.py`, `improvement_recursive_improve.py`

- [ ] **Step 1: autoresearch_state.py**

- Docstrings at `:87,89,90,311`
- `non_experiment` variable at `:200,204,209,218` → `non_round`

- [ ] **Step 2: improvement_recursive_improve.py**

`:253-260` — use new `research_round_id()` helper instead of local construction (already uses `research_round_trace_exports_root`, just confirm).

- [ ] **Step 3: Commit**

```
refactor: state/improvement docstring + variable rename
```

---

## Task 13: New tests per Spec §10

**Files:**
- Create: `tests/test_research_round_id_lookup.py`

- [ ] **Step 1: Implement the three assertions**

```python
"""Spec A1 §10 — research_round_id is the unique key for a backtest."""
from pathlib import Path

import pytest

from autoresearch_runtime_paths import research_round_id
from research_memory import get_round_result


def test_get_round_result_returns_seeded_record(seeded_round_db):
    rrid = research_round_id(1, 1)
    result = get_round_result(seeded_round_db.root, research_round_id=rrid)
    assert result["research_round_id"] == rrid


def test_get_round_result_raises_keyerror_for_missing(seeded_round_db):
    with pytest.raises(KeyError):
        get_round_result(seeded_round_db.root, research_round_id="job-9-round-9")


def test_same_thesis_id_across_rounds_produces_distinct_artifacts(
    seeded_round_db,
):
    """Two backtest runs with the same thesis_id in the same job must produce
    distinct on-disk artifact dirs under
    experiments/job-1-round-1/ and experiments/job-1-round-2/.
    """
    # seed two rounds, same thesis_id
    ...
    artifact_dir_1 = ...
    artifact_dir_2 = ...
    assert artifact_dir_1 != artifact_dir_2
    assert "job-1-round-1" in str(artifact_dir_1)
    assert "job-1-round-2" in str(artifact_dir_2)
```

(Seed via the same helpers used by `test_experiment_db_sqlite_runtime.py`.)

- [ ] **Step 2: Tests pass**

`pytest tests/test_research_round_id_lookup.py -x`

- [ ] **Step 3: Commit**

```
test: add research_round_id lookup tests per Spec A1 §10
```

---

## Task 14: Migration script

**Files:**
- Create: `scripts/migrate_experiment_dirs.py`
- Create: `tests/test_migrate_experiment_dirs.py`

- [ ] **Step 1: Failing tests**

Cover four cases per Spec §10:
- `--dry-run` prints rename list, applies nothing
- `--apply` renames every dir; second invocation idempotent
- `--apply` with collision (one thesis_id → two round ids) → non-zero exit
- `--reverse --apply` undoes prior migration

```python
def test_dry_run_lists_renames_without_applying(tmp_path, monkeypatch):
    ...
    assert (tmp_path / "experiments" / "ema-x").exists()
    assert "WOULD RENAME ema-x -> job-1-round-1" in capsys.readouterr().out


def test_apply_renames_and_is_idempotent(tmp_path):
    ...
    rc1 = run_migration(tmp_path, apply=True)
    rc2 = run_migration(tmp_path, apply=True)
    assert rc1 == 0
    assert rc2 == 0
    assert (tmp_path / "experiments" / "job-1-round-1").exists()


def test_collision_aborts(tmp_path):
    # same thesis_id maps to two research_round_ids in DB
    ...
    rc = run_migration(tmp_path, apply=True)
    assert rc != 0


def test_reverse_undoes_apply(tmp_path):
    run_migration(tmp_path, apply=True)
    run_migration(tmp_path, apply=True, reverse=True)
    assert (tmp_path / "experiments" / "ema-x").exists()
```

- [ ] **Step 2: Implementation**

Style: model on `scripts/patch_openai_oauth_ai_sdk_v6_usage.py` (argparse + Path walks) and `scripts/token_audit.py` (SQLite reader).

Args: `--root` (defaults to `runtime/`), `--db` (defaults to canonical backtest db path), `--dry-run` (default true), `--apply`, `--reverse`.

Pseudocode:
1. Open the backtest db; load (thesis_id, job_id) → research_round_id map. Detect collisions (thesis_id mapping to multiple round_ids in same job) → abort with the offending pair.
2. Walk `runtime/jobs/job-*/research/round-*/experiments/`. For each subdir:
   - If name matches an existing `research_round_id` and `--reverse` not set: skip (idempotent).
   - Else look up (thesis_id, job_id) → research_round_id via the map.
   - In `--reverse`: invert (rename `research_round_id` dirs back to thesis_id).
   - In dry-run: print intended.
   - In apply: `Path.rename`.

- [ ] **Step 3: Tests pass**

`pytest tests/test_migrate_experiment_dirs.py -x`

- [ ] **Step 4: Commit**

```
feat: scripts/migrate_experiment_dirs.py

Idempotent migrator (dry-run default). Aborts on collision.
--reverse undoes a prior migration. Required by Spec A1 §6.
```

---

## Task 15: Final sweep — grep gate + full suite + PR

- [ ] **Step 1: Run grep gates**

```bash
grep -rn "get_experiment_result\b" --include="*.py"
grep -rn "list_experiment_results\b" --include="*.py"
grep -rn "GetExperimentResultArgs\|ListExperimentResultsArgs" --include="*.py"
grep -rn "fallback_experiment_id" --include="*.py"
grep -rn 'experiments/.*thesis_id\|experiments/.*\\{thesis_id\\}' --include="*.py"
grep -rn '"type": "run_experiment"\|"type": "experiment"' --include="*.py"
grep -rn '"experiment_result"' --include="*.py"
grep -rn "FULL EXPERIMENT HISTORY\|EXPERIMENT RESULTS SUMMARY\|LATEST EXPERIMENT OUTCOME" --include="*.py"
```

All must return zero matches. If hits remain, address them and re-grep.

- [ ] **Step 2: Run full pytest locally as sanity check**

`pytest -x` — paste output into PR description.

- [ ] **Step 3: Push branch, watch CI**

```bash
git push -u origin avinash8891/irvine
gh run watch --exit-status
```

- [ ] **Step 4: Open PR with grep evidence**

PR body includes the grep-gate output (zero hits per line) per Spec §10 documentation criterion.

---

## Self-review notes

**Spec coverage:** every Spec A1 §4 site mapped to a task above. §6 migration covered by Task 14. §10 success criteria covered by grep gate (Task 15) + new tests (Task 13).

**Audit coverage:** all THESIS-LOOKUP-BUG sites and RENAME-ROUND sites in the three audit reports map to tasks. Items marked KEEP-BACKTEST (e.g. `experiment_evaluation` surface, `experiment_evaluator.py`, `tests/test_experiment_db_*.py`) are explicitly out of scope.

**Type consistency:** `research_round_id` is the canonical string parameter everywhere. `BacktestRunRecord.research_round_id` (column at `backtest_run_db.py:167`) already exists. Schemas, MCP wrappers, memory helpers, validator allowlists all use the same name.

**Risks:**
- Several characterization tests snapshot `_VALID_PROCESS_TOOLS` literals across 14 files — the consolidation step in Task 6 may need to be deferred and a literal sweep done instead if export-then-import is non-trivial.
- Some `thesis_id` lookup sites in `autoresearch_research.py` (e.g. `:518-524 latest_thesis_details`) are ambiguous — they look up "the most recent round for this thesis." If the contract is "proposal-level historical context" leave as-is; if it's "this round's record" replace via `research_round_id`. Subagent should confirm by reading callers.
