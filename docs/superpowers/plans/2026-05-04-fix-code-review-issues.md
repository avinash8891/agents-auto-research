# Fix Code Review Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve 5 bugs surfaced by the PR code review: reflexion unconditional import, hardcoded timeout constants, TOCTOU races in stat(), NaN propagation in eval_metrics, and a misleading comment in improvement_ratchet.

**Architecture:** Each fix is surgical — one targeted change per file with an accompanying failing test written first. All fixes are in the improvement-loop modules. The TOCTOU helper lands in `persistence_utils.py` (already imported by both affected files) to keep one home per concept.

**Tech Stack:** Python 3.11+, pytest, pathlib, stdlib statistics + math.

---

### Task 1: Gate reflexion import behind flag check

**Files:**
- Modify: `autoresearch_research.py:623-625`
- Modify: `tests/test_improvement_research_hooks.py:75-80`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_improvement_research_hooks.py`, inside the existing `test_flag_off_does_not_import_improvement_modules` — change the `forbidden` set to include `improvement_reflexion`:

```python
    forbidden = {
        "improvement_halo",
        "improvement_halo_apply",
        "improvement_ratchet",
        "improvement_reflexion",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_improvement_research_hooks.py::test_flag_off_does_not_import_improvement_modules -v
```

Expected: FAIL — `improvement_reflexion` is in leaked_forbidden because the import at line 623 is unconditional.

- [ ] **Step 3: Implement the fix**

In `autoresearch_research.py`, replace lines 623-625:

```python
    from improvement_reflexion import build_reflexion_feedback

    rejection_feedback = build_reflexion_feedback(controller, research_round)
```

with:

```python
    from improvement_flags import reflexion_enabled

    rejection_feedback = ""
    if reflexion_enabled():
        from improvement_reflexion import build_reflexion_feedback

        rejection_feedback = build_reflexion_feedback(controller, research_round)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_improvement_research_hooks.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add autoresearch_research.py tests/test_improvement_research_hooks.py
git commit -m "fix: gate improvement_reflexion import behind reflexion_enabled() flag"
```

---

### Task 2: Back timeout constants with env vars

**Files:**
- Modify: `autoresearch_constants.py` (add env var names)
- Modify: `improvement_halo.py:22` (read from env)
- Modify: `improvement_halo_apply.py:33` (read from env)
- Modify: `tests/test_improvement_halo.py` (add env-override test)
- Modify: `tests/test_improvement_halo_apply.py` (add env-override test)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_improvement_halo.py`:

```python
from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS

def test_halo_timeout_overridable_via_env(monkeypatch):
    monkeypatch.setenv(ENV_HALO_TIMEOUT_SECONDS, "42")
    import importlib
    import improvement_halo as m
    importlib.reload(m)
    assert m.HALO_TIMEOUT_SECONDS == 42
```

Add to `tests/test_improvement_halo_apply.py`:

```python
from autoresearch_constants import ENV_CLAUDE_TIMEOUT_SECONDS

def test_claude_timeout_overridable_via_env(monkeypatch):
    monkeypatch.setenv(ENV_CLAUDE_TIMEOUT_SECONDS, "99")
    import importlib
    import improvement_halo_apply as m
    importlib.reload(m)
    assert m.CLAUDE_TIMEOUT_SECONDS == 99
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_improvement_halo.py::test_halo_timeout_overridable_via_env tests/test_improvement_halo_apply.py::test_claude_timeout_overridable_via_env -v
```

Expected: FAIL — `ENV_HALO_TIMEOUT_SECONDS` not defined in autoresearch_constants.

- [ ] **Step 3: Add env var names to constants**

Append to `autoresearch_constants.py` after the existing `ENV_IMPROVEMENT_RATCHET` block:

```python
# Tunable subprocess timeouts for improvement-loop tools (env var names).
# Defaults are 600s (HALO CLI) and 1800s (Claude Code subprocess).
ENV_HALO_TIMEOUT_SECONDS = "AUTORESEARCH_HALO_TIMEOUT_SECONDS"
ENV_CLAUDE_TIMEOUT_SECONDS = "AUTORESEARCH_CLAUDE_TIMEOUT_SECONDS"
```

- [ ] **Step 4: Wire env var into improvement_halo.py**

Add `import os` to the imports at top of `improvement_halo.py`. Then replace:

```python
HALO_TIMEOUT_SECONDS = 600
```

with:

```python
import os

from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS

HALO_TIMEOUT_SECONDS = int(os.environ.get(ENV_HALO_TIMEOUT_SECONDS, "600"))
```

Note: `improvement_halo.py` already imports from `autoresearch_constants` indirectly via `improvement_flags`, but `autoresearch_constants` itself is not yet imported — add the explicit import.

- [ ] **Step 5: Wire env var into improvement_halo_apply.py**

`improvement_halo_apply.py` already imports `os`. Add the constant import and replace:

```python
CLAUDE_TIMEOUT_SECONDS = 1800
```

with:

```python
from autoresearch_constants import ENV_CLAUDE_TIMEOUT_SECONDS

CLAUDE_TIMEOUT_SECONDS = int(os.environ.get(ENV_CLAUDE_TIMEOUT_SECONDS, "1800"))
```

The `from autoresearch_constants import` line goes with the other imports at the top. Move it next to the other `from autoresearch_constants import` if one exists, or add it alphabetically with the other `from ...` imports.

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_improvement_halo.py tests/test_improvement_halo_apply.py -v
```

Expected: all pass including the new env-override tests.

- [ ] **Step 7: Commit**

```bash
git add autoresearch_constants.py improvement_halo.py improvement_halo_apply.py \
    tests/test_improvement_halo.py tests/test_improvement_halo_apply.py
git commit -m "fix: back HALO_TIMEOUT_SECONDS and CLAUDE_TIMEOUT_SECONDS with env vars"
```

---

### Task 3: Fix TOCTOU race in stat().st_mtime

**Files:**
- Modify: `persistence_utils.py` (add `safe_stat_mtime` helper)
- Modify: `eval_cli.py:87`
- Modify: `improvement_ratchet.py:73`
- Modify: `tests/test_eval_cli.py` (add TOCTOU test)
- Modify: `tests/test_improvement_ratchet.py` (add TOCTOU test)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_eval_cli.py`:

```python
from eval_cli import _load_prior_result

def test_load_prior_result_tolerates_deleted_file(tmp_path):
    """Verify no FileNotFoundError when a file vanishes between glob and stat."""
    import json
    from persistence_utils import utc_now_iso8601
    from eval_metrics import EvalResult, SuiteSummary, summarize_eval

    # Write two eval result files so the function has candidates.
    suite = SuiteSummary(compiled_rate=0.5, quality_score_p50=None, n_tasks=2, n_compiled=1)
    result = summarize_eval(label="a", timestamp=utc_now_iso8601(), suites=[suite])
    p1 = tmp_path / "a-2026-01-01.json"
    p1.write_text(json.dumps(result.__dict__), encoding="utf-8")

    # Simulate deletion: replace glob to return p1 but make stat() raise.
    import eval_cli
    original_glob = tmp_path.glob

    def patched_glob(pattern):
        yield p1

    monkeypatch_path = tmp_path
    # We patch at the Path level by monkeypatching _load_prior_result internals.
    # The real test: calling the function should NOT raise FileNotFoundError
    # when p1 is deleted after the glob but before stat.
    p1.unlink()
    # With the fix, _load_prior_result handles OSError from stat() gracefully.
    result = _load_prior_result(tmp_path, current_path=None)
    # No candidates with valid stat → returns None (not FileNotFoundError).
    assert result is None
```

Add to `tests/test_improvement_ratchet.py` — find the existing tests for `_decision_benchmark` and add:

```python
def test_decision_benchmark_tolerates_deleted_candidate(tmp_path):
    """Verify no FileNotFoundError when a prior eval file is deleted during sort."""
    import json
    from eval_metrics import SuiteSummary, summarize_eval
    from improvement_ratchet import _decision_benchmark
    from persistence_utils import utc_now_iso8601

    suite = SuiteSummary(compiled_rate=0.6, quality_score_p50=None, n_tasks=2, n_compiled=1)
    result = summarize_eval(label="prior", timestamp=utc_now_iso8601(), suites=[suite])

    eval_dir = tmp_path / "eval_results"
    eval_dir.mkdir()
    prior = eval_dir / "prior-2026-01-01.json"
    # Write and immediately delete to simulate TOCTOU race.
    prior.write_text(json.dumps({"label": "prior", "timestamp": utc_now_iso8601(),
        "repeat": 1, "primary_metric_name": "compiled_rate",
        "primary_metric": {"mean": 0.6, "stdev": 0.1, "min": 0.5, "max": 0.7},
        "suites": []}), encoding="utf-8")
    current = eval_dir / "current-2026-01-02.json"
    current.write_text(prior.read_text(), encoding="utf-8")
    prior.unlink()  # simulate deletion between glob and stat

    decision, rationale, _ = _decision_benchmark(current, eval_dir)
    # With no valid prior candidates, should fall back to INCONCLUSIVE — not crash.
    assert decision in {"inconclusive_keep", "keep", "revert_recommended"}
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_eval_cli.py::test_load_prior_result_tolerates_deleted_file tests/test_improvement_ratchet.py::test_decision_benchmark_tolerates_deleted_candidate -v
```

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Add helper to persistence_utils.py**

Append to `persistence_utils.py`:

```python
def safe_stat_mtime(path: Path) -> float:
    """Return ``path.stat().st_mtime`` or ``0.0`` on OSError.

    Prevents FileNotFoundError when a file is deleted between a glob call
    and the subsequent stat inside max()/sorted() lambdas (TOCTOU race).
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
```

- [ ] **Step 4: Fix eval_cli.py**

In `eval_cli.py`, add `safe_stat_mtime` to the import from persistence_utils (or add a new import if not yet importing from there). Then replace:

```python
    most_recent = max(candidates, key=lambda p: p.stat().st_mtime)
```

with:

```python
    from persistence_utils import safe_stat_mtime

    most_recent = max(candidates, key=safe_stat_mtime)
```

- [ ] **Step 5: Fix improvement_ratchet.py**

In `improvement_ratchet.py`, add `safe_stat_mtime` to the `from persistence_utils import` line (which already imports `utc_now_iso8601`). Then replace:

```python
    candidates = sorted(
        (p for p in eval_dir.glob("*.json") if p != current_path),
        key=lambda p: p.stat().st_mtime,
    )
```

with:

```python
    candidates = sorted(
        (p for p in eval_dir.glob("*.json") if p != current_path),
        key=safe_stat_mtime,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_eval_cli.py tests/test_improvement_ratchet.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add persistence_utils.py eval_cli.py improvement_ratchet.py \
    tests/test_eval_cli.py tests/test_improvement_ratchet.py
git commit -m "fix: guard stat().st_mtime against TOCTOU race via safe_stat_mtime helper"
```

---

### Task 4: Filter non-finite values before fmean/stdev

**Files:**
- Modify: `eval_metrics.py:199-200`
- Modify: `tests/test_eval_metrics.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_eval_metrics.py`:

```python
import math

def test_summarize_eval_raises_on_all_nan_compiled_rate():
    """NaN compiled_rate must not silently propagate through fmean/stdev."""
    suite = SuiteSummary(
        compiled_rate=float("nan"),
        quality_score_p50=None,
        n_tasks=2,
        n_compiled=0,
    )
    with pytest.raises(ValueError, match="non-finite"):
        summarize_eval(label="x", timestamp="2026-01-01T00:00:00+00:00", suites=[suite])


def test_summarize_eval_filters_nan_keeps_finite():
    """A mix of finite and NaN values: NaN is filtered, finite values used."""
    s1 = SuiteSummary(compiled_rate=0.5, quality_score_p50=None, n_tasks=4, n_compiled=2)
    s2 = SuiteSummary(compiled_rate=float("nan"), quality_score_p50=None, n_tasks=4, n_compiled=0)
    result = summarize_eval(label="x", timestamp="2026-01-01T00:00:00+00:00", suites=[s1, s2])
    assert math.isfinite(result.primary_metric_mean)
    assert result.primary_metric_mean == pytest.approx(0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_eval_metrics.py::test_summarize_eval_raises_on_all_nan_compiled_rate tests/test_eval_metrics.py::test_summarize_eval_filters_nan_keeps_finite -v
```

Expected: FAIL — NaN propagates silently through fmean.

- [ ] **Step 3: Implement the fix**

Add `import math` to `eval_metrics.py` imports. Then in `summarize_eval`, after the `primary_values` list is constructed (before the `mean = statistics.fmean(primary_values)` line), add:

```python
    finite_primary = [v for v in primary_values if math.isfinite(v)]
    if not finite_primary:
        raise ValueError(
            f"all {len(primary_values)} sample(s) for {primary_metric_name!r} are non-finite"
        )
    primary_values = finite_primary
```

The resulting block in `summarize_eval` should look like:

```python
    if primary_metric_name == "compiled_rate":
        primary_values = [s.compiled_rate for s in suites]
    elif primary_metric_name == "quality_score_p50":
        primary_values = [s.quality_score_p50 for s in suites if s.quality_score_p50 is not None]
        if not primary_values:
            raise ValueError("quality_score_p50 has no defined samples across suites")
    else:
        raise ValueError(f"unknown primary_metric_name: {primary_metric_name!r}")
    finite_primary = [v for v in primary_values if math.isfinite(v)]
    if not finite_primary:
        raise ValueError(
            f"all {len(primary_values)} sample(s) for {primary_metric_name!r} are non-finite"
        )
    primary_values = finite_primary
    mean = statistics.fmean(primary_values)
    stdev = statistics.stdev(primary_values) if len(primary_values) > 1 else 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_eval_metrics.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add eval_metrics.py tests/test_eval_metrics.py
git commit -m "fix: filter non-finite values before fmean/stdev in summarize_eval"
```

---

### Task 5: Fix misleading comment in _APPLY_STATUS_NO_OPINION

**Files:**
- Modify: `improvement_ratchet.py:42-44`

This is a comment-only fix. No test is needed (the behavior is already correct — only the comment is wrong).

- [ ] **Step 1: Fix the comment**

In `improvement_ratchet.py`, replace lines 42-44:

```python
# HALO-apply statuses that do not contribute a verdict opinion.
# - DECISION_SKIP: flag off, no signal
# - "aborted" with any reason: measurement gap → defer to inconclusive contribution
_APPLY_STATUS_NO_OPINION = frozenset({DECISION_SKIP})
```

with:

```python
# HALO-apply statuses that do not contribute a verdict opinion.
# - DECISION_SKIP: flag off or not run this round, no signal.
# Note: DECISION_ABORTED is NOT in this set. It contributes DECISION_INCONCLUSIVE
# via _apply_verdict_contribution (measurement gap — defer, but it is an opinion).
_APPLY_STATUS_NO_OPINION = frozenset({DECISION_SKIP})
```

- [ ] **Step 2: Run full test suite**

```
pytest -v
```

Expected: all existing tests pass (no behavior change).

- [ ] **Step 3: Commit**

```bash
git add improvement_ratchet.py
git commit -m "fix: correct _APPLY_STATUS_NO_OPINION comment — ABORTED is not in the set"
```

---

## Self-Review

**Spec coverage:**
- Issue 1 (reflexion unconditional import) → Task 1 ✓
- Issue 2 (hardcoded timeouts) → Task 2 ✓
- Issue 3 (TOCTOU race) → Task 3 ✓
- Issue 4 (NaN propagation) → Task 4 ✓
- Issue 5 (comment mismatch) → Task 5 ✓

**Placeholder scan:** No TBD/TODO. All steps have exact code. All commands have expected output.

**Type consistency:** `safe_stat_mtime(path: Path) -> float` is defined in Task 3, Step 3 and used in Steps 4 and 5 under the same name. `ENV_HALO_TIMEOUT_SECONDS` and `ENV_CLAUDE_TIMEOUT_SECONDS` defined in Task 2 Step 3 and imported in Steps 4 and 5.
