from __future__ import annotations

import feature_table

from research_prompts import _build_mechanism_system_prompt, _entry_filter_columns


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


def test_entry_filter_columns_includes_discovered_regime_dimensions(monkeypatch) -> None:
    # The runtime rule validator accepts any actual feature-table column (causal_rule),
    # incl. the regime feed's extra dimensions. The prompt's list must include them.
    monkeypatch.setattr(
        feature_table,
        "regime_feature_columns",
        lambda: frozenset({"regime_label", "volatility_regime", "trend_label"}),
    )
    cols = _entry_filter_columns()
    assert "volatility_regime" in cols
    assert "trend_label" in cols
    assert "day_of_week" in cols  # static queryable columns still present


def test_entry_filter_columns_falls_back_when_regime_feed_unreachable(monkeypatch) -> None:
    def _no_parquet():
        raise FileNotFoundError("regime_labels.parquet missing")

    monkeypatch.setattr(feature_table, "regime_feature_columns", _no_parquet)
    cols = _entry_filter_columns()
    assert "day_of_week" in cols  # static set still rendered, no crash


def test_rule_column_constraint_is_causal_not_a_closed_list() -> None:
    # The old wording ("ONLY these entry-time columns" / "above only") contradicted the
    # validator, which allows any non-outcome feature column. Constraint must be causality.
    prompt = _build_mechanism_system_prompt()
    assert "over ONLY these entry-time columns" not in prompt
    assert "the entry-time columns above only" not in prompt
    assert "look-ahead" in prompt


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
