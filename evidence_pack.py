from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from autoresearch_artifacts import round_number_from_path as _round_number_from_path
from autoresearch_runtime_paths import iter_family_backtest_db_paths, resolve_runtime_root
from causal_model import CausalModelStore, residual_map
from feature_table import ENTRY_TIME_COLUMNS
from research_types import CausalFactor, CausalModel
from screening import ScreeningResult

log = logging.getLogger(__name__)


class Corpus(BaseModel):
    family: str
    round_number: int
    model: CausalModel
    residual_summary: list[dict] = Field(default_factory=list)
    residual_stats: dict = Field(default_factory=dict)
    screening_history: list[ScreeningResult] = Field(default_factory=list)
    harvest_verdicts: list[dict] = Field(default_factory=list)
    cross_family: list[CausalFactor] = Field(default_factory=list)
    rejection_feedback: str | None = None


def build_corpus(family: str, round_number: int, job: int | None = None) -> Corpus:
    code_root = Path.cwd()
    runtime_root = resolve_runtime_root(code_root)
    model = CausalModelStore(runtime_root=runtime_root, code_root=code_root).load(family)
    features = _load_round_feature_table(runtime_root, round_number, job=job)
    residual_summary = _residual_summary(model, features) if features is not None else []
    return Corpus(
        family=family,
        round_number=round_number,
        model=model,
        residual_summary=residual_summary,
        residual_stats=_residual_stats(residual_summary),
        screening_history=_load_screening_history(family, round_number, job=job),
        harvest_verdicts=_load_harvest_verdicts(runtime_root, round_number, job=job),
        cross_family=_load_cross_family_factors(runtime_root, family),
        rejection_feedback=_load_rejection_feedback(runtime_root, round_number, job=job),
    )


def render_corpus(corpus: Corpus) -> str:
    lines = [
        "## Corpus",
        f"- family: {corpus.family}",
        f"- round_number: {corpus.round_number}",
        "",
        "## Model",
        f"- family: {corpus.model.family}",
        f"- version: {corpus.model.version}",
        f"- holdout_start: {corpus.model.holdout_start or 'none'}",
    ]
    if corpus.model.factors:
        for factor in sorted(corpus.model.factors, key=lambda item: item.factor_id):
            lines.extend(_render_factor(factor))
    else:
        lines.append("- factors: none")

    lines.extend(["", "## Residual Summary"])
    if corpus.residual_summary:
        for item in corpus.residual_summary:
            lines.extend(_render_mapping_item(item, heading=str(item.get("trade_id", "trade"))))
    else:
        lines.append("- none")

    lines.extend(["", "## Residual Stats"])
    if corpus.residual_stats:
        lines.extend(_render_mapping(corpus.residual_stats))
    else:
        lines.append("- none")

    lines.extend(["", "## Screening History"])
    if corpus.screening_history:
        for screening in corpus.screening_history:
            lines.extend(
                [
                    f"### {screening.rule}",
                    f"- verdict: {screening.verdict}",
                    f"- sample_count: {screening.sample_count}",
                    f"- flagged_loss_rate: {_format_float(screening.flagged_loss_rate)}",
                    f"- base_loss_rate: {_format_float(screening.base_loss_rate)}",
                    f"- lift: {_format_float(screening.lift)}",
                    f"- p_value: {_format_float(screening.p_value)}",
                    f"- overlap_with: {screening.overlap_with or 'none'}",
                ]
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Harvest Verdicts"])
    if corpus.harvest_verdicts:
        for verdict in corpus.harvest_verdicts:
            lines.extend(
                _render_mapping_item(verdict, heading=f"round {verdict.get('round', 'unknown')}")
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Cross Family"])
    if corpus.cross_family:
        for factor in sorted(corpus.cross_family, key=lambda item: item.factor_id):
            lines.extend(_render_factor(factor))
    else:
        lines.append("- none")

    lines.extend(["", "## Rejection Feedback"])
    lines.append(corpus.rejection_feedback or "- none")
    return "\n".join(lines) + "\n"


def _load_round_feature_table(
    runtime_root: Path, round_number: int, *, job: int | None = None
) -> pd.DataFrame | None:
    if job is not None:
        round_dir = "round-0-baseline" if round_number == 0 else f"round-{round_number}"
        path = (
            runtime_root
            / "runtime"
            / "jobs"
            / f"job-{job}"
            / "research"
            / round_dir
            / "backtest"
            / "feature_table.parquet"
        )
        return pd.read_parquet(path) if path.exists() else None
    round_glob = "round-0-baseline" if round_number == 0 else f"round-{round_number}"
    candidates = sorted(
        runtime_root.glob(f"runtime/jobs/*/research/{round_glob}/backtest/feature_table.parquet")
    )
    if not candidates:
        return None
    return pd.read_parquet(candidates[-1])


def _residual_summary(model: CausalModel, features: pd.DataFrame) -> list[dict]:
    if features.empty:
        return []
    residuals = residual_map(model, features).head(20)
    feature_by_trade = features.assign(trade_id=features["trade_id"].astype(str)).set_index(
        "trade_id"
    )
    summary: list[dict] = []
    for row in residuals.to_dict("records"):
        trade_id = str(row["trade_id"])
        item: dict[str, Any] = {
            "trade_id": trade_id,
            "predicted": row["predicted"],
            "actual": row["actual"],
            "abs_pnl": float(row["abs_pnl"]),
            "unexplained_abs_pnl": float(row["unexplained_abs_pnl"]),
        }
        if trade_id in feature_by_trade.index:
            features_row = feature_by_trade.loc[trade_id]
            for column in sorted((ENTRY_TIME_COLUMNS & set(features.columns)) - {"trade_id"}):
                item[column] = _jsonable(features_row[column])
        summary.append(item)
    return summary


def _residual_stats(summary: list[dict]) -> dict:
    if not summary:
        return {}
    frame = pd.DataFrame(summary)
    stats: dict[str, Any] = {
        "count": int(len(frame)),
        "unexplained_abs_pnl_total": float(frame["unexplained_abs_pnl"].sum()),
    }
    if "entry_ts" in frame:
        entry_ts = pd.to_datetime(frame["entry_ts"], utc=True)
        stats["by_hour"] = {
            str(int(hour)): int(count)
            for hour, count in entry_ts.dt.hour.value_counts().sort_index().items()
        }
    if "regime_label" in frame:
        stats["by_regime"] = {
            str(label): int(count)
            for label, count in frame["regime_label"]
            .astype(str)
            .value_counts()
            .sort_index()
            .items()
        }
    if "gap_pct" in frame:
        stats["by_gap"] = {
            "gap_down": int((frame["gap_pct"].astype(float) < 0).sum()),
            "gap_up": int((frame["gap_pct"].astype(float) > 0).sum()),
            "flat": int((frame["gap_pct"].astype(float) == 0).sum()),
        }
    return stats


def _load_screening_history(
    family: str, round_number: int, *, job: int | None = None
) -> list[ScreeningResult]:
    results: list[ScreeningResult] = []
    for db_path in iter_family_backtest_db_paths(Path.cwd(), family=family):
        try:
            with sqlite3.connect(db_path) as conn:
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(screenings)").fetchall()
                }
                if job is not None and "job_id" not in columns:
                    continue
                where = "round_number <= ?"
                params: list[int] = [round_number]
                if job is not None:
                    where += " AND job_id = ?"
                    params.append(job)
                rows = conn.execute(
                    f"""
                    SELECT rule, verdict, sample_count, lift, p_value, overlap_with
                    FROM screenings
                    WHERE {where}
                    ORDER BY round_number, created_at_utc, screening_id
                    """,
                    params,
                ).fetchall()
        except sqlite3.Error:
            continue
        for rule, verdict, sample_count, lift, p_value, overlap_with in rows:
            results.append(
                ScreeningResult(
                    rule=rule,
                    verdict=verdict,
                    sample_count=int(sample_count),
                    flagged_loss_rate=float("nan"),
                    base_loss_rate=float("nan"),
                    lift=float(lift),
                    p_value=float(p_value),
                    overlap_with=overlap_with,
                )
            )
    return results


def _load_harvest_verdicts(
    runtime_root: Path, round_number: int, *, job: int | None = None
) -> list[dict]:
    verdicts: list[dict] = []
    job_glob = f"job-{job}" if job is not None else "*"
    for path in sorted(
        runtime_root.glob(f"runtime/jobs/{job_glob}/research/round-*/harvest_verdict*.json")
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("harvest verdict payload must be a JSON object")
            payload_round = int(payload.get("round") or _round_number_from_path(path) or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("skipping unreadable harvest verdict artifact %s: %s", path, exc)
            continue
        if payload_round <= round_number:
            payload["round"] = payload_round
            verdicts.append(payload)
    return sorted(
        verdicts, key=lambda item: (int(item.get("round") or 0), json.dumps(item, sort_keys=True))
    )


def _load_cross_family_factors(runtime_root: Path, family: str) -> list[CausalFactor]:
    factors: list[CausalFactor] = []
    for path in sorted(runtime_root.glob("*_causal_model.json")):
        other_family = path.name.removesuffix("_causal_model.json")
        if other_family == family:
            continue
        model = CausalModel.model_validate(json.loads(path.read_text(encoding="utf-8")))
        factors.extend(
            factor
            for factor in model.factors
            if factor.status in {"supported", "harvested", "refuted"}
        )
    return sorted(factors, key=lambda item: item.factor_id)


def _load_rejection_feedback(
    runtime_root: Path, round_number: int, *, job: int | None = None
) -> str | None:
    job_glob = f"job-{job}" if job is not None else "*"
    candidates = sorted(
        runtime_root.glob(
            f"runtime/jobs/{job_glob}/research/round-{round_number}/rejection_feedback.txt"
        )
    )
    if not candidates:
        return None
    feedback = candidates[-1].read_text(encoding="utf-8").strip()
    return feedback or None


def _render_factor(factor: CausalFactor) -> list[str]:
    return [
        f"### {factor.factor_id}",
        f"- status: {factor.status}",
        f"- direction: {factor.direction}",
        f"- rule: {factor.rule}",
        f"- evidence_rounds: {','.join(str(round_no) for round_no in factor.evidence_rounds)}",
        f"- lesson: {factor.lesson or 'none'}",
        f"- story: {factor.story}",
    ]


def _render_mapping_item(item: dict, *, heading: str) -> list[str]:
    return [f"### {heading}", *_render_mapping(item)]


def _render_mapping(item: dict, *, prefix: str = "") -> list[str]:
    lines: list[str] = []
    for key in sorted(item):
        value = item[key]
        rendered_key = f"{prefix}{key}"
        if isinstance(value, dict):
            lines.append(f"- {rendered_key}:")
            lines.extend(_render_mapping(value, prefix="  "))
        elif isinstance(value, list):
            lines.append(f"- {rendered_key}:")
            for entry in value:
                if isinstance(entry, dict):
                    lines.extend(_render_mapping(entry, prefix="  "))
                else:
                    lines.append(f"  - {_format_value(entry)}")
        else:
            lines.append(f"- {rendered_key}: {_format_value(value)}")
    return lines


def _jsonable(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            pass
    return value


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return _format_float(value)
    return str(value)


def _format_float(value: float) -> str:
    return f"{float(value):.6g}"
