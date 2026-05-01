# Task Proposals from Codebase Review

## 1) Typo fix task
**Issue found:** `research_prompts.py` contains `u2014` instead of an em dash in researcher instructions, which degrades prompt readability and may affect model interpretation.

**Proposed task:** Replace `u2014` with a proper em dash (`—`) in the researcher prompt and add a small regression test that fails if escaped Unicode artifacts reappear in long prompt literals.

**Acceptance criteria:**
- Prompt text uses `—` (or a plain ASCII alternative like `-`) rather than `u2014`.
- New test checks that key prompt templates do not contain `u2014`-style artifact substrings.

## 2) Bug fix task
**Issue found:** The normalized tracker still lists unresolved high-severity Research Loop Orchestration bugs with `needs_repro` status (e.g., B007 / rlo-013), meaning there are known-but-unreproduced failure modes in a critical loop.

**Proposed task:** Reproduce and close one `needs_repro` RLO item (start with B007 / rlo-013) by creating a deterministic reproducer and implementing the minimum code fix.

**Acceptance criteria:**
- A targeted test or scripted reproducer fails on pre-fix code and passes post-fix.
- `BUGS.md` entry for selected bug is updated from `needs_repro` to `fixed` with concrete evidence.

## 3) Code comment/documentation discrepancy task
**Issue found:** `pyproject.toml` comments describe ignoring both `E402` and `F401`, but `ignore = ["E402"]` only configures `E402`.

**Proposed task:** Reconcile comments and config by either adding `F401` to ignore list (if intentional) or removing/updating the stale comment text.

**Acceptance criteria:**
- Ruff config and adjacent comments agree on what is intentionally ignored.
- A lint run confirms no surprise `F401` regressions or hidden exemptions.

## 4) Test improvement task
**Issue found:** `README.md` describes local pre-commit flow, and the repo has a test for `.pre-commit-config.yaml`, but there is no guard ensuring README’s setup snippet stays aligned with actual tooling expectations.

**Proposed task:** Add a lightweight test that validates the README development setup commands remain synchronized with repository tooling files (e.g., references to pre-commit and installation command structure).

**Acceptance criteria:**
- New test fails if the development instructions in README drift from the expected setup pattern.
- Test is scoped to stable text invariants to avoid fragile formatting failures.
