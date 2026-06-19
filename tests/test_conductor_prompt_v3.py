"""Tests for the mechanism-only conductor system prompt."""

from __future__ import annotations

from research_prompts import _build_mechanism_system_prompt
from scripts.check_prompt_drift import (
    MECHANISM_PROMPT_FIELDS,
    REQUIRED_WIRED_SAFETY_RAILS,
    check_output_fields,
    check_safety_rail_wiring,
)


def test_mechanism_prompt_is_role_grounded_and_tool_aware() -> None:
    # The conductor prompt was rebuilt from the #69-deleted rich version: it now
    # carries a role + objective + backtest semantics and is tool-aware (the
    # "corpus-only, toolless" contract was deliberately reversed). The rendered
    # corpus is still the primary evidence, but the agent has research tools.
    prompt = _build_mechanism_system_prompt()

    assert "rendered corpus" in prompt.lower()
    assert "OBJECTIVE" in prompt
    assert "RESEARCH TOOLS" in prompt
    assert "analyze_trades" in prompt


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


def test_mechanism_prompt_predictions_use_real_schema_field_names() -> None:
    """The prompt must describe predictions with the actual Pydantic field names,
    not leak the internal type name 'MetricName' as a JSON key. Regression for the
    ModelBehaviorError where the model emitted {"MetricName":..,"Prediction":..}
    (8 schema validation errors) because the prompt said 'distinct MetricName
    values' and showed only predictions:null."""
    from research_types import Prediction

    prompt = _build_mechanism_system_prompt()
    for field in Prediction.model_fields:  # metric, direction, predicted, rationale
        assert f'"{field}"' in prompt, f"prediction field {field!r} missing from prompt"
    assert "MetricName" not in prompt


def test_mechanism_prompt_points_proposed_change_at_config_levers_and_decline() -> None:
    """proposed_change keys must come from the corpus '## Config Levers' (the
    conductor invented 'rule' before), and the decline shape must be stated so
    actionable=false with null rule validates against the schema."""
    text = _build_mechanism_system_prompt().lower()
    assert "config levers" in text
    assert "decline" in text


def test_mechanism_prompt_describes_requested_primitive_path() -> None:
    """The conductor must know it can request a new primitive when no lever
    expresses its rule (route-to-builder), instead of stuffing a label into a
    lever or declining a real edge."""
    text = _build_mechanism_system_prompt().lower()
    assert "requested_primitive" in text
    assert "builder" in text
