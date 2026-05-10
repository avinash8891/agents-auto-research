"""Tests for L6 (web_search before analyze_trades) and L7 (list_experiment_results required)."""

from __future__ import annotations


def test_l6_check_returns_error_when_web_search_not_called() -> None:
    from research_conductor import _check_web_search_called_first

    err = _check_web_search_called_first(set())
    assert err is not None
    assert "web_search" in err


def test_l6_check_returns_none_after_web_search_called() -> None:
    from research_conductor import _check_web_search_called_first

    err = _check_web_search_called_first({"web_search"})
    assert err is None


def test_l7_check_returns_error_when_experiment_results_not_called() -> None:
    from research_conductor import _check_experiment_results_consulted

    err = _check_experiment_results_consulted(set())
    assert err is not None
    assert "list_experiment_results" in err


def test_l7_check_returns_none_after_experiment_results_called() -> None:
    from research_conductor import _check_experiment_results_consulted

    err = _check_experiment_results_consulted({"list_experiment_results"})
    assert err is None
