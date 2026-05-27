"""Tests for research_memory enrichments.

Covers:
- latest_thesis_details: thesis proposal metadata lookup by thesis_id
- _experiment_index_entry: now includes hypothesis and mechanism from BacktestRunRecord
"""

from __future__ import annotations

from pathlib import Path

from backtest_run_db import BacktestRunDB, BacktestRunRecord
from persistence_utils import utc_now_iso8601
from research_memory import _experiment_index_entry, latest_thesis_details


def _add_thesis_attempt(db: BacktestRunDB, thesis_id: str, **overrides: object) -> None:
    db.add_research_thesis_attempt(
        {
            "research_round_id": "job-1-round-1",
            "attempt_number": 1,
            "thesis_id": thesis_id,
            "strategy_family": "ema",
            "config_changes": {"ema_length": 10},
            "validator_status": "compiled",
            "mechanism_dimension": "entry_timing",
            "hypothesis": "EMA crossover predicts short-term momentum",
            "mechanism": "Trend persistence after crossover is the causal driver",
            "thesis_details": {
                "expected_effects": [{"metric": "profit_factor", "direction": "increase"}],
                "evidence": ["historical EMA study 2023"],
                "evidence_strength": "proxy",
                "closest_prior_theses_considered": ["ema-baseline"],
                "orthogonality_defense": "Targets entry timing, not parameter tuning",
                "falsification_or_alternative": "If volatility regime dominates, reject",
                "why_not_overfit": "Confirmed on three separate date ranges",
                "disqualifiers": [{"condition": "trade_count < 50", "metric": "trade_count"}],
            },
            "validation_failure_reason": "",
            "selected_for_execution": 1,
            "created_at_utc": utc_now_iso8601(),
            **overrides,
        }
    )


# ── latest_thesis_details ─────────────────────────────────────────


def test_latest_thesis_details_returns_proposal_fields(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    _add_thesis_attempt(db, "ema-crossover-momentum")

    result = latest_thesis_details(tmp_path, "ema-crossover-momentum")

    assert result["hypothesis"] == "EMA crossover predicts short-term momentum"
    assert result["mechanism"] == "Trend persistence after crossover is the causal driver"
    assert result["config_changes"] == {"ema_length": 10}
    assert result["evidence_strength"] == "proxy"
    assert result["closest_prior_theses_considered"] == ["ema-baseline"]
    assert result["orthogonality_defense"] == "Targets entry timing, not parameter tuning"
    assert result["falsification_or_alternative"] == "If volatility regime dominates, reject"
    assert result["why_not_overfit"] == "Confirmed on three separate date ranges"
    assert len(result["expected_effects"]) == 1
    assert result["expected_effects"][0]["metric"] == "profit_factor"
    assert len(result["evidence"]) == 1


def test_latest_thesis_details_returns_empty_for_unknown_thesis(tmp_path: Path) -> None:
    BacktestRunDB(tmp_path / "ema_backtest_runs.db")

    result = latest_thesis_details(tmp_path, "ema-does-not-exist")

    assert result == {}


def test_latest_thesis_details_returns_empty_when_no_db_exists(tmp_path: Path) -> None:
    result = latest_thesis_details(tmp_path, "any-thesis-id")

    assert result == {}


def test_latest_thesis_details_returns_last_attempt_when_multiple(tmp_path: Path) -> None:
    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    _add_thesis_attempt(db, "ema-multi", hypothesis="First attempt", attempt_number=1)
    _add_thesis_attempt(db, "ema-multi", hypothesis="Second attempt", attempt_number=2)

    result = latest_thesis_details(tmp_path, "ema-multi")

    assert result["hypothesis"] == "Second attempt"


def test_theme_keywords_round_trip_through_persistence(tmp_path: Path) -> None:
    """`_theme_keywords_from_prior` is the source of truth for the
    dominant-cluster overlap gate. The keyword set MUST survive the
    log_research_round → SQLite → _iter_thesis_attempts round trip, or
    the gate silently fails-open in production (tests would still pass
    because they build priors by hand).

    Regression: the thesis_details whitelist in autoresearch_research.py
    used to omit theme_keywords, so production rounds persisted priors
    without keywords and the overlap gate could never fire on real data.
    """
    from research_memory import _iter_thesis_attempts
    from thesis_validator import _theme_keywords_from_prior

    db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    _add_thesis_attempt(
        db,
        "ema-overlap-source",
        thesis_details={
            "theme_keywords": ["entry_timing", "morning_session", "opening_volatility"]
        },
    )

    entries = _iter_thesis_attempts(tmp_path, thesis_id="ema-overlap-source")
    assert entries, "persisted attempt must be readable back"
    assert _theme_keywords_from_prior(entries[0]) == {
        "entry_timing",
        "morning_session",
        "opening_volatility",
    }


def test_latest_thesis_details_picks_globally_latest_across_family_dbs(tmp_path: Path) -> None:
    """When two family DBs share a thesis_id, latest_thesis_details must return
    the globally most recent attempt — not whichever DB sorts first by filename.

    Regression: _iter_thesis_attempts concatenated per-DB results in filename
    order ('ema_*' before 'orb_*'), so an older EMA attempt would shadow a newer
    ORB attempt and steer the next conductor prompt with unrelated predictions.
    """
    ema_db = BacktestRunDB(tmp_path / "ema_backtest_runs.db")
    orb_db = BacktestRunDB(tmp_path / "orb_backtest_runs.db")
    _add_thesis_attempt(
        ema_db,
        "shared-thesis-id",
        hypothesis="Older EMA attempt",
        attempt_number=1,
        created_at_utc="2026-01-01T00:00:00+00:00",
    )
    _add_thesis_attempt(
        orb_db,
        "shared-thesis-id",
        hypothesis="Newer ORB attempt",
        attempt_number=1,
        created_at_utc="2026-05-01T00:00:00+00:00",
    )

    result = latest_thesis_details(tmp_path, "shared-thesis-id")

    assert result["hypothesis"] == "Newer ORB attempt"


# ── _experiment_index_entry hypothesis/mechanism enrichment ──────


def _make_experiment_item(
    *,
    thesis_id: str = "ema-test-thesis",
    hypothesis: str = "EMA length 12 improves trend capture",
    mechanism: str = "Longer EMA filters noise and reduces false signals",
    accepted: bool = False,
) -> dict:
    record = BacktestRunRecord(
        run_id="run-abc",
        thesis_id=thesis_id,
        config_path="runtime/jobs/job-1/research/round-1/selected_config.json",
        runtime_config={"ema_length": 12},
        code_commit="abc1234",
        data_hash="datahash",
        train_metrics={"profit_factor": 1.6, "max_drawdown": -0.15},
        validation_metrics={"pct_profitable_windows": 0.62},
        trade_count=84,
        trades_file="trades.csv",
        strategy_events_file="events.csv",
        diagnostics_file="diagnostics.json",
        strategy_diagnostics={},
        accepted=accepted,
        rejection_reason="" if accepted else "metric below threshold",
        verdict_status="accepted" if accepted else "rejected",
        verdict_summary="ok" if accepted else "sharpe below baseline",
        family="ema",
        hypothesis=hypothesis,
        mechanism=mechanism,
        job=1,
    )
    return {
        "record": record,
        "primary_metric_name": "sharpe",
        "best_direction": "higher",
        "metric": 1.5,
        "job_id": 1,
        "metric_rankable": True,
        "invalid_result": False,
    }


def test_experiment_index_entry_includes_hypothesis_and_mechanism() -> None:
    item = _make_experiment_item()

    entry = _experiment_index_entry(item)

    assert entry["hypothesis"] == "EMA length 12 improves trend capture"
    assert entry["mechanism"] == "Longer EMA filters noise and reduces false signals"


def test_experiment_index_entry_truncates_long_hypothesis() -> None:
    long_hypothesis = "A" * 500
    item = _make_experiment_item(hypothesis=long_hypothesis)

    entry = _experiment_index_entry(item)

    assert len(entry["hypothesis"]) <= 303  # 300 chars + "..."


def test_experiment_index_entry_hypothesis_empty_when_not_set() -> None:
    item = _make_experiment_item(hypothesis="", mechanism="")

    entry = _experiment_index_entry(item)

    assert entry["hypothesis"] == ""
    assert entry["mechanism"] == ""
