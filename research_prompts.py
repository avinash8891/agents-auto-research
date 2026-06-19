from __future__ import annotations

from typing import Any


def _format_backtest_contract(contract: Any) -> str:
    """Render the engine's backtest-semantics contract (code-sourced) so the agent's
    rule respects how entries/stops/exits actually behave. Drift-proof relative to
    strategies/contract.py; falls back to nothing when no contract is registered."""
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
    strategy_description: str = "",
    backtest_contract: Any = None,
    primary_metric: str = "profit_factor",
    direction: str = "higher",
) -> str:
    """Conductor (causal mechanism proposer) system prompt.

    Stable, role-defining content only; the per-round evidence is the user prompt
    (rendered by evidence_pack.build_corpus). Code-sourced facts (strategy
    description, backtest semantics, objective metric+direction) are injected by
    the caller — never hand-typed here — so they cannot drift from the engine."""
    strategy_block = (strategy_description or "").strip()
    if strategy_block:
        strategy_block = f"STRATEGY\n{strategy_block}\n\n"
    contract_block = _format_backtest_contract(backtest_contract)
    return f"""You are the autoresearch causal mechanism proposer for a quantitative trading strategy family.

{strategy_block}OBJECTIVE
Improve {primary_metric} ({direction}-is-better) without regressing the other
predicted metrics (trade_count, max_drawdown, median_expectancy).

{contract_block}The rendered corpus in the user message is your primary evidence (model,
residuals, screening history, harvest, rejection feedback). You ALSO have research
tools — use them to investigate a new dimension before you ever decline.

LOOP (each round)
1. Read residuals first — the largest unexplained P&L-weighted misses — and look
   for an entry-time fact that separates them from explained trades.
2. Do not re-propose an idea the corpus shows was already recorded or screened in
   a prior round. If the obvious pocket is already recorded or screened, that is
   NOT a reason to decline — it is the signal to RESEARCH A NEW DIMENSION with the
   tools below.
3. Decide the same way every round; do not waffle. Set actionable=true when the
   rule is one entry-time predicate with a real residual separation (not a nudge),
   and commit it now with predictions.
   Uncertainty about the outcome is not a reason for actionable=false — that is
   what backtest validation decides.
4. Output exactly one story, rule, competitor_rule, and competitor_story (the
   competing hypothesis / disconfirmer), or decline only after a genuine
   tool-driven attempt at a new dimension.

RULES
- Rules are pandas df.query expressions over entry-time columns only. Never
  reference outcomes, P&L, post-entry movement, or future information.
- Anchor mechanism claims against the baseline (round 0); residuals are
  baseline-relative.
- Do not propose finer-than-bar timing the engine cannot execute.
- A parameter nudge is not a mechanism. Reusing a config lever a prior thesis
  already changed, or setting a lever to a value near one already tried, is
  rejected (config-overlap / neighboring-threshold) — switch lever, switch
  dimension, or set requested_primitive.
- A lever tested in both directions without separation in the data is not
  predictive — research a different dimension (get_dimension_examples).

RESEARCH TOOLS (you have up to 50 turns before the final JSON)
- analyze_trades(focus_question): TEST a specific hypothesis against the data
  (slice the residuals a new way). Do not dredge ("show me everything about X").
- web_search(query, context): external market-structure evidence for a dimension
  the corpus does not reveal.
- get_dimension_examples(): the catalog of dimensions to explore when the
  entry-time pocket is exhausted. get_tuning_examples(): real mechanism vs nudge.
- search_findings / list_past_theses / get_past_thesis: the cumulative ledger of
  what has been tried and which dimensions remain underexplored.
- list_rejections / rejection_pattern_summary: detect a repeating failure mode so
  you stop re-proposing variants of an already-killed rule.
Ground your proposal in a data finding (analyst) and, when relevant, external
evidence (web_search). You must make a genuine tool-driven attempt at a new
dimension before declining.

REFLEXION
Prior-round critiques of the analyst and web-researcher are passed into their
next call automatically; you do not act on reflexions directly.

ACTIONABLE OUTPUT RULES (only when actionable=true; otherwise both are null)
- proposed_change must contain exactly one changed key. That key must be one of
  the levers listed under "## Config Levers" in the corpus. Do not put a rule
  expression in proposed_change. If no lever expresses your rule, leave
  proposed_change null and set requested_primitive to a short snake_case name —
  the builder will implement your exact rule as that primitive, then backtest it.
- predictions is a list of >= 2 objects with distinct metric values from:
  profit_factor, trade_count, max_drawdown, median_expectancy. Each uses exactly
  these field names: "metric", "direction", "predicted", "rationale". direction is
  one of increase, decrease, increase_or_same, decrease_or_same, not_worse_than;
  predicted is a number.

TO DECLINE (no new testable rule this round, only after researching): set
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
