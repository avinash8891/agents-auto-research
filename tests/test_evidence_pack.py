from __future__ import annotations

from evidence_pack import Corpus, build_corpus, render_corpus
from research_types import CausalFactor
from screening import ScreeningResult


def _factor(factor_id: str, rule: str) -> CausalFactor:
    return CausalFactor(
        factor_id=factor_id,
        story=f"Story for {factor_id}",
        rule=rule,
        direction="loss",
        evidence_rounds=[2],
        status="candidate",
    )


def _screening(factor_id: str, verdict: str) -> ScreeningResult:
    return ScreeningResult(
        factor_id=factor_id,
        rule="gap_pct < 0",
        direction="loss",
        support=3,
        total_trades=5,
        flagged_loss_rate=0.667,
        unflagged_loss_rate=0.0,
        flagged_pnl_mean=-1.667,
        unflagged_pnl_mean=2.5,
        verdict=verdict,
        competing_hypothesis="",
    )


def test_corpus_model_captures_round_evidence_snapshot() -> None:
    corpus = build_corpus(
        family="ema",
        research_round_id="job-1-round-2",
        feature_table_path="runtime/jobs/job-1/research/round-2/backtest/feature_table.parquet",
        factors=[_factor("f001", "gap_pct < 0")],
        screenings=[_screening("f001", "supported")],
        model_accuracy=0.75,
        residual_map={"t2": -0.2, "t1": 0.1},
    )

    assert isinstance(corpus, Corpus)
    assert corpus.family == "ema"
    assert corpus.feature_table_path.endswith("feature_table.parquet")
    assert corpus.factors[0].factor_id == "f001"
    assert corpus.screenings[0].verdict == "supported"


def test_render_corpus_is_deterministic_and_sorted() -> None:
    left = build_corpus(
        family="ema",
        research_round_id="job-1-round-2",
        feature_table_path="feature_table.parquet",
        factors=[_factor("f002", "vol_pctile_20d > 0.7"), _factor("f001", "gap_pct < 0")],
        screenings=[_screening("f002", "refuted"), _screening("f001", "supported")],
        model_accuracy=0.75,
        residual_map={"t2": -0.2, "t1": 0.1},
    )
    right = build_corpus(
        family="ema",
        research_round_id="job-1-round-2",
        feature_table_path="feature_table.parquet",
        factors=[_factor("f001", "gap_pct < 0"), _factor("f002", "vol_pctile_20d > 0.7")],
        screenings=[_screening("f001", "supported"), _screening("f002", "refuted")],
        model_accuracy=0.75,
        residual_map={"t1": 0.1, "t2": -0.2},
    )

    rendered = render_corpus(left)

    assert rendered == render_corpus(right)
    assert rendered.index("f001") < rendered.index("f002")
    assert "## Corpus" in rendered
    assert "## Causal Factors" in rendered
    assert "## Screenings" in rendered
    assert "## Residuals" in rendered
    assert "generated_at" not in rendered
