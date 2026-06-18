"""Tests for the mechanism-only conductor system prompt."""

from __future__ import annotations

from research_prompts import _build_mechanism_system_prompt
from scripts.check_prompt_drift import (
    MECHANISM_PROMPT_FIELDS,
    REQUIRED_WIRED_SAFETY_RAILS,
    check_output_fields,
    check_safety_rail_wiring,
)


def test_mechanism_prompt_is_short_and_corpus_only() -> None:
    prompt = _build_mechanism_system_prompt()

    assert prompt.count("\n") + 1 < 80
    assert "rendered corpus" in prompt.lower()
    assert "tool results" in prompt.lower()
    assert "analyze_trades" not in prompt


def test_mechanism_prompt_mentions_residuals_and_competitor_rule() -> None:
    prompt = _build_mechanism_system_prompt()
    text = prompt.lower()

    assert "residuals" in text
    assert "competitor_rule" in prompt
    assert "competing hypothesis" in text


def test_mechanism_prompt_lists_required_output_fields() -> None:
    prompt = _build_mechanism_system_prompt()

    for field in MECHANISM_PROMPT_FIELDS:
        assert field in prompt, f"output schema field '{field}' missing from prompt"
    assert check_output_fields(prompt) == []


def test_mechanism_prompt_field_check_only_counts_output_shape() -> None:
    prompt = _build_mechanism_system_prompt()
    prompt_without_rule_output = prompt.replace('  "rule": "...",\n', "")
    prompt_with_global_rule_mention = prompt_without_rule_output + "\nOutside output: rule\n"

    findings = check_output_fields(prompt_with_global_rule_mention)

    assert any("'rule'" in finding for finding in findings)


def test_safety_rails_named_in_prompts_have_production_callers() -> None:
    findings = check_safety_rail_wiring(REQUIRED_WIRED_SAFETY_RAILS)

    assert findings == []


def test_mechanism_prompt_gives_deterministic_actionable_rule() -> None:
    """Rule 5 must give a repeatable actionable criterion (commit a testable
    rule now, don't defer on uncertainty) and forbid re-proposing an idea
    already recorded/screened in a prior round — the anti-wobble guidance."""
    text = _build_mechanism_system_prompt().lower()
    assert "do not waffle" in text
    assert "uncertainty about the outcome is not a reason" in text
    assert "re-propose" in text
    assert "already recorded or screened" in text
