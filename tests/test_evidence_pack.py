from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest_run_db import BacktestRunDB
from causal_model import save_model
from evidence_pack import Corpus, build_corpus, render_corpus
from research_types import CausalFactor, CausalModel
from screening import ScreeningResult, write_screenings


def _factor(
    factor_id: str,
    rule: str,
    *,
    status: str = "candidate",
    lesson: str = "",
) -> CausalFactor:
    return CausalFactor(
        factor_id=factor_id,
        story=f"Story for {factor_id}",
        rule=rule,
        direction="loss",
        evidence_rounds=[2],
        status=status,
        lesson=lesson,
    )


def _screening(rule: str, verdict: str) -> ScreeningResult:
    return ScreeningResult(
        rule=rule,
        verdict=verdict,
        sample_count=40,
        flagged_loss_rate=0.8,
        base_loss_rate=0.56,
        lift=0.24,
        p_value=0.004,
        overlap_with=None,
    )


def _write_feature_table(runtime_root: Path) -> None:
    path = runtime_root / "runtime/jobs/job-1/research/round-2/backtest/feature_table.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "trade_id": "train-loss",
                "entry_ts": pd.Timestamp("2020-01-02 14:35:00", tz="UTC"),
                "gap_pct": -1.0,
                "regime_label": "trend",
                "out_is_loss": True,
                "out_pnl": -2.0,
            },
            {
                "trade_id": "train-win",
                "entry_ts": pd.Timestamp("2020-02-02 15:35:00", tz="UTC"),
                "gap_pct": 0.4,
                "regime_label": "chop",
                "out_is_loss": False,
                "out_pnl": 1.0,
            },
            {
                "trade_id": "holdout-loss",
                "entry_ts": pd.Timestamp("2023-01-05 14:35:00", tz="UTC"),
                "gap_pct": -1.2,
                "regime_label": "trend",
                "out_is_loss": True,
                "out_pnl": -5.0,
            },
            {
                "trade_id": "holdout-win",
                "entry_ts": pd.Timestamp("2023-02-06 15:35:00", tz="UTC"),
                "gap_pct": 0.8,
                "regime_label": "chop",
                "out_is_loss": False,
                "out_pnl": 3.0,
            },
        ]
    ).to_parquet(path)


def _write_harvest(runtime_root: Path) -> None:
    path = runtime_root / "runtime/jobs/job-1/research/round-2/harvest_verdict.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
        {
          "round": 2,
          "change": "gap_pct < 0 admission",
          "registered_predictions": [
            {"metric": "pnl_weighted_accuracy", "predicted": 0.75, "actual": 0.52, "gap": -0.23}
          ],
          "lesson": "Gap-down rule admitted screening but failed registered prediction."
        }
        """,
        encoding="utf-8",
    )


def _setup_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    save_model(
        CausalModel(
            family="ema",
            version=3,
            factors=[
                _factor(
                    "f001",
                    "gap_pct < 0",
                    status="refuted",
                    lesson="Registered prediction missed by 23 percentage points.",
                )
            ],
            accuracy_history=[],
        )
    )
    save_model(
        CausalModel(
            family="orb",
            version=1,
            factors=[_factor("f101", "gap_pct > 0", status="supported")],
            accuracy_history=[],
        )
    )
    _write_feature_table(tmp_path)
    _write_harvest(tmp_path)
    BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    write_screenings(
        tmp_path / "ema_backtest_runs.db",
        [_screening("gap_pct < 0", "pass")],
        round_number=2,
        competitor_rule="gap_pct > 0",
    )


def test_build_corpus_reads_runtime_artifacts_for_refuted_harvest(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)

    corpus = build_corpus("ema", 2)

    assert isinstance(corpus, Corpus)
    assert corpus.family == "ema"
    assert corpus.round_number == 2
    assert corpus.model.factors[0].status == "refuted"
    assert "23 percentage points" in corpus.model.factors[0].lesson
    assert corpus.screening_history[0].rule == "gap_pct < 0"
    assert corpus.screening_history[0].verdict == "pass"
    assert corpus.harvest_verdicts[0]["registered_predictions"][0]["gap"] == -0.23
    assert corpus.residual_summary[0]["trade_id"] in {"holdout-loss", "holdout-win"}
    assert corpus.cross_family[0].factor_id == "f101"


def test_build_corpus_skips_malformed_harvest_artifacts(tmp_path: Path, monkeypatch) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    bad_path = tmp_path / "runtime/jobs/job-1/research/round-2/harvest_verdict_bad.json"
    bad_path.write_text("{not-json", encoding="utf-8")

    corpus = build_corpus("ema", 2)

    assert len(corpus.harvest_verdicts) == 1
    assert corpus.harvest_verdicts[0]["lesson"].startswith("Gap-down rule")


def test_build_corpus_skips_non_object_harvest_artifacts(tmp_path: Path, monkeypatch) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    bad_path = tmp_path / "runtime/jobs/job-1/research/round-2/harvest_verdict_list.json"
    bad_path.write_text("[]", encoding="utf-8")

    corpus = build_corpus("ema", 2)

    assert len(corpus.harvest_verdicts) == 1
    assert corpus.harvest_verdicts[0]["lesson"].startswith("Gap-down rule")


def test_build_corpus_feature_table_is_scoped_to_active_job(tmp_path: Path, monkeypatch) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    other_path = tmp_path / "runtime/jobs/job-2/research/round-2/backtest/feature_table.parquet"
    other_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "trade_id": "other-job",
                "entry_ts": pd.Timestamp("2023-01-05 14:35:00", tz="UTC"),
                "gap_pct": -9.0,
                "regime_label": "trend",
                "out_is_loss": True,
                "out_pnl": -50.0,
            }
        ]
    ).to_parquet(other_path)

    corpus = build_corpus("ema", 2, job=1)

    assert {row["trade_id"] for row in corpus.residual_summary}
    assert "other-job" not in {row["trade_id"] for row in corpus.residual_summary}


def test_build_corpus_does_not_fabricate_missing_screening_rates(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)

    corpus = build_corpus("ema", 2)

    assert pd.isna(corpus.screening_history[0].flagged_loss_rate)
    assert pd.isna(corpus.screening_history[0].base_loss_rate)


def test_render_corpus_is_deterministic_and_ordered(tmp_path: Path, monkeypatch) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    left = build_corpus("ema", 2)
    right = build_corpus("ema", 2)

    rendered = render_corpus(left)

    assert rendered == render_corpus(right)
    assert rendered.index("## Model") < rendered.index("## Residual Summary")
    assert rendered.index("## Residual Summary") < rendered.index("## Residual Stats")
    assert rendered.index("## Residual Stats") < rendered.index("## Screening History")
    assert rendered.index("## Screening History") < rendered.index("## Harvest Verdicts")
    assert rendered.index("## Harvest Verdicts") < rendered.index("## Cross Family")
    assert rendered.index("## Cross Family") < rendered.index("## Rejection Feedback")
    assert "Registered prediction missed by 23 percentage points." in rendered
    assert "gap: -0.23" in rendered
    assert "sample_count: 40" in rendered
    assert "generated_at" not in rendered
