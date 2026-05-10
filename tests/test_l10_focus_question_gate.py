"""Tests for L10: don't-fish-with-analyst gate on focus_question."""

from __future__ import annotations


def test_l10_rejects_too_short_focus_question() -> None:
    from research_conductor import _check_focus_question_specific

    err = _check_focus_question_specific("PF by hour?")
    assert err is not None
    assert "specific" in err.lower() or "short" in err.lower()


def test_l10_rejects_generic_break_down_phrasing() -> None:
    from research_conductor import _check_focus_question_specific

    err = _check_focus_question_specific(
        "Break down PF by entry hour and tell me what you find."
    )
    assert err is not None


def test_l10_rejects_generic_show_me_phrasing() -> None:
    from research_conductor import _check_focus_question_specific

    err = _check_focus_question_specific(
        "Show me everything about the trades from yesterday morning please."
    )
    assert err is not None


def test_l10_accepts_specific_hypothesis_test() -> None:
    from research_conductor import _check_focus_question_specific

    specific = (
        "Test whether wick-only false breaks (where the alert candle's high "
        "exceeds prior bar high but the close is back below) account for the "
        "37% stop-out rate I am seeing in the diagnostics."
    )
    err = _check_focus_question_specific(specific)
    assert err is None
