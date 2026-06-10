from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from autoresearch_constants import (
    research_engine_screening_min_lift,
    research_engine_screening_min_support,
)
from feature_table import ENTRY_TIME_COLUMNS, OUTCOME_COLUMNS
from persistence_utils import utc_now_iso8601
from research_types import CausalFactor

_QUERY_KEYWORDS = frozenset({"and", "or", "not", "in", "True", "False", "None", "is", "abs"})
_QUERY_NAME_RE = re.compile(r"`([^`]+)`|\b[A-Za-z_]\w*\b")


@dataclass(frozen=True)
class ScreeningResult:
    factor_id: str
    rule: str
    direction: str
    support: int
    total_trades: int
    flagged_loss_rate: float
    unflagged_loss_rate: float
    flagged_pnl_mean: float
    unflagged_pnl_mean: float
    verdict: str
    competing_hypothesis: str


def screen_factors(
    feature_table: pd.DataFrame,
    factors: Sequence[CausalFactor],
    *,
    research_engine_config: dict,
) -> list[ScreeningResult]:
    min_support = research_engine_screening_min_support({"research_engine": research_engine_config})
    min_lift = research_engine_screening_min_lift({"research_engine": research_engine_config})
    return [
        _screen_one(feature_table, factor, min_support=min_support, min_lift=min_lift)
        for factor in factors
    ]


def write_screenings(
    db_path: Path,
    results: Sequence[ScreeningResult],
    *,
    family: str,
    research_round_id: str,
    thresholds: dict,
) -> None:
    thresholds_json = json.dumps(thresholds, sort_keys=True, separators=(",", ":"))
    created_at = utc_now_iso8601()
    with sqlite3.connect(db_path) as conn:
        _ensure_screenings_table(conn)
        for result in results:
            screening_id = f"{research_round_id}:{result.factor_id}"
            conn.execute(
                """
                INSERT OR REPLACE INTO screenings (
                    screening_id, created_at_utc, family, research_round_id, factor_id,
                    rule, direction, support, total_trades, flagged_loss_rate,
                    unflagged_loss_rate, flagged_pnl_mean, unflagged_pnl_mean,
                    verdict, competing_hypothesis, thresholds_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    screening_id,
                    created_at,
                    family,
                    research_round_id,
                    result.factor_id,
                    result.rule,
                    result.direction,
                    result.support,
                    result.total_trades,
                    result.flagged_loss_rate,
                    result.unflagged_loss_rate,
                    result.flagged_pnl_mean,
                    result.unflagged_pnl_mean,
                    result.verdict,
                    result.competing_hypothesis,
                    thresholds_json,
                ),
            )
        conn.commit()


def _screen_one(
    feature_table: pd.DataFrame,
    factor: CausalFactor,
    *,
    min_support: int,
    min_lift: float,
) -> ScreeningResult:
    _validate_rule_references(factor.rule)
    try:
        matching = feature_table.query(factor.rule)
    except Exception as exc:
        raise ValueError(f"factor {factor.factor_id} rule failed: {factor.rule}") from exc
    flagged = pd.Series(feature_table.index.isin(matching.index), index=feature_table.index)
    support = int(flagged.sum())
    total = int(len(feature_table))
    flagged_rows = feature_table.loc[flagged]
    unflagged_rows = feature_table.loc[~flagged]
    flagged_loss_rate = _loss_rate(flagged_rows)
    unflagged_loss_rate = _loss_rate(unflagged_rows)
    flagged_pnl_mean = _pnl_mean(flagged_rows)
    unflagged_pnl_mean = _pnl_mean(unflagged_rows)
    verdict, competing = _verdict(
        direction=factor.direction,
        support=support,
        flagged_loss_rate=flagged_loss_rate,
        unflagged_loss_rate=unflagged_loss_rate,
        min_support=min_support,
        min_lift=min_lift,
    )
    return ScreeningResult(
        factor_id=factor.factor_id,
        rule=factor.rule,
        direction=factor.direction,
        support=support,
        total_trades=total,
        flagged_loss_rate=flagged_loss_rate,
        unflagged_loss_rate=unflagged_loss_rate,
        flagged_pnl_mean=flagged_pnl_mean,
        unflagged_pnl_mean=unflagged_pnl_mean,
        verdict=verdict,
        competing_hypothesis=competing,
    )


def _verdict(
    *,
    direction: str,
    support: int,
    flagged_loss_rate: float,
    unflagged_loss_rate: float,
    min_support: int,
    min_lift: float,
) -> tuple[str, str]:
    if support < min_support:
        return "underpowered", ""
    loss_lift = flagged_loss_rate - unflagged_loss_rate
    win_lift = unflagged_loss_rate - flagged_loss_rate
    if direction == "loss":
        if loss_lift >= min_lift:
            return "supported", ""
        if win_lift >= min_lift:
            return "refuted", "rule flags wins, not losses"
    else:
        if win_lift >= min_lift:
            return "supported", ""
        if loss_lift >= min_lift:
            return "refuted", "rule flags losses, not wins"
    return "inconclusive", ""


def _loss_rate(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    return float(frame["out_is_loss"].astype(bool).mean())


def _pnl_mean(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    value = frame["out_pnl"].astype(float).mean()
    return float(value) if np.isfinite(value) else 0.0


def _validate_rule_references(rule: str) -> None:
    for match in _QUERY_NAME_RE.finditer(rule):
        name = match.group(1) or match.group(0)
        if name in _QUERY_KEYWORDS:
            continue
        if name in OUTCOME_COLUMNS:
            raise ValueError(f"screening rule references outcome column: {name}")
        if name not in ENTRY_TIME_COLUMNS:
            raise ValueError(f"screening rule references unknown entry column: {name}")


def _ensure_screenings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screenings (
            screening_id TEXT PRIMARY KEY,
            created_at_utc TEXT NOT NULL,
            family TEXT NOT NULL,
            research_round_id TEXT NOT NULL,
            factor_id TEXT NOT NULL,
            rule TEXT NOT NULL,
            direction TEXT NOT NULL,
            support INTEGER NOT NULL,
            total_trades INTEGER NOT NULL,
            flagged_loss_rate REAL NOT NULL,
            unflagged_loss_rate REAL NOT NULL,
            flagged_pnl_mean REAL NOT NULL,
            unflagged_pnl_mean REAL NOT NULL,
            verdict TEXT NOT NULL,
            competing_hypothesis TEXT NOT NULL,
            thresholds_json TEXT NOT NULL
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_round_factor
        ON screenings (research_round_id, factor_id)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screenings_verdict
        ON screenings (verdict)
        """)
