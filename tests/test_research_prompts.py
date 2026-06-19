from __future__ import annotations

from research_prompts import _build_mechanism_system_prompt


def test_mechanism_prompt_matches_single_change_runtime_contract() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "- proposed_change must contain exactly one changed key." in prompt
    assert "inseparable pair" not in prompt


def test_mechanism_prompt_lists_full_entry_filter_columns_from_schema() -> None:
    """The entry-filter column list is sourced from feature_table, not a stale
    hardcoded subset — so day-of-week (the 'trade on Wednesday' dimension) and every
    queryable column are exposed, while identifier columns are not offered as filters."""
    from feature_table import RULE_QUERYABLE_COLUMNS

    prompt = _build_mechanism_system_prompt()

    for column in RULE_QUERYABLE_COLUMNS:
        assert column in prompt
    assert "day_of_week" in prompt  # was missing from the old hardcoded 7-column list
    assert "trade_id" not in prompt
    assert "entry_ts" not in prompt


def test_mechanism_prompt_exposes_regime_summary_tool() -> None:
    prompt = _build_mechanism_system_prompt()

    assert "get_regime_summary" in prompt


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
