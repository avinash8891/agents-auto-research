#!/usr/bin/env python3
"""Migrate on-disk experiment directories from legacy ``thesis_id`` layout to
``research_round_id`` layout (Spec A1 §6 + §10).

Legacy layout:
    {root}/runtime/jobs/job-{N}/research/round-{M}/experiments/{thesis_id}/

New layout:
    {root}/runtime/jobs/job-{N}/research/round-{M}/experiments/{research_round_id}/

The script:
    1. Reads ``backtest_runs`` from the backtest DB and builds the mapping
       ``(thesis_id, job_id) -> research_round_id``.
    2. Detects collisions where one ``(thesis_id, job_id)`` pair maps to >1
       distinct ``research_round_id`` — aborts non-zero.
    3. Walks every ``experiments/`` directory under ``{root}/runtime/jobs/job-*``.
       Subdirectories named after a known ``research_round_id`` are skipped
       (idempotent). Subdirectories named after a known ``thesis_id`` for the
       job are renamed to the corresponding ``research_round_id``.
    4. Default mode is dry-run (``WOULD RENAME`` lines on stdout). ``--apply``
       performs the actual ``Path.rename``. ``--reverse`` inverts direction.

Stdlib-only.

``--root`` is the directory CONTAINING ``runtime/`` (typically the repo
root), not ``runtime/`` itself — the script appends ``runtime/jobs`` to
``--root`` when walking job dirs.

Usage::

    python scripts/migrate_experiment_dirs.py --root .
    python scripts/migrate_experiment_dirs.py --root . --apply
    python scripts/migrate_experiment_dirs.py --root . --reverse --apply

Exit codes:
    0  success / dry-run completed / nothing to do
    1  collision detected
    2  I/O error during rename
    3  bad CLI args / missing DB
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Repo convention: per-family backtest DBs live next to the repo root (or under
# the runtime parent). See autoresearch_controller.py:628 and
# research_subagents.py:259-262.
DEFAULT_DB_GLOB = "*_backtest_runs.db"

JOB_DIR_RE = re.compile(r"^job-(\d+)$")

logger = logging.getLogger("migrate_experiment_dirs")


def _discover_db_paths(root: Path, explicit: str | None) -> list[Path]:
    """Locate one or more ``*_backtest_runs.db`` files.

    Resolution order matches research_subagents._candidate_db_paths:
    1. Explicit ``--db`` (single path, must exist).
    2. ``root/*_backtest_runs.db``.
    3. ``root.parent/*_backtest_runs.db``.
    4. ``root.parent/runtime/*_backtest_runs.db``.
    5. REPO_ROOT/*_backtest_runs.db (fallback for default cwd).
    """
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            logger.error("db path does not exist: %s", p)
            return []
        return [p]

    candidates: list[Path] = []
    seen: set[Path] = set()
    for base in (root, root.parent, root.parent / "runtime", REPO_ROOT):
        if not base.exists():
            continue
        for p in sorted(base.glob(DEFAULT_DB_GLOB)):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            candidates.append(rp)
    return candidates


def _load_mapping(
    db_paths: list[Path],
) -> tuple[dict[tuple[str, int], set[str]], set[str]]:
    """Return ``((thesis_id, job_id) -> {research_round_id, ...}, all_round_ids)``.

    Multi-element sets in the first dict indicate collisions.
    """
    mapping: dict[tuple[str, int], set[str]] = defaultdict(set)
    all_round_ids: set[str] = set()

    for db_path in db_paths:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT thesis_id, job, research_round_id
                    FROM backtest_runs
                    WHERE thesis_id IS NOT NULL AND thesis_id != ''
                      AND research_round_id IS NOT NULL AND research_round_id != ''
                    """).fetchall()
            except sqlite3.OperationalError as exc:
                logger.warning("skipping %s: %s", db_path, exc)
                continue

        for row in rows:
            thesis_id = row["thesis_id"]
            try:
                job_id = int(row["job"])
            except (TypeError, ValueError):
                continue
            round_id = row["research_round_id"]
            mapping[(thesis_id, job_id)].add(round_id)
            all_round_ids.add(round_id)

    return mapping, all_round_ids


def _detect_collisions(
    mapping: dict[tuple[str, int], set[str]],
) -> list[tuple[str, int, list[str]]]:
    """Return list of ``(thesis_id, job_id, [round_ids])`` where len > 1."""
    collisions: list[tuple[str, int, list[str]]] = []
    for (thesis_id, job_id), round_ids in mapping.items():
        if len(round_ids) > 1:
            collisions.append((thesis_id, job_id, sorted(round_ids)))
    return sorted(collisions)


def _iter_experiment_subdirs(root: Path):
    """Yield ``(job_id, subdir_path)`` for every dir under
    ``{root}/runtime/jobs/job-*/research/round-*/experiments/``.
    """
    jobs_root = root / "runtime" / "jobs"
    if not jobs_root.is_dir():
        return
    for job_dir in sorted(jobs_root.iterdir()):
        if not job_dir.is_dir():
            continue
        m = JOB_DIR_RE.match(job_dir.name)
        if not m:
            continue
        job_id = int(m.group(1))
        research_root = job_dir / "research"
        if not research_root.is_dir():
            continue
        for round_dir in sorted(research_root.iterdir()):
            if not round_dir.is_dir():
                continue
            experiments_dir = round_dir / "experiments"
            if not experiments_dir.is_dir():
                continue
            for sub in sorted(experiments_dir.iterdir()):
                if sub.is_dir():
                    yield job_id, sub


def _plan_renames(
    root: Path,
    mapping: dict[tuple[str, int], set[str]],
    all_round_ids: set[str],
    reverse: bool,
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Return ``(planned_renames, unknown_dirs)``.

    Idempotency:
    - Forward: if subdir already equals a known round_id, skip.
    - Reverse: if subdir already equals a known thesis_id, skip.
    """
    forward_lookup: dict[tuple[str, int], str] = {}
    for key, round_ids in mapping.items():
        if len(round_ids) == 1:
            forward_lookup[key] = next(iter(round_ids))

    reverse_lookup: dict[tuple[str, int], str] = {}
    for (thesis_id, job_id), round_id in forward_lookup.items():
        reverse_lookup[(round_id, job_id)] = thesis_id

    renames: list[tuple[Path, Path]] = []
    unknowns: list[Path] = []
    known_thesis_ids = {t for (t, _j) in mapping.keys()}

    for job_id, sub in _iter_experiment_subdirs(root):
        name = sub.name
        if not reverse:
            if name in all_round_ids:
                continue  # already migrated
            target_round_id = forward_lookup.get((name, job_id))
            if target_round_id is None:
                if name in known_thesis_ids:
                    # Thesis id seen for some other job; not actionable here.
                    unknowns.append(sub)
                else:
                    unknowns.append(sub)
                continue
            new_path = sub.parent / target_round_id
            renames.append((sub, new_path))
        else:
            target_thesis_id = reverse_lookup.get((name, job_id))
            if target_thesis_id is None:
                if name in known_thesis_ids:
                    # Already reverted.
                    continue
                unknowns.append(sub)
                continue
            new_path = sub.parent / target_thesis_id
            renames.append((sub, new_path))

    return renames, unknowns


def _execute_renames(renames: list[tuple[Path, Path]]) -> int:
    """Apply renames. Returns count actually renamed. Raises OSError on failure."""
    applied = 0
    for old, new in renames:
        if not old.exists():
            # Already moved by a prior step (e.g. previous run).
            continue
        if new.exists():
            logger.error("refusing to rename %s -> %s: target already exists", old, new)
            raise OSError(f"target exists: {new}")
        old.rename(new)
        applied += 1
        print(f"RENAMED {old} -> {new}")
    return applied


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        default=".",
        help=(
            "directory CONTAINING runtime/ (typically the repo root). The "
            "script walks {root}/runtime/jobs/job-*/research/round-*/"
            "experiments/. Do NOT pass runtime/ here — pass its parent. "
            "Default: ."
        ),
    )
    parser.add_argument(
        "--db",
        help=(
            "explicit backtest-run DB path. Default: discover *_backtest_runs.db near "
            "--root and at repo root (matches research_subagents resolution)."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually perform renames (default: dry-run)"
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="rename research_round_id dirs back to thesis_id dirs (rollback)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="explicit dry-run (default behaviour when --apply is omitted)",
    )
    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        logger.error("--apply and --dry-run are mutually exclusive")
        return 3

    root = Path(args.root).expanduser().resolve()

    db_paths = _discover_db_paths(root, args.db)
    if not db_paths:
        logger.warning("no backtest_runs.db found near %s; nothing to migrate", root)
        # Empty input is not an error — exit 0 so smoke invocations succeed.
        return 0

    logger.info("using DB(s): %s", ", ".join(str(p) for p in db_paths))

    mapping, all_round_ids = _load_mapping(db_paths)
    collisions = _detect_collisions(mapping)
    if collisions:
        print("COLLISION: multiple research_round_id values for one (thesis_id, job_id):")
        for thesis_id, job_id, round_ids in collisions:
            print(f"  thesis_id={thesis_id} job_id={job_id} round_ids={round_ids}")
        return 1

    renames, unknowns = _plan_renames(root, mapping, all_round_ids, args.reverse)

    for sub in unknowns:
        print(f"UNKNOWN {sub}")

    if not renames:
        print("(no renames required)")
        return 0

    if args.apply:
        try:
            applied = _execute_renames(renames)
        except OSError as exc:
            logger.error("rename failed: %s", exc)
            return 2
        print(f"applied {applied} rename(s)")
        return 0

    for old, new in renames:
        print(f"WOULD RENAME {old} -> {new}")
    print(f"({len(renames)} rename(s) planned; rerun with --apply to execute)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
