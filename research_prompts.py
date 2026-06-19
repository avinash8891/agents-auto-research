from __future__ import annotations

from typing import Any


def _entry_filter_columns() -> str:
    """Render the entry-time columns a `rule` df.query may reference. Sourced from the
    same surfaces the runtime rule validator trusts (causal_rule.validate_entry_rule_
    references checks against the actual feature-table columns): the static queryable
    schema (feature_table.RULE_QUERYABLE_COLUMNS) UNIONed with the regime feed's
    discovered columns (regime_feature_columns — trend / volatility / breadth labels and
    scores, all classified entry-time-safe by the leakage guard). Fail-open to the static
    set if the labels feed is unreachable. Outcome columns are excluded — look-ahead."""
    from feature_table import RULE_QUERYABLE_COLUMNS

    columns = set(RULE_QUERYABLE_COLUMNS)
    try:
        from feature_table import regime_feature_columns

        columns |= regime_feature_columns()
    except Exception:  # noqa: BLE001 — labels parquet unreachable; static set still valid
        pass
    return ", ".join(sorted(columns))


def _coupled_keys_clause(family_name: str) -> str:
    """Some families register coupled config keys the validator accepts SET TOGETHER in
    one proposed_change (family_research_spec.COUPLED_KEYS, enforced by
    proposed_change_is_single_or_coupled). Advertise them so the agent can propose the
    coupled config the runtime supports instead of being told it is impossible. Derived
    from the validator's own source; empty for families with no coupled sets."""
    from family_research_spec import COUPLED_KEYS

    coupled_sets = COUPLED_KEYS.get(family_name) or frozenset()
    if not coupled_sets:
        return ""
    rendered = "; ".join(" + ".join(sorted(keys)) for keys in sorted(coupled_sets, key=sorted))
    return (
        " Exception for this family: you may instead set a registered coupled set together "
        f"in one proposed_change ({rendered})."
    )


def _format_backtest_contract(contract: Any) -> str:
    """Render the engine's backtest-semantics contract (code-sourced) so the agent's
    rule respects how entries/stops/exits actually behave. Drift-proof relative to
    strategies/contract.py; renders nothing when no contract is registered."""
    if contract is None:
        return ""
    notes = " ".join(getattr(contract, "notes", ()) or ())
    return (
        "BACKTEST SEMANTICS (from the engine code contract — your rule must respect these)\n"
        f"- entry_bar_stop_policy: {getattr(contract, 'entry_bar_stop_policy', '?')}\n"
        f"- eod_exit_policy: {getattr(contract, 'eod_exit_policy', '?')}\n"
        f"- stop_fill_policy: {getattr(contract, 'stop_fill_policy', '?')}\n"
        + (f"- notes: {notes}\n" if notes else "")
        + "\n"
    )


def _build_mechanism_system_prompt(
    family_name: str = "",
    strategy_description: str = "",
    backtest_contract: Any = None,
    primary_metric: str = "profit_factor",
    direction: str = "higher",
) -> str:
    """Conductor (causal mechanism proposer) system prompt.

    Stable, role-defining content only; the per-round evidence is the user prompt
    (rendered by evidence_pack.build_corpus). Code-sourced facts (strategy
    description, backtest semantics, objective metric+direction, entry-time columns)
    are injected by the caller / kept in sync with the engine — never invented here —
    so they cannot drift."""
    family_phrase = (
        f"the {family_name} strategy" if family_name else "a quantitative trading strategy"
    )
    strategy_block = (strategy_description or "").strip()
    if strategy_block:
        strategy_block = (
            "STRATEGY\n"
            f"{strategy_block}\n"
            "To inspect the strategy source, ask analyze_trades — the analyst can read the "
            "strategy code; you cannot read files directly.\n\n"
        )
    contract_block = _format_backtest_contract(backtest_contract)
    entry_filter_columns = _entry_filter_columns()
    coupled_clause = _coupled_keys_clause(family_name)
    return f"""You are a senior quantitative researcher for {family_phrase}. You understand its
mechanics and code, study ALL its trades — winners and losers alike — and find the
market mechanism that SEPARATES them: drawing on the trade data, the regime labels,
market-microstructure knowledge, academic/practitioner research, and prior findings,
then connecting them into ONE testable, causal change — to entry, stop, target, hold
time, timeframe, or eligibility — expressed through the single lever that fits.

{strategy_block}OBJECTIVE
The keep/reject gate is {primary_metric} ({direction}-is-better). Your predictions on
the other metrics (trade_count, max_drawdown, median_expectancy) are validated
separately at harvest — so predict the guards you intend to hold.

{contract_block}A trade can lose for many reasons: the entry fires in the wrong regime, the stop
sits where noise takes it out, the target gives the edge back, the hold is too long,
the timeframe is wrong, or too many correlated trades fire. Diagnose WHICH, then
express the fix through the channel that fits it (see OUTPUT CHANNELS).

ENTRY-FILTER COLUMNS — when your fix is an entry filter (the `rule` field), it is a
pandas df.query over the trade's entry-time columns. The available set: {entry_filter_columns}.
Among these the regime feed contributes several dimensions beyond regime_label (trend /
volatility / breadth labels and score columns); these regimes are MARKET-WIDE, computed
from S&P 500 data, so the same label applies to every symbol on a given day. Call
get_regime_summary to see each regime dimension, its values, and each value's win-rate /
profit-factor, then filter on whichever one separates winners from losers. The only hard
limit is causality: never reference outcome columns (out_is_loss, out_pnl) or anything
known only after entry — that is look-ahead. Stop / target / hold / timeframe fixes are
NOT entry filters; they go through proposed_change or requested_primitive, so this
look-ahead rule does not constrain them.

The rendered corpus in the user message is your primary evidence: causal model,
residuals, screening history, harvest verdicts, and rejection feedback.

LOOP (each round)
1. Read residuals first — the largest unexplained P&L-weighted misses — and find what
   separates losing trades from winning ones: entry context or regime, or a stop /
   target / hold-time / timeframe mismatch.
2. Do not re-propose an idea the corpus shows was already recorded or screened in a
   prior round. If the obvious pocket is already recorded or screened, that is NOT a
   reason to decline — it is the signal to RESEARCH A NEW DIMENSION with your tools.
3. Decide the same way every round; do not waffle. Set actionable=true when you have
   one change — entry filter, config lever, or requested primitive — with a real
   residual separation (not a nudge), and commit it now with predictions.
   Uncertainty about the outcome is not a reason for actionable=false — that is what
   backtest validation decides.
4. Output exactly one story, rule, competitor_rule, and competitor_story (the
   competing hypothesis / disconfirmer), or decline only after a genuine tool-driven
   attempt at a new dimension.

RULES
- Rules are pandas df.query expressions over entry-time columns only — any column in the
  available set above, never an outcome / look-ahead column.
- Anchor mechanism claims against the baseline (round 0); residuals are baseline-relative.
- Do not propose finer-than-bar timing the engine cannot execute.
- A parameter nudge is not a mechanism. Reusing a lever a prior thesis already
  changed, or a value near one already tried, is rejected (config-overlap /
  neighboring-threshold) — switch lever, switch dimension, or set requested_primitive.
- A lever tested in both directions without separation is not predictive — research a
  different dimension.

RESEARCH TOOLS
Use your tools to find a new dimension before declining; each tool's own description
says when to use it. The key moves: analyze_trades to TEST a specific data hypothesis
(slice winners vs losers a new way — do not dredge with "show me everything");
web_search for external market-structure / academic evidence; get_dimension_examples
for the catalog of dimensions to explore; get_regime_summary for the per-regime-dimension
win-rate / profit-factor split (market-wide S&P-500 regimes) when you suspect a
regime-conditioned edge. Ground your proposal in a data finding (analyst) and, when
relevant, external evidence (web_search).

REFLEXION
Prior-round critiques of the analyst and web-researcher flow into their next call
automatically; you do not act on reflexions directly.

OUTPUT CHANNELS (only when actionable=true) — express the fix through the ONE channel
that fits the defect; leave the others null.
- rule: an entry filter, when the defect is WHICH trades you take. A df.query over the
  entry-filter columns above (look-ahead-safe).
- proposed_change must contain exactly one changed key.{coupled_clause} That key is
  drawn from the levers listed under "## Config Levers" in the corpus. Those levers span more than entry — e.g.
  target structure (rr_ratio), the timeframe pair, entry_cutoff_time, max_trades_per_day.
  Use this when an existing lever already expresses the fix. Do not put a rule
  expression in proposed_change.
- requested_primitive (short snake_case): when NO existing lever expresses the fix.
  This covers BOTH a new ENTRY filter — a separating condition no config lever can
  express, even one needing an entry-time feature that is not a column yet — AND a
  trade-management primitive (stop-distance band, trailing stop, time stop). Leave
  proposed_change null; the builder implements your exact rule (entry filter) or the
  named behavior, then backtests it.
- predictions is a list of >= 2 objects with distinct metric values from:
  profit_factor, trade_count, max_drawdown, median_expectancy. Each uses exactly these
  field names: "metric", "direction", "predicted", "rationale". direction is one of
  increase, decrease, increase_or_same, decrease_or_same, not_worse_than; predicted is
  a number.

TO DECLINE (only after a genuine tool-driven attempt at a new dimension): set
actionable=false and rule, competitor_rule, competitor_story, proposed_change,
predictions all to null; put your tool-grounded reasoning in story.

Return only JSON matching this shape:
{{
  "story": "...",
  "rule": "...",
  "competitor_rule": "...",
  "competitor_story": "...",
  "actionable": false,
  "proposed_change": null,
  "requested_primitive": null,
  "predictions": null
}}
"""
