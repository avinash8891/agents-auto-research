# Plan 002: Make coverage measure current runtime code before ratcheting

> **Executor instructions**: Follow this plan step by step. Run every verification command before moving on. If a STOP condition happens, stop and report.
>
> **Drift check (run first)**: `git diff --stat 6f876e9..HEAD -- pyproject.toml tests TECH_DEBT_AUDIT.md`
> If these files changed, compare the excerpts below against live code before editing.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/001-fix-py-modules-manifest.md
- **Category**: tests
- **Planned at**: commit `6f876e9`, 2026-06-18

## Why this matters

The CI coverage gate is `45`, and the measured source list excludes many current runtime modules. Raising straight to 80 is likely noisy; measuring the right modules first creates a real baseline and a ratchet that future changes can enforce. This plan is scoped to the smallest useful improvement: align coverage source with packaged runtime modules, then ratchet to the observed passing floor.

## Current state

`pyproject.toml` only measures a legacy subset:

```toml
# pyproject.toml:118
[tool.coverage.run]
source = [
    "autoresearch_controller",
    "autoresearch_state",
    "autoresearch_artifacts",
    "autoresearch_planning",
    "autoresearch_research",
    "autoresearch_experiment",
    "autoresearch_orchestration",
    "compiler_pipeline",
    "research_conductor",
]
omit = ["tests/*"]

# pyproject.toml:141
# Current baseline for the configured legacy source set is ~45.7%.
# Keep CI enforcing no broad collapse, then ratchet this upward with focused coverage work.
fail_under = 45
```

`TECH_DEBT_AUDIT.md` still calls out the coverage debt:

```text
TECH_DEBT_AUDIT.md:53
Coverage gate is 45%; project rule (CLAUDE.md) requires 80%.
```

Repo verification convention:

- Local targeted checks are allowed.
- Full-suite verification for normal code changes is GitHub Actions `CI`, not local full-suite pytest.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Local coverage probe | `python3 -m pytest --cov --cov-report=term-missing` | exits 0 locally if cheap enough; otherwise use CI |
| Targeted dependency test | `python3 -m pytest tests/test_dependencies.py -v` | exit 0 |
| Full CI gate | GitHub Actions `CI` after push | pre-commit, Bandit, prompt drift, pytest all pass |

## Scope

**In scope**:
- `pyproject.toml`
- `tests/test_dependencies.py` if needed to guard coverage source drift
- `TECH_DEBT_AUDIT.md` only to update the coverage row/status after the new baseline is known

**Out of scope**:
- Writing broad new tests for uncovered modules.
- Refactoring production code to improve coverage.
- Raising directly to 80 unless it already passes.

## Git workflow

- Keep the current branch name.
- Commit message style: `test: measure current runtime modules in coverage`.
- Push and verify GitHub Actions `CI`.

## Steps

### Step 1: Depend on the fixed package manifest

Complete Plan 001 first. Coverage source should be based on the modules the project packages, not a hand-maintained legacy subset.

**Verify**: `python3 -m pytest tests/test_dependencies.py -v` exits 0.

### Step 2: Replace the legacy coverage source list

In `pyproject.toml`, set coverage `source` to the runtime package/module surface:

- Include package directories: `backtest`, `strategies`, `trace_adapters`.
- Include all top-level modules declared in `tool.setuptools.py-modules`.
- Keep `omit = ["tests/*"]`.

Use a plain TOML list. Do not add generated/runtime directories.

**Verify**: `python3 -m pytest --cov --cov-report=term-missing` runs far enough to print a coverage total. If the command is too slow locally, push and use the GitHub Actions `CI` coverage output as the source of truth.

### Step 3: Set `fail_under` to the new passing baseline

Use the total coverage from Step 2. Set `fail_under` to the integer floor that passes today and is higher than 45 if possible. If the broader source list drops below 45, keep `45` and update the comment to say broader measurement exposed a lower real baseline.

Do not chase coverage by adding superficial tests in this plan.

**Verify**: the same coverage command exits 0 with the chosen gate.

### Step 4: Update the audit note

In `TECH_DEBT_AUDIT.md`, update the F002 coverage row and Top 5 wording to match the new source list and gate. If 80 is still not reached, keep it TODO and state the new measured baseline.

**Verify**: `rg -n "fail_under|coverage gate|45|80" TECH_DEBT_AUDIT.md pyproject.toml` shows no stale claim that contradicts the live config.

## Done Criteria

- [ ] Coverage source includes current packaged modules and packages.
- [ ] `fail_under` is set to the observed passing baseline for that source set.
- [ ] Coverage comments in `pyproject.toml` describe the current baseline, not the old legacy subset.
- [ ] `TECH_DEBT_AUDIT.md` no longer claims a stale coverage state.
- [ ] GitHub Actions `CI` passes after push.
- [ ] `plans/README.md` status row is updated.

## STOP Conditions

Stop and report if:

- Plan 001 is not done.
- Coverage collection crashes before producing a report.
- The broader source list creates an impractically low baseline and the maintainer needs to choose between broad measurement and a short-term gate.
- Fixing the gate appears to require broad new tests or production refactors.

## Maintenance Notes

Reviewers should scrutinize that the new source list is generated from real runtime surface, not padded to preserve a nicer percentage. The next plan after this should add focused tests for the highest-risk uncovered modules rather than gaming the metric.
