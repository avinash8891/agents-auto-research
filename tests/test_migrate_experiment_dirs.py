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
