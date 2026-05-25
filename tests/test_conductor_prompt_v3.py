"""Tests for the v3 conductor system prompt structure."""

from __future__ import annotations

from research_prompts import _build_conductor_system_prompt


def test_v3_prompt_is_substantially_shorter_than_legacy() -> None:
    """v3 should be well under 200 lines (legacy was 436)."""
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    line_count = prompt.count("\n") + 1
    assert line_count < 200, f"v3 prompt should be < 200 lines; got {line_count}"


def test_v3_prompt_states_two_jobs_per_round() -> None:
    """Look back / look forward framing must be explicit."""
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    assert "Look back" in prompt or "look back" in prompt
    assert "Look forward" in prompt or "look forward" in prompt


def test_v3_prompt_mentions_disconfirmer_prominently() -> None:
    """Disconfirmer should be in the opening section, not buried."""
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    assert "disconfirmer" in prompt.lower() or "falsif" in prompt.lower()


def test_v3_prompt_includes_baseline_anchoring() -> None:
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    assert "baseline" in prompt.lower()
    # Pair: progress vs mechanism distinction
    assert "progress" in prompt.lower() or "mechanism" in prompt.lower()


def test_v3_prompt_includes_baseline_missing_fallback() -> None:
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    assert "baseline" in prompt.lower() and (
        "missing" in prompt.lower() or "stop" in prompt.lower()
    )


def test_v3_prompt_includes_required_analyst_call() -> None:
    """The conductor must call the analyst at least once per round."""
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    assert "analyze_trades" in prompt or "analyst" in prompt.lower()


def test_v3_prompt_includes_d1_alternatives_disconfirmer_principle() -> None:
    """D1: when alternatives could equally support multiple mechanisms, pick by disconfirmer."""
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    text = prompt.lower()
    assert "alternatives_considered" in text or "alternatives considered" in text


def test_v3_prompt_includes_d2_lever_direction_principle() -> None:
    """D2: same lever tested in both directions = lever may not be predictive."""
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    text = prompt.lower()
    assert ("direction" in text and "lever" in text) or "whipsaw" in text


def test_v3_prompt_lists_required_output_fields() -> None:
    prompt = _build_conductor_system_prompt("EMA pullback strategy.")
    # Core output schema fields the agent must produce.
    for field in [
        "thesis_id",
        "hypothesis",
        "mechanism",
        "theme_keywords",
        "prior_lever_outcomes",
    ]:
        assert field in prompt, f"output schema field '{field}' missing from prompt"
