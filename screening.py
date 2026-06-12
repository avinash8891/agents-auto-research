"""Screen candidate causal rules against the training feature table.

Rule screening uses a two-proportion z-test to compare the flagged loss rate
against the unflagged population:

    z = (p1 - p2) / sqrt(pooled * (1 - pooled) * (1/n1 + 1/n2))

The returned p-value is the two-sided normal tail probability implemented as
``erfc(abs(z) / sqrt(2))``.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Literal, Sequence

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel

from autoresearch_constants import (
    research_engine_max_p_value,
    research_engine_max_population_overlap,
    research_engine_min_abs_lift,
    research_engine_min_sample,
)
from causal_rule import RuleExpressionError, evaluate_entry_rule
from persistence_utils import utc_now_iso8601
from research_types import CausalFactor, CausalModel


class ScreeningResult(BaseModel):
    rule: str
    verdict: Literal[
        "pass",
        "kill_min_sample",
        "kill_no_lift",
        "kill_duplicate",
        "kill_bad_rule",
        "kill_lost_to_competitor",
    ]
    sample_count: int
    flagged_loss_rate: float
    base_loss_rate: float
    lift: float
    p_value: float
    overlap_with: str | None


def screen(
    rule: str,
    competitor_rule: str | None,
    model: CausalModel,
    features_train: pd.DataFrame,
    *,
    config: dict | None = None,
) -> ScreeningResult:
    proposal, _ = screen_pair(rule, competitor_rule, model, features_train, config=config)
    return proposal


def screen_pair(
    rule: str,
    competitor_rule: str | None,
    model: CausalModel,
    features_train: pd.DataFrame,
    *,
    config: dict | None = None,
) -> tuple[ScreeningResult, ScreeningResult | None]:
    """Screen the proposal and its competitor with one evaluation per rule.

    The competitor's full ScreeningResult is returned so callers can persist
    it without re-screening.
    """
    thresholds = _thresholds_for_family(model.family, config=config)
    factor_masks = _factor_masks(model.factors, features_train)
    proposal = _full_screen(rule, features_train, thresholds, factor_masks)
    competitor: ScreeningResult | None = None
    if competitor_rule:
        competitor = _full_screen(competitor_rule, features_train, thresholds, factor_masks)
        if (
            proposal.verdict == "pass"
            and competitor.verdict != "kill_bad_rule"
            and _passes_screening_thresholds(competitor, thresholds)
            and abs(proposal.lift) + 0.01 < abs(competitor.lift)
        ):
            proposal = proposal.model_copy(update={"verdict": "kill_lost_to_competitor"})
    return proposal, competitor


def _full_screen(
    rule: str,
    features_train: pd.DataFrame,
    thresholds: dict[str, float | int],
    factor_masks: Sequence[tuple[str, np.ndarray]],
) -> ScreeningResult:
    mask = _evaluate_rule(rule, features_train)
    result = _screen_rule_from_mask(rule, features_train, mask)
    if result.verdict == "kill_bad_rule":
        return result
    if result.sample_count < thresholds["min_sample"]:
        return result.model_copy(update={"verdict": "kill_min_sample"})
    assert mask is not None  # kill_bad_rule returned above when evaluation failed
    overlap_with = _overlapping_factor_id(
        mask,
        factor_masks,
        max_population_overlap=thresholds["max_population_overlap"],
    )
    if overlap_with is not None:
        return result.model_copy(update={"verdict": "kill_duplicate", "overlap_with": overlap_with})
    if not _passes_screening_thresholds(result, thresholds):
        return result.model_copy(update={"verdict": "kill_no_lift"})
    return result.model_copy(update={"verdict": "pass"})


def _passes_screening_thresholds(
    result: ScreeningResult, thresholds: dict[str, float | int]
) -> bool:
    return (
        result.sample_count >= thresholds["min_sample"]
        and abs(result.lift) >= thresholds["min_abs_lift"]
        and result.p_value <= thresholds["max_p_value"]
    )


def write_screenings(
    db_path: Path,
    results: Sequence[ScreeningResult],
    *,
    round_number: int,
    job_id: int | None = None,
    is_competitor: bool = False,
) -> None:
    created_at = utc_now_iso8601()
    with sqlite3.connect(db_path) as conn:
        _ensure_screenings_table(conn)
        for index, result in enumerate(results, start=1):
            _insert_screening_with_retry(
                conn,
                base_id=_screening_id(round_number, index, result),
                round_number=round_number,
                job_id=job_id,
                result=result,
                created_at=created_at,
                is_competitor=is_competitor,
            )
        conn.commit()


def _screen_rule_from_mask(
    rule: str,
    features_train: pd.DataFrame,
    flagged: pd.Series | None,
) -> ScreeningResult:
    base_loss_rate = _loss_rate(features_train)
    if flagged is None:
        return ScreeningResult(
            rule=rule,
            verdict="kill_bad_rule",
            sample_count=0,
            flagged_loss_rate=0.0,
            base_loss_rate=base_loss_rate,
            lift=0.0,
            p_value=1.0,
            overlap_with=None,
        )
    sample_count = int(flagged.sum())
    flagged_loss_rate = _loss_rate(features_train.loc[flagged])
    return ScreeningResult(
        rule=rule,
        verdict="pass",
        sample_count=sample_count,
        flagged_loss_rate=flagged_loss_rate,
        base_loss_rate=base_loss_rate,
        lift=flagged_loss_rate - base_loss_rate,
        p_value=_two_proportion_p_value(
            losses_a=int(features_train.loc[flagged, "out_is_loss"].astype(bool).sum()),
            count_a=sample_count,
            losses_b=int(features_train.loc[~flagged, "out_is_loss"].astype(bool).sum()),
            count_b=int((~flagged).sum()),
        ),
        overlap_with=None,
    )


def _evaluate_rule(rule: str, features_train: pd.DataFrame) -> pd.Series | None:
    try:
        return evaluate_entry_rule(rule, features_train)
    except RuleExpressionError:
        return None


def _factor_masks(
    factors: Sequence[CausalFactor],
    features_train: pd.DataFrame,
) -> list[tuple[str, np.ndarray]]:
    """Evaluate every active factor rule once; reused across overlap checks."""
    masks: list[tuple[str, np.ndarray]] = []
    for factor in factors:
        if factor.status in {"demoted", "refuted"}:
            continue
        existing = _evaluate_rule(factor.rule, features_train)
        if existing is None:
            continue
        masks.append((factor.factor_id, existing.to_numpy(dtype=bool)))
    return masks


def _overlapping_factor_id(
    candidate: pd.Series,
    factor_masks: Sequence[tuple[str, np.ndarray]],
    *,
    max_population_overlap: float,
) -> str | None:
    candidate_mask = candidate.to_numpy(dtype=bool)
    for factor_id, existing_mask in factor_masks:
        union = int((candidate_mask | existing_mask).sum())
        if union == 0:
            continue
        overlap = int((candidate_mask & existing_mask).sum()) / union
        if overlap > max_population_overlap:
            return factor_id
    return None


def _loss_rate(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return float(frame["out_is_loss"].astype(bool).mean())


def _two_proportion_p_value(
    *,
    losses_a: int,
    count_a: int,
    losses_b: int,
    count_b: int,
) -> float:
    if count_a == 0 or count_b == 0:
        return 1.0
    p_a = losses_a / count_a
    p_b = losses_b / count_b
    pooled = (losses_a + losses_b) / (count_a + count_b)
    variance = pooled * (1.0 - pooled) * ((1.0 / count_a) + (1.0 / count_b))
    if variance <= 0.0:
        return 1.0
    z_score = abs(p_a - p_b) / math.sqrt(variance)
    return float(math.erfc(z_score / math.sqrt(2.0)))


def _thresholds_for_family(family: str, *, config: dict | None = None) -> dict[str, float]:
    config = config if config is not None else _load_family_config(family)
    return {
        "min_sample": research_engine_min_sample(config),
        "min_abs_lift": research_engine_min_abs_lift(config),
        "max_p_value": research_engine_max_p_value(config),
        "max_population_overlap": research_engine_max_population_overlap(config),
    }


def _load_family_config(family: str) -> dict:
    path = Path.cwd() / "configs" / f"{family}_base.yaml"
    if not path.exists():
        raise FileNotFoundError(f"missing strategy family config for screening thresholds: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _screening_id(round_number: int, index: int, result: ScreeningResult) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "round_number": round_number,
                "index": index,
                "rule": result.rule,
                "verdict": result.verdict,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"round-{round_number}:{index}:{digest}"


def _insert_screening_with_retry(
    conn: sqlite3.Connection,
    *,
    base_id: str,
    round_number: int,
    job_id: int | None,
    result: ScreeningResult,
    created_at: str,
    is_competitor: bool = False,
) -> None:
    suffix = 1
    while True:
        screening_id = base_id if suffix == 1 else f"{base_id}:retry-{suffix}"
        try:
            conn.execute(
                """
                INSERT INTO screenings (
                    screening_id, round_number, job_id, rule, verdict,
                    sample_count, flagged_loss_rate, base_loss_rate, lift, p_value,
                    overlap_with, created_at_utc, is_competitor
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    screening_id,
                    round_number,
                    job_id,
                    result.rule,
                    result.verdict,
                    result.sample_count,
                    result.flagged_loss_rate,
                    result.base_loss_rate,
                    result.lift,
                    result.p_value,
                    result.overlap_with,
                    created_at,
                    1 if is_competitor else 0,
                ),
            )
            return
        except sqlite3.IntegrityError as exc:
            if "screening_id" not in str(exc):
                raise
            suffix += 1


def _ensure_screenings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screenings (
            screening_id TEXT PRIMARY KEY,
            round_number INTEGER NOT NULL,
            job_id INTEGER,
            rule TEXT NOT NULL,
            verdict TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            flagged_loss_rate REAL,
            base_loss_rate REAL,
            lift REAL NOT NULL,
            p_value REAL NOT NULL,
            overlap_with TEXT,
            created_at_utc TEXT NOT NULL,
            is_competitor INTEGER NOT NULL DEFAULT 0
        )
        """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(screenings)").fetchall()}
    if "job_id" not in columns:
        try:
            conn.execute("ALTER TABLE screenings ADD COLUMN job_id INTEGER")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    for column in ("flagged_loss_rate", "base_loss_rate"):
        if column not in columns:
            try:
                conn.execute(f"ALTER TABLE screenings ADD COLUMN {column} REAL")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    if "is_competitor" not in columns:
        try:
            conn.execute(
                "ALTER TABLE screenings ADD COLUMN is_competitor INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_round
        ON screenings (round_number)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_job_round
        ON screenings (job_id, round_number)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_verdict
        ON screenings (verdict)
        """)
