"""Unit tests for eval_metrics."""

from __future__ import annotations

import math

import pytest

from eval_metrics import (
    SuiteSummary,
    TaskOutcome,
    compare_eval_results,
    summarize_eval,
    summarize_suite,
)


def _outcome(family: str, outcome: str, score: float | None = None) -> TaskOutcome:
    return TaskOutcome(family=family, dataset_window="2024", outcome=outcome, overall_score=score)


def test_summarize_suite_compiled_rate_basic():
    suite = summarize_suite(
        [
            _outcome("ema", "compiled", 1.0),
            _outcome("ema", "rejected", 0.0),
            _outcome("orb", "compiled", 1.0),
            _outcome("orb", "conductor_error", 0.0),
        ]
    )
    assert suite.n_tasks == 4
    assert suite.n_compiled == 2
    assert suite.compiled_rate == 0.5
    assert suite.quality_score_p50 == 0.5  # median of [0,0,1,1]


def test_summarize_suite_empty_list_yields_zero_rate():
    suite = summarize_suite([])
    assert suite.n_tasks == 0
    assert suite.compiled_rate == 0.0
    assert suite.quality_score_p50 is None


def test_summarize_suite_handles_no_quality_scores():
    suite = summarize_suite([_outcome("ema", "compiled", None), _outcome("ema", "rejected", None)])
    assert suite.compiled_rate == 0.5
    assert suite.quality_score_p50 is None


def test_summarize_eval_single_suite_zero_stdev():
    suite = SuiteSummary(compiled_rate=0.6, quality_score_p50=0.7, n_tasks=10, n_compiled=6)
    result = summarize_eval(label="t", timestamp="2026-01-01T00:00:00+00:00", suites=[suite])
    assert result.primary_metric_name == "compiled_rate"
    assert result.primary_metric_mean == 0.6
    assert result.primary_metric_stdev == 0.0
    assert result.primary_metric_min == 0.6
    assert result.primary_metric_max == 0.6
    assert result.repeat == 1
    assert result.secondary_quality_p50_mean == 0.7


def test_summarize_eval_multi_suite_variance():
    suites = [
        SuiteSummary(0.5, 0.5, 10, 5),
        SuiteSummary(0.7, 0.6, 10, 7),
        SuiteSummary(0.6, 0.5, 10, 6),
    ]
    result = summarize_eval(label="t", timestamp="2026-01-01T00:00:00+00:00", suites=suites)
    assert result.primary_metric_mean == pytest.approx(0.6, abs=1e-9)
    assert result.primary_metric_stdev > 0.0
    assert result.primary_metric_min == 0.5
    assert result.primary_metric_max == 0.7


def test_summarize_eval_secondary_metric():
    suites = [
        SuiteSummary(0.5, None, 10, 5),
        SuiteSummary(0.6, 0.4, 10, 6),
    ]
    result = summarize_eval(label="t", timestamp="ts", suites=suites)
    assert result.secondary_quality_p50_mean == 0.4


def test_summarize_eval_quality_metric_path():
    suites = [
        SuiteSummary(0.5, 0.4, 10, 5),
        SuiteSummary(0.6, 0.6, 10, 6),
    ]
    result = summarize_eval(
        label="t",
        timestamp="ts",
        suites=suites,
        primary_metric_name="quality_score_p50",
    )
    assert result.primary_metric_name == "quality_score_p50"
    assert result.primary_metric_mean == pytest.approx(0.5, abs=1e-9)


def test_summarize_eval_quality_metric_undefined_raises():
    suites = [SuiteSummary(0.5, None, 10, 5)]
    with pytest.raises(ValueError, match="no defined samples"):
        summarize_eval(
            label="t", timestamp="ts", suites=suites, primary_metric_name="quality_score_p50"
        )


def test_summarize_eval_unknown_metric_name_raises():
    suites = [SuiteSummary(0.5, 0.5, 10, 5)]
    with pytest.raises(ValueError, match="unknown primary_metric_name"):
        summarize_eval(label="t", timestamp="ts", suites=suites, primary_metric_name="nonsense")


def test_summarize_eval_no_suites_raises():
    with pytest.raises(ValueError, match="at least one suite summary"):
        summarize_eval(label="t", timestamp="ts", suites=[])


def test_compare_eval_results_positive_lift():
    prior = summarize_eval(
        label="prior",
        timestamp="t1",
        suites=[
            SuiteSummary(0.5, 0.5, 10, 5),
            SuiteSummary(0.4, 0.5, 10, 4),
            SuiteSummary(0.6, 0.5, 10, 6),
        ],
    )
    current = summarize_eval(
        label="current",
        timestamp="t2",
        suites=[
            SuiteSummary(0.7, 0.5, 10, 7),
            SuiteSummary(0.7, 0.5, 10, 7),
            SuiteSummary(0.7, 0.5, 10, 7),
        ],
    )
    delta = compare_eval_results(current, prior)
    assert delta["delta_sign"] == "+"
    assert delta["delta"] == pytest.approx(0.2, abs=1e-9)
    assert delta["delta_in_stdevs"] is not None
    assert delta["delta_in_stdevs"] > 1.0


def test_compare_eval_results_zero_stdev_yields_none():
    prior = summarize_eval(
        label="prior",
        timestamp="t1",
        suites=[SuiteSummary(0.5, 0.5, 10, 5)],
    )
    current = summarize_eval(
        label="current",
        timestamp="t2",
        suites=[SuiteSummary(0.5, 0.5, 10, 5)],
    )
    delta = compare_eval_results(current, prior)
    assert delta["delta_sign"] == "0"
    assert delta["delta"] == 0.0
    assert delta["delta_in_stdevs"] is None


def test_compare_eval_results_metric_mismatch_raises():
    prior = summarize_eval(label="p", timestamp="t1", suites=[SuiteSummary(0.5, 0.5, 10, 5)])
    current = summarize_eval(
        label="c",
        timestamp="t2",
        suites=[SuiteSummary(0.7, 0.7, 10, 7)],
        primary_metric_name="quality_score_p50",
    )
    with pytest.raises(ValueError, match="primary metric mismatch"):
        compare_eval_results(current, prior)


def test_eval_result_to_dict_round_trip_shape():
    result = summarize_eval(
        label="x",
        timestamp="2026-01-01T00:00:00+00:00",
        suites=[SuiteSummary(0.5, 0.5, 10, 5), SuiteSummary(0.7, 0.6, 10, 7)],
    )
    d = result.to_dict()
    assert d["label"] == "x"
    assert d["primary_metric_name"] == "compiled_rate"
    assert "primary_metric" in d


def test_summarize_eval_raises_on_all_nan_compiled_rate():
    """NaN compiled_rate must not silently propagate through fmean/stdev."""
    suite = SuiteSummary(
        compiled_rate=float("nan"),
        quality_score_p50=None,
        n_tasks=2,
        n_compiled=0,
    )
    with pytest.raises(ValueError, match="non-finite"):
        summarize_eval(label="x", timestamp="2026-01-01T00:00:00+00:00", suites=[suite])


def test_summarize_eval_filters_nan_keeps_finite():
    """A mix of finite and NaN compiled_rate: NaN filtered, finite values used."""
    s1 = SuiteSummary(compiled_rate=0.5, quality_score_p50=None, n_tasks=4, n_compiled=2)
    s2 = SuiteSummary(compiled_rate=float("nan"), quality_score_p50=None, n_tasks=4, n_compiled=0)
    result = summarize_eval(label="x", timestamp="2026-01-01T00:00:00+00:00", suites=[s1, s2])
    assert math.isfinite(result.primary_metric_mean)
    assert result.primary_metric_mean == pytest.approx(0.5)
