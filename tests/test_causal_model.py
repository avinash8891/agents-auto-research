from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pandas as pd
import pytest

from causal_model import (
    CausalModelStore,
    holdout_mask,
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
                "symbol": "AAA",
                "entry_ts": pd.Timestamp("2020-01-02 14:35:00", tz="UTC"),
                "gap_pct": -1.2,
                "vol_pctile_20d": 0.8,
                "out_is_loss": True,
                "out_pnl": -4.0,
            },
            {
                "trade_id": "train-2",
                "symbol": "BBB",
                "entry_ts": pd.Timestamp("2020-02-02 14:35:00", tz="UTC"),
                "gap_pct": 0.3,
                "vol_pctile_20d": 0.2,
                "out_is_loss": False,
                "out_pnl": 1.0,
            },
            {
                "trade_id": "train-3",
                "symbol": "AAA",
                "entry_ts": pd.Timestamp("2021-01-03 14:35:00", tz="UTC"),
                "gap_pct": -0.8,
                "vol_pctile_20d": 0.7,
                "out_is_loss": True,
                "out_pnl": -5.0,
            },
            {
                "trade_id": "train-4",
                "symbol": "BBB",
                "entry_ts": pd.Timestamp("2021-02-04 14:35:00", tz="UTC"),
                "gap_pct": 0.4,
                "vol_pctile_20d": 0.2,
                "out_is_loss": False,
                "out_pnl": 2.0,
            },
            {
                "trade_id": "train-5",
                "symbol": "BBB",
                "entry_ts": pd.Timestamp("2022-03-04 14:35:00", tz="UTC"),
                "gap_pct": 0.6,
                "vol_pctile_20d": 0.4,
                "out_is_loss": False,
                "out_pnl": 3.0,
            },
            {
                "trade_id": "holdout-loss-1",
                "symbol": "AAA",
                "entry_ts": pd.Timestamp("2023-01-05 14:35:00", tz="UTC"),
                "gap_pct": -1.1,
                "vol_pctile_20d": 0.9,
                "out_is_loss": True,
                "out_pnl": -3.0,
            },
            {
                "trade_id": "holdout-win-1",
                "symbol": "BBB",
                "entry_ts": pd.Timestamp("2023-02-06 14:35:00", tz="UTC"),
                "gap_pct": 0.2,
                "vol_pctile_20d": 0.3,
                "out_is_loss": False,
                "out_pnl": 8.0,
            },
            {
                "trade_id": "holdout-loss-2",
                "symbol": "AAA",
                "entry_ts": pd.Timestamp("2023-03-01 14:35:00", tz="UTC"),
                "gap_pct": -0.7,
                "vol_pctile_20d": 0.8,
                "out_is_loss": True,
                "out_pnl": -2.0,
            },
            {
                "trade_id": "holdout-win-2",
                "symbol": "BBB",
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


def _large_feature_table(row_count: int = 20_000, factor_count: int = 16) -> pd.DataFrame:
    rows = {
        "trade_id": [f"trade-{index}" for index in range(row_count)],
        "entry_ts": pd.date_range("2020-01-01", periods=row_count, freq="min", tz="UTC"),
        "out_is_loss": [(index % 3) == 0 for index in range(row_count)],
        "out_pnl": [float((index % 11) - 5 or 1) for index in range(row_count)],
    }
    for factor_index in range(factor_count):
        rows[f"x{factor_index}"] = [(index + factor_index) % 17 for index in range(row_count)]
    return pd.DataFrame(rows)


def _large_factors(factor_count: int = 16) -> list[CausalFactor]:
    return [
        CausalFactor(
            factor_id=f"f{factor_index}",
            story="Synthetic factor for prediction vectorization coverage.",
            rule=f"x{factor_index} < {factor_index % 17}",
            direction="loss",
            evidence_rounds=[1],
            status="candidate",
        )
        for factor_index in range(factor_count)
    ]


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
    fresh_orb = load_model("orb")
    assert fresh_orb == CausalModel(
        family="orb",
        version=0,
        factors=[],
        accuracy_history=[],
        holdout_start=fresh_orb.holdout_start,
    )
    assert fresh_orb.holdout_start.endswith("+00:00")


def test_causal_model_store_uses_explicit_runtime_and_code_roots_without_env_or_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime"
    unrelated_cwd = tmp_path / "cwd"
    for path in (code_root / "configs", runtime_root, unrelated_cwd):
        path.mkdir(parents=True)
    (code_root / "configs" / "ema_base.yaml").write_text(
        "validation_start: 2020-01-01\nvalidation_end: 2024-01-01\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AUTORESEARCH_RUNTIME_ROOT", raising=False)
    monkeypatch.chdir(unrelated_cwd)
    model = CausalModel(family="ema", version=1, factors=[_planted_factor()], accuracy_history=[])

    store = CausalModelStore(runtime_root=runtime_root, code_root=code_root)
    store.save(model)

    path = runtime_root / "ema_causal_model.json"
    assert path.exists()
    assert not (unrelated_cwd / "ema_causal_model.json").exists()
    assert store.load("ema") == model.model_copy(
        update={"holdout_start": json.loads(path.read_text(encoding="utf-8"))["holdout_start"]}
    )


def test_causal_model_store_seeds_fresh_model_holdout_from_code_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code_root = tmp_path / "code"
    runtime_root = tmp_path / "runtime"
    unrelated_cwd = tmp_path / "cwd"
    for path in (code_root / "configs", runtime_root, unrelated_cwd):
        path.mkdir(parents=True)
    (code_root / "configs" / "ema_base.yaml").write_text(
        "validation_start: 2020-01-01\nvalidation_end: 2024-01-01\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(unrelated_cwd)

    model = CausalModelStore(runtime_root=runtime_root, code_root=code_root).load("ema")

    assert model == CausalModel(
        family="ema",
        version=0,
        factors=[],
        accuracy_history=[],
        holdout_start="2022-12-31T18:00:00+00:00",
    )
    assert not (runtime_root / "ema_causal_model.json").exists()


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


def test_harvested_factors_do_not_contribute_to_prediction() -> None:
    table = _feature_table()
    harvested_model = CausalModel(
        family="ema",
        version=1,
        factors=[_planted_factor().model_copy(update={"status": "harvested"})],
        accuracy_history=[],
    )
    empty_model = CausalModel(family="ema", version=1, factors=[], accuracy_history=[])

    pd.testing.assert_series_equal(predict(harvested_model, table), predict(empty_model, table))


def test_holdout_mask_uses_configured_research_engine_holdout_fraction() -> None:
    table = pd.DataFrame(
        {
            "trade_id": ["a", "b", "c"],
            "entry_ts": pd.to_datetime(["2020-06-01", "2022-06-01", "2023-06-01"], utc=True),
            "out_is_loss": [False, True, True],
            "out_pnl": [1.0, -1.0, -2.0],
        }
    )

    default_mask = holdout_mask(table, family="ema")
    half_mask = holdout_mask(table, family="ema", holdout_fraction=0.50)

    assert default_mask.tolist() == [False, False, True]
    assert half_mask.tolist() == [False, True, True]


def test_residual_map_uses_pre_holdout_training_trades_only() -> None:
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
        "train-3",
        "train-1",
        "train-5",
        "train-4",
        "train-2",
    ]
    assert residuals.loc[residuals["trade_id"] == "train-1", "unexplained_abs_pnl"].item() == 0.0
    assert residuals.loc[residuals["trade_id"] == "train-3", "unexplained_abs_pnl"].item() == 0.0


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


def test_causal_model_rejects_outcome_like_dynamic_rule_columns() -> None:
    table = _feature_table()
    table["future_pnl"] = 1.0

    with pytest.raises(ValueError, match="leakage column"):
        predict(
            CausalModel(
                family="ema",
                version=1,
                factors=[
                    CausalFactor(
                        factor_id="f997",
                        story="Future pnl leakage should not be allowed.",
                        rule="future_pnl > 0",
                        direction="loss",
                        evidence_rounds=[],
                        status="candidate",
                    )
                ],
                accuracy_history=[],
            ),
            table,
        )


def test_causal_model_accepts_string_literal_rule_values() -> None:
    model = CausalModel(
        family="ema",
        version=1,
        factors=[
            CausalFactor(
                factor_id="symbol-rule",
                story="AAA entries carry loss risk.",
                rule="symbol == 'AAA'",
                direction="loss",
                evidence_rounds=[],
                status="candidate",
            )
        ],
        accuracy_history=[],
    )

    predictions = predict(model, _feature_table())

    assert predictions.loc[5] > predictions.loc[6]


def test_predict_vectorizes_large_naive_bayes_scoring_without_changing_probabilities() -> None:
    table = _large_feature_table()
    factors = _large_factors()
    model = CausalModel(family="ema", version=1, factors=factors, accuracy_history=[])

    started_at = time.perf_counter()
    predictions = predict(model, table)
    duration = time.perf_counter() - started_at

    assert duration < 1.0
    assert predictions.index.equals(table.index)
    assert not predictions.isna().any()
    expected = _reference_naive_bayes_probabilities(
        table,
        factors,
        family=model.family,
        rows=[0, 1, 17, 997, 19_999],
    )
    for index, probability in expected.items():
        assert predictions.loc[index] == pytest.approx(probability)


def _reference_naive_bayes_probabilities(
    table: pd.DataFrame,
    factors: list[CausalFactor],
    *,
    family: str,
    rows: list[int],
) -> dict[int, float]:
    train = ~holdout_mask(table, family=family)
    y_train = table.loc[train, "out_is_loss"].astype(bool)
    loss_count = int(y_train.sum())
    win_count = int((~y_train).sum())
    train_count = int(len(y_train))
    log_loss_prior = math.log((loss_count + 1.0) / (train_count + 2.0))
    log_win_prior = math.log((win_count + 1.0) / (train_count + 2.0))
    expected: dict[int, float] = {}
    for index in rows:
        log_loss = log_loss_prior
        log_win = log_win_prior
        for factor_index, factor in enumerate(factors):
            flag = table.loc[index, f"x{factor_index}"] < factor_index % 17
            train_flags = table.loc[train, f"x{factor_index}"] < factor_index % 17
            p_loss = (int(train_flags[y_train].sum()) + 1.0) / (loss_count + 2.0)
            p_win = (int(train_flags[~y_train].sum()) + 1.0) / (win_count + 2.0)
            log_loss += math.log(p_loss if flag else 1.0 - p_loss)
            log_win += math.log(p_win if flag else 1.0 - p_win)
        max_log = max(log_loss, log_win)
        loss = math.exp(log_loss - max_log)
        win = math.exp(log_win - max_log)
        expected[index] = float(loss / (loss + win))
    return expected


def test_causal_model_rejects_duplicate_factor_ids() -> None:
    duplicate = _planted_factor()
    model = CausalModel(
        family="ema",
        version=1,
        factors=[duplicate, duplicate.model_copy(update={"rule": "vol_pctile_20d > 0.5"})],
        accuracy_history=[],
    )

    with pytest.raises(ValueError, match="duplicate factor_id"):
        predict(model, _feature_table())
