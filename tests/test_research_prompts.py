from __future__ import annotations

from research_prompts import _build_mechanism_system_prompt


def test_mechanism_prompt_documents_requested_primitive_contract() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "requested_primitive" in prompt
    assert "available entry-time columns are what exists today, not a ceiling" in prompt
    assert (
        "- actionable=true requires predictions and either proposed_change or "
        "requested_primitive."
    ) in prompt
