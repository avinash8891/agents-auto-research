from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import yaml

from autoresearch_constants import research_engine_holdout_fraction
from autoresearch_runtime_paths import resolve_runtime_root
from causal_rule import RuleExpressionError, evaluate_entry_rule
from persistence_utils import write_json_atomic
from research_types import AccuracyPoint, CausalFactor, CausalModel

_ACTIVE_FACTOR_STATUSES = frozenset({"candidate", "supported", "harvested"})


@dataclass(frozen=True)
class _FittedNaiveBayes:
    factors: tuple[CausalFactor, ...]
    class_priors: dict[str, float]
    factor_likelihoods: dict[str, dict[str, float]]


@dataclass(frozen=True)
class CausalModelStore:
    """Path owner for causal-model state and family config."""

    runtime_root: Path
    code_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_root", self.runtime_root.resolve())
        object.__setattr__(self, "code_root", self.code_root.resolve())

    @classmethod
    def default(cls, code_root: Path | None = None) -> "CausalModelStore":
        root = (code_root or Path.cwd()).resolve()
        return cls(runtime_root=resolve_runtime_root(root), code_root=root)

    def model_path(self, family: str) -> Path:
        return self.runtime_root / f"{family}_causal_model.json"

    def load(self, family: str) -> CausalModel:
        path = self.model_path(family)
        if not path.exists():
            return CausalModel(
                family=family,
                version=0,
                factors=[],
                accuracy_history=[],
                holdout_start=self._holdout_start_for_family(family),
            )
        return CausalModel.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, model: CausalModel) -> None:
        model_to_write = model
        if not model_to_write.holdout_start:
            model_to_write = model_to_write.model_copy(
                update={"holdout_start": self._holdout_start_for_family(model_to_write.family)}
            )
        write_json_atomic(self.model_path(model_to_write.family), model_to_write.model_dump())

    def _holdout_start_for_family(self, family: str) -> str:
        start, end = self._family_validation_bounds(family)
        config = self._family_config(family)
        cutoff = start + (end - start) * (1.0 - research_engine_holdout_fraction(config))
        return cutoff.isoformat()

    def _family_validation_bounds(self, family: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        config = self._family_config(family)
        path = self._family_config_path(family)
        missing = [key for key in ("validation_start", "validation_end") if key not in config]
        if missing:
            raise ValueError(f"{path} missing validation date keys: {missing}")
        start = _utc_timestamp(config["validation_start"])
        end = _utc_timestamp(config["validation_end"])
        if end <= start:
            raise ValueError(f"{path} validation_end must be after validation_start")
        return start, end

    def _family_config(self, family: str) -> dict:
        path = self._family_config_path(family)
        if not path.exists():
            raise FileNotFoundError(f"missing strategy family config for holdout split: {path}")
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _family_config_path(self, family: str) -> Path:
        return self.code_root / "configs" / f"{family}_base.yaml"


def _causal_model_store(
    *, runtime_root: Path | None = None, code_root: Path | None = None
) -> CausalModelStore:
    if runtime_root is not None:
        return CausalModelStore(
            runtime_root=runtime_root,
            code_root=(code_root or Path.cwd()),
        )
    return CausalModelStore.default(code_root)


def _utc_timestamp(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(str(value))
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def load_model(
    family: str, *, runtime_root: Path | None = None, code_root: Path | None = None
) -> CausalModel:
    return _causal_model_store(runtime_root=runtime_root, code_root=code_root).load(family)


def save_model(
    model: CausalModel, *, runtime_root: Path | None = None, code_root: Path | None = None
) -> None:
    _causal_model_store(runtime_root=runtime_root, code_root=code_root).save(model)


def predict(model: CausalModel, features: pd.DataFrame) -> pd.Series:
    _validate_feature_table(features)
    active_factors = _active_factors(model)
    holdout = holdout_mask(features, family=model.family, holdout_start=model.holdout_start)
    train = ~holdout
    if not train.any():
        raise ValueError("causal model requires at least one pre-holdout training row")
    fitted = _fit_naive_bayes(features, active_factors, train)
    return _predict_with_fitted(fitted, features)


def score_on_holdout(model: CausalModel, features: pd.DataFrame) -> AccuracyPoint:
    predictions = predict(model, features)
    holdout = holdout_mask(features, family=model.family, holdout_start=model.holdout_start)
    train = ~holdout
    if not holdout.any():
        raise ValueError("causal model requires at least one holdout row")
    actual = features.loc[holdout, "out_is_loss"].astype(bool)
    predicted = predictions.loc[holdout] > 0.5
    weights = features.loc[holdout, "out_pnl"].astype(float).abs()
    accuracy = _weighted_hit_rate(predicted, actual, weights)
    majority_loss = bool(features.loc[train, "out_is_loss"].astype(bool).mean() > 0.5)
    naive_predictions = pd.Series(majority_loss, index=actual.index)
    naive_accuracy = _weighted_hit_rate(naive_predictions, actual, weights)
    return AccuracyPoint(
        round_number=len(model.accuracy_history) + 1,
        model_version=model.version,
        pnl_weighted_accuracy=accuracy,
        naive_accuracy=naive_accuracy,
        skill=accuracy - naive_accuracy,
        holdout_trade_count=int(holdout.sum()),
    )


def residual_map(model: CausalModel, features: pd.DataFrame) -> pd.DataFrame:
    predictions = predict(model, features)
    holdout = holdout_mask(features, family=model.family, holdout_start=model.holdout_start)
    train = ~holdout
    if not train.any():
        raise ValueError("causal model requires at least one pre-holdout training row")
    actual_loss = features.loc[train, "out_is_loss"].astype(bool)
    predicted_loss = predictions.loc[train] > 0.5
    residuals = pd.DataFrame(
        {
            "trade_id": features.loc[train, "trade_id"].astype(str),
            "predicted": predicted_loss.map({True: "loss", False: "win"}),
            "actual": actual_loss.map({True: "loss", False: "win"}),
            "abs_pnl": features.loc[train, "out_pnl"].astype(float).abs(),
        }
    ).reset_index(drop=True)
    residuals = residuals.assign(
        unexplained_abs_pnl=residuals["abs_pnl"].where(
            residuals["predicted"] != residuals["actual"],
            0.0,
        )
    )
    return residuals.sort_values(
        ["unexplained_abs_pnl", "abs_pnl", "trade_id"],
        ascending=[False, False, True],
        kind="mergesort",
    ).reset_index(drop=True)


def holdout_mask(
    feature_table: pd.DataFrame,
    *,
    family: str = "",
    holdout_start: str = "",
    holdout_fraction: float | None = None,
) -> pd.Series:
    entry_ts = pd.to_datetime(feature_table["entry_ts"], utc=True)
    if entry_ts.empty:
        return pd.Series([], index=feature_table.index, dtype=bool)
    if holdout_start:
        cutoff = pd.Timestamp(holdout_start)
    elif holdout_fraction is not None:
        start = entry_ts.min()
        end = entry_ts.max()
        cutoff = start + (end - start) * (1.0 - holdout_fraction)
    elif family:
        cutoff = pd.Timestamp(_holdout_start_for_family(family))
    else:
        start = entry_ts.min()
        end = entry_ts.max()
        cutoff = start + (end - start) * 0.75
    return pd.Series(entry_ts >= cutoff, index=feature_table.index)


def fit_causal_model(
    feature_table: pd.DataFrame,
    factors: Sequence[CausalFactor],
) -> _FittedNaiveBayes:
    _validate_feature_table(feature_table)
    holdout = holdout_mask(feature_table)
    train = ~holdout
    if not train.any():
        raise ValueError("causal model requires at least one pre-holdout training row")
    return _fit_naive_bayes(feature_table, tuple(factors), train)


def _fit_naive_bayes(
    features: pd.DataFrame,
    factors: tuple[CausalFactor, ...],
    train: pd.Series,
) -> _FittedNaiveBayes:
    flags = _factor_flags(features, factors)
    y_train = features.loc[train, "out_is_loss"].astype(bool)
    loss_count = int(y_train.sum())
    win_count = int((~y_train).sum())
    train_count = int(len(y_train))
    likelihoods: dict[str, dict[str, float]] = {}
    for factor in factors:
        train_flags = flags[factor.factor_id].loc[train]
        likelihoods[factor.factor_id] = {
            "p_flag_given_loss": (int(train_flags[y_train].sum()) + 1.0) / (loss_count + 2.0),
            "p_flag_given_win": (int(train_flags[~y_train].sum()) + 1.0) / (win_count + 2.0),
        }
    return _FittedNaiveBayes(
        factors=factors,
        class_priors={
            "loss": (loss_count + 1.0) / (train_count + 2.0),
            "win": (win_count + 1.0) / (train_count + 2.0),
        },
        factor_likelihoods=likelihoods,
    )


def _predict_with_fitted(fitted: _FittedNaiveBayes, features: pd.DataFrame) -> pd.Series:
    flags = _factor_flags(features, fitted.factors)
    row_count = len(features)
    log_loss = np.full(row_count, math.log(fitted.class_priors["loss"]), dtype=float)
    log_win = np.full(row_count, math.log(fitted.class_priors["win"]), dtype=float)
    for factor in fitted.factors:
        likelihood = fitted.factor_likelihoods[factor.factor_id]
        flagged = flags[factor.factor_id].to_numpy(dtype=bool, copy=False)
        loss_prob = likelihood["p_flag_given_loss"]
        win_prob = likelihood["p_flag_given_win"]
        log_loss += np.where(flagged, math.log(loss_prob), math.log(1.0 - loss_prob))
        log_win += np.where(flagged, math.log(win_prob), math.log(1.0 - win_prob))
    probabilities = _normalize_binary_log_prob_array(log_loss, log_win)
    return pd.Series(probabilities, index=features.index, name="p_loss")


def _active_factors(model: CausalModel) -> tuple[CausalFactor, ...]:
    return tuple(factor for factor in model.factors if factor.status in _ACTIVE_FACTOR_STATUSES)


def _factor_flags(
    feature_table: pd.DataFrame,
    factors: tuple[CausalFactor, ...],
) -> dict[str, pd.Series]:
    flags: dict[str, pd.Series] = {}
    for factor in factors:
        if factor.factor_id in flags:
            raise ValueError(f"duplicate factor_id in model: {factor.factor_id}")
        try:
            flags[factor.factor_id] = evaluate_entry_rule(factor.rule, feature_table)
        except RuleExpressionError as exc:
            raise ValueError(
                f"factor {factor.factor_id} rule failed: {factor.rule}: {exc}"
            ) from exc
    return flags


def _validate_feature_table(feature_table: pd.DataFrame) -> None:
    required = {"trade_id", "entry_ts", "out_is_loss", "out_pnl"}
    missing = required - set(feature_table.columns)
    if missing:
        raise ValueError(f"feature_table missing required causal model columns: {sorted(missing)}")


def _weighted_hit_rate(
    predicted: pd.Series,
    actual: pd.Series,
    weights: pd.Series,
) -> float:
    correct = (predicted.astype(bool) == actual.astype(bool)).astype(float)
    total_weight = float(weights.sum())
    if total_weight == 0.0:
        return float(correct.mean()) if len(correct) else 0.0
    return float((correct * weights.astype(float)).sum() / total_weight)


def _normalize_binary_log_prob(log_loss: float, log_win: float) -> float:
    max_log = max(log_loss, log_win)
    loss = math.exp(log_loss - max_log)
    win = math.exp(log_win - max_log)
    return float(loss / (loss + win))


def _normalize_binary_log_prob_array(log_loss: np.ndarray, log_win: np.ndarray) -> np.ndarray:
    max_log = np.maximum(log_loss, log_win)
    loss = np.exp(log_loss - max_log)
    win = np.exp(log_win - max_log)
    return loss / (loss + win)


def _holdout_start_for_family(family: str) -> str:
    return CausalModelStore.default()._holdout_start_for_family(family)
