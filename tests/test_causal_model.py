from __future__ import annotations

import pandas as pd
import pytest

from causal_model import fit_causal_model, holdout_mask
from research_types import CausalFactor


def _feature_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_id": "t1",
                "entry_ts": pd.Timestamp("2024-01-01 14:35:00", tz="UTC"),
                "gap_pct": -1.2,
                "vol_pctile_20d": 0.8,
                "out_is_loss": True,
                "out_pnl": -4.0,
            },
            {
                "trade_id": "t2",
                "entry_ts": pd.Timestamp("2024-01-02 14:35:00", tz="UTC"),
                "gap_pct": 0.3,
                "vol_pctile_20d": 0.2,
                "out_is_loss": False,
                "out_pnl": 1.0,
            },
            {
                "trade_id": "t3",
                "entry_ts": pd.Timestamp("2024-01-03 14:35:00", tz="UTC"),
                "gap_pct": -0.8,
                "vol_pctile_20d": 0.7,
                "out_is_loss": True,
                "out_pnl": -5.0,
            },
            {
                "trade_id": "t4",
                "entry_ts": pd.Timestamp("2024-01-04 14:35:00", tz="UTC"),
                "gap_pct": 0.4,
                "vol_pctile_20d": 0.2,
                "out_is_loss": False,
                "out_pnl": 2.0,
            },
            {
                "trade_id": "t5",
                "entry_ts": pd.Timestamp("2024-01-05 14:35:00", tz="UTC"),
                "gap_pct": -1.1,
                "vol_pctile_20d": 0.9,
                "out_is_loss": True,
                "out_pnl": -3.0,
            },
            {
                "trade_id": "t6",
                "entry_ts": pd.Timestamp("2024-01-06 14:35:00", tz="UTC"),
                "gap_pct": 0.2,
                "vol_pctile_20d": 0.3,
                "out_is_loss": False,
                "out_pnl": 8.0,
            },
        ]
    )


def _factors() -> list[CausalFactor]:
    return [
        CausalFactor(
            factor_id="f001",
            story="Gap-down entries have been absorbing weak inventory.",
            rule="gap_pct < 0",
            direction="loss",
            evidence_rounds=[1],
            status="candidate",
        ),
        CausalFactor(
            factor_id="f002",
            story="Quiet volatility is where the entry has worked.",
            rule="vol_pctile_20d < 0.5",
            direction="win",
            evidence_rounds=[1],
            status="candidate",
        ),
    ]


def test_causal_factor_schema_accepts_spec_fields() -> None:
    factor = _factors()[0]

    assert factor.factor_id == "f001"
    assert factor.direction == "loss"
    assert factor.status == "candidate"


def test_holdout_mask_uses_final_quarter_of_validation_date_range_not_row_count() -> None:
    frame = pd.DataFrame(
        {
            "entry_ts": pd.to_datetime(
                [
                    "2024-01-01 14:30:00+00:00",
                    "2024-01-01 15:30:00+00:00",
                    "2024-01-02 14:30:00+00:00",
                    "2024-01-03 14:30:00+00:00",
                    "2024-01-04 14:30:00+00:00",
                ],
                utc=True,
            )
        }
    )

    mask = holdout_mask(frame)

    assert mask.tolist() == [False, False, False, False, True]


def test_fit_causal_model_scores_holdout_with_pnl_weighting_and_residuals() -> None:
    table = _feature_table()

    model = fit_causal_model(table, _factors())
    predictions = model.predict_proba(table)

    assert list(predictions.columns) == ["trade_id", "p_loss", "p_win", "predicted_direction"]
    assert model.holdout_trade_ids == ["t5", "t6"]
    assert model.pnl_weighted_accuracy == pytest.approx(1.0)
    assert set(model.residual_map) == {"t5", "t6"}
    assert model.residual_map["t5"] == pytest.approx(1.0 - predictions.loc[4, "p_loss"])
    assert model.residual_map["t6"] == pytest.approx(0.0 - predictions.loc[5, "p_loss"])
    assert predictions.loc[4, "predicted_direction"] == "loss"
    assert predictions.loc[5, "predicted_direction"] == "win"
    assert (
        model.factor_likelihoods["f001"]["p_flag_given_loss"]
        > model.factor_likelihoods["f001"]["p_flag_given_win"]
    )


def test_fit_causal_model_rejects_rules_that_reference_outcome_columns() -> None:
    with pytest.raises(ValueError, match="out_is_loss"):
        fit_causal_model(
            _feature_table(),
            [
                CausalFactor(
                    factor_id="f999",
                    story="Outcome leakage should not be allowed.",
                    rule="out_is_loss == True",
                    direction="loss",
                    evidence_rounds=[],
                    status="candidate",
                )
            ],
        )
