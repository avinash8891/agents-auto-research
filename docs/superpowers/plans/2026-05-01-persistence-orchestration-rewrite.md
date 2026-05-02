# Persistence and Orchestration Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make persistence and orchestration deterministic, round-trip safe, and easier to extend without hidden state or full-table rewrite behavior.

**Architecture:** Keep SQLite and the controller, but stop treating `experiment_db.py` and `autoresearch_controller.py` as monoliths. Move serialization policy into dedicated helpers, keep DB access focused on storage, and keep orchestration focused on state transitions. Preserve current file formats and command-line behavior where possible, but fix the places where falsey metrics, non-finite values, and hidden controller context create correctness and maintenance risk.

**Tech Stack:** Python 3.10+, `sqlite3`, `json`, `pytest`, existing repo helpers in `persistence_utils.py`, `experiment_db.py`, `autoresearch_state.py`, `autoresearch_cli.py`, `autoresearch_controller.py`

---

### Task 1: Lock the current behavior down with regression tests

**Files:**
- Modify: `tests/test_experiment_db_sqlite_runtime.py`
- Modify: `tests/test_autoresearch_cli.py`
- Modify: `tests/test_autoresearch_state.py`
- Modify: `tests/test_autoresearch_controller_characterization.py`

- [ ] **Step 1: Add a test for strict JSON round-trip of non-finite metrics**

```python
def test_non_finite_metrics_round_trip_through_sqlite_and_cli(tmp_path):
    # Arrange: create a session, store a record with float("inf"), read it back.
    # Assert: the stored row can be read, CLI stats can compute, and the metric is float("inf") again.
    ...
```

- [ ] **Step 2: Add a test for custom primary metric export/import**

```python
def test_custom_primary_metric_preserved_during_export_import(tmp_path):
    # Arrange: session_meta metricName="calmar", a record with calmar only in validation_metrics.
    # Assert: export_entries() writes primary_metric_name="calmar" and import_entries() restores it.
    ...
```

- [ ] **Step 3: Add a test for zero-valued metrics not being dropped**

```python
def test_zero_metric_is_not_treated_as_missing(tmp_path):
    # Arrange: train_metrics or validation_metrics contains 0.0.
    # Assert: best_by_metric() and conductor formatting still see the value.
    ...
```

- [ ] **Step 4: Run the focused tests and confirm they fail for the current code**

Run:

```bash
pytest tests/test_experiment_db_sqlite_runtime.py tests/test_autoresearch_cli.py tests/test_autoresearch_state.py tests/test_autoresearch_controller_characterization.py -q
```

Expected: failures that point at strict JSON handling, custom metric round-trip, or falsey-metric handling.

---

### Task 2: Separate JSON policy from storage policy

**Files:**
- Modify: `persistence_utils.py`
- Modify: `experiment_db.py`
- Modify: `autoresearch_cli.py`
- Modify: `backtest/output.py` if needed for payload symmetry

- [ ] **Step 1: Add a read-side JSON normalizer that converts `"Infinity"`, `"-Infinity"`, and `"NaN"` back to floats before numeric code sees them**

```python
def json_loads_relaxed(payload: str) -> Any:
    return _json_relax_value(json.loads(payload))
```

- [ ] **Step 2: Use relaxed JSON reads everywhere persistence is read back into Python objects**

```python
# experiment_db._load()
runtime_config=json_loads_relaxed(row["runtime_config_json"])
train_metrics=json_loads_relaxed(row["train_metrics_json"])
validation_metrics=json_loads_relaxed(row["validation_metrics_json"])
strategy_diagnostics=json_loads_relaxed(row["strategy_diagnostics_json"])
usage=json_loads_relaxed(row["usage_json"])
```

- [ ] **Step 3: Normalize CLI session reads before statistics and confidence calculations**

```python
metric = record.validation_metrics.get(primary_metric_name)
if metric is None:
    metric = record.train_metrics.get(primary_metric_name)
metric = float(metric) if metric is not None else None
```

- [ ] **Step 4: Keep strict JSON on write, but make the write/read contract explicit in comments and tests**

```python
# Non-finite floats are written as string sentinels on disk and converted back on load.
```

- [ ] **Step 5: Run the JSON and CLI tests**

Run:

```bash
pytest tests/test_experiment_db_sqlite_runtime.py tests/test_autoresearch_cli.py -q
```

Expected: metrics with `inf`/`nan` no longer break session summary or confidence calculations.

---

### Task 3: Fix metric selection and export/import round-trips

**Files:**
- Modify: `experiment_db.py`
- Modify: `autoresearch_cli.py`
- Modify: `autoresearch_experiment.py`
- Modify: `autoresearch_state.py`

- [ ] **Step 1: Replace truthiness-based metric lookup with explicit `is not None` checks**

```python
val = r.train_metrics.get(metric)
if val is None:
    val = r.validation_metrics.get(metric)
```

- [ ] **Step 2: Preserve `primary_metric_name` in exported entries and restore it on import**

```python
return {
    "metric": _coerce_metric_float(primary_metric_value),
    "primary_metric_name": primary_metric_name,
    "metrics": metrics,
    ...
}
```

- [ ] **Step 3: Make `read_results()` and conductor formatting use the configured primary metric consistently**

```python
metric = record.validation_metrics.get(primary_metric_name)
if metric is None:
    metric = record.train_metrics.get(primary_metric_name, 0.0)
```

- [ ] **Step 4: Remove any remaining export paths that hardcode `median_expectancy` as the canonical score**

```python
# Audit: autoresearch_cli.read_session(), experiment_db.read_results(), _record_to_entry(), _entry_to_record()
```

- [ ] **Step 5: Re-run the round-trip tests**

Run:

```bash
pytest tests/test_experiment_db_sqlite_runtime.py tests/test_autoresearch_cli.py tests/test_autoresearch_state.py -q
```

Expected: export/import round-trips for a custom primary metric preserve the score and the metric name.

---

### Task 4: Split orchestration into smaller, testable boundaries

**Files:**
- Modify: `autoresearch_orchestration.py` - existing orchestration seam; keep it as the single decision-flow module
- Modify: `autoresearch_controller.py`
- Modify: `autoresearch_experiment.py`
- Modify: `autoresearch_research.py`
- Modify: `tests/test_autoresearch_controller_characterization.py`

- [ ] **Step 1: Refine the controller’s pure decision flow in the existing orchestration module**

```python
def resolve_next_action(controller, state):
    resumed = try_resume_halted_thesis(controller)
    if resumed is not None:
        return resumed
    baseline_action = check_baseline_rerun(controller)
    if baseline_action:
        return apply_forced_baseline_rerun(controller, baseline_action)
    return reconcile_state(controller)
```

- [ ] **Step 2: Move `RunContext`-dependent helper sequencing behind explicit function arguments**

```python
def derive_trade_analysis(root, family_name, config, metric, decision, output, contract=None):
    ...
```

- [ ] **Step 3: Keep `AutoresearchController` as a thin facade over state, DB, and orchestration services**

```python
class AutoresearchController:
    def execute_once(self) -> int:
        state = resolve_next_action(self, self.read_state())
        ...
```

- [ ] **Step 4: Preserve all public CLI and entrypoint names so callers do not break**

```python
# Keep: autoresearch_controller.main, AutoresearchController, default_controller_paths
```

- [ ] **Step 5: Add a characterization test for the orchestration seam**

```python
def test_execute_once_transitions_from_blocked_to_running(monkeypatch):
    # Assert the same visible state transition still occurs after the refactor.
    ...
```

- [ ] **Step 6: Run the controller characterization tests**

Run:

```bash
pytest tests/test_autoresearch_controller_characterization.py tests/test_autoresearch_research.py -q
```

Expected: the external behavior is unchanged, but the controller internals are thinner.

---

### Task 5: Reduce persistence write amplification

**Files:**
- Modify: `experiment_db.py`
- Modify: `tests/test_experiment_db_crash_consistency.py`
- Modify: `tests/test_experiment_db_sqlite_runtime.py`

- [ ] **Step 1: Replace the full-table rewrite path with row-level upserts where safe**

```python
def add(self, result: ExperimentResult) -> None:
    # Use INSERT OR REPLACE for a single record instead of rewriting the whole table.
    ...
```

- [ ] **Step 2: Keep export/import as an explicit reconciliation path, but stop using it for normal single-record writes**

```python
def import_entries(self, entries):
    # Batch reconciliation only.
```

- [ ] **Step 3: Verify the crash-consistency tests still pass**

Run:

```bash
pytest tests/test_experiment_db_crash_consistency.py tests/test_experiment_db_sqlite_runtime.py -q
```

Expected: no regressions in durability, and single writes stop triggering whole-database rewrites.

---

### Task 6: Final validation and cleanup

**Files:**
- Modify: any files changed above if tests expose edge cases
- Modify: `docs/` only if behavior changed externally

- [ ] **Step 1: Run the full autoresearch and backtest test subset**

Run:

```bash
pytest tests/test_autoresearch_cli.py tests/test_autoresearch_state.py tests/test_autoresearch_controller_characterization.py tests/test_experiment_db_sqlite_runtime.py tests/test_experiment_db_crash_consistency.py tests/test_backtest_output.py tests/test_metrics.py -q
```

- [ ] **Step 2: Inspect for remaining falsey-value checks and hidden-state usage**

```bash
rg -n " or .*validation_metrics| or .*train_metrics|if .*get\\(.*\\):|self\\.ctx\\.|current_artifact_dir|parent_experiment_id" .
```

- [ ] **Step 3: Commit the refactor in one coherent slice only after all tests are green**

```bash
git add <touched files>
git commit -m "refactor: split persistence policy and orchestration seams"
```

---

**Coverage check**
- Persistence round-trips: covered by Task 1, Task 2, Task 3, Task 5
- Custom metric sessions: covered by Task 1, Task 3
- Performance/write amplification: covered by Task 5
- Orchestration seam cleanup: covered by Task 4
- Regression verification: covered by Task 6
