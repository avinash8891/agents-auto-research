"""Tests for scripts/migrate_experiment_dirs.py (Spec A1 §6 + §10)."""

from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "migrate_experiment_dirs.py"


def _load_migrate_module():
    spec = importlib.util.spec_from_file_location("migrate_experiment_dirs", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate_experiment_dirs = _load_migrate_module()


# Real production names — strategy family "ema", job ids that match
# autoresearch_runtime_paths.job_runtime_root.
THESIS_ID_A = "ema_breakout_v1"
THESIS_ID_B = "ema_pullback_volume_v2"
JOB_ID = 12


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE backtest_runs (
            run_id TEXT PRIMARY KEY,
            thesis_id TEXT NOT NULL,
            job INTEGER NOT NULL,
            research_round_id TEXT NOT NULL DEFAULT '',
            research_round_number INTEGER NOT NULL DEFAULT -1,
            trades_file TEXT NOT NULL DEFAULT '',
            strategy_events_file TEXT NOT NULL DEFAULT '',
            diagnostics_file TEXT NOT NULL DEFAULT ''
        )
        """)
    return conn


def _seed(
    conn: sqlite3.Connection,
    thesis_id: str,
    job_id: int,
    round_id: str,
    run_id: str,
    round_number: int = 1,
) -> None:
    conn.execute(
        "INSERT INTO backtest_runs (run_id, thesis_id, job, research_round_id, "
        "research_round_number) VALUES (?, ?, ?, ?, ?)",
        (run_id, thesis_id, job_id, round_id, round_number),
    )
    conn.commit()


def _make_experiments_dir(root: Path, job_id: int, round_number: int, name: str) -> Path:
    sub = (
        root
        / "runtime"
        / "jobs"
        / f"job-{job_id}"
        / "research"
        / f"round-{round_number}"
        / "experiments"
        / name
    )
    sub.mkdir(parents=True, exist_ok=True)
    # drop a real-looking artifact so renames move actual content, not empty dirs.
    (sub / "thesis.json").write_text('{"thesis_id": "%s"}' % name)
    return sub


@pytest.fixture
def staged_runtime(tmp_path: Path):
    """Return (root, db_path) with one legacy experiments/{thesis_id}/ dir staged."""
    root = tmp_path / "runtime_root"
    root.mkdir()
    db_path = tmp_path / "ema_backtest_runs.db"
    conn = _make_db(db_path)
    round_id = "job-12-round-1"
    _seed(conn, THESIS_ID_A, JOB_ID, round_id, "run-001", round_number=1)
    conn.close()
    legacy_dir = _make_experiments_dir(root, JOB_ID, 1, THESIS_ID_A)
    return root, db_path, legacy_dir, round_id


class TestMigrateExperimentDirs:
    def test_dry_run_lists_renames_without_applying(
        self, staged_runtime, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root, db_path, legacy_dir, round_id = staged_runtime
        target_dir = legacy_dir.parent / round_id

        exit_code = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--dry-run"]
        )

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "WOULD RENAME" in captured.out
        assert str(legacy_dir) in captured.out
        assert round_id in captured.out
        assert legacy_dir.exists()
        assert not target_dir.exists()

    def test_apply_renames_and_is_idempotent(
        self, staged_runtime, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root, db_path, legacy_dir, round_id = staged_runtime
        target_dir = legacy_dir.parent / round_id

        # First apply.
        exit_code_1 = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--apply"]
        )
        out_1 = capsys.readouterr().out
        assert exit_code_1 == 0
        assert target_dir.is_dir()
        assert not legacy_dir.exists()
        assert (target_dir / "thesis.json").exists()
        assert "RENAMED" in out_1

        # Second apply — no-op.
        exit_code_2 = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--apply"]
        )
        out_2 = capsys.readouterr().out
        assert exit_code_2 == 0
        assert target_dir.is_dir()
        assert not legacy_dir.exists()
        assert "no renames required" in out_2

    def test_collision_aborts(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        round_id_1 = "job-12-round-1"
        round_id_2 = "job-12-round-2"
        # Same (thesis, job, round_number) but two different research_round_ids
        # — that is the actual collision case the script must detect.
        _seed(conn, THESIS_ID_A, JOB_ID, round_id_1, "run-001", round_number=1)
        _seed(conn, THESIS_ID_A, JOB_ID, round_id_2, "run-002", round_number=1)
        conn.close()
        _make_experiments_dir(root, JOB_ID, 1, THESIS_ID_A)

        exit_code = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--dry-run"]
        )

        captured = capsys.readouterr()
        assert exit_code != 0
        assert "COLLISION" in captured.out
        assert round_id_1 in captured.out
        assert round_id_2 in captured.out
        assert THESIS_ID_A in captured.out

    def test_reverse_undoes_apply(self, staged_runtime, capsys: pytest.CaptureFixture[str]) -> None:
        root, db_path, legacy_dir, round_id = staged_runtime
        target_dir = legacy_dir.parent / round_id

        forward = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--apply"]
        )
        capsys.readouterr()
        assert forward == 0
        assert target_dir.is_dir()
        assert not legacy_dir.exists()

        reverse = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--reverse", "--apply"]
        )
        capsys.readouterr()
        assert reverse == 0
        assert legacy_dir.is_dir()
        assert not target_dir.exists()
        assert (legacy_dir / "thesis.json").exists()


class TestCliSmoke:
    def test_empty_root_does_not_crash(self, tmp_path: Path) -> None:
        """CLI invocation against an empty root exits 0."""
        fake_root = tmp_path / "fake_runtime"
        fake_root.mkdir()
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(fake_root),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    def test_missing_root_exits_nonzero(self, tmp_path: Path) -> None:
        """--root is now required; invoking without it must fail (argparse exit 2)."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--dry-run"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(tmp_path),
        )
        assert result.returncode != 0
        assert "--root" in (result.stderr + result.stdout)

    def test_root_validated_even_with_explicit_db(self, tmp_path: Path) -> None:
        """When --db is explicit AND valid, a typo --root must still exit 3.
        Without this check the script silently scans a nonexistent tree and
        appears to succeed."""
        good_db = tmp_path / "ema_backtest_runs.db"
        good_db.touch()
        bogus_root = tmp_path / "does_not_exist"
        assert not bogus_root.exists()
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(bogus_root),
                "--db",
                str(good_db),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 3
        assert "--root path does not exist" in (result.stderr + result.stdout)

    def test_apply_rewrites_db_artifact_paths(self, tmp_path: Path) -> None:
        """When a legacy dir contains files referenced by backtest_runs columns,
        --apply must rewrite those columns so the conductor doesn't lose
        access to trades.csv/diagnostics.json/etc. after the rename."""
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        round_id = "job-12-round-1"
        legacy_dir = _make_experiments_dir(root, JOB_ID, 1, THESIS_ID_A)
        trades_old = str(legacy_dir / "trades.csv")
        events_old = str(legacy_dir / "strategy_events.parquet")
        diag_old = str(legacy_dir / "diagnostics.json")
        # Seed a row that points at the legacy paths.
        conn.execute(
            "INSERT INTO backtest_runs (run_id, thesis_id, job, "
            "research_round_id, research_round_number, "
            "trades_file, strategy_events_file, diagnostics_file) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "run-001",
                THESIS_ID_A,
                JOB_ID,
                round_id,
                1,
                trades_old,
                events_old,
                diag_old,
            ),
        )
        conn.commit()
        conn.close()

        rc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--apply",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rc.returncode == 0, f"stdout={rc.stdout!r} stderr={rc.stderr!r}"

        # The dir was renamed.
        new_dir = legacy_dir.parent / round_id
        assert new_dir.is_dir()
        assert not legacy_dir.exists()

        # And the DB columns now point at the new location.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT trades_file, strategy_events_file, diagnostics_file "
                "FROM backtest_runs WHERE run_id = ?",
                ("run-001",),
            ).fetchone()
        finally:
            conn.close()
        assert THESIS_ID_A not in row[0]
        assert round_id in row[0]
        assert THESIS_ID_A not in row[1]
        assert round_id in row[1]
        assert THESIS_ID_A not in row[2]
        assert round_id in row[2]

    def test_apply_drops_migration_marker_in_renamed_dir(self, tmp_path: Path) -> None:
        """Each renamed dir must contain a ``.migrated_from_thesis_id__{old}``
        marker so compiler_builder's legacy-dir guardrail can distinguish a
        migration-touched dir from stale builder state."""
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        round_id = "job-12-round-1"
        _seed(conn, THESIS_ID_A, JOB_ID, round_id, "run-001", round_number=1)
        conn.close()
        legacy_dir = _make_experiments_dir(root, JOB_ID, 1, THESIS_ID_A)

        rc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--apply",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rc.returncode == 0
        new_dir = legacy_dir.parent / round_id
        assert (new_dir / f".migrated_from_thesis_id__{THESIS_ID_A}").exists()

    def test_thesis_id_named_like_other_round_id_still_migrates(self, tmp_path: Path) -> None:
        """A thesis_id literally named like another (job, round)'s
        research_round_id must still migrate when it lives under a different
        (job, round). Global all_round_ids membership is NOT a skip signal."""
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        # Round 1 of job 1 has a real round_id "job-1-round-1".
        _seed(conn, "real_thesis_v1", 1, "job-1-round-1", "run-001", round_number=1)
        # Round 3 of job 2 has a thesis_id that LOOKS like job-1-round-1.
        round_id_for_collider = "job-2-round-3"
        _seed(
            conn,
            "job-1-round-1",
            2,
            round_id_for_collider,
            "run-002",
            round_number=3,
        )
        conn.close()
        # Create the colliding-named thesis dir under job-2/round-3.
        collider_dir = _make_experiments_dir(root, 2, 3, "job-1-round-1")

        rc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rc.returncode == 0
        # The dir should be planned for rename, NOT silently skipped.
        assert "WOULD RENAME" in rc.stdout
        assert str(collider_dir) in rc.stdout
        assert round_id_for_collider in rc.stdout

    def test_paths_with_sql_wildcards_do_not_corrupt_unrelated_rows(self, tmp_path: Path) -> None:
        """A thesis_id containing ``_`` (SQL LIKE wildcard) must not match
        unrelated artifact-path rows. Rewrite uses Python prefix match, not
        SQL LIKE, so the wildcard hazard cannot fire."""
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        thesis_with_underscore = "foo_bar"  # underscore is SQL LIKE wildcard.
        round_id = "job-12-round-1"
        legacy_dir = _make_experiments_dir(root, JOB_ID, 1, thesis_with_underscore)
        trades_path = str(legacy_dir / "trades.csv")
        # Row that SHOULD be rewritten.
        conn.execute(
            "INSERT INTO backtest_runs (run_id, thesis_id, job, "
            "research_round_id, research_round_number, trades_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("run-001", thesis_with_underscore, JOB_ID, round_id, 1, trades_path),
        )
        # Sibling path that LIKE-prefix would have falsely matched
        # (replace the `_` with any other char and the SQL LIKE pattern
        # `.../experiments/foo_bar%` matches `.../experiments/fooXbar/...`).
        sibling_path = str(legacy_dir.parent / "fooXbar" / "trades.csv")
        conn.execute(
            "INSERT INTO backtest_runs (run_id, thesis_id, job, "
            "research_round_id, research_round_number, trades_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("run-sibling", "fooXbar", 99, "job-99-round-7", 7, sibling_path),
        )
        conn.commit()
        conn.close()

        rc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--apply",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert rc.returncode == 0

        conn = sqlite3.connect(db_path)
        try:
            target = conn.execute(
                "SELECT trades_file FROM backtest_runs WHERE run_id = ?",
                ("run-001",),
            ).fetchone()
            sibling = conn.execute(
                "SELECT trades_file FROM backtest_runs WHERE run_id = ?",
                ("run-sibling",),
            ).fetchone()
        finally:
            conn.close()
        # Target row was rewritten.
        assert round_id in target[0]
        assert thesis_with_underscore not in target[0]
        # Sibling row was NOT touched (no wildcard expansion bleed-through).
        assert sibling[0] == sibling_path

    def test_reverse_removes_migration_marker(self, staged_runtime) -> None:
        """Rollback must remove the migration marker; otherwise the
        compiler_builder guardrail stays silently suppressed on a dir that
        is no longer migration-touched."""
        root, db_path, legacy_dir, round_id = staged_runtime
        target_dir = legacy_dir.parent / round_id

        forward = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--apply",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert forward.returncode == 0
        marker_name = f".migrated_from_thesis_id__{legacy_dir.name}"
        assert (target_dir / marker_name).exists()

        reverse = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--reverse",
                "--apply",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert reverse.returncode == 0
        # The legacy dir is restored, and the marker is GONE from inside it.
        assert legacy_dir.is_dir()
        assert not (legacy_dir / marker_name).exists()

    def test_rewrite_db_paths_handles_windows_separator(self, tmp_path: Path) -> None:
        """A row whose path was persisted with backslashes (e.g. originating
        from a Windows agent) must still be rewritten when the dir it points
        into is migrated."""
        _rewrite_db_paths = migrate_experiment_dirs._rewrite_db_paths

        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        # Build literal Windows-style paths (using \ even on POSIX hosts);
        # _rewrite_db_paths must accept either separator when comparing the
        # column value to the rename's `old` prefix.
        old_dir_str = str(root) + "\\runtime\\jobs\\job-12\\research\\round-1\\experiments\\foo"
        new_dir_str = (
            str(root) + "\\runtime\\jobs\\job-12\\research\\round-1\\experiments\\job-12-round-1"
        )
        trades_old = old_dir_str + "\\trades.csv"
        conn.execute(
            "INSERT INTO backtest_runs (run_id, thesis_id, job, "
            "research_round_id, research_round_number, trades_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("run-win", "foo", 12, "job-12-round-1", 1, trades_old),
        )
        conn.commit()
        conn.close()

        updated = _rewrite_db_paths(
            [db_path],
            [(Path(old_dir_str), Path(new_dir_str))],
        )

        assert updated == 1
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT trades_file FROM backtest_runs WHERE run_id = ?",
                ("run-win",),
            ).fetchone()
        finally:
            conn.close()
        assert "foo" not in row[0].rsplit("\\", 1)[0]
        assert "job-12-round-1" in row[0]

    def test_migration_visits_round_0_baseline_directories(self, tmp_path: Path) -> None:
        """autoresearch_runtime_paths emits 'round-0-baseline' (not 'round-0')
        for the baseline-round directory. The walker must accept the
        '-baseline' suffix or baseline artifacts stay in the legacy layout."""
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        baseline_round_id = "job-12-round-0"
        # Seed a baseline row.
        conn.execute(
            "INSERT INTO backtest_runs (run_id, thesis_id, job, "
            "research_round_id, research_round_number) VALUES (?, ?, ?, ?, ?)",
            ("run-baseline", "baseline_ema", JOB_ID, baseline_round_id, 0),
        )
        conn.commit()
        conn.close()
        # Stage the legacy baseline dir under the round-0-baseline path.
        baseline_legacy_dir = (
            root
            / "runtime"
            / "jobs"
            / f"job-{JOB_ID}"
            / "research"
            / "round-0-baseline"
            / "experiments"
            / "baseline_ema"
        )
        baseline_legacy_dir.mkdir(parents=True, exist_ok=True)
        (baseline_legacy_dir / "thesis.json").write_text('{"thesis_id": "baseline_ema"}')

        rc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(root),
                "--db",
                str(db_path),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert rc.returncode == 0
        assert "WOULD RENAME" in rc.stdout
        assert str(baseline_legacy_dir) in rc.stdout
        assert baseline_round_id in rc.stdout

    def test_typo_root_exits_3(self, tmp_path: Path) -> None:
        """An explicit --root pointing at a nonexistent path is an operator
        typo, not "nothing to migrate" — exit 3 like the --db typo case."""
        bogus = tmp_path / "does_not_exist"
        assert not bogus.exists()
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--root",
                str(bogus),
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "--root path does not exist" in (result.stderr + result.stdout)


class TestRegressionFindings:
    """Regression tests for PR review findings E, F, G."""

    def test_explicit_missing_db_exits_3(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Finding E: an explicit --db path that does not exist must exit 3,
        not silently exit 0 as if migration completed."""
        root = tmp_path / "runtime_root"
        root.mkdir()
        bogus = tmp_path / "does_not_exist.db"

        exit_code = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(bogus), "--dry-run"]
        )

        assert exit_code == 3

    def test_reverse_lookup_duplicate_aborts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Finding F: if two distinct thesis_ids map to the same
        (research_round_id, job_id, round_number), the migration must abort
        instead of silently overwriting one of them in the reverse lookup.
        """
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        shared_round_id = "job-12-round-1"
        # Two distinct thesis_ids share the same research_round_id at the same
        # (job_id, round_number). Canonical scheme prevents this, but a
        # malformed DB row could otherwise corrupt the reverse migration.
        _seed(conn, THESIS_ID_A, JOB_ID, shared_round_id, "run-001", round_number=1)
        _seed(conn, THESIS_ID_B, JOB_ID, shared_round_id, "run-002", round_number=1)
        conn.close()

        exit_code = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--dry-run"]
        )

        captured = capsys.readouterr()
        assert exit_code == 1
        assert "COLLISION" in captured.out
        assert shared_round_id in captured.out
        assert THESIS_ID_A in captured.out
        assert THESIS_ID_B in captured.out

    def test_same_thesis_across_rounds_migrates_both(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Finding G: the same thesis_id legitimately appearing in two
        different rounds of the same job must produce two independent
        renames, not a false collision.
        """
        root = tmp_path / "runtime_root"
        root.mkdir()
        db_path = tmp_path / "ema_backtest_runs.db"
        conn = _make_db(db_path)
        round_id_1 = "job-12-round-1"
        round_id_2 = "job-12-round-2"
        _seed(conn, THESIS_ID_A, JOB_ID, round_id_1, "run-001", round_number=1)
        _seed(conn, THESIS_ID_A, JOB_ID, round_id_2, "run-002", round_number=2)
        conn.close()

        legacy_1 = _make_experiments_dir(root, JOB_ID, 1, THESIS_ID_A)
        legacy_2 = _make_experiments_dir(root, JOB_ID, 2, THESIS_ID_A)
        target_1 = legacy_1.parent / round_id_1
        target_2 = legacy_2.parent / round_id_2

        # Dry-run plans both renames.
        exit_code = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--dry-run"]
        )
        out = capsys.readouterr().out
        assert exit_code == 0, out
        assert "COLLISION" not in out
        assert str(legacy_1) in out and round_id_1 in out
        assert str(legacy_2) in out and round_id_2 in out

        # Apply moves both to their per-round targets.
        exit_code = migrate_experiment_dirs.main(
            ["--root", str(root), "--db", str(db_path), "--apply"]
        )
        assert exit_code == 0
        assert target_1.is_dir() and not legacy_1.exists()
        assert target_2.is_dir() and not legacy_2.exists()
        assert (target_1 / "thesis.json").exists()
        assert (target_2 / "thesis.json").exists()
