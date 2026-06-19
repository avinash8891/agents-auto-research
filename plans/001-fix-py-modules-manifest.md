# Plan 001: Make packaged top-level modules match the repo

> **Executor instructions**: Follow this plan step by step. Run every verification command before moving on. If a STOP condition happens, stop and report.
>
> **Drift check (run first)**: `git diff --stat 6f876e9..HEAD -- pyproject.toml tests/test_dependencies.py`
> If either file changed, compare the excerpts below against live code before editing.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `6f876e9`, 2026-06-18

## Why this matters

`pip install -e .` hides packaging omissions because imports resolve from the checkout. A wheel or non-editable install only gets the modules declared in `pyproject.toml`, and 18 current root modules are not declared. Several missing modules are imported by declared modules, so a packaged runtime can fail after deployment even when CI passes.

## Current state

- `pyproject.toml` uses explicit `tool.setuptools.py-modules`; packages are limited to `backtest*`, `strategies*`, and `trace_adapters*`.
- `tests/test_dependencies.py` has a narrow regression test that only checks two helper modules.

Current excerpt:

```toml
# pyproject.toml:10
[tool.setuptools.packages.find]
include = ["backtest*", "strategies*", "trace_adapters*"]

# pyproject.toml:14
[tool.setuptools]
py-modules = [
    "agent_infra",
    ...
    "walkforward",
]
```

```python
# tests/test_dependencies.py:32
def test_explicit_py_modules_include_helper_modules_used_by_packaged_modules() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    py_modules = set(pyproject["tool"]["setuptools"]["py-modules"])

    assert "causal_rule" in py_modules
    assert "feature_table_extractors" in py_modules
```

Advisor read-only check at `6f876e9` found these root modules missing from `py-modules`:

```text
analyst_dataframe_helpers
autoresearch_artifact_schemas
autoresearch_paths
autoresearch_runtime_paths
behavior_signals
diagnostic_contracts
eval_cli
eval_harness
eval_metrics
improvement_flags
improvement_halo
improvement_halo_apply
improvement_ratchet
improvement_recursive_improve
improvement_reflexion
reflexio_agent_reflections
rejection_artifact
web_research_cli
```

Representative imports proving runtime reachability:

```text
autoresearch_research.py imports autoresearch_artifact_schemas, autoresearch_runtime_paths, rejection_artifact, improvement_flags, improvement_reflexion, eval_harness
research_subagents.py imports web_research_cli
thesis_validator.py imports behavior_signals
compiler_implementation_verify.py imports diagnostic_contracts
```

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Targeted test | `python3 -m pytest tests/test_dependencies.py -v` | exit 0 |
| Full CI gate | GitHub Actions `CI` after push | pre-commit, Bandit, prompt drift, pytest all pass |

## Scope

**In scope**:
- `pyproject.toml`
- `tests/test_dependencies.py`

**Out of scope**:
- Moving modules into packages.
- Adding new packaging dependencies.
- Changing CI install mode.

## Git workflow

- Keep the current branch name.
- Commit message style: conventional, e.g. `fix: include all root modules in package manifest`.
- Per repo rules, push and verify the GitHub Actions `CI` workflow if this is executed as code work.

## Steps

### Step 1: Make the test fail for any omitted root module

Replace `test_explicit_py_modules_include_helper_modules_used_by_packaged_modules` with a general check:

- Load `pyproject.toml`.
- Build `declared = set(pyproject["tool"]["setuptools"]["py-modules"])`.
- Build `root_modules = {path.stem for path in repo_root.glob("*.py") if path.name != "__init__.py"}`.
- Assert `sorted(root_modules - declared) == []`.
- Keep or add a second assertion that `sorted(declared - root_modules) == []`; `tests/test_step0_dead_code_deletion.py` already checks deleted entries, but one local test here makes packaging drift obvious.

**Verify**: `python3 -m pytest tests/test_dependencies.py::test_explicit_py_modules_include_helper_modules_used_by_packaged_modules -v` should fail and list the missing modules above.

### Step 2: Add the missing modules to `pyproject.toml`

Add the 18 missing module names to `tool.setuptools.py-modules`. Keep the list alphabetically grouped enough to remain readable; do not rename modules.

**Verify**: `python3 -m pytest tests/test_dependencies.py::test_explicit_py_modules_include_helper_modules_used_by_packaged_modules -v` exits 0.

### Step 3: Run the dependency file

Run all dependency checks.

**Verify**: `python3 -m pytest tests/test_dependencies.py -v` exits 0.

## Done Criteria

- [ ] `tests/test_dependencies.py` compares all root `*.py` modules to `tool.setuptools.py-modules`.
- [ ] `pyproject.toml` declares every current root Python module.
- [ ] `python3 -m pytest tests/test_dependencies.py -v` exits 0.
- [ ] No files outside the in-scope list are modified.
- [ ] `plans/README.md` status row is updated.

## STOP Conditions

Stop and report if:

- A missing module is intentionally not meant to be packaged; that needs a maintainer decision, not an executor guess.
- `pyproject.toml` has switched away from explicit `py-modules`.
- Fixing the test requires moving files or changing import style.

## Maintenance Notes

This is intentionally boring: the repo already chose explicit top-level module packaging. Future additions of root `*.py` files should fail `tests/test_dependencies.py` until `pyproject.toml` is updated.
