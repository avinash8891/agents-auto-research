from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Literal, Sequence

import pandas as pd
import yaml
from pydantic import BaseModel

from autoresearch_constants import (
    research_engine_max_p_value,
    research_engine_max_population_overlap,
    research_engine_min_abs_lift,
    research_engine_min_sample,
)
from feature_table import ENTRY_TIME_COLUMNS, OUTCOME_COLUMNS
from persistence_utils import utc_now_iso8601
from research_types import CausalFactor, CausalModel

_QUERY_KEYWORDS = frozenset({"and", "or", "not", "in", "True", "False", "None", "is", "abs"})
_QUERY_NAME_RE = re.compile(r"`([^`]+)`|\b[A-Za-z_]\w*\b")
_MAX_RULE_CHARS = 500


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
) -> ScreeningResult:
    thresholds = _thresholds_for_family(model.family)
    proposal = _screen_rule(rule, features_train)
    if proposal.verdict == "kill_bad_rule":
        return proposal
    overlap_with = _overlapping_factor_id(
        rule,
        model.factors,
        features_train,
        max_population_overlap=thresholds["max_population_overlap"],
    )
    if overlap_with is not None:
        return proposal.model_copy(
            update={"verdict": "kill_duplicate", "overlap_with": overlap_with}
        )
    if proposal.sample_count < thresholds["min_sample"]:
        return proposal.model_copy(update={"verdict": "kill_min_sample"})
    if (
        abs(proposal.lift) < thresholds["min_abs_lift"]
        or proposal.p_value > thresholds["max_p_value"]
    ):
        return proposal.model_copy(update={"verdict": "kill_no_lift"})
    if competitor_rule:
        competitor = _screen_rule(competitor_rule, features_train)
        if competitor.verdict != "kill_bad_rule" and abs(proposal.lift) + 0.01 < abs(
            competitor.lift
        ):
            return proposal.model_copy(update={"verdict": "kill_lost_to_competitor"})
    return proposal.model_copy(update={"verdict": "pass"})


def write_screenings(
    db_path: Path,
    results: Sequence[ScreeningResult],
    *,
    round_number: int,
    competitor_rule: str | None = None,
) -> None:
    created_at = utc_now_iso8601()
    with sqlite3.connect(db_path) as conn:
        _ensure_screenings_table(conn)
        for index, result in enumerate(results, start=1):
            conn.execute(
                """
                INSERT INTO screenings (
                    screening_id, round_number, rule, competitor_rule, verdict,
                    sample_count, lift, p_value, overlap_with, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _screening_id(round_number, index, result),
                    round_number,
                    result.rule,
                    competitor_rule,
                    result.verdict,
                    result.sample_count,
                    result.lift,
                    result.p_value,
                    result.overlap_with,
                    created_at,
                ),
            )
        conn.commit()


def _screen_rule(rule: str, features_train: pd.DataFrame) -> ScreeningResult:
    flagged = _evaluate_rule(rule, features_train)
    if flagged is None:
        return ScreeningResult(
            rule=rule,
            verdict="kill_bad_rule",
            sample_count=0,
            flagged_loss_rate=0.0,
            base_loss_rate=_loss_rate(features_train),
            lift=0.0,
            p_value=1.0,
            overlap_with=None,
        )
    sample_count = int(flagged.sum())
    flagged_loss_rate = _loss_rate(features_train.loc[flagged])
    base_loss_rate = _loss_rate(features_train)
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
    if not rule.strip() or len(rule) > _MAX_RULE_CHARS:
        return None
    try:
        _validate_rule_references(rule)
        matching = features_train.query(rule)
    except Exception:
        return None
    return pd.Series(features_train.index.isin(matching.index), index=features_train.index)


def _overlapping_factor_id(
    rule: str,
    factors: Sequence[CausalFactor],
    features_train: pd.DataFrame,
    *,
    max_population_overlap: float,
) -> str | None:
    candidate = _evaluate_rule(rule, features_train)
    if candidate is None:
        return None
    candidate_ids = _flagged_trade_ids(features_train, candidate)
    for factor in factors:
        if factor.status == "refuted":
            continue
        existing = _evaluate_rule(factor.rule, features_train)
        if existing is None:
            continue
        overlap = _jaccard(candidate_ids, _flagged_trade_ids(features_train, existing))
        if overlap > max_population_overlap:
            return factor.factor_id
    return None


def _flagged_trade_ids(features_train: pd.DataFrame, flagged: pd.Series) -> set[str]:
    return set(features_train.loc[flagged, "trade_id"].astype(str))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


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


def _validate_rule_references(rule: str) -> None:
    for match in _QUERY_NAME_RE.finditer(rule):
        name = match.group(1) or match.group(0)
        if name in _QUERY_KEYWORDS:
            continue
        if name in OUTCOME_COLUMNS:
            raise ValueError(f"screening rule references outcome column: {name}")
        if name not in ENTRY_TIME_COLUMNS:
            raise ValueError(f"screening rule references unknown entry column: {name}")


def _thresholds_for_family(family: str) -> dict[str, float]:
    config = _load_family_config(family)
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


def _ensure_screenings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screenings (
            screening_id TEXT PRIMARY KEY,
            round_number INTEGER NOT NULL,
            rule TEXT NOT NULL,
            competitor_rule TEXT,
            verdict TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            lift REAL NOT NULL,
            p_value REAL NOT NULL,
            overlap_with TEXT,
            created_at_utc TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_round
        ON screenings (round_number)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_verdict
        ON screenings (verdict)
        """)
