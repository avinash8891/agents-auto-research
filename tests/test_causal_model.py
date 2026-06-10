from __future__ import annotations

import json

import pandas as pd
import pytest

from causal_model import (
    load_model,
    predict,
    residual_map,
    save_model,
    score_on_holdout,
)
from research_types import AccuracyPoint, CausalFactor, CausalModel


def _feature_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_id": "train-1",
                "entry_ts": pd.Timestamp("2020-01-02 14:35:00", tz="UTC"),
                "gap_pct": -1.2,
                "vol_pctile_20d": 0.8,
                "out_is_loss": True,
                "out_pnl": -4.0,
            },
            {
                "trade_id": "train-2",
                "entry_ts": pd.Timestamp("2020-02-02 14:35:00", tz="UTC"),
                "gap_pct": 0.3,
                "vol_pctile_20d": 0.2,
                "out_is_loss": False,
                "out_pnl": 1.0,
            },
            {
                "trade_id": "train-3",
                "entry_ts": pd.Timestamp("2021-01-03 14:35:00", tz="UTC"),
                "gap_pct": -0.8,
                "vol_pctile_20d": 0.7,
                "out_is_loss": True,
                "out_pnl": -5.0,
            },
            {
                "trade_id": "train-4",
                "entry_ts": pd.Timestamp("2021-02-04 14:35:00", tz="UTC"),
                "gap_pct": 0.4,
                "vol_pctile_20d": 0.2,
                "out_is_loss": False,
                "out_pnl": 2.0,
            },
            {
                "trade_id": "train-5",
                "entry_ts": pd.Timestamp("2022-03-04 14:35:00", tz="UTC"),
                "gap_pct": 0.6,
                "vol_pctile_20d": 0.4,
                "out_is_loss": False,
                "out_pnl": 3.0,
            },
            {
                "trade_id": "holdout-loss-1",
                "entry_ts": pd.Timestamp("2023-01-05 14:35:00", tz="UTC"),
                "gap_pct": -1.1,
                "vol_pctile_20d": 0.9,
                "out_is_loss": True,
                "out_pnl": -3.0,
            },
            {
                "trade_id": "holdout-win-1",
                "entry_ts": pd.Timestamp("2023-02-06 14:35:00", tz="UTC"),
                "gap_pct": 0.2,
                "vol_pctile_20d": 0.3,
                "out_is_loss": False,
                "out_pnl": 8.0,
            },
            {
                "trade_id": "holdout-loss-2",
                "entry_ts": pd.Timestamp("2023-03-01 14:35:00", tz="UTC"),
                "gap_pct": -0.7,
                "vol_pctile_20d": 0.8,
                "out_is_loss": True,
                "out_pnl": -2.0,
            },
            {
                "trade_id": "holdout-win-2",
                "entry_ts": pd.Timestamp("2023-04-01 14:35:00", tz="UTC"),
                "gap_pct": 0.6,
                "vol_pctile_20d": 0.6,
                "out_is_loss": False,
                "out_pnl": 6.0,
            },
        ]
    )


def _planted_factor() -> CausalFactor:
    return CausalFactor(
        factor_id="f001",
        story="Gap-down entries have been absorbing weak inventory.",
        rule="gap_pct < 0",
        direction="loss",
        evidence_rounds=[1],
        status="candidate",
    )


def _garbage_factor() -> CausalFactor:
    return CausalFactor(
        factor_id="f999",
        story="A non-separating volatility rule should not improve the model.",
        rule="vol_pctile_20d > 1.0",
        direction="loss",
        evidence_rounds=[1],
        status="candidate",
    )


def test_causal_factor_and_model_schema_match_spec() -> None:
    factor = _planted_factor()
    point = AccuracyPoint(
        round_number=1,
        model_version=1,
        pnl_weighted_accuracy=0.75,
        naive_accuracy=0.5,
        skill=0.25,
        holdout_trade_count=4,
    )
    model = CausalModel(
        family="ema",
        version=1,
        factors=[factor],
        accuracy_history=[point],
    )

    assert factor.created_at.endswith("+00:00")
    assert factor.lesson == ""
    assert model.factors == [factor]
    assert model.accuracy_history == [point]


def test_load_and_save_model_use_runtime_root_atomic_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTORESEARCH_RUNTIME_ROOT", str(tmp_path))
    model = CausalModel(family="ema", version=1, factors=[_planted_factor()], accuracy_history=[])

    save_model(model)

    path = tmp_path / "ema_causal_model.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["family"] == "ema"
    assert json.loads(path.read_text(encoding="utf-8"))["holdout_start"].endswith("+00:00")
    assert load_model("ema") == model.model_copy(
        update={"holdout_start": json.loads(path.read_text(encoding="utf-8"))["holdout_start"]}
    )
    assert load_model("orb") == CausalModel(
        family="orb", version=0, factors=[], accuracy_history=[]
    )


def test_predict_scores_holdout_with_pnl_weighted_skill_over_naive() -> None:
    table = _feature_table()
    planted_model = CausalModel(
        family="ema",
        version=1,
        factors=[_planted_factor()],
        accuracy_history=[],
    )
    garbage_model = CausalModel(
        family="ema",
        version=1,
        factors=[_garbage_factor()],
        accuracy_history=[],
    )

    planted_predictions = predict(planted_model, table)
    planted_score = score_on_holdout(planted_model, table)
    garbage_score = score_on_holdout(garbage_model, table)

    assert planted_predictions.index.equals(table.index)
    assert planted_predictions.name == "p_loss"
    assert planted_predictions.loc[5] > 0.5
    assert planted_predictions.loc[6] < 0.5
    assert planted_score.holdout_trade_count == 4
    assert planted_score.pnl_weighted_accuracy == pytest.approx(1.0)
    assert planted_score.naive_accuracy == pytest.approx(14.0 / 19.0)
    assert planted_score.skill == pytest.approx(5.0 / 19.0)
    assert garbage_score.skill == pytest.approx(0.0)


def test_residual_map_ranks_unexplained_pnl_before_explained_pattern_trades() -> None:
    table = _feature_table()
    model = CausalModel(family="ema", version=1, factors=[_planted_factor()], accuracy_history=[])

    residuals = residual_map(model, table)

    assert list(residuals.columns) == [
        "trade_id",
        "predicted",
        "actual",
        "abs_pnl",
        "unexplained_abs_pnl",
    ]
    assert residuals["trade_id"].tolist() == [
        "holdout-win-1",
        "holdout-win-2",
        "holdout-loss-1",
        "holdout-loss-2",
    ]
    assert (
        residuals.loc[residuals["trade_id"] == "holdout-loss-1", "unexplained_abs_pnl"].item()
        == 0.0
    )
    assert (
        residuals.loc[residuals["trade_id"] == "holdout-loss-2", "unexplained_abs_pnl"].item()
        == 0.0
    )


def test_refuted_factors_contribute_nothing_to_prediction() -> None:
    table = _feature_table()
    refuted = _planted_factor().model_copy(update={"status": "refuted"})
    refuted_model = CausalModel(family="ema", version=1, factors=[refuted], accuracy_history=[])
    empty_model = CausalModel(family="ema", version=1, factors=[], accuracy_history=[])

    pd.testing.assert_series_equal(predict(refuted_model, table), predict(empty_model, table))


def test_causal_model_rejects_rules_that_reference_outcome_columns() -> None:
    with pytest.raises(ValueError, match="out_is_loss"):
        predict(
            CausalModel(
                family="ema",
                version=1,
                factors=[
                    CausalFactor(
                        factor_id="f998",
                        story="Outcome leakage should not be allowed.",
                        rule="out_is_loss == True",
                        direction="loss",
                        evidence_rounds=[],
                        status="candidate",
                    )
                ],
                accuracy_history=[],
            ),
            _feature_table(),
        )
