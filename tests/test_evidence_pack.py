from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from backtest_run_db import BacktestRunDB
from causal_model import save_model
from evidence_pack import CORPUS_MAX_RENDER_CHARS, Corpus, build_corpus, render_corpus
from feature_table import FeatureTableArtifact
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
    artifact = FeatureTableArtifact.for_round(runtime_root, 1, 2)
    artifact.write(
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
        )
    )


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


def _write_walkforward_report(
    runtime_root: Path,
    thesis_id: str,
    *,
    family: str = "ema",
    verdict: str = "graduated",
    graduated: bool = True,
) -> None:
    path = runtime_root / "walkforward" / f"{thesis_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
        {{
          "family": "{family}",
          "thesis_id": "{thesis_id}",
          "survival_pct": 0.6,
          "survival_rate": 0.75,
          "usable_windows": 4,
          "graduated": {str(graduated).lower()},
          "verdict": "{verdict}",
          "windows": [
            {{"window": 1, "directions_hold": true}},
            {{"window": 2, "directions_hold": false}}
          ]
        }}
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
        job_id=1,
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
    assert corpus.screening_history[0].flagged_loss_rate == 0.8
    assert corpus.screening_history[0].base_loss_rate == 0.56
    assert corpus.harvest_verdicts[0]["registered_predictions"][0]["gap"] == -0.23
    assert {row["trade_id"] for row in corpus.residual_summary} == {"train-loss", "train-win"}
    assert corpus.cross_family[0].factor_id == "f101"


def test_build_corpus_uses_explicit_runtime_and_code_roots(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime-root"
    code_root = tmp_path / "code-root"
    runtime_root.mkdir()
    code_root.mkdir()
    (code_root / "configs").mkdir()
    (code_root / "configs" / "ema_base.yaml").write_text(
        "validation_start: '2020-01-01'\nvalidation_end: '2024-01-01'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path / "wrong-root"))
    monkeypatch.chdir(code_root)
    save_model(
        CausalModel(
            family="ema",
            version=1,
            factors=[_factor("f001", "gap_pct < 0", status="supported")],
            accuracy_history=[],
        ),
        runtime_root=runtime_root,
        code_root=code_root,
    )
    _write_feature_table(runtime_root)
    write_screenings(
        runtime_root / "ema_backtest_runs.db",
        [_screening("gap_pct < 0", "pass")],
        round_number=2,
        job_id=1,
    )

    corpus = build_corpus(
        "ema",
        2,
        job=1,
        runtime_root=runtime_root,
        code_root=code_root,
    )

    assert corpus.model.factors[0].factor_id == "f001"
    assert {row["trade_id"] for row in corpus.residual_summary} == {"train-loss", "train-win"}
    assert [item.rule for item in corpus.screening_history] == ["gap_pct < 0"]


def test_build_corpus_keeps_harvested_cross_family_factors(tmp_path: Path, monkeypatch) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    save_model(
        CausalModel(
            family="orb",
            version=2,
            factors=[_factor("f101", "gap_pct > 0", status="harvested")],
            accuracy_history=[],
        )
    )

    corpus = build_corpus("ema", 2)

    assert [factor.factor_id for factor in corpus.cross_family] == ["f101"]


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
    other_artifact = FeatureTableArtifact.for_round(tmp_path, 2, 2)
    other_artifact.write(
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
        )
    )

    corpus = build_corpus("ema", 2, job=1)

    assert {row["trade_id"] for row in corpus.residual_summary} == {"train-loss", "train-win"}
    assert "other-job" not in {row["trade_id"] for row in corpus.residual_summary}


def test_build_corpus_harvest_verdicts_are_scoped_to_active_job(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    other_path = tmp_path / "runtime/jobs/job-2/research/round-2/harvest_verdict.json"
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.write_text(
        """
        {
          "round": 2,
          "change": "other job",
          "registered_predictions": [],
          "lesson": "Other job verdict must not enter this corpus."
        }
        """,
        encoding="utf-8",
    )

    corpus = build_corpus("ema", 2, job=1)

    assert len(corpus.harvest_verdicts) == 1
    assert corpus.harvest_verdicts[0]["lesson"].startswith("Gap-down rule")


def test_build_corpus_reads_walkforward_report_summaries(tmp_path: Path, monkeypatch) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    _write_walkforward_report(tmp_path, "ema-thesis-1", verdict="graduated", graduated=True)
    _write_walkforward_report(
        tmp_path,
        "ema-thesis-2",
        verdict="demoted",
        graduated=False,
    )
    _write_walkforward_report(tmp_path, "orb-thesis-1", family="orb")
    (tmp_path / "walkforward" / "errors.json").write_text(
        '{"errors": [{"thesis_id": "walkforward-error-thesis"}]}',
        encoding="utf-8",
    )

    corpus = build_corpus("ema", 2)
    rendered = render_corpus(corpus)

    assert [item["thesis_id"] for item in corpus.walkforward_reports] == [
        "ema-thesis-1",
        "ema-thesis-2",
    ]
    assert corpus.walkforward_reports[0]["graduated"] is True
    assert corpus.walkforward_reports[1]["verdict"] == "demoted"
    assert corpus.walkforward_reports[0]["window_count"] == 2
    assert "## Walkforward Reports" in rendered
    assert "### thesis ema-thesis-1" in rendered
    assert "survival_rate: 0.75" in rendered
    assert "orb-thesis-1" not in rendered
    assert "walkforward-error-thesis" not in rendered


def test_build_corpus_rejection_feedback_is_scoped_to_active_job(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    own_path = tmp_path / "runtime/jobs/job-1/research/round-2/rejection_feedback.txt"
    own_path.write_text("own job feedback", encoding="utf-8")
    other_path = tmp_path / "runtime/jobs/job-2/research/round-2/rejection_feedback.txt"
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.write_text("other job feedback", encoding="utf-8")

    corpus = build_corpus("ema", 2, job=1)

    assert corpus.rejection_feedback == "own job feedback"


def test_build_corpus_does_not_fabricate_legacy_missing_screening_rates(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    db_path = tmp_path / "ema_backtest_runs.db"
    db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE screenings (
                screening_id TEXT PRIMARY KEY,
                round_number INTEGER NOT NULL,
                job_id INTEGER,
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
        conn.execute(
            """
            INSERT INTO screenings (
                screening_id, round_number, job_id, rule, competitor_rule, verdict,
                sample_count, lift, p_value, overlap_with, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy",
                2,
                1,
                "gap_pct < 0",
                "gap_pct > 0",
                "pass",
                40,
                0.24,
                0.004,
                None,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()

    corpus = build_corpus("ema", 2)

    assert pd.isna(corpus.screening_history[0].flagged_loss_rate)
    assert pd.isna(corpus.screening_history[0].base_loss_rate)


def test_build_corpus_screening_history_is_scoped_to_active_job(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    write_screenings(
        tmp_path / "ema_backtest_runs.db",
        [_screening("other_job_rule", "pass")],
        round_number=2,
        job_id=2,
    )

    corpus = build_corpus("ema", 2, job=1)

    assert [item.rule for item in corpus.screening_history] == ["gap_pct < 0"]


def test_build_corpus_marks_competitor_rows_instead_of_hiding_them(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    write_screenings(
        tmp_path / "ema_backtest_runs.db",
        [_screening("gap_pct > 0", "pass")],
        round_number=2,
        job_id=1,
        is_competitor=True,
    )

    corpus = build_corpus("ema", 2, job=1)
    rendered = render_corpus(corpus)

    # Spec: competitor screenings are stored AND visible — but marked, so the
    # LLM cannot mistake a competitor pass for a proposal pass.
    by_rule = {item.rule: item for item in corpus.screening_history}
    assert set(by_rule) == {"gap_pct < 0", "gap_pct > 0"}
    assert by_rule["gap_pct > 0"].is_competitor is True
    assert by_rule["gap_pct < 0"].is_competitor is False
    proposal_block = rendered.split("### gap_pct < 0")[1].split("###")[0]
    competitor_block = rendered.split("### gap_pct > 0")[1].split("###")[0]
    assert "- role: competitor_hypothesis" in competitor_block
    assert "- role: proposal" in proposal_block


def test_build_corpus_includes_all_prior_screenings_without_round_cap(
    tmp_path: Path, monkeypatch
) -> None:
    _setup_runtime(tmp_path, monkeypatch)
    db_path = tmp_path / "ema_backtest_runs.db"
    db_path.unlink()
    BacktestRunDB(db_path)
    for round_number in range(1, 13):
        write_screenings(
            db_path,
            [
                _screening(
                    f"rule_{round_number}", "pass" if round_number % 2 == 0 else "kill_no_lift"
                )
            ],
            round_number=round_number,
            job_id=1,
        )

    corpus = build_corpus("ema", 12, job=1)
    rendered = render_corpus(corpus)

    # Spec: ALL prior screenings, both verdicts — no round-count truncation.
    assert [item.rule for item in corpus.screening_history] == [
        f"rule_{round_number}" for round_number in range(1, 13)
    ]
    assert "rule_1" in rendered
    assert "truncated" not in rendered


def test_render_corpus_truncates_oldest_screenings_only_past_max_chars() -> None:
    long_suffix = "x" * 2_000
    screenings = [
        ScreeningResult(
            rule=f"rule_{index:03d}_{long_suffix} > 0",
            verdict="kill_no_lift",
            sample_count=40,
            flagged_loss_rate=0.5,
            base_loss_rate=0.4,
            lift=0.1,
            p_value=0.5,
            overlap_with=None,
        )
        for index in range(120)  # ~240k chars of screening blocks
    ]
    corpus = Corpus(
        family="ema",
        round_number=14,
        model=CausalModel(family="ema", version=1),
        screening_history=screenings,
    )

    rendered = render_corpus(corpus)

    assert len(rendered) <= CORPUS_MAX_RENDER_CHARS
    truncated_count = 120 - rendered.count("### rule_")
    assert truncated_count > 0
    # Explicit, never-silent marker naming exactly how many were dropped.
    assert f"- [truncated {truncated_count} older screenings]" in rendered
    # Oldest-first: the newest screening survives, the oldest goes first.
    assert "rule_119" in rendered
    assert "rule_000" not in rendered


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
    assert rendered.index("## Harvest Verdicts") < rendered.index("## Walkforward Reports")
    assert rendered.index("## Walkforward Reports") < rendered.index("## Cross Family")
    assert rendered.index("## Cross Family") < rendered.index("## Rejection Feedback")
    assert "Registered prediction missed by 23 percentage points." in rendered
    assert "gap: -0.23" in rendered
    assert "sample_count: 40" in rendered
    assert "generated_at" not in rendered
