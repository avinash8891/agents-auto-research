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
            research_round_id TEXT NOT NULL DEFAULT ''
        )
        """)
    return conn


def _seed(
    conn: sqlite3.Connection, thesis_id: str, job_id: int, round_id: str, run_id: str
) -> None:
    conn.execute(
        "INSERT INTO backtest_runs (run_id, thesis_id, job, research_round_id) "
        "VALUES (?, ?, ?, ?)",
        (run_id, thesis_id, job_id, round_id),
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
    _seed(conn, THESIS_ID_A, JOB_ID, round_id, "run-001")
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
        _seed(conn, THESIS_ID_A, JOB_ID, round_id_1, "run-001")
        _seed(conn, THESIS_ID_A, JOB_ID, round_id_2, "run-002")
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
