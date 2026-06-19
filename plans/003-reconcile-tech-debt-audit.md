# Plan 003: Reconcile the stale tech debt audit

> **Executor instructions**: Follow this plan step by step. Run every verification command before moving on. If a STOP condition happens, stop and report.
>
> **Drift check (run first)**: `git diff --stat 6f876e9..HEAD -- TECH_DEBT_AUDIT.md pyproject.toml backtest/runner.py tests/test_backtest_output.py`
> If these files changed, compare the excerpts below against live code before editing.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/001-fix-py-modules-manifest.md, plans/002-ratchet-coverage-source-and-gate.md
- **Category**: docs
- **Planned at**: commit `6f876e9`, 2026-06-18

## Why this matters

`TECH_DEBT_AUDIT.md` is used as agent guidance, but it mixes stale TODO statuses with descriptions that say the same items are fixed. That wastes agent time and creates false positives in future audits. Reconcile it after Plans 001 and 002 so it reflects the live repo instead of old findings.

## Current state

Examples of contradictions:

```text
TECH_DEBT_AUDIT.md:52
| 🔴 TODO | F001 | ... `autoresearch_orchestration` ... absent from `py-modules` ... → **FIXED** |
```

At commit `6f876e9`, `pyproject.toml` already includes `autoresearch_orchestration`:

```toml
# pyproject.toml:25
"autoresearch_experiment",
"autoresearch_logging",
"autoresearch_orchestration",
"autoresearch_planning",
```

Another stale item is already fixed in code:

```text
TECH_DEBT_AUDIT.md:120
| 🔴 TODO | F019 | ... `backtest/runner.py:32` ... `default="/tmp"` |
```

Live code:

```python
# backtest/runner.py:32
parser.add_argument(
    "--output-dir",
    default=os.environ.get("AUTORESEARCH_OUTPUT_DIR", "."),
    help="Directory to write result.json and trades CSV",
)
```

There is already a regression test:

```text
tests/test_backtest_output.py:219
test_runner_output_dir_default_is_not_tmp
```

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Stale claim scan | `rg -n "→ \\*\\*FIXED\\*\\*|default=\"/tmp\"|fail_under|TODO \\| F00" TECH_DEBT_AUDIT.md pyproject.toml backtest/runner.py` | no contradictory stale claims |
| Targeted tests | `python3 -m pytest tests/test_dependencies.py tests/test_backtest_output.py::test_runner_output_dir_default_is_not_tmp -v` | exit 0 |

## Scope

**In scope**:
- `TECH_DEBT_AUDIT.md`

**Out of scope**:
- Production code changes.
- Reopening old findings without current code evidence.
- Rewriting the audit into a new format.

## Git workflow

- Keep the current branch name.
- Commit message style: `docs: reconcile stale tech debt audit`.

## Steps

### Step 1: Recheck each TODO row with a `→ **FIXED**` suffix

Use this scan:

```bash
rg -n "→ \\*\\*FIXED\\*\\*" TECH_DEBT_AUDIT.md
```

For each hit, verify the cited code with `rg` or `nl -ba`. If fixed, change status to `✅ STALE` and remove the TODO wording. If still present, remove the `→ **FIXED**` suffix and leave TODO.

**Verify**: `rg -n "→ \\*\\*FIXED\\*\\*" TECH_DEBT_AUDIT.md` returns no matches.

### Step 2: Reconcile F001 and F019 explicitly

- F001: after Plan 001, `pyproject.toml` should include every root module. Mark the old narrow `autoresearch_orchestration` finding stale or replace it with the broader packaged-module finding if Plan 001 has not landed.
- F019: mark stale if `backtest/runner.py` still defaults to `os.environ.get("AUTORESEARCH_OUTPUT_DIR", ".")` and the targeted test exists.

**Verify**:

```bash
rg -n "autoresearch_orchestration|output-dir|default=\"/tmp\"|AUTORESEARCH_OUTPUT_DIR" TECH_DEBT_AUDIT.md pyproject.toml backtest/runner.py tests/test_backtest_output.py
```

Expected: no audit row claims `/tmp` is still the live default.

### Step 3: Reconcile F002 after Plan 002

Update the coverage row and Top 5 coverage section with the current `pyproject.toml` source list and `fail_under` value. Keep it TODO if the gate is still below the project target, but make the description current.

**Verify**:

```bash
rg -n "fail_under|coverage gate|legacy source|45|80" TECH_DEBT_AUDIT.md pyproject.toml
```

Expected: no contradiction between the audit and `pyproject.toml`.

### Step 4: Run targeted tests

Run:

```bash
python3 -m pytest tests/test_dependencies.py tests/test_backtest_output.py::test_runner_output_dir_default_is_not_tmp -v
```

Expected: exit 0.

## Done Criteria

- [ ] No `→ **FIXED**` suffix remains on TODO rows.
- [ ] F001, F002, and F019 match the live code and tests.
- [ ] Targeted tests exit 0.
- [ ] Only `TECH_DEBT_AUDIT.md` is modified by this plan.
- [ ] `plans/README.md` status row is updated.

## STOP Conditions

Stop and report if:

- A finding cannot be verified from live code.
- Reconciliation would require production code changes.
- Plan 001 or Plan 002 has not landed and the relevant audit text depends on their result.

## Maintenance Notes

Do not treat the old audit as authoritative over live code. For future audit edits, prefer `✅ STALE` for fixed findings and reserve `🔴 TODO` for problems reproduced against the current tree.
