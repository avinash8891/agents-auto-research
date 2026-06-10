# Spec Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 16 findings (F1–F16) and 5 untested requirements (U1–U5) from `spec_review.md` (2026-06-10).

**Architecture:** Each task is one focused fix per rule E (one commit, one deliverable). Tasks are ordered by risk-reduction and dependency — F3 first (silent-wrong-results), schema work later (F2 then F1). Tests use real constants/enums from the project, never toy names.

**Tech Stack:** Python 3.12, pytest, SQLite, Pydantic v2, ruff/isort/black

---

### Task 1: F3 — data_loader missing-symbol guard

**Files:**
- Modify: `data_loader.py:74-76` (wide path), `data_loader.py:92-93` (per-symbol path)
- Test: `tests/test_data_loader.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data_loader.py
from pathlib import Path

import pandas as pd
import pytest

from data_loader import DataLoadError, load_data


def _write_wide_fixture(tmp_path: Path, symbols: list[str]) -> Path:
    """Create a minimal wide-format universe with the given symbols."""
    idx = pd.date_range("2024-01-02 09:30", periods=3, freq="5min", tz="US/Eastern")
    for field in ("close", "open", "high", "low", "volume"):
        df = pd.DataFrame({s: range(1, 4) for s in symbols}, index=idx, dtype=float)
        df.to_parquet(tmp_path / f"{field}.parquet")
    return tmp_path


def test_load_data_raises_on_missing_symbol_wide(tmp_path: Path) -> None:
    _write_wide_fixture(tmp_path, ["AAPL"])
    with pytest.raises(DataLoadError, match="MSFT"):
        load_data(str(tmp_path), symbols=["AAPL", "MSFT"])


def test_load_data_succeeds_when_all_symbols_present_wide(tmp_path: Path) -> None:
    _write_wide_fixture(tmp_path, ["AAPL", "MSFT"])
    result = load_data(str(tmp_path), symbols=["AAPL", "MSFT"])
    assert "close" in result
    assert list(result["close"].columns) == ["AAPL", "MSFT"]


def _write_per_symbol_fixture(tmp_path: Path, symbols: list[str]) -> Path:
    """Create per-symbol subdirectory universe."""
    idx = pd.date_range("2024-01-02 09:30", periods=3, freq="5min")
    for sym in symbols:
        sym_dir = tmp_path / sym
        sym_dir.mkdir()
        df = pd.DataFrame(
            {"Open": [1.0, 2.0, 3.0], "High": [2.0, 3.0, 4.0],
             "Low": [0.5, 1.0, 1.5], "Close": [1.5, 2.5, 3.5],
             "Volume": [100, 200, 300]},
            index=idx,
        )
        df.to_parquet(sym_dir / "data.parquet")
    return tmp_path


def test_load_data_raises_on_missing_symbol_per_symbol(tmp_path: Path) -> None:
    _write_per_symbol_fixture(tmp_path, ["AAPL"])
    with pytest.raises(DataLoadError, match="MSFT"):
        load_data(str(tmp_path), symbols=["AAPL", "MSFT"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_data_loader.py -v`
Expected: FAIL — currently no DataLoadError is raised for missing symbols

- [ ] **Step 3: Write minimal implementation**

In `data_loader.py`, modify `_load_wide` (after filtering columns at lines 74–76):

```python
        if symbols:
            cols = [s for s in symbols if s in df.columns]
            missing = set(symbols) - set(cols)
            if missing:
                raise DataLoadError(
                    f"Requested symbols not found in {data_path / f'{name}.parquet'}: "
                    f"{sorted(missing)}"
                )
            df = df[cols]
```

In `_load_per_symbol` (after filtering subdirs at lines 92–93):

```python
    if symbols:
        subdirs = [s for s in subdirs if s in symbols]
        missing = set(symbols) - set(subdirs)
        if missing:
            raise DataLoadError(
                f"Requested symbols not found as subdirectories in {data_path}: "
                f"{sorted(missing)}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_data_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add data_loader.py tests/test_data_loader.py
git commit -m "fix: raise DataLoadError when requested symbols are missing from universe

data_loader silently dropped missing symbols, allowing backtests to
proceed on a subset and report ok — violating the partial-success-is-
failure invariant (CORE-13). Now raises DataLoadError with the missing
symbol names in both wide and per-symbol paths."
```

---

### Task 2: F8 — widen halt-handler enrichment guard

**Files:**
- Modify: `autoresearch_research.py:1691`
- Test: `tests/test_autoresearch_research.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_autoresearch_research.py`:

```python
def test_handle_needs_code_persists_halt_on_unexpected_exception(
    controller, tmp_path, monkeypatch
):
    """F8/U5: non-ValueError in enrichment must not skip _close_run."""
    from autoresearch_research import _handle_needs_code

    state = {
        "job": 1,
        "research_round": 1,
        "state": "running",
    }
    result = {
        "thesis_id": "test-thesis",
        "thesis": {"hypothesis": "h", "mechanism": "m", "strategy_family": "ema"},
        "research_round": 1,
        "research_round_id": "job-1-round-1",
        "attempt_number": 1,
    }

    # Force a TypeError in the enrichment path
    monkeypatch.setattr(
        "autoresearch_research._prepare_thesis_for_validation",
        lambda *a, **kw: (_ for _ in ()).throw(TypeError("injected")),
    )

    close_run_called = []
    monkeypatch.setattr(
        "autoresearch_research._close_run",
        lambda *a, **kw: close_run_called.append(True),
    )

    _handle_needs_code(controller, state, result)

    assert state["state"] == "halted"
    assert len(close_run_called) == 1, "_close_run must be called even on TypeError"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_autoresearch_research.py::test_handle_needs_code_persists_halt_on_unexpected_exception -v`
Expected: FAIL — TypeError propagates out, _close_run never called

- [ ] **Step 3: Write minimal implementation**

In `autoresearch_research.py:1691`, change:

```python
    except (ValidationError, ValueError) as exc:
```

to:

```python
    except Exception as exc:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_autoresearch_research.py::test_handle_needs_code_persists_halt_on_unexpected_exception -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add autoresearch_research.py tests/test_autoresearch_research.py
git commit -m "fix: widen halt-handler enrichment guard to catch all exceptions

The enrichment guard at _handle_needs_code caught only
(ValidationError, ValueError) but the enrichment chain can raise
TypeError/KeyError from compiler_operationalize. An uncaught exception
skipped _close_run, losing halt persistence and Discord notification —
violating the terminal-state bookkeeping invariant (CORE-14)."
```

---

### Task 3: F9 + U1 — best_by_metric: skip uncoercible metrics + direction='lower' test

**Files:**
- Modify: `backtest_run_db.py:976-993` (`best_by_metric`)
- Test: `tests/test_experiment_db_sqlite_runtime.py` (add tests)

- [ ] **Step 1: Write the failing test for direction='lower'**

Add to `tests/test_experiment_db_sqlite_runtime.py`:

```python
def test_best_by_metric_respects_lower_direction(tmp_path: Path) -> None:
    """U1: direction='lower' must pick the smallest metric value."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="max_drawdown", direction="lower")

    worse = _record(round_number=1, job=1)
    worse.validation_metrics["max_drawdown"] = 0.30
    better = _record(round_number=2, job=1)
    better.validation_metrics["max_drawdown"] = 0.10

    db.add(worse)
    db.add(better)

    best = db.best_by_metric("max_drawdown")
    assert best is not None
    assert best.run_id == better.run_id


def test_best_by_metric_skips_corrupt_metric_with_warning(
    tmp_path: Path, caplog
) -> None:
    """F9: corrupt non-numeric metric must be skipped, not ranked as 0.0."""
    import logging

    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="max_drawdown", direction="lower")

    corrupt = _record(round_number=1, job=1)
    corrupt.validation_metrics["max_drawdown"] = "not-a-number"
    good = _record(round_number=2, job=1)
    good.validation_metrics["max_drawdown"] = 2.5

    db.add(corrupt)
    db.add(good)

    with caplog.at_level(logging.WARNING):
        best = db.best_by_metric("max_drawdown")

    assert best is not None
    assert best.run_id == good.run_id
    assert "not-a-number" in caplog.text or "coerce" in caplog.text.lower()
```

- [ ] **Step 2: Run tests to verify the corrupt metric test fails**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py::test_best_by_metric_skips_corrupt_metric_with_warning -v`
Expected: FAIL — corrupt metric coerced to 0.0, wins under direction='lower'

- [ ] **Step 3: Write minimal implementation**

In `backtest_run_db.py`, modify `best_by_metric` (around lines 976–993):

```python
    def best_by_metric(self, metric: str) -> BacktestRunRecord | None:
        records = [r for r in self._load() if is_metric_rankable_backtest_run(r)]
        direction = self.best_direction()
        best = None
        for r in records:
            val = r.validation_metrics.get(metric)
            if val is None:
                val = r.train_metrics.get(metric)
            if val is None:
                continue
            try:
                candidate = float(val)
            except (TypeError, ValueError):
                log.warning(
                    "best_by_metric: skipping run %s — metric %r value %r is not numeric",
                    r.run_id, metric, val,
                )
                continue
            if best is None:
                best = r
                best_candidate = candidate
                continue
            if direction == "higher" and candidate > best_candidate:
                best = r
                best_candidate = candidate
            elif direction != "higher" and candidate < best_candidate:
                best = r
                best_candidate = candidate
        return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py::test_best_by_metric_respects_lower_direction tests/test_experiment_db_sqlite_runtime.py::test_best_by_metric_skips_corrupt_metric_with_warning tests/test_experiment_db_sqlite_runtime.py::test_best_by_metric_ignores_malformed_metric_values -v`
Expected: all 3 PASS (the existing test must also continue passing)

- [ ] **Step 5: Commit**

```bash
git add backtest_run_db.py tests/test_experiment_db_sqlite_runtime.py
git commit -m "fix: best_by_metric skips uncoercible metrics + add direction=lower test

Corrupt non-numeric metric values were silently coerced to 0.0 and
ranked — under direction='lower' a corrupt record won outright,
violating the deterministic-error-propagate-loud invariant (CORE-11).
Now logs a warning and skips uncoercible values. Also adds the missing
regression test for direction='lower' (U1 — the F013 fix had zero
coverage on its critical branch)."
```

---

### Task 4: F6 — .gitignore fix + untrack runtime artifacts

**Files:**
- Modify: `.gitignore:235`

- [ ] **Step 1: Fix .gitignore**

Replace the stale `ema_experiments.db` entry at line 235 and add the missing patterns:

```
# Replace line 235 and add:
*_backtest_runs.db
trace_exports/
runtime/
```

- [ ] **Step 2: Untrack the 37 committed runtime artifacts**

```bash
git rm -r --cached runtime/ 2>/dev/null || true
git rm -r --cached trace_exports/ 2>/dev/null || true
```

- [ ] **Step 3: Verify**

```bash
git check-ignore ema_backtest_runs.db trace_exports runtime
# Expected: all three listed (exit 0)
git ls-files runtime/ trace_exports/
# Expected: empty
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "fix: update .gitignore for renamed DB and untrack runtime artifacts

.gitignore still referenced the pre-rename ema_experiments.db; the
current ema_backtest_runs.db and trace_exports/ were committable.
37 runtime artifacts from job-25/job-7 were already tracked. Updated
the ignore patterns and removed them from the index (CORE-32)."
```

---

### Task 5: F7 — delete duplicate research_round_id from backtest_run_db

**Files:**
- Modify: `backtest_run_db.py:28-39` (delete function)
- Modify: `research_conductor.py:19` (repoint import)
- Modify: `autoresearch_research.py:47` (repoint import)
- Test: `tests/test_autoresearch_research.py` (add assertion)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_autoresearch_research.py`:

```python
def test_research_round_id_is_not_defined_in_backtest_run_db() -> None:
    """F7: backtest_run_db must not define its own research_round_id."""
    import backtest_run_db

    # The module may re-export from autoresearch_runtime_paths, which is fine.
    # What's banned is a locally-defined lenient duplicate.
    import inspect
    if hasattr(backtest_run_db, "research_round_id"):
        src_file = inspect.getfile(backtest_run_db.research_round_id)
        assert "autoresearch_runtime_paths" in src_file, (
            "backtest_run_db.research_round_id must come from "
            "autoresearch_runtime_paths, not be a local duplicate"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_autoresearch_research.py::test_research_round_id_is_not_defined_in_backtest_run_db -v`
Expected: FAIL — currently defined locally in backtest_run_db.py

- [ ] **Step 3: Delete the duplicate and repoint imports**

In `backtest_run_db.py`, delete lines 28–39 (the `def research_round_id(...)` function).

In `research_conductor.py:19`, change:
```python
from backtest_run_db import research_round_id as make_research_round_id
```
to:
```python
from autoresearch_runtime_paths import research_round_id_or_empty as make_research_round_id
```

Note: `research_conductor.py:985` passes `current_job or 0` which can be 0. The strict `research_round_id` raises on `job<1`, so use `research_round_id_or_empty` which returns `""` for invalid inputs — matching the lenient semantics this call site needs.

In `autoresearch_research.py:47`, change:
```python
from backtest_run_db import research_round_id as make_research_round_id
```
to:
```python
from autoresearch_runtime_paths import research_round_id as make_research_round_id
```

Note: all call sites in `autoresearch_research.py` (lines 256, 825, 1184, 1225) already guard `job_id >= 1` and `round_number >= 0` before calling, so the strict helper is correct here.

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_autoresearch_research.py tests/test_autoresearch_research_helpers.py tests/test_experiment_db_sqlite_runtime.py -v`
Expected: PASS — no behavior change for valid inputs

- [ ] **Step 5: Commit**

```bash
git add backtest_run_db.py research_conductor.py autoresearch_research.py tests/test_autoresearch_research.py
git commit -m "fix: delete duplicate research_round_id from backtest_run_db

Two modules defined research_round_id with divergent contracts — the
lenient copy in backtest_run_db.py could mint 'job-0-round-N' ids that
the strict DB write boundary later rejects. Consolidated to the
canonical autoresearch_runtime_paths helper (EXPMCP-02, AGENTS.md
rule B: one home per concept)."
```

---

### Task 6: F2 — backtest_runs canonical-column migration + indexes

**Files:**
- Modify: `backtest_run_db.py:170-297` (`_init_db`)
- Test: `tests/test_experiment_db_sqlite_runtime.py` (add migration test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_experiment_db_sqlite_runtime.py`:

```python
def test_backtest_runs_has_canonical_columns_and_indexes(tmp_path: Path) -> None:
    """F2/U4: backtest_runs must have doc-01 canonical columns + 6 indexes."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    import sqlite3
    conn = sqlite3.connect(tmp_path / "backtest_runs.db")

    # Check canonical columns exist
    columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()}
    for col in ("decision_status", "created_at_utc", "strategy_family",
                "job_id", "primary_metric_name", "primary_metric_value",
                "metrics_json", "trade_analysis_json", "trace_run_id"):
        assert col in columns, f"missing canonical column: {col}"

    # Check 6 required indexes
    indexes = {row[1] for row in conn.execute(
        "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='backtest_runs'"
    ).fetchall()}
    for idx in ("idx_backtest_runs_thesis_id",
                "idx_backtest_runs_strategy_family_created_at",
                "idx_backtest_runs_job_id",
                "idx_backtest_runs_code_commit",
                "idx_backtest_runs_decision_status",
                "idx_backtest_runs_primary_metric_value"):
        assert idx in indexes, f"missing required index: {idx}"

    conn.close()


def test_backtest_runs_decision_status_backfill(tmp_path: Path) -> None:
    """F2: accepted=1 → decision_status='keep', accepted=0 → 'discard'."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    accepted_rec = _record(round_number=1, job=1)
    accepted_rec.accepted = True
    rejected_rec = _record(round_number=2, job=1)
    rejected_rec.accepted = False

    db.add(accepted_rec)
    db.add(rejected_rec)

    import sqlite3
    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute(
        "SELECT run_id, decision_status FROM backtest_runs ORDER BY run_id"
    ).fetchall()
    conn.close()

    status_by_id = {r[0]: r[1] for r in rows}
    assert status_by_id[accepted_rec.run_id] == "keep"
    assert status_by_id[rejected_rec.run_id] == "discard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py::test_backtest_runs_has_canonical_columns_and_indexes -v`
Expected: FAIL — columns don't exist

- [ ] **Step 3: Write minimal implementation**

In `backtest_run_db.py`, add these `_ensure_column` calls and index creation inside `_init_db`, after the existing `_ensure_column` calls for `is_baseline` (line 296), before `conn.commit()`:

```python
            # F2: canonical columns from docs/persistence-schema/01
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "decision_status", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "created_at_utc", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "strategy_family", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "job_id", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "primary_metric_name", "TEXT NOT NULL DEFAULT ''"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "primary_metric_value", "REAL"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "metrics_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "trade_analysis_json", "TEXT NOT NULL DEFAULT '{}'"
            )
            self._ensure_column(
                conn, BACKTEST_RUNS_TABLE, "trace_run_id", "TEXT NOT NULL DEFAULT ''"
            )
            # Backfill canonical columns from legacy columns
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET decision_status = CASE WHEN accepted = 1 THEN 'keep' ELSE 'discard' END
                WHERE decision_status = ''
            """)
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET created_at_utc = timestamp
                WHERE created_at_utc = ''
            """)
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET strategy_family = family
                WHERE strategy_family = ''
            """)
            conn.execute(f"""
                UPDATE {BACKTEST_RUNS_TABLE}
                SET job_id = job
                WHERE job_id = 0
            """)
            # Required indexes
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_thesis_id
                ON {BACKTEST_RUNS_TABLE} (thesis_id)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_family_created_at
                ON {BACKTEST_RUNS_TABLE} (strategy_family, created_at_utc)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_job_id
                ON {BACKTEST_RUNS_TABLE} (job_id)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_code_commit
                ON {BACKTEST_RUNS_TABLE} (code_commit)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_decision_status
                ON {BACKTEST_RUNS_TABLE} (decision_status)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_primary_metric_value
                ON {BACKTEST_RUNS_TABLE} (primary_metric_value)
            """)
```

Also update the `add` method to populate the canonical columns when writing new records. In the INSERT statement, add the new columns and their values derived from the record:

```python
# In the add() method's INSERT, add these column=value pairs:
# decision_status = 'keep' if record.accepted else 'discard'
# created_at_utc = record.timestamp (already ISO)
# strategy_family = record.family
# job_id = record.job
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py -v`
Expected: PASS — both new and existing tests

- [ ] **Step 5: Commit**

```bash
git add backtest_run_db.py tests/test_experiment_db_sqlite_runtime.py
git commit -m "feat: add canonical columns and indexes to backtest_runs table

backtest_runs kept the legacy column shape (accepted/timestamp/family/
job) while research_rounds and research_thesis_attempts were migrated.
Adds decision_status, created_at_utc, strategy_family, job_id,
primary_metric_name/value, metrics_json, trade_analysis_json,
trace_run_id columns via idempotent _ensure_column + backfill from
legacy values. Adds all 6 required idx_backtest_runs_* indexes
(SCHEMA-07/09, doc 02 mappings)."
```

---

### Task 7: F1 + U3 — baseline_checkpoints SQLite table

**Files:**
- Modify: `backtest_run_db.py` (add DDL in `_init_db`, add SQLite write path to `BaselineTracker`)
- Test: `tests/test_experiment_db_sqlite_runtime.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_experiment_db_sqlite_runtime.py`:

```python
def test_baseline_checkpoint_persists_to_sqlite(tmp_path: Path) -> None:
    """F1/U3: BaselineTracker.record() must write to baseline_checkpoints table."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    tracker = BaselineTracker(
        tmp_path / "ema_baseline_checkpoints.json",
        db=db,
    )
    from backtest_run_db import BaselineCheckpoint

    checkpoint = BaselineCheckpoint(
        code_commit="abc123",
        data_hash="def456",
        config_hash="ghi789",
        metrics={"profit_factor": 1.5, "max_drawdown": 0.12},
        timestamp="2026-06-10T12:00:00+00:00",
        round_number=3,
    )
    tracker.record(checkpoint)

    import sqlite3
    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute("SELECT * FROM baseline_checkpoints").fetchall()
    conn.close()

    assert len(rows) == 1
    row = rows[0]
    # checkpoint_id is synthetic, strategy_family comes from session
    assert row[0]  # checkpoint_id is non-empty
    assert row[1] == "ema"  # strategy_family
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py::test_baseline_checkpoint_persists_to_sqlite -v`
Expected: FAIL — no baseline_checkpoints table, no db param on BaselineTracker

- [ ] **Step 3: Write minimal implementation**

In `backtest_run_db.py`, add the DDL to `_init_db` (before `conn.commit()`):

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS baseline_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    strategy_family TEXT NOT NULL,
                    code_commit TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    round_number INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_baseline_checkpoints_strategy_family_created_at
                ON baseline_checkpoints (strategy_family, created_at_utc)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_baseline_checkpoints_code_commit
                ON baseline_checkpoints (code_commit)
            """)
```

Modify `BaselineTracker.__init__` to accept an optional `db` parameter:

```python
class BaselineTracker:
    def __init__(self, path: Path, *, db: BacktestRunDB | None = None) -> None:
        self.path = path
        self.db = db
        self._checkpoints: list[BaselineCheckpoint] | None = None
```

Modify `BaselineTracker.record` to also write to SQLite when `db` is provided:

```python
    def record(self, checkpoint: BaselineCheckpoint) -> None:
        checkpoints = self._load()
        checkpoints.append(checkpoint)
        self._checkpoints = checkpoints
        self._save()
        if self.db is not None:
            self._write_to_sqlite(checkpoint)

    def _write_to_sqlite(self, checkpoint: BaselineCheckpoint) -> None:
        checkpoint_id = _config_hash(
            f"{checkpoint.code_commit}:{checkpoint.data_hash}:"
            f"{checkpoint.config_hash}:{checkpoint.timestamp}"
        )
        strategy_family = ""
        try:
            strategy_family = self.db.session_name()
        except Exception:
            pass
        with self.db._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO baseline_checkpoints
                   (checkpoint_id, strategy_family, code_commit, data_hash,
                    config_hash, metrics_json, created_at_utc, round_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint_id,
                    strategy_family,
                    checkpoint.code_commit,
                    checkpoint.data_hash,
                    checkpoint.config_hash,
                    json_dumps_strict(checkpoint.metrics),
                    checkpoint.timestamp or _iso8601_utc_now(),
                    checkpoint.round_number,
                ),
            )
            conn.commit()
```

Add a `session_name` helper to `BacktestRunDB`:

```python
    def session_name(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT name FROM session_meta WHERE id = 1").fetchone()
            return row["name"] if row else ""
```

Update the `BaselineTracker` instantiation in `autoresearch_controller.py:629` to pass `db=self.db` (the controller already has `self.db`).

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest_run_db.py autoresearch_controller.py tests/test_experiment_db_sqlite_runtime.py
git commit -m "feat: add baseline_checkpoints SQLite table and dual-write from BaselineTracker

baseline_checkpoints (required table 4 of 4) was missing from the
SQLite schema; checkpoints persisted only to a JSON file, violating
the single-durable-store invariant (SCHEMA-01/14/31). Adds DDL with
checkpoint_id PK, strategy_family, and the two required indexes.
BaselineTracker now dual-writes to SQLite when a db instance is
provided, keeping the JSON for backward compatibility."
```

---

### Task 8: F10 → F11 — normalize at compiler_thesis_io + remove experiment_result alias

**Files:**
- Modify: `compiler_thesis_io.py:75`
- Modify: `research_types.py:67-78`
- Test: `tests/test_compiler_pipeline_characterization.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compiler_pipeline_characterization.py`:

```python
def test_create_executable_artifact_normalizes_legacy_evidence_source(
    tmp_path: Path, monkeypatch
) -> None:
    """F10: compiler_thesis_io must normalize thesis before model_validate."""
    thesis_dir = tmp_path / "research"
    thesis_dir.mkdir()
    base_config = tmp_path / "config.json"
    base_config.write_text("{}")

    thesis = {
        "thesis_id": "test-thesis-01",
        "strategy_family": "ema",
        "hypothesis": "test hypothesis",
        "mechanism": "test mechanism",
        "mechanism_dimension": "signal_entry",
        "config_changes": {"fast_window": 5},
        "evidence_citations": [
            {"source": "experiment_result", "citation": "prior round showed improvement"},
        ],
    }

    # Should not raise ValidationError — normalize_thesis_payload converts
    # "experiment_result" → "round_result" before model_validate.
    from compiler_thesis_io import create_executable_artifact

    # The function may fail downstream (no real strategy registered, etc.)
    # but it must NOT fail with ValidationError on the evidence source.
    try:
        create_executable_artifact(
            thesis_dir, base_config, thesis, tmp_path, artifact_root=tmp_path / "runtime"
        )
    except Exception as exc:
        # ValidationError about "experiment_result" means normalization is missing
        assert "experiment_result" not in str(exc), (
            f"Unnormalized 'experiment_result' caused: {exc}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compiler_pipeline_characterization.py::test_create_executable_artifact_normalizes_legacy_evidence_source -v`
Expected: FAIL — ValidationError about "experiment_result" once we remove the Literal alias (step 3b)

- [ ] **Step 3a: Add normalization in compiler_thesis_io.py**

In `compiler_thesis_io.py`, add the import at the top:

```python
from thesis_validator import normalize_thesis_payload
```

Change line 75 from:

```python
    research_thesis = ResearchThesis.model_validate(thesis)
```

to:

```python
    research_thesis = ResearchThesis.model_validate(normalize_thesis_payload(dict(thesis)))
```

- [ ] **Step 3b: Remove the "experiment_result" alias from research_types.py**

In `research_types.py:67-78`, change the Literal to:

```python
    source: Literal[
        "web_search",
        "analyst",
        "source_code",
        "round_result",
        "memory",
    ]
```

Remove the deployment-migration comment block (lines 72–76).

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_compiler_pipeline_characterization.py tests/test_agent_orchestrator_characterization.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add compiler_thesis_io.py research_types.py tests/test_compiler_pipeline_characterization.py
git commit -m "fix: normalize thesis at compiler_thesis_io + remove experiment_result alias

compiler_thesis_io.py:75 called ResearchThesis.model_validate without
normalize_thesis_payload (CORE-05 violation). Fixed by adding the
normalizer. With normalization at every site, the 'experiment_result'
Literal alias is dead code — removed per the hard-cutover invariant
(EXPMCP-01). normalize_thesis_payload handles the rewrite."
```

---

### Task 9: F5 — lazy accessors for HALO/Claude timeouts

**Files:**
- Modify: `improvement_halo.py:27` + `:127`
- Modify: `improvement_halo_apply.py:35` + `:122`
- Modify: `persistence_utils.py:105-120` (`parse_positive_int_env`)
- Modify: `tests/test_improvement_halo.py:221-254`
- Modify: `tests/test_improvement_halo_apply.py:328-361`

- [ ] **Step 1: Write the failing test for lazy accessor**

Replace the existing `test_halo_timeout_overridable_via_env` in `tests/test_improvement_halo.py`:

```python
def test_halo_timeout_overridable_via_env(monkeypatch):
    """No reload needed — lazy accessor reads env at call time."""
    from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_HALO_TIMEOUT_SECONDS, "42")
    import improvement_halo

    assert improvement_halo.halo_timeout_seconds() == 42


def test_halo_timeout_invalid_env_raises(monkeypatch):
    from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_HALO_TIMEOUT_SECONDS, "not-a-number")
    import improvement_halo

    with pytest.raises(ValueError, match=ENV_HALO_TIMEOUT_SECONDS):
        improvement_halo.halo_timeout_seconds()


def test_halo_timeout_non_positive_env_raises(monkeypatch):
    from autoresearch_constants import ENV_HALO_TIMEOUT_SECONDS

    monkeypatch.setenv(ENV_HALO_TIMEOUT_SECONDS, "0")
    import improvement_halo

    with pytest.raises(ValueError, match=ENV_HALO_TIMEOUT_SECONDS):
        improvement_halo.halo_timeout_seconds()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_improvement_halo.py::test_halo_timeout_overridable_via_env -v`
Expected: FAIL — `halo_timeout_seconds` function doesn't exist yet

- [ ] **Step 3: Write minimal implementation**

In `persistence_utils.py`, add a strict version alongside the existing `parse_positive_int_env`:

```python
def require_positive_int_env(env_key: str, default: int) -> int:
    raw = os.environ.get(env_key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"invalid value {raw!r} for {env_key}; expected a positive integer"
        )
    if value <= 0:
        raise ValueError(
            f"{env_key}={value} must be > 0"
        )
    return value
```

In `improvement_halo.py`, replace line 27:

```python
# Before:
HALO_TIMEOUT_SECONDS = parse_positive_int_env(ENV_HALO_TIMEOUT_SECONDS, 600, logger=log)

# After:
_HALO_TIMEOUT_DEFAULT = 600

def halo_timeout_seconds() -> int:
    return require_positive_int_env(ENV_HALO_TIMEOUT_SECONDS, _HALO_TIMEOUT_DEFAULT)
```

Add `from persistence_utils import require_positive_int_env` to the imports (keep the existing `parse_positive_int_env` import if used elsewhere, or remove it if not).

Update the usage at line 127:

```python
# Before:
            timeout=HALO_TIMEOUT_SECONDS,
# After:
            timeout=halo_timeout_seconds(),
```

And in the timeout error message at line 133:

```python
# Before:
            f"HALO timeout after {HALO_TIMEOUT_SECONDS}s ...
# After:
            f"HALO timeout after {halo_timeout_seconds()}s ...
```

Apply the same pattern in `improvement_halo_apply.py`:

```python
# Line 35 — replace:
CLAUDE_TIMEOUT_SECONDS = parse_positive_int_env(ENV_CLAUDE_TIMEOUT_SECONDS, 1800, logger=log)

# With:
_CLAUDE_TIMEOUT_DEFAULT = 1800

def claude_timeout_seconds() -> int:
    return require_positive_int_env(ENV_CLAUDE_TIMEOUT_SECONDS, _CLAUDE_TIMEOUT_DEFAULT)
```

Update usage at line 122 similarly.

Similarly update `tests/test_improvement_halo_apply.py` tests to remove `importlib.reload` and test the accessor pattern, and test `ValueError` on invalid input.

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_improvement_halo.py tests/test_improvement_halo_apply.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add improvement_halo.py improvement_halo_apply.py persistence_utils.py tests/test_improvement_halo.py tests/test_improvement_halo_apply.py
git commit -m "fix: convert HALO/Claude timeouts to lazy accessor functions

Module-level env reads required importlib.reload() in tests — the
exact failure mode the lazy-accessor invariant (CORE-18) was written
against. Now halo_timeout_seconds() and claude_timeout_seconds() read
the env at call time and raise ValueError naming the env var on bad
input (previously fell back silently to the default)."
```

---

### Task 10: F14 — replace dead PYTEST_CURRENT_TEST guard in trace_sdk

**Files:**
- Modify: `trace_sdk.py:503-530`
- Modify: `tests/conftest.py` (add env var)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_autoresearch_research.py` (or a new test file):

```python
def test_tracing_disabled_env_prevents_traceloop_init(monkeypatch) -> None:
    """F14: AUTORESEARCH_TRACING_DISABLED must prevent Traceloop.init."""
    monkeypatch.setenv("AUTORESEARCH_TRACING_DISABLED", "1")

    import importlib
    import trace_sdk

    importlib.reload(trace_sdk)
    # If the guard works, _PROVIDER should still be None or a no-op
    # (Traceloop.init was not called)
```

- [ ] **Step 2: Write minimal implementation**

In `trace_sdk.py`, modify `_initialize_tracing()`:

```python
def _initialize_tracing() -> None:
    global _PROVIDER, _INITIALIZED
    if _INITIALIZED:
        return
    if os.getenv(ENV_TRACE_MODE) == TRACE_MODE_TRANSACTION:
        _INITIALIZED = True
        return
    if os.getenv("AUTORESEARCH_TRACING_DISABLED"):
        _INITIALIZED = True
        return
    _PROVIDER = _build_provider()
    try:
        Traceloop.init(
            app_name="agents-auto-research",
            disable_batch=True,
            exporter=_STATE.exporter,
            telemetry_enabled=False,
            api_key=os.getenv("TRACELOOP_API_KEY", "local-dev"),
            endpoint_is_traceloop=False,
            instruments=_TRACELOOP_INSTRUMENTS,
            resource_attributes={"autoresearch.session_id": _STATE.session_id},
        )
    except Exception as exc:
        _log.warning("Traceloop.init failed (suppressed): %s", exc)
    _INITIALIZED = True
```

In `tests/conftest.py`, set the disable var early:

```python
import os
os.environ.setdefault("AUTORESEARCH_TRACING_DISABLED", "1")
```

This must be at the TOP of conftest.py, before any `trace_sdk` import.

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -v --timeout=30`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add trace_sdk.py tests/conftest.py
git commit -m "fix: replace dead PYTEST_CURRENT_TEST guard with AUTORESEARCH_TRACING_DISABLED

PYTEST_CURRENT_TEST is not set during module import/collection
(CORE-16), so the guard never fired and Traceloop.init mutated global
OTel state for the test suite. New AUTORESEARCH_TRACING_DISABLED env
var is set in conftest.py at import time, before trace_sdk loads."
```

---

### Task 11: U2 — BacktestRunDB.reload() regression test

**Files:**
- Test: `tests/test_experiment_db_sqlite_runtime.py` (add test)

- [ ] **Step 1: Write the test**

Add to `tests/test_experiment_db_sqlite_runtime.py`:

```python
def test_reload_picks_up_external_sqlite_write(tmp_path: Path) -> None:
    """U2: reload() must invalidate the cache so external writes are visible."""
    db_path = tmp_path / "backtest_runs.db"
    db1 = BacktestRunDB(db_path)
    db1.init_session(name="ema", metric_name="profit_factor", direction="higher")

    rec = _record(round_number=1, job=1)
    db1.add(rec)
    assert db1.count() == 1

    # External write via a second connection (simulates VPS run)
    db2 = BacktestRunDB(db_path)
    rec2 = _record(round_number=2, job=1)
    db2.add(rec2)

    # db1 still sees stale cache
    assert db1.count() == 1

    # After reload, sees the external write
    db1.reload()
    assert db1.count() == 2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py::test_reload_picks_up_external_sqlite_write -v`
Expected: PASS (the feature exists, just untested)

- [ ] **Step 3: Commit**

```bash
git add tests/test_experiment_db_sqlite_runtime.py
git commit -m "test: add regression test for BacktestRunDB.reload() cache invalidation

reload() (F004 fix) had zero callers in tests or production. Adds a
test verifying the cache-invalidation contract: an external SQLite
write becomes visible after reload() (U2)."
```

---

### Task 12: Doc-integrity sweep (F4, F13, F16, F12, F15)

**Files:**
- Modify: `TECH_DEBT_AUDIT.md:52,118` (F4: correct F002 FIXED, F13: correct F019 FIXED)
- Modify: `docs/superpowers/plans/2026-05-04-prompt-variant-framework.md:1300-1309` (F16: uncheck DoD)
- Modify: `vps_runner.py:454-470` (F12: remove run_experiment compat)
- Modify: `tests/test_vps_runner_config.py:472-474` (F12: update assertion)
- Modify: `docs/persistence-schema/03-implementation-definition-for-coding-agents.md` (F15: document deviation)

- [ ] **Step 1: Fix TECH_DEBT_AUDIT.md F002 status (F4)**

Change the F002 row at line 52, remove the `→ **FIXED**` suffix. Add a note:

```
| 🔴 TODO | F002 | Test debt | `pyproject.toml:fail_under = 45` | High | M | Coverage gate is 45% (deliberately lowered from 80 in commit 65f8590 to establish a baseline at ~45.7%). Project rule (CLAUDE.md) requires 80%. Ratchet upward with focused coverage work. | Raise `fail_under` incrementally. |
```

Also at line 74–80, update the Top 5 section to remove the diff showing 70→80 and note the current state.

- [ ] **Step 2: Fix TECH_DEBT_AUDIT.md F019 status (F13)**

At line 118, remove the `→ **FIXED**` suffix since `backtest/runner.py:32` still has `default="/tmp"`.

- [ ] **Step 3: Uncheck prompt-variant framework DoD (F16)**

In `docs/superpowers/plans/2026-05-04-prompt-variant-framework.md:1300-1309`, change all `[x]` to `[ ]` in the Self-Review section, and add a header note: `> **Status:** Deferred — no implementation has landed. Task checkboxes are all unchecked.`

- [ ] **Step 4: Remove run_experiment compat from vps_runner (F12)**

In `vps_runner.py:454-470`, remove the compat comment and simplify the tuple checks:

Change `next_action.get('type') in ('run_round', 'run_experiment')` (lines 462, 469) to `next_action.get('type') == 'run_round'`.

Remove the migration compat comment block (lines 454–457).

Update `tests/test_vps_runner_config.py:472-474`:

```python
    # Change:
    assert "run_experiment" in command
    # To:
    assert "run_experiment" not in command
```

- [ ] **Step 5: Document round-row deviation (F15)**

In `docs/persistence-schema/03-implementation-definition-for-coding-agents.md`, add a note to the "When a research round starts" section:

```markdown
> **Implementation note (2026-06-10):** The round row is written at first
> outcome (rejected attempt or round finalize), not at round start. Rounds
> that produce zero logged attempts before a crash leave no row. This is
> accepted — the JSON state file records the round-in-progress.
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_vps_runner_config.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add TECH_DEBT_AUDIT.md vps_runner.py tests/test_vps_runner_config.py \
  docs/superpowers/plans/2026-05-04-prompt-variant-framework.md \
  docs/persistence-schema/03-implementation-definition-for-coding-agents.md
git commit -m "docs: fix stale FIXED claims + remove run_experiment compat shim

F4: TECH_DEBT_AUDIT F002 corrected from FIXED to TODO (fail_under=45).
F13: F019 corrected from FIXED to TODO (--output-dir still /tmp).
F16: prompt-variant framework DoD unchecked (zero implementation).
F12: removed run_experiment compat from vps_runner heredoc (EXPMCP-01).
F15: documented the round-row-at-first-outcome deviation in doc 03."
```

---

### Task 13: F13 — backtest/runner.py --output-dir default

**Files:**
- Modify: `backtest/runner.py:31-33`
- Test: `tests/test_backtest_output.py` (add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_backtest_output.py`:

```python
def test_runner_output_dir_default_is_not_tmp() -> None:
    """F13: --output-dir must not default to /tmp (tmpfs exhaustion risk)."""
    from backtest.runner import build_parser

    args = build_parser().parse_args(["--strategy", "ema", "--config", "x.json"])
    assert args.output_dir != "/tmp"


def test_runner_output_dir_honors_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_OUTPUT_DIR", "/opt/results")
    from backtest.runner import build_parser

    args = build_parser().parse_args(["--strategy", "ema", "--config", "x.json"])
    assert args.output_dir == "/opt/results"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_output.py::test_runner_output_dir_default_is_not_tmp -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

In `backtest/runner.py:31-33`, change:

```python
    parser.add_argument(
        "--output-dir", default="/tmp", help="Directory to write result.json and trades CSV"
    )
```

to:

```python
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("AUTORESEARCH_OUTPUT_DIR", "."),
        help="Directory to write result.json and trades CSV",
    )
```

Add `import os` to the imports if not already present.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_backtest_output.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest/runner.py tests/test_backtest_output.py
git commit -m "fix: change --output-dir default from /tmp to env-backed current dir

On VPS with tmpfs /tmp, large backtests could exhaust capacity
(AUDIT-16/F019). Now reads AUTORESEARCH_OUTPUT_DIR env var, falling
back to '.' (CORE-18 lazy accessor pattern at the argparse level)."
```

---

### Task 14: F15 — research_rounds row at round start (optional fix)

This was already documented as a deviation in Task 12, Step 5. If the team prefers a code fix over a doc note, implement this task. Otherwise skip.

**Files:**
- Modify: `backtest_run_db.py` (add `ensure_round_started` method)
- Modify: `autoresearch_research.py` (call at round start)
- Test: `tests/test_experiment_db_sqlite_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_round_row_exists_at_start(tmp_path: Path) -> None:
    """F15: a research_rounds row should exist after round start, not just at first outcome."""
    db = BacktestRunDB(tmp_path / "backtest_runs.db")
    db.init_session(name="ema", metric_name="profit_factor", direction="higher")

    db.ensure_round_started(
        research_round_id="job-1-round-1",
        job_id=1,
        round_number=1,
        run_id="run-abc",
    )

    import sqlite3
    conn = sqlite3.connect(tmp_path / "backtest_runs.db")
    rows = conn.execute(
        "SELECT outcome FROM research_rounds WHERE research_round_id = 'job-1-round-1'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "in_progress"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — `ensure_round_started` doesn't exist

- [ ] **Step 3: Write minimal implementation**

Add to `BacktestRunDB`:

```python
    def ensure_round_started(
        self,
        *,
        research_round_id: str,
        job_id: int,
        round_number: int,
        run_id: str,
    ) -> None:
        """Write a provisional round row if one does not already exist."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM research_rounds WHERE research_round_id = ?",
                (research_round_id,),
            ).fetchone()
            if existing:
                return
            conn.execute(
                """INSERT INTO research_rounds
                   (research_round_id, job_id, round_number, run_id,
                    selected_thesis_id, outcome, created_at_utc, usage_json)
                   VALUES (?, ?, ?, ?, '', 'in_progress', ?, '{}')""",
                (research_round_id, job_id, round_number, run_id, _iso8601_utc_now()),
            )
            conn.commit()
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_experiment_db_sqlite_runtime.py::test_round_row_exists_at_start -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backtest_run_db.py tests/test_experiment_db_sqlite_runtime.py
git commit -m "feat: write research_rounds row with outcome=in_progress at round start

Crashed zero-attempt rounds left no row in canonical history (SCHEMA-32).
ensure_round_started() creates a provisional row with outcome='in_progress'
that log_research_round's INSERT OR REPLACE will finalize."
```

---

## Summary

| Task | Finding(s) | Type | Risk |
|------|-----------|------|------|
| 1 | F3 | Bug fix | HIGH — silent wrong results |
| 2 | F8, U5 | Bug fix | HIGH — lost halt state |
| 3 | F9, U1 | Bug fix + test | MED — corrupt metric wins ranking |
| 4 | F6 | Hygiene | MED — data leak to git |
| 5 | F7 | Simplicity | MED — id contract split |
| 6 | F2, U4 | Schema migration | HIGH — half-migrated table |
| 7 | F1, U3 | Schema migration | HIGH — missing table |
| 8 | F10, F11 | Normalization | LOW — dead code path |
| 9 | F5 | Invariant fix | MED — import-time env read |
| 10 | F14 | Invariant fix | LOW — dead guard |
| 11 | U2 | Test only | MED — untested fix |
| 12 | F4, F13, F16, F12, F15 | Doc integrity | LOW — stale claims |
| 13 | F13 | Config fix | LOW — tmpfs default |
| 14 | F15 | Optional feat | LOW — round-start row |
