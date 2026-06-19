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


def test_mechanism_prompt_advertises_coupled_keys_for_families_that_have_them() -> None:
    from family_research_spec import COUPLED_KEYS

    orb_prompt = _build_mechanism_system_prompt(family_name="orb")
    # ORB registers a coupled set; the prompt must advertise it, else the agent is told
    # the valid coupled proposed_change (which the validator accepts) is impossible.
    assert "coupled set" in orb_prompt
    for key in {k for keys in COUPLED_KEYS["orb"] for k in keys}:
        assert key in orb_prompt

    # A family with no coupled sets keeps the plain single-key rule, no exception text.
    ema_prompt = _build_mechanism_system_prompt(family_name="ema")
    assert "coupled set" not in ema_prompt
    # The single-key contract sentence stays intact in both.
    assert "- proposed_change must contain exactly one changed key." in orb_prompt
    assert "- proposed_change must contain exactly one changed key." in ema_prompt


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
