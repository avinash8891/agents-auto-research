#!/usr/bin/env python3
"""Migrate on-disk experiment directories from legacy ``thesis_id`` layout to
``research_round_id`` layout (Spec A1 §6 + §10).

Legacy layout:
    {root}/runtime/jobs/job-{N}/research/round-{M}/experiments/{thesis_id}/

New layout:
    {root}/runtime/jobs/job-{N}/research/round-{M}/experiments/{research_round_id}/

The script:
    1. Reads ``backtest_runs`` from the backtest DB and builds the mapping
       ``(thesis_id, job_id, round_number) -> research_round_id``. Keying by
       round_number is required because the same ``thesis_id`` can legitimately
       appear in multiple research rounds for the same job.
    2. Detects collisions where one ``(thesis_id, job_id, round_number)`` triple
       maps to >1 distinct ``research_round_id`` — aborts non-zero.
    3. Walks every ``experiments/`` directory under ``{root}/runtime/jobs/job-*``.
       Subdirectories named after a known ``research_round_id`` are skipped
       (idempotent). Subdirectories named after a known ``thesis_id`` for the
       (job, round) are renamed to the corresponding ``research_round_id``.
    4. Default mode is dry-run (``WOULD RENAME`` lines on stdout). ``--apply``
       performs the actual ``Path.rename``. ``--reverse`` inverts direction.

Stdlib-only.

``--root`` is REQUIRED and is the directory CONTAINING ``runtime/`` (typically
the repo root), not ``runtime/`` itself — the script appends ``runtime/jobs`` to
``--root`` when walking job dirs. There is intentionally no default to prevent
discovery from leaking into an unrelated checkout when run from an arbitrary
CWD.

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

# Repo convention: per-family backtest DBs live next to the repo root. See
# autoresearch_controller.py:628 and research_subagents.py:259-262.
DEFAULT_DB_GLOB = "*_backtest_runs.db"

JOB_DIR_RE = re.compile(r"^job-(\d+)$")
# autoresearch_runtime_paths.research_round_root() emits "round-{N}" for
# non-baseline rounds and the literal "round-0-baseline" for round 0. Both
# layouts can host legacy experiments/{thesis_id}/ dirs, so the walker has
# to accept either spelling and emit round_number=0 for the baseline form.
ROUND_DIR_RE = re.compile(r"^round-(\d+)(?:-baseline)?$")

logger = logging.getLogger("migrate_experiment_dirs")


class ExplicitDbMissingError(ValueError):
    """Raised when an explicit operator-supplied path does not exist.

    Covers both ``--db /typo/path`` and ``--root /typo/path``. Distinct from
    the auto-discovery empty case so ``main()`` can exit 3 for operator
    typos instead of silently treating it as "nothing to migrate".
    """


def _discover_db_paths(root: Path, explicit: str | None) -> list[Path]:
    """Locate one or more ``*_backtest_runs.db`` files.

    Resolution:
    - Explicit ``--db``: single path, must exist (raises
      ``ExplicitDbMissingError`` otherwise — caller translates to exit 3).
    - Auto-discovery: ``{root}/**/*_backtest_runs.db`` only. We do NOT ascend
      past ``--root`` (no ``root.parent`` probe), because doing so makes the
      script latch onto a sibling checkout's DB when invoked from an unrelated
      CWD. Operators wanting a DB outside ``--root`` must pass ``--db`` directly.
    """
    # --root must exist regardless of --db, because the directory walk that
    # actually renames artifacts uses --root. An explicit --db with a mistyped
    # --root would otherwise silently scan a nonexistent tree and exit 0.
    if not root.exists():
        raise ExplicitDbMissingError(f"--root path does not exist: {root}")

    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.exists():
            raise ExplicitDbMissingError(f"--db path does not exist: {p}")
        return [p]

    candidates: list[Path] = []
    seen: set[Path] = set()
    for p in sorted(root.rglob(DEFAULT_DB_GLOB)):
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        candidates.append(rp)
    return candidates


def _load_mapping(
    db_paths: list[Path],
) -> tuple[dict[tuple[str, int, int], set[str]], set[str]]:
    """Return ``((thesis_id, job_id, round_number) -> {research_round_id, ...},
    all_round_ids)``.

    Multi-element sets in the first dict indicate collisions within a single
    ``(thesis_id, job_id, round_number)`` triple.
    """
    mapping: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    all_round_ids: set[str] = set()

    for db_path in db_paths:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT thesis_id, job, research_round_number, research_round_id
                    FROM backtest_runs
                    WHERE thesis_id IS NOT NULL AND thesis_id != ''
                      AND research_round_id IS NOT NULL AND research_round_id != ''
                      AND research_round_number IS NOT NULL
                      AND research_round_number >= 0
                    """).fetchall()
            except sqlite3.OperationalError as exc:
                logger.warning("skipping %s: %s", db_path, exc)
                continue

        for row in rows:
            thesis_id = row["thesis_id"]
            try:
                job_id = int(row["job"])
                round_number = int(row["research_round_number"])
            except (TypeError, ValueError):
                continue
            round_id = row["research_round_id"]
            mapping[(thesis_id, job_id, round_number)].add(round_id)
            all_round_ids.add(round_id)

    return mapping, all_round_ids


def _detect_collisions(
    mapping: dict[tuple[str, int, int], set[str]],
) -> list[tuple[str, int, int, list[str]]]:
    """Return list of ``(thesis_id, job_id, round_number, [round_ids])`` where
    len > 1."""
    collisions: list[tuple[str, int, int, list[str]]] = []
    for (thesis_id, job_id, round_number), round_ids in mapping.items():
        if len(round_ids) > 1:
            collisions.append((thesis_id, job_id, round_number, sorted(round_ids)))
    return sorted(collisions)


def _iter_experiment_subdirs(root: Path):
    """Yield ``(job_id, round_number, subdir_path)`` for every dir under
    ``{root}/runtime/jobs/job-*/research/round-*/experiments/``.
    """
    jobs_root = root / "runtime" / "jobs"
    if not jobs_root.is_dir():
        return
    for job_dir in sorted(jobs_root.iterdir()):
        if not job_dir.is_dir():
            continue
        m_job = JOB_DIR_RE.match(job_dir.name)
        if not m_job:
            continue
        job_id = int(m_job.group(1))
        research_root = job_dir / "research"
        if not research_root.is_dir():
            continue
        for round_dir in sorted(research_root.iterdir()):
            if not round_dir.is_dir():
                continue
            m_round = ROUND_DIR_RE.match(round_dir.name)
            if not m_round:
                continue
            round_number = int(m_round.group(1))
            experiments_dir = round_dir / "experiments"
            if not experiments_dir.is_dir():
                continue
            for sub in sorted(experiments_dir.iterdir()):
                if sub.is_dir():
                    yield job_id, round_number, sub


def _build_lookups(
    mapping: dict[tuple[str, int, int], set[str]],
) -> tuple[dict[tuple[str, int, int], str], dict[tuple[str, int, int], str]]:
    """Return ``(forward_lookup, reverse_lookup)``.

    ``forward_lookup``: ``(thesis_id, job_id, round_number) -> research_round_id``.
    ``reverse_lookup``: ``(research_round_id, job_id, round_number) -> thesis_id``.

    Raises ``ValueError`` if the reverse mapping is ambiguous — i.e. two
    distinct ``thesis_id`` rows share the same ``(research_round_id, job_id,
    round_number)`` triple. The canonical id scheme makes this impossible, but
    a malformed DB row could otherwise cause the migration to silently
    overwrite the last write.
    """
    forward_lookup: dict[tuple[str, int, int], str] = {}
    for key, round_ids in mapping.items():
        if len(round_ids) == 1:
            forward_lookup[key] = next(iter(round_ids))

    reverse_lookup: dict[tuple[str, int, int], str] = {}
    for (thesis_id, job_id, round_number), round_id in forward_lookup.items():
        rkey = (round_id, job_id, round_number)
        existing = reverse_lookup.get(rkey)
        if existing is not None and existing != thesis_id:
            raise ValueError(
                "reverse-lookup collision: research_round_id="
                f"{round_id} job_id={job_id} round_number={round_number} maps to "
                f"both thesis_id={existing!r} and thesis_id={thesis_id!r}"
            )
        reverse_lookup[rkey] = thesis_id

    return forward_lookup, reverse_lookup


def _plan_renames(
    root: Path,
    mapping: dict[tuple[str, int, int], set[str]],
    reverse: bool,
) -> tuple[list[tuple[Path, Path]], list[Path]]:
    """Return ``(planned_renames, unknown_dirs)``.

    Idempotency is scoped to the directory's own ``(job, round)``: forward
    skips when the subdir already equals ``job-{job}-round-{round}``; reverse
    skips when it equals a thesis_id known for that ``(job, round)``. Global
    membership in ``all_round_ids`` is intentionally NOT a skip signal — a
    thesis_id literally named ``job-1-round-1`` appearing under
    ``job-2/round-3`` must still migrate.
    """
    forward_lookup, reverse_lookup = _build_lookups(mapping)

    renames: list[tuple[Path, Path]] = []
    unknowns: list[Path] = []
    known_thesis_ids = {t for (t, _j, _r) in mapping.keys()}

    for job_id, round_number, sub in _iter_experiment_subdirs(root):
        name = sub.name
        # The expected canonical round-id for THIS directory's parent
        # (job, round). Used to detect "already migrated" without misfiring
        # on a thesis_id that happens to look like another round's id (e.g.
        # thesis_id "job-1-round-1" appearing under job-2/round-3 would
        # otherwise be globally-skipped and never migrated).
        expected_round_id = f"job-{job_id}-round-{round_number}"
        if not reverse:
            if name == expected_round_id:
                continue  # already migrated for this (job, round)
            target_round_id = forward_lookup.get((name, job_id, round_number))
            if target_round_id is None:
                unknowns.append(sub)
                continue
            new_path = sub.parent / target_round_id
            renames.append((sub, new_path))
        else:
            target_thesis_id = reverse_lookup.get((name, job_id, round_number))
            if target_thesis_id is None:
                if name in known_thesis_ids:
                    # Already reverted.
                    continue
                unknowns.append(sub)
                continue
            new_path = sub.parent / target_thesis_id
            renames.append((sub, new_path))

    return renames, unknowns


_MIGRATION_MARKER_PREFIX = ".migrated_from_thesis_id__"


def _execute_renames(renames: list[tuple[Path, Path]], *, reverse: bool = False) -> int:
    """Apply renames. Returns count actually renamed. Raises OSError on failure.

    Forward (``reverse=False``): drops a ``.migrated_from_thesis_id__{old_name}``
    marker file in each renamed directory so downstream code
    (compiler_builder's legacy-dir guardrail) can distinguish a
    migration-touched dir — which may legitimately contain a thesis.json
    carried over from the legacy layout — from an actually-stale builder
    artifact dir.

    Reverse: BEFORE renaming back, remove any migration marker(s) inside
    the dir. Leaving them in place after rollback would keep the
    guardrail silently suppressed on a dir that's no longer migration-
    touched, which would mask a real stale-builder regression next time.
    """
    applied = 0
    for old, new in renames:
        if not old.exists():
            # Already moved by a prior step (e.g. previous run).
            continue
        if new.exists():
            logger.error("refusing to rename %s -> %s: target already exists", old, new)
            raise OSError(f"target exists: {new}")
        if reverse:
            for marker_path in old.iterdir():
                if marker_path.name.startswith(_MIGRATION_MARKER_PREFIX):
                    try:
                        marker_path.unlink()
                    except OSError:
                        # Best-effort: a leftover marker is preferable to
                        # failing the rollback.
                        pass
        old.rename(new)
        if not reverse:
            marker = new / f"{_MIGRATION_MARKER_PREFIX}{old.name}"
            try:
                marker.touch(exist_ok=True)
            except OSError:
                # Marker is advisory — failure to drop it shouldn't undo
                # the successful rename. compiler_builder's guardrail will
                # then treat the dir as legacy-with-builder-artifacts (loud
                # failure), which is the safer fallback.
                pass
        applied += 1
        print(f"RENAMED {old} -> {new}")
    return applied


_ARTIFACT_PATH_COLUMNS = ("trades_file", "strategy_events_file", "diagnostics_file")


def _rewrite_db_paths(db_paths: list[Path], renames: list[tuple[Path, Path]]) -> int:
    """Rewrite backtest_runs artifact-path columns to match renamed dirs.

    Without this, the DB still points at old experiments/{thesis_id}/ paths
    after rename, and BacktestRunDB._load → _existing_artifact_file rejects
    them as missing — losing trade/diagnostic artifacts post-migration.
    Returns the number of rows updated (sum across columns).

    Match is performed in Python (not SQL LIKE) so directory names containing
    ``_`` or ``%`` cannot accidentally rewrite unrelated rows via the SQL
    wildcard expansion. The prefix check accepts both POSIX (``/``) and
    Windows (``\\``) separators so rows persisted on either OS migrate
    correctly.
    """
    if not renames:
        return 0
    updated = 0
    substitutions = [(str(old), str(new)) for old, new in renames]
    for db_path in db_paths:
        conn = sqlite3.connect(db_path)
        try:
            for col in _ARTIFACT_PATH_COLUMNS:
                rows = list(conn.execute(f"SELECT run_id, {col} FROM backtest_runs"))  # noqa: S608
                for run_id, existing in rows:
                    if not isinstance(existing, str) or not existing:
                        continue
                    for old_str, new_str in substitutions:
                        if (
                            existing == old_str
                            or existing.startswith(old_str + "/")
                            or existing.startswith(old_str + "\\")
                        ):
                            rewritten = new_str + existing[len(old_str) :]
                            conn.execute(
                                f"UPDATE backtest_runs SET {col} = ? WHERE run_id = ?",  # noqa: S608
                                (rewritten, run_id),
                            )
                            updated += 1
                            break
            conn.commit()
        finally:
            conn.close()
    return updated


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        required=True,
        help=(
            "REQUIRED: directory CONTAINING runtime/ (typically the repo "
            "root). The script walks {root}/runtime/jobs/job-*/research/"
            "round-*/experiments/. Do NOT pass runtime/ here — pass its "
            "parent. No default: operator must opt in to prevent discovery "
            "from leaking into an unrelated checkout."
        ),
    )
    parser.add_argument(
        "--db",
        help=(
            "explicit backtest-run DB path. If omitted, the script searches "
            "{root}/**/*_backtest_runs.db. An explicit --db that does not "
            "exist exits 3 (operator typo); an empty auto-discovery exits 0."
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

    try:
        db_paths = _discover_db_paths(root, args.db)
    except ExplicitDbMissingError as exc:
        logger.error("%s", exc)
        return 3

    if not db_paths:
        # Auto-discovery yielded nothing — treat as smoke / nothing to do.
        # (Explicit-but-missing was already handled above with exit 3.)
        logger.warning("no backtest_runs.db found under %s; nothing to migrate", root)
        return 0

    logger.info("using DB(s): %s", ", ".join(str(p) for p in db_paths))

    mapping, _all_round_ids = _load_mapping(db_paths)
    collisions = _detect_collisions(mapping)
    if collisions:
        print(
            "COLLISION: multiple research_round_id values for one "
            "(thesis_id, job_id, round_number):"
        )
        for thesis_id, job_id, round_number, round_ids in collisions:
            print(
                f"  thesis_id={thesis_id} job_id={job_id} "
                f"round_number={round_number} round_ids={round_ids}"
            )
        return 1

    try:
        renames, unknowns = _plan_renames(root, mapping, args.reverse)
    except ValueError as exc:
        # Reverse-lookup duplicate — distinct thesis_ids share the same
        # (research_round_id, job_id, round_number) target.
        print(f"COLLISION: {exc}")
        return 1

    for sub in unknowns:
        print(f"UNKNOWN {sub}")

    if not renames:
        print("(no renames required)")
        return 0

    if args.apply:
        try:
            applied = _execute_renames(renames, reverse=args.reverse)
        except OSError as exc:
            logger.error("rename failed: %s", exc)
            return 2
        try:
            rewritten = _rewrite_db_paths(db_paths, renames)
        except sqlite3.Error as exc:
            # State is now half-migrated: directories renamed, DB columns
            # still pointing at old paths. Surface the failure loudly so the
            # operator can fix the DB (or reverse the migration) instead of
            # discovering missing artifacts at backtest-load time.
            logger.error(
                "DB artifact-path rewrite failed after %d rename(s): %s",
                applied,
                exc,
            )
            return 2
        print(f"applied {applied} rename(s); rewrote {rewritten} DB artifact path(s)")
        return 0

    for old, new in renames:
        print(f"WOULD RENAME {old} -> {new}")
    print(f"({len(renames)} rename(s) planned; rerun with --apply to execute)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
