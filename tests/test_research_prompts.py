from __future__ import annotations

from research_prompts import _build_mechanism_system_prompt


def test_mechanism_prompt_matches_single_change_runtime_contract() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "- proposed_change must contain exactly one changed key." in prompt
    assert "inseparable pair" not in prompt


def test_mechanism_prompt_directs_tool_driven_research_before_declining() -> None:
    prompt = _build_mechanism_system_prompt()

    # The proposer must know it has research tools and use them to find a new
    # dimension before declining (the #68 'corpus-only' prompt forbade tool use,
    # which is why the conductor declined instead of researching).
    assert "RESEARCH TOOLS" in prompt
    for tool in ("analyze_trades", "web_search", "get_dimension_examples"):
        assert tool in prompt
    assert "before you ever\ndecline" in prompt or "before declining" in prompt
    assert "RESEARCH A NEW DIMENSION" in prompt
