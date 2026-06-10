from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, Field

from research_types import CausalFactor
from screening import ScreeningResult


class ScreeningEvidence(BaseModel):
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
    competing_hypothesis: str = ""


class Corpus(BaseModel):
    family: str
    research_round_id: str
    feature_table_path: str
    model_accuracy: float | None = None
    factors: list[CausalFactor] = Field(default_factory=list)
    screenings: list[ScreeningEvidence] = Field(default_factory=list)
    residual_map: dict[str, float] = Field(default_factory=dict)


def build_corpus(
    *,
    family: str,
    research_round_id: str,
    feature_table_path: str,
    factors: Sequence[CausalFactor],
    screenings: Sequence[ScreeningResult | ScreeningEvidence],
    model_accuracy: float | None,
    residual_map: dict[str, float],
) -> Corpus:
    return Corpus(
        family=family,
        research_round_id=research_round_id,
        feature_table_path=feature_table_path,
        model_accuracy=model_accuracy,
        factors=sorted(factors, key=lambda factor: factor.factor_id),
        screenings=sorted(
            [_coerce_screening(screening) for screening in screenings],
            key=lambda screening: screening.factor_id,
        ),
        residual_map={key: residual_map[key] for key in sorted(residual_map)},
    )


def render_corpus(corpus: Corpus) -> str:
    lines = [
        "## Corpus",
        f"- family: {corpus.family}",
        f"- research_round_id: {corpus.research_round_id}",
        f"- feature_table_path: {corpus.feature_table_path}",
        f"- model_accuracy: {_format_optional_float(corpus.model_accuracy)}",
        "",
        "## Causal Factors",
    ]
    if corpus.factors:
        for factor in sorted(corpus.factors, key=lambda item: item.factor_id):
            lines.extend(
                [
                    f"### {factor.factor_id}",
                    f"- status: {factor.status}",
                    f"- direction: {factor.direction}",
                    f"- rule: {factor.rule}",
                    f"- evidence_rounds: {','.join(str(round_no) for round_no in factor.evidence_rounds)}",
                    f"- story: {factor.story}",
                ]
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Screenings"])
    if corpus.screenings:
        for screening in sorted(corpus.screenings, key=lambda item: item.factor_id):
            lines.extend(
                [
                    f"### {screening.factor_id}",
                    f"- verdict: {screening.verdict}",
                    f"- direction: {screening.direction}",
                    f"- support: {screening.support}/{screening.total_trades}",
                    f"- flagged_loss_rate: {_format_float(screening.flagged_loss_rate)}",
                    f"- unflagged_loss_rate: {_format_float(screening.unflagged_loss_rate)}",
                    f"- flagged_pnl_mean: {_format_float(screening.flagged_pnl_mean)}",
                    f"- unflagged_pnl_mean: {_format_float(screening.unflagged_pnl_mean)}",
                    f"- competing_hypothesis: {screening.competing_hypothesis or 'none'}",
                    f"- rule: {screening.rule}",
                ]
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Residuals"])
    if corpus.residual_map:
        for trade_id in sorted(corpus.residual_map):
            lines.append(f"- {trade_id}: {_format_float(corpus.residual_map[trade_id])}")
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _coerce_screening(screening: ScreeningResult | ScreeningEvidence) -> ScreeningEvidence:
    if isinstance(screening, ScreeningEvidence):
        return screening
    return ScreeningEvidence(
        factor_id=screening.factor_id,
        rule=screening.rule,
        direction=screening.direction,
        support=screening.support,
        total_trades=screening.total_trades,
        flagged_loss_rate=screening.flagged_loss_rate,
        unflagged_loss_rate=screening.unflagged_loss_rate,
        flagged_pnl_mean=screening.flagged_pnl_mean,
        unflagged_pnl_mean=screening.unflagged_pnl_mean,
        verdict=screening.verdict,
        competing_hypothesis=screening.competing_hypothesis,
    )


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return "none"
    return _format_float(value)


def _format_float(value: float) -> str:
    return f"{float(value):.6g}"
