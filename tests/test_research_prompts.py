from __future__ import annotations

from research_prompts import _build_mechanism_system_prompt


def test_mechanism_prompt_matches_single_change_runtime_contract() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "- proposed_change must contain exactly one changed key." in prompt
    assert "inseparable pair" not in prompt
