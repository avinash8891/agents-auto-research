# Autoresearch Root-Cause Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent recurring autoresearch bugs caused by split roots, drifted artifact schemas, unwired safety rails, strategy-family divergence, and prompts/docs that claim unsupported behavior.

**Architecture:** Introduce canonical runtime/path and artifact-schema APIs, then delete duplicate per-call-site logic as callers move onto those APIs. Add CI guardrails that fail when safety rails or prompt claims drift away from production code.

**Tech Stack:** Python 3.12, pathlib, dataclasses, Pydantic where already used, pytest, pre-commit, GitHub Actions.

---

## File Structure

- Modify `autoresearch_runtime_paths.py`: owns `AutoresearchRuntimeContext`, all runtime DB paths, job paths, and artifact roots.
- Modify `autoresearch_controller.py`: constructs one runtime context and exposes paths from it.
- Modify `research_memory.py`, `thesis_validator.py`, `research_tools_mcp.py`, `research_subagents.py`: consume runtime DB paths from the context helper instead of globbing roots independently.
- Create `autoresearch_artifact_schemas.py`: canonical round/result/artifact payload models and writer/reader helpers.
- Modify `autoresearch_research.py`, `autoresearch_planning.py`: use canonical round artifact schema for `round.json`.
- Modify `scripts/check_prompt_drift.py`: add checks for unwired safety rails and documented prompt/tool contracts.
- Modify `.github/workflows/ci.yml`: keep prompt drift wired and run any new guardrail script.
- Add tests in `tests/test_autoresearch_runtime_paths.py`, `tests/test_autoresearch_artifact_schemas.py`, `tests/test_research_memory.py`, and `tests/test_conductor_prompt_v3.py`.

## Task 1: Runtime Context Is The Single Root Authority

**Files:**
- Modify: `autoresearch_runtime_paths.py`
- Modify: `autoresearch_controller.py`
- Test: `tests/test_autoresearch_runtime_paths.py`
- Test: `tests/test_autoresearch_controller_characterization.py`

- [ ] **Step 1: Write failing runtime-context tests**

```python
def test_runtime_context_separates_code_root_runtime_root_and_family_db(tmp_path):
    from autoresearch_runtime_paths import AutoresearchRuntimeContext

    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime"
    code_root.mkdir()
    runtime_root.mkdir()

    ctx = AutoresearchRuntimeContext.for_family(
        code_root=code_root,
        runtime_root=runtime_root,
        family_name="ema",
    )

    assert ctx.code_root == code_root.resolve()
    assert ctx.runtime_root == runtime_root.resolve()
    assert ctx.backtest_db_path == runtime_root / "ema_backtest_runs.db"
    assert ctx.baseline_checkpoints_path == runtime_root / "ema_baseline_checkpoints.json"
    assert ctx.jobs_root == runtime_root / "runtime" / "jobs"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_autoresearch_runtime_paths.py::test_runtime_context_separates_code_root_runtime_root_and_family_db -v`

Expected: FAIL with `ImportError` or `AttributeError` because `AutoresearchRuntimeContext` does not exist.

- [ ] **Step 3: Implement the context**

Add a frozen dataclass to `autoresearch_runtime_paths.py`:

```python
@dataclass(frozen=True)
class AutoresearchRuntimeContext:
    code_root: Path
    runtime_root: Path
    family_name: str
    state_path: Path
    current_md_path: Path
    jobs_root: Path
    backtest_db_path: Path
    baseline_checkpoints_path: Path

    @classmethod
    def for_family(cls, *, code_root: Path, runtime_root: Path, family_name: str) -> "AutoresearchRuntimeContext":
        code_root = code_root.resolve()
        runtime_root = runtime_root.resolve()
        prefix = "" if family_name == "orb" else f"{family_name}_"
        return cls(
            code_root=code_root,
            runtime_root=runtime_root,
            family_name=family_name,
            state_path=runtime_root / f"{prefix}autoresearch.next.json",
            current_md_path=runtime_root / f"{prefix}autoresearch.current.md",
            jobs_root=runtime_root / "runtime" / "jobs",
            backtest_db_path=runtime_root / f"{family_name}_backtest_runs.db",
            baseline_checkpoints_path=runtime_root / f"{family_name}_baseline_checkpoints.json",
        )
```

- [ ] **Step 4: Wire controller to context**

Replace independent path derivation in `AutoresearchController.__init__` with `AutoresearchRuntimeContext.for_family(...)`. Preserve constructor overrides by resolving relative override paths against `runtime_root`.

- [ ] **Step 5: Verify GREEN**

Run: `pytest tests/test_autoresearch_runtime_paths.py tests/test_autoresearch_controller_characterization.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add autoresearch_runtime_paths.py autoresearch_controller.py tests/test_autoresearch_runtime_paths.py tests/test_autoresearch_controller_characterization.py
git commit -m "refactor: centralize autoresearch runtime paths"
```

## Task 2: Runtime DB Discovery Uses Context, Not Per-Call-Site Globs

**Files:**
- Modify: `autoresearch_runtime_paths.py`
- Modify: `research_memory.py`
- Modify: `thesis_validator.py`
- Modify: `research_subagents.py`
- Test: `tests/test_research_memory.py`
- Test: `tests/test_thesis_validator.py`

- [ ] **Step 1: Write failing tests for split-root DB discovery**

Add assertions that `research_memory._iter_backtest_db_paths(code_root, family="ema")` and `thesis_validator._iter_backtest_db_paths(code_root, family="ema")` return only the runtime-root EMA DB when `AUTORESEARCH_RUNTIME_ROOT` points outside the code root.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_research_memory.py::test_iter_backtest_db_paths_uses_runtime_context_family_filter tests/test_thesis_validator.py::test_iter_backtest_db_paths_uses_runtime_context_family_filter -v`

Expected: FAIL because each module still owns its own discovery logic.

- [ ] **Step 3: Add shared DB-path helpers**

Add `iter_family_backtest_db_paths(code_root: Path, *, family: str | None = None) -> list[Path]` to `autoresearch_runtime_paths.py`. It should resolve the runtime root once, include code root only for local-backcompat, dedupe paths, and apply family filtering before returning paths.

- [ ] **Step 4: Delete duplicate discovery logic**

Replace `_iter_backtest_db_paths` implementations in `research_memory.py`, `thesis_validator.py`, and `research_subagents.py` with the shared helper. Remove local root-list construction.

- [ ] **Step 5: Verify GREEN**

Run: `pytest tests/test_research_memory.py tests/test_thesis_validator.py tests/test_research_conductor_paths.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add autoresearch_runtime_paths.py research_memory.py thesis_validator.py research_subagents.py tests/test_research_memory.py tests/test_thesis_validator.py
git commit -m "refactor: use shared runtime db discovery"
```

## Task 3: Round Artifacts Have One Schema

**Files:**
- Create: `autoresearch_artifact_schemas.py`
- Modify: `autoresearch_research.py`
- Modify: `autoresearch_planning.py`
- Test: `tests/test_autoresearch_artifact_schemas.py`
- Test: `tests/test_autoresearch_planning.py`

- [ ] **Step 1: Write failing writer-reader round artifact test**

```python
def test_round_artifact_writer_shape_is_terminal_reader_shape(tmp_path):
    from autoresearch_artifact_schemas import RoundArtifact, write_round_artifact, read_round_artifact
    from autoresearch_planning import should_terminate

    round_dir = tmp_path / "runtime" / "jobs" / "job-1" / "research" / "round-1"
    artifact = RoundArtifact(
        status="completed",
        outcome="research_exhausted",
        round_number=1,
        selected_thesis_id="",
        generated_configs=[],
        new_theses_generated=0,
        suggested_theses=[],
        findings=["no remaining credible thesis"],
    )
    write_round_artifact(round_dir / "round.json", artifact)

    assert read_round_artifact(round_dir / "round.json") == artifact
    assert should_terminate(tmp_path, family=None, run_queue_dir=tmp_path, research_dir=round_dir, results=[], job=1)
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_autoresearch_artifact_schemas.py::test_round_artifact_writer_shape_is_terminal_reader_shape -v`

Expected: FAIL because the schema module does not exist.

- [ ] **Step 3: Implement schema and helpers**

Create `RoundArtifact` as a Pydantic model or frozen dataclass with explicit defaults. The writer must serialize only the canonical field names the reader consumes.

- [ ] **Step 4: Replace manual `round.json` writes and reads**

Use `write_round_artifact` in `autoresearch_research._write_round_artifacts` and `read_round_artifact` in `autoresearch_planning.should_terminate`. Delete local shape assumptions.

- [ ] **Step 5: Verify GREEN**

Run: `pytest tests/test_autoresearch_artifact_schemas.py tests/test_autoresearch_planning.py tests/test_autoresearch_research.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add autoresearch_artifact_schemas.py autoresearch_research.py autoresearch_planning.py tests/test_autoresearch_artifact_schemas.py tests/test_autoresearch_planning.py
git commit -m "refactor: share round artifact schema"
```

## Task 4: CI Fails On Unwired Safety Rails

**Files:**
- Modify: `scripts/check_prompt_drift.py`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_conductor_prompt_v3.py`

- [ ] **Step 1: Add failing guardrail tests**

Add tests that assert every safety function named in prompts/docs has at least one production caller outside its defining module. Start with `normalize_config_changes`, `_enforce_tool_models`, and `create_executable_artifact`.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_conductor_prompt_v3.py::test_safety_rails_named_in_prompts_have_production_callers -v`

Expected: FAIL if any named safety rail is unwired.

- [ ] **Step 3: Implement static caller discovery**

Extend `scripts/check_prompt_drift.py` with AST or regex-based checks that ignore tests and the defining module. Keep the allowlist explicit and short.

- [ ] **Step 4: Wire or remove unwired rails**

For each failure, either wire the production caller or remove the prompt/doc claim and corresponding dead code. Prefer deletion when the rail is not part of the current architecture.

- [ ] **Step 5: Verify GREEN**

Run: `python scripts/check_prompt_drift.py && pytest tests/test_conductor_prompt_v3.py -q`

Expected: guardrail script exits 0 and tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_prompt_drift.py .github/workflows/ci.yml tests/test_conductor_prompt_v3.py
git commit -m "test: fail ci on unwired safety rails"
```

## Task 5: Strategy Families Share A Backtest Contract

**Files:**
- Create: `strategies/contract.py`
- Modify: `strategies/ema/*`
- Modify: `strategies/orb/*`
- Test: `tests/test_strategy_family_contract.py`

- [ ] **Step 1: Write failing cross-family contract tests**

Test that every registered strategy family declares fill convention, EOD policy, entry-bar stop policy, unbounded-run policy, and result schema version.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_strategy_family_contract.py -v`

Expected: FAIL because no shared contract exists.

- [ ] **Step 3: Implement shared contract**

Create a dataclass or Pydantic model for strategy-family backtest semantics. Register one instance per strategy family.

- [ ] **Step 4: Delete contradictory per-family defaults**

Remove duplicated implicit behavior where possible. Replace hidden defaults with contract fields.

- [ ] **Step 5: Verify GREEN**

Run: `pytest tests/test_strategy_family_contract.py tests/test_ema_backtest_characterization.py tests/test_orb_* -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add strategies tests/test_strategy_family_contract.py
git commit -m "refactor: require shared strategy backtest contract"
```

## Task 6: Docs And Prompts Are Generated From Code Contracts

**Files:**
- Modify: `research_prompts.py`
- Modify: `agent_prompts.py`
- Modify: `scripts/check_prompt_drift.py`
- Test: `tests/test_conductor_prompt_v3.py`

- [ ] **Step 1: Write failing prompt-ground-truth tests**

Assert prompts cannot mention unsupported config keys, primitives, fake metrics, or tool names.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_conductor_prompt_v3.py -q`

Expected: FAIL for any current unsupported claim.

- [ ] **Step 3: Use code contracts as prompt inputs**

Replace hand-maintained prompt lists with data derived from strategy specs, research tool schemas, and validator-inspected fields.

- [ ] **Step 4: Verify GREEN**

Run: `python scripts/check_prompt_drift.py && pytest tests/test_conductor_prompt_v3.py -q`

Expected: no prompt drift.

- [ ] **Step 5: Commit**

```bash
git add research_prompts.py agent_prompts.py scripts/check_prompt_drift.py tests/test_conductor_prompt_v3.py
git commit -m "refactor: ground prompts in code contracts"
```

## Self-Review

- Spec coverage: the plan maps directly to the five root patterns: roots, schema drift, unwired rails, strategy drift, and prompt/doc falsehoods.
- Placeholder scan: no task uses TBD/TODO/later language.
- Type consistency: `AutoresearchRuntimeContext`, `RoundArtifact`, and shared DB helper names are consistent across tasks.
