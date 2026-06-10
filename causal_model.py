from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from feature_table import ENTRY_TIME_COLUMNS, OUTCOME_COLUMNS
from research_types import CausalFactor

_QUERY_KEYWORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "in",
        "True",
        "False",
        "None",
        "is",
        "abs",
    }
)
_QUERY_NAME_RE = re.compile(r"`([^`]+)`|\b[A-Za-z_]\w*\b")


@dataclass(frozen=True)
class CausalModel:
    factors: tuple[CausalFactor, ...]
    class_priors: dict[str, float]
    factor_likelihoods: dict[str, dict[str, float]]
    holdout_start: pd.Timestamp
    holdout_end: pd.Timestamp
    holdout_trade_ids: list[str]
    pnl_weighted_accuracy: float
    residual_map: dict[str, float]

    def predict_proba(self, feature_table: pd.DataFrame) -> pd.DataFrame:
        flags = _factor_flags(feature_table, self.factors)
        probabilities: list[dict[str, object]] = []
        for index, row in feature_table.iterrows():
            log_loss = math.log(self.class_priors["loss"])
            log_win = math.log(self.class_priors["win"])
            for factor in self.factors:
                likelihood = self.factor_likelihoods[factor.factor_id]
                flagged = bool(flags[factor.factor_id].loc[index])
                loss_prob = likelihood["p_flag_given_loss"]
                win_prob = likelihood["p_flag_given_win"]
                if flagged:
                    log_loss += math.log(loss_prob)
                    log_win += math.log(win_prob)
                else:
                    log_loss += math.log(1.0 - loss_prob)
                    log_win += math.log(1.0 - win_prob)
            p_loss = _normalize_binary_log_prob(log_loss, log_win)
            probabilities.append(
                {
                    "trade_id": str(row["trade_id"]),
                    "p_loss": p_loss,
                    "p_win": 1.0 - p_loss,
                    "predicted_direction": "loss" if p_loss >= 0.5 else "win",
                }
            )
        return pd.DataFrame(
            probabilities,
            columns=["trade_id", "p_loss", "p_win", "predicted_direction"],
        )


def holdout_mask(feature_table: pd.DataFrame) -> pd.Series:
    entry_ts = pd.to_datetime(feature_table["entry_ts"], utc=True)
    if entry_ts.empty:
        return pd.Series([], index=feature_table.index, dtype=bool)
    start = entry_ts.min()
    end = entry_ts.max()
    cutoff = start + (end - start) * 0.75
    return pd.Series(entry_ts >= cutoff, index=feature_table.index)


def fit_causal_model(
    feature_table: pd.DataFrame,
    factors: Sequence[CausalFactor],
) -> CausalModel:
    _validate_feature_table(feature_table)
    factors_tuple = tuple(factors)
    flags = _factor_flags(feature_table, factors_tuple)
    holdout = holdout_mask(feature_table)
    train = ~holdout
    if not train.any():
        raise ValueError("causal model requires at least one pre-holdout training row")

    y_train = feature_table.loc[train, "out_is_loss"].astype(bool)
    loss_count = int(y_train.sum())
    win_count = int((~y_train).sum())
    train_count = int(len(y_train))
    class_priors = {
        "loss": (loss_count + 1.0) / (train_count + 2.0),
        "win": (win_count + 1.0) / (train_count + 2.0),
    }
    factor_likelihoods = _fit_factor_likelihoods(
        flags,
        factors_tuple,
        train,
        y_train,
        loss_count=loss_count,
        win_count=win_count,
    )

    draft = CausalModel(
        factors=factors_tuple,
        class_priors=class_priors,
        factor_likelihoods=factor_likelihoods,
        holdout_start=pd.to_datetime(feature_table.loc[holdout, "entry_ts"], utc=True).min(),
        holdout_end=pd.to_datetime(feature_table.loc[holdout, "entry_ts"], utc=True).max(),
        holdout_trade_ids=feature_table.loc[holdout, "trade_id"].astype(str).tolist(),
        pnl_weighted_accuracy=0.0,
        residual_map={},
    )
    predictions = draft.predict_proba(feature_table)
    accuracy = _pnl_weighted_accuracy(feature_table, predictions, holdout)
    residuals = _residual_map(feature_table, predictions, holdout)
    return CausalModel(
        factors=draft.factors,
        class_priors=draft.class_priors,
        factor_likelihoods=draft.factor_likelihoods,
        holdout_start=draft.holdout_start,
        holdout_end=draft.holdout_end,
        holdout_trade_ids=draft.holdout_trade_ids,
        pnl_weighted_accuracy=accuracy,
        residual_map=residuals,
    )


def _fit_factor_likelihoods(
    flags: dict[str, pd.Series],
    factors: tuple[CausalFactor, ...],
    train: pd.Series,
    y_train: pd.Series,
    *,
    loss_count: int,
    win_count: int,
) -> dict[str, dict[str, float]]:
    likelihoods: dict[str, dict[str, float]] = {}
    for factor in factors:
        train_flags = flags[factor.factor_id].loc[train]
        loss_flags = train_flags[y_train]
        win_flags = train_flags[~y_train]
        likelihoods[factor.factor_id] = {
            "p_flag_given_loss": (int(loss_flags.sum()) + 1.0) / (loss_count + 2.0),
            "p_flag_given_win": (int(win_flags.sum()) + 1.0) / (win_count + 2.0),
        }
    return likelihoods


def _factor_flags(
    feature_table: pd.DataFrame,
    factors: tuple[CausalFactor, ...],
) -> dict[str, pd.Series]:
    flags: dict[str, pd.Series] = {}
    for factor in factors:
        _validate_rule_references(factor.rule)
        try:
            matching = feature_table.query(factor.rule)
        except Exception as exc:
            raise ValueError(f"factor {factor.factor_id} rule failed: {factor.rule}") from exc
        flags[factor.factor_id] = pd.Series(
            feature_table.index.isin(matching.index),
            index=feature_table.index,
            dtype=bool,
        )
    return flags


def _validate_rule_references(rule: str) -> None:
    for match in _QUERY_NAME_RE.finditer(rule):
        name = match.group(1) or match.group(0)
        if name in _QUERY_KEYWORDS:
            continue
        if name in OUTCOME_COLUMNS:
            raise ValueError(f"causal factor rule references outcome column: {name}")
        if name not in ENTRY_TIME_COLUMNS:
            raise ValueError(f"causal factor rule references unknown entry column: {name}")


def _validate_feature_table(feature_table: pd.DataFrame) -> None:
    required = {"trade_id", "entry_ts", "out_is_loss", "out_pnl"}
    missing = required - set(feature_table.columns)
    if missing:
        raise ValueError(f"feature_table missing required causal model columns: {sorted(missing)}")


def _normalize_binary_log_prob(log_loss: float, log_win: float) -> float:
    max_log = max(log_loss, log_win)
    loss = math.exp(log_loss - max_log)
    win = math.exp(log_win - max_log)
    return float(loss / (loss + win))


def _pnl_weighted_accuracy(
    feature_table: pd.DataFrame,
    predictions: pd.DataFrame,
    holdout: pd.Series,
) -> float:
    if not holdout.any():
        return 0.0
    actual = np.where(feature_table.loc[holdout, "out_is_loss"].astype(bool), "loss", "win")
    predicted = predictions.loc[holdout, "predicted_direction"].to_numpy()
    weights = feature_table.loc[holdout, "out_pnl"].astype(float).abs().to_numpy()
    correct = (actual == predicted).astype(float)
    total_weight = float(weights.sum())
    if total_weight == 0.0:
        return float(correct.mean())
    return float(np.average(correct, weights=weights))


def _residual_map(
    feature_table: pd.DataFrame,
    predictions: pd.DataFrame,
    holdout: pd.Series,
) -> dict[str, float]:
    residuals: dict[str, float] = {}
    for index, row in feature_table.loc[holdout].iterrows():
        actual_loss = 1.0 if bool(row["out_is_loss"]) else 0.0
        residuals[str(row["trade_id"])] = actual_loss - float(predictions.loc[index, "p_loss"])
    return residuals
