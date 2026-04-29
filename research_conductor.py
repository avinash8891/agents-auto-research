"""Research conductor — Claude SDK (opus) agent with MCP tools.

The conductor drives the investigation:
  - mempalace (native Python API) for persistent memory (data facts only)
  - Custom in-process MCP server exposing all tools to the conductor

Architecture:
  Conductor (claude-opus, Claude SDK)
    └─ research-tools MCP (in-process SDK)
         ├─ analyze_trades → calls Codex analyst agent (gpt-5.5 + FunctionTools)
         ├─ web_search → calls Codex web researcher agent (gpt-5.5 + WebSearchTool)
         ├─ save_finding → validates + writes to mempalace via Python API
         ├─ search_findings → searches mempalace via Python API
         └─ memory_status → palace overview via Python API
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from research_infra import (
    _ROOT,
    _ensure_oauth_proxy,
    _parse_json,
)
from research_memory import (
    _palace_search,
    _palace_status,
)
from research_memory import list_past_theses as list_past_theses_for_root
from research_memory import (
    save_research_finding,
)
from research_subagents import _call_analyst, _call_web_researcher
from research_tools_mcp import (
    _build_research_tools_mcp as _build_research_tools_mcp_impl,
)
from research_usage import _accumulate_usage, get_round_usage, reset_round_usage
from trace_logger import trace, trace_agent_prompt, trace_agent_response

__all__ = [
    "run_research_conductor",
    "run_research_conductor_sync",
    "reset_round_usage",
    "get_round_usage",
]


def _build_research_tools_mcp(
    trades_file: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
):
    return _build_research_tools_mcp_impl(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        call_analyst=_call_analyst,
        call_web_researcher=_call_web_researcher,
        save_research_finding=save_research_finding,
        palace_search=_palace_search,
        palace_status=_palace_status,
        root=_ROOT,
        list_past_theses_for_root=list_past_theses_for_root,
    )


# ---------------------------------------------------------------------------
# Strategy descriptions (what the strategy IS, not parameter values)
# ---------------------------------------------------------------------------

STRATEGY_DESCRIPTIONS = {
    "orb": """OPENING RANGE BREAKOUT (ORB) STRATEGY

Mechanics:
- Computes the Opening Range (OR) from the first N minutes of trading (configurable, default 30 min).
- OR high = highest high during OR window; OR low = lowest low.
- Long entry: first bar that breaks above OR high (next-bar open after breakout).
- Short entry: first bar that breaks below OR low.
- Stop loss: opposite side of the opening range (long stop = OR low, short stop = OR high).
- Target = entry + risk-reward ratio * risk distance (default RR=2).
- Exits: target hit, stop hit, time stop (default 15:30), max hold bars,
  volatility trailing stop, failed breakout reversal, opposite-side break.
- Regime classification: each day is classified as wide-OR, narrow-OR,
  trend-day, chop-day, or normal based on OR width and intraday behavior.
- Regime gating: can skip or require specific regime types.
- Universe filter: stocks-in-play (top-N by first-30-min dollar volume or
  relative volume) or explicit symbol list.
- Relative volume (RVOL) gate: optional filter requiring volume above
  trailing baseline before taking entries.

To understand what the engine supports and what can be changed,
READ THE SOURCE CODE. Do not guess parameter names.

Source code for signal mechanics (use these to verify hypotheses):
- orb_signals.py: OR computation, breakout detection, entry/stop/target calc
- orb_exits.py: exit logic (stop, target, time stop, trailing stop, failed breakout)
- regime_filter.py: regime classification (wide/narrow OR, trend/chop day)
- backtest_orb_v2.py: main backtest orchestration, universe filtering""",
    "ema": """5 EMA PULLBACK/REVERSAL STRATEGY

Mechanics:
- Uses an exponential moving average (EMA) on intraday bars.
- BEARISH (short) setups use a shorter timeframe (e.g. 5min bars).
- BULLISH (long) setups use a longer timeframe (e.g. 15min bars).
- Entry occurs when price pulls back to the EMA and reverses.
- Entry is at the alert candle's extreme (break level), not next-bar open.
- Stop is at the alert candle's opposite extreme.
- Target = entry + risk-reward ratio * risk distance.
- Each timeframe is self-contained (no cross-timeframe merging).
- Grounded in practitioner transcripts: primarily a short-selling strategy,
  entries concentrated in first 30 minutes after open.

To understand what the engine supports and what can be changed,
READ THE SOURCE CODE. Do not guess parameter names.

Source code for signal mechanics (use these to verify hypotheses):
- ema_signals.py: signal generation, alert candle detection, EMA computation,
  daily reset logic, ema_alert_carry() stateful loop
- ema_exits.py: exit logic (stop/target/timeout)
- backtest_5ema.py: entry filters, main backtest orchestration""",
}


# ---------------------------------------------------------------------------
# Conductor system prompt
# ---------------------------------------------------------------------------


def _build_conductor_system_prompt(strategy_description: str) -> str:
    return f"""You are a trading strategy research conductor.

STRATEGY:
{strategy_description}

You have these tools:
- analyze_trades: dispatch an independent analyst with a specific focus question
- web_search: search the web for external evidence
- save_finding: save a structured research finding to persistent memory (REQUIRED format)
- search_findings: search your persistent memory for previously saved data facts
- memory_status: check what's in your memory
- list_past_theses: list ALL previously proposed theses and their outcomes — CALL THIS BEFORE proposing to avoid duplicates

YOUR FIRST ACTION EVERY ROUND: call list_past_theses. Read what has already
been tried. You MUST propose something that explores a DIFFERENT MECHANISM
DIMENSION than all previous theses (see MECHANISM RESEARCH DIMENSIONS below).

RESEARCH PRINCIPLES (from Lopez de Prado, "Advances in Financial Machine Learning"):
- "Do not research under the influence of a backtest." Your job is to
  understand the MECHANISM — why the strategy should work or fail — not
  to mine the backtest data for lucky parameter values.
- A hypothesis must have an ECONOMIC RATIONALE before you look at any numbers.
  Start with: what market microstructure phenomenon is this strategy trying to
  capture? Under what conditions does that phenomenon exist? When does it break?
- Statistical patterns without causal mechanisms are noise. "PF is higher at
  10:00 than 11:00" is not a thesis. "Breakouts in the first 30 minutes have
  higher follow-through because of overnight order flow imbalance" is.

═══════════════════════════════════════════════════════════════════
MECHANISM RESEARCH DIMENSIONS
═══════════════════════════════════════════════════════════════════

These are the STRUCTURAL DIMENSIONS of any trading strategy. Each represents
a fundamentally different research question. You must explore DIFFERENT
dimensions across rounds, not variations within the same one.

1. ENTRY TIMING — When does the market microstructure create the opportunity?
   Questions: What time window has the strongest edge? Why does it exist?
   Example: "opening auction imbalance dissipates within 15 min"

2. EXIT MECHANISM — How does the trade capture the move it entered for?
   Questions: Fixed target vs trailing? Time-based vs price-structure-based?
   Example: "winners trend for 20+ minutes, trailing captures 2x more"

3. SIGNAL QUALITY — What distinguishes strong signals from noise?
   Questions: What market context predicts which signals work?
   Example: "signals on days with >1.5x relative volume have 2x PF"

4. REGIME CONDITIONING — When does the strategy's edge appear/disappear?
   Questions: VIX level? Trend vs mean-reversion? Day-of-week?
   Example: "edge exists only when VIX is 15-30; disappears in low-vol"

5. PORTFOLIO CONSTRUCTION — How should multiple signals be combined?
   Questions: Signal prioritization? Diversification? Correlation risk?
   Example: "ranking signals by overnight gap size improves PF 40%"

6. RISK STRUCTURE — Is the stop/target framework matched to the move?
   Questions: Favorable excursion distribution? Risk:reward vs win rate?
   Example: "optimal stop is 1.5 ATR not fixed %, reduces noise exits"

7. MARKET MICROSTRUCTURE — What creates the edge at the execution level?
   Questions: Order flow? Bid-ask spread? Adverse selection?
   Example: "entries coincide with market-maker inventory rebalancing"

When proposing a thesis, you MUST state which dimension it explores.
If prior theses already explored that dimension, you MUST choose a
different one OR explain what fundamentally new mechanism within that
dimension you are testing (not a parameter variant).

═══════════════════════════════════════════════════════════════════
PARAMETER TUNING DETECTOR
═══════════════════════════════════════════════════════════════════

Before proposing, check: is this ACTUALLY a new mechanism, or am I just
moving a number? These are PARAMETER TUNING and will be REJECTED:

  ✗ Previous thesis changed gap_exclude_pct from 0.005 to 0.01.
    New thesis changes gap_exclude_pct from 0.01 to 0.002.
    → SAME MECHANISM (gap filtering). Rejected.

  ✗ Previous thesis changed max_trades_per_day from 3 to 5.
    New thesis changes max_trades_per_day from 5 to 7.
    → SAME MECHANISM (position sizing/capacity). Rejected.

  ✓ Previous thesis tested entry time window (entry_timing dimension).
    New thesis tests trailing stop vs fixed target (exit_mechanism dimension).
    → DIFFERENT DIMENSION. Acceptable.

WORKFLOW:

1. READ THE EXPERIMENT RESULTS TABLE in the user prompt. Note the metrics
   but do NOT let them drive your research direction. They tell you the
   strategy's current state, not what to do next.

2. THINK ABOUT THE MECHANISM. Before calling any tool, reason about:
   - What economic phenomenon is this strategy trying to capture?
   - Under what market conditions should this work? When should it fail?
   - What is the weakest link in the strategy's logic chain?
   Write your reasoning in your response before making tool calls.

3. SEARCH FOR EXTERNAL EVIDENCE FIRST. Call web_search to find published
   research, academic papers, or practitioner discussions about the
   mechanism you identified. You need an economic rationale BEFORE
   looking at the data. You MUST call web_search at least once per round.

   *** HARD GATE: You MUST complete at least one web_search call and
   receive its results BEFORE calling analyze_trades. If you call
   analyze_trades before web_search, your thesis will be REJECTED.
   The purpose: you must arrive at the analyst with a SPECIFIC hypothesis
   grounded in external evidence, not use the analyst to go fishing. ***

4. USE THE ANALYST TO VALIDATE YOUR HYPOTHESIS, not to discover one.
   The analyst has access to trades.csv, strategy_events.parquet,
   diagnostics.json, the strategy source code, AND raw OHLCV data in
   the data/ directory. Ask the analyst to:
   - Test a SPECIFIC structural hypothesis you formed from steps 2-3
   - Read strategy source code to understand how the engine works
   - Compute market context from raw OHLCV data (ATR, volume profiles,
     gap sizes, range characteristics — whatever your hypothesis needs)
   Do NOT ask the analyst to "break down PF by X" unless you have a
   reason to believe X matters mechanistically.

5. VERIFY MECHANISM IN CODE. When you find a statistical pattern, you MUST
   ask the analyst to read the strategy source code and explain the causal
   chain. The source files are listed in the strategy description above.

═══════════════════════════════════════════════════════════════════
MECHANISM → IMPLEMENTATION GAP CHECK (required before proposing)
═══════════════════════════════════════════════════════════════════

Before writing your thesis JSON, you MUST verify that your config_changes
actually implement the mechanism you discovered. Ask yourself:

  "Does config key X directly control mechanism Y, or is it a proxy?"

Common gap examples:
  ✗ Mechanism: "different trade types (breakout vs pullback) have different
    win rates." Config change: adjust entry_start_time.
    → GAP: time filter is a PROXY for trade type, not a direct control.
    The thesis must either (a) propose a code change that actually filters
    by trade type, or (b) explicitly acknowledge the proxy relationship
    and explain why the time filter is a valid approximation.

  ✗ Mechanism: "volatility regime affects edge." Config change: adjust
    risk_reward_ratio.
    → GAP: R:R is not a volatility filter. Need a regime gate instead.

  ✓ Mechanism: "stop is too tight relative to ATR, causing noise exits."
    Config change: increase stop_atr_multiple from 1.0 to 1.5.
    → DIRECT: the config key directly controls what the mechanism describes.

If there is a gap, you must either:
  (a) Set requires_code_change=true and describe the needed engine change, OR
  (b) Explain in "mechanism" why the proxy is valid with supporting evidence

6. SAVE DATA FACTS using the save_finding tool. Save structural observations
   about the market or strategy mechanism. Do NOT save experiment outcomes
   or opinions about what parameter values are "good."

7. PROPOSE exactly ONE thesis. Your thesis must:
   - Follow from an economic rationale (step 2) grounded in external
     evidence (step 3) and validated by data (step 4)
   - Explain WHY the change works mechanistically, not just that "the
     numbers look better"
   - If you cannot articulate the causal chain, you have not done enough
     research. Go back to steps 2-5.

MEMPALACE RULES (strict):
- SAVE: data facts, dataset properties, seasonal patterns, web research findings
- DO NOT SAVE: experiment outcomes, thesis evaluations, "I think X is better"
- Experiment results are in the results table. You see them fresh every round.
  Form your own conclusions. Do not cache opinions.

MEMPALACE FINDING FORMAT (required for every save):
Every memory you save MUST include these fields as a structured prefix:
  TYPE: one of [observation, hypothesis, validated_finding, rejected_finding, open_question, implementation_note]
  STATUS: one of [unvalidated, validated, rejected, stale]
  EVIDENCE: which round/experiment produced this (e.g. "round_003, thesis short_ema_8")
  SCOPE: what data this applies to (e.g. "train_period_only", "full_sample", "SPY_only")
  EXPIRES_IF: condition that would invalidate this (e.g. "fails on validation split", "baseline changes")

Example good save:
  "TYPE:observation | STATUS:unvalidated | EVIDENCE:round_003 analyst | SCOPE:train_2023-2025 | EXPIRES_IF:baseline drift >u00a05%
   Tuesdays have PF=1.7 vs Friday PF=2.7 across 3017 trades in train period."

Example BAD save (will poison future rounds):
  "EMA 8 works better than EMA 5" u2190 opinion, no scope, no expiration
  "Gap filter is too strict" u2190 conclusion without evidence or scope

When you READ a finding from search_findings, check its STATUS and SCOPE before trusting it.
If a finding is STATUS:unvalidated and from a different data period, treat it as
a hypothesis to verify, not a fact to build on.

OUTPUT FORMAT (final response after all tool calls):
{{
  "reasoning": "2-3 sentences citing specific numbers from analysis",
  "suggested_theses": [
    {{
      "thesis_id": "short_snake_case_name",
      "mechanism_dimension": "one of: entry_timing, exit_mechanism, signal_quality, regime_conditioning, portfolio_construction, risk_structure, market_microstructure",
      "dimension_novelty": "why this is not a parameter variation of any prior thesis in the same dimension",
      "hypothesis": "what this tests and why (must be specific and testable)",
      "mechanism": "structural change and WHY it should produce the expected effects",
      "evidence": ["data points from analyst or web research that support this"],
      "why_not_overfit": "why this generalizes beyond the sample",
      "config_changes": {{"key": "value"}},
      "expected_effects": [
        {{
          "metric": "profit_factor",
          "direction": "increase",
          "threshold": 0.05,
          "rationale": "why this metric should move in this direction"
        }},
        {{
          "metric": "trade_count",
          "direction": "increase_or_same",
          "rationale": "this change should not reduce opportunities"
        }}
      ],
      "disqualifiers": [
        {{
          "name": "trade_count_collapse",
          "condition": "trade_count decreases by more than 30 percent versus baseline",
          "severity": "hard_fail"
        }},
        {{
          "name": "drawdown_expansion",
          "condition": "max_drawdown worsens by more than 15 percent versus baseline",
          "severity": "hard_fail"
        }}
      ],
      "requires_code_change": false
    }}
  ],
  "should_stop": false
}}

WHAT YOU ARE:
You are a RESEARCHER, not an optimizer. Your job is to understand WHY
the strategy works or fails u2014 what market microstructure, what behavioral
pattern, what structural property of the data creates the edge (or kills it).

YOU ARE NOT:
- A parameter sweeper. "Try rr=4, then rr=5" is not research.
- A grid searcher. Testing values without understanding the mechanism is waste.
- An optimizer. Finding the best number is not the goal.

THE RESEARCH PROCESS:
1. Discover a MECHANISM in the data (e.g. "trades entered in the first
   15 minutes have PF=2.8 vs PF=0.9 after that, because the opening
   auction creates a liquidity imbalance that decays within 15 minutes")
2. Ground it in market microstructure via web_search (e.g. "opening
   auction imbalance is documented in [source] as a persistent intraday
   effect caused by overnight order accumulation")
3. THEN propose a test. The config_change or code_change is the CONSEQUENCE
   of understanding the mechanism, not the starting point.

THESIS REQUIREMENTS (will be REJECTED if any are missing):
- hypothesis: must describe a MECHANISM, not a parameter value
  BAD: "increasing the risk-reward ratio should improve PF"
  GOOD: "the opening auction creates a liquidity imbalance that decays
         within 15 minutes; restricting entries to this window captures
         the structural edge while avoiding noise after the imbalance fades"
- mechanism: the structural WHY u2014 what happens in the market, not what the
  code does with the parameter. Must reference either: (a) a data pattern
  from the analyst with >50 trades, or (b) external evidence from web_search
- evidence: must include at least one web_search finding AND one analyst finding
- expected_effects: at least TWO measurable predictions with direction
  Valid directions: "increase", "decrease", "increase_or_same", "decrease_or_same", "not_worse_than"
  Metrics: profit_factor, max_drawdown, trade_count, median_expectancy,
  pct_profitable_windows, avg_sharpe_across_windows
- disqualifiers: at least ONE condition that would disprove the thesis
  severity: "hard_fail" (auto-reject) or "soft_fail" (flag for review)

RULES:
- config_changes is a DELTA. Keys you omit stay at baseline values.
- Do NOT repeat a thesis_id from the experiment results table.
- Every thesis must have config_changes (to test the mechanism) or requires_code_change=true.
- If requires_code_change=true, describe what the engine needs in "mechanism".
- Reason from the data. Do not anchor to previous rounds' conclusions.
- A thesis without a structural mechanism backed by both data AND external
  evidence will be REJECTED. "I think X might work" is not a thesis.

Return ONLY the JSON object as your final response."""


# ---------------------------------------------------------------------------
# Run the conductor
# ---------------------------------------------------------------------------


async def run_research_conductor(
    trades_file: str,
    experiment_results: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str = "ema",
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
) -> dict[str, Any] | None:
    """Run the research conductor.

    Args:
        trades_file: Path to latest trades CSV (empty if no trades yet).
        experiment_results: Formatted table of all experiment results.
        latest_outcome: Metrics from the most recent experiment.
        research_round: Current round number.
        family_name: Strategy family name.
        strategy_events_file: Path to strategy_events.parquet (optional).
        diagnostics_file: Path to diagnostics.json (optional).
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    strategy_desc = STRATEGY_DESCRIPTIONS.get(
        family_name, f"Strategy family: {family_name}"
    )

    # Build in-process MCP server with research tools
    _ensure_oauth_proxy()
    research_mcp = _build_research_tools_mcp(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
    )

    # Single MCP server with all tools (no external mempalace process)
    mcp_servers: dict[str, Any] = {
        "research-tools": {
            "type": "sdk",
            "name": "research-tools",
            "instance": research_mcp._mcp_server,
        },
    }

    system_prompt = _build_conductor_system_prompt(strategy_desc)

    # Build user prompt with experiment results table
    outcome_lines = (
        json.dumps(latest_outcome, indent=2) if latest_outcome else "(no results yet)"
    )

    if trades_file:
        evidence_lines = f"Trades file for analysis: {trades_file}"
        if strategy_events_file:
            evidence_lines += (
                f"\nStrategy events file: {strategy_events_file}"
                "\n  (Contains EVERY setup the strategy considered — accepted AND rejected."
                "  Use this to understand WHY signals were filtered out.)"
            )
        if diagnostics_file:
            evidence_lines += (
                f"\nDiagnostics file: {diagnostics_file}"
                "\n  (Quick summary of event counts and rejection breakdown. Read this FIRST.)"
            )
        user_prompt = (
            f"Research round: {research_round}\n\n"
            f"LATEST EXPERIMENT OUTCOME:\n{outcome_lines}\n\n"
            f"FULL EXPERIMENT RESULTS TABLE:\n{experiment_results}\n\n"
            f"{evidence_lines}\n\n"
            f"Analyze the trades, check your data-fact memory, and propose your next thesis."
        )
    else:
        user_prompt = (
            f"Research round: {research_round}\n\n"
            f"No experiments have been run yet. No trades file available.\n\n"
            f"FULL EXPERIMENT RESULTS TABLE:\n{experiment_results}\n\n"
            f"Check memory for data facts, do web research on the strategy, "
            f"and propose your first thesis."
        )

    if rejection_feedback:
        user_prompt += (
            f"\n\nYOUR PREVIOUS THESIS WAS REJECTED BY THE VALIDATOR:\n"
            f"{rejection_feedback}\n\n"
            f"Propose a DIFFERENT thesis that avoids this issue. "
            f"Read the source code to understand what the strategy does."
        )

    trace(
        "CONDUCTOR",
        f"START round={research_round} trades={'YES' if trades_file else 'NO'}",
    )
    trace_id = trace_agent_prompt("research-conductor", user_prompt, system_prompt)

    result_text = ""
    got_assistant_text = False
    try:
        async for message in query(
            prompt=user_prompt,
            options=ClaudeAgentOptions(
                system_prompt=system_prompt,
                model="claude-opus-4-6",
                mcp_servers=mcp_servers,
                allowed_tools=[
                    "mcp__research-tools__analyze_trades",
                    "mcp__research-tools__web_search",
                    "mcp__research-tools__save_finding",
                    "mcp__research-tools__search_findings",
                    "mcp__research-tools__memory_status",
                    "mcp__research-tools__list_past_theses",
                ],
                permission_mode="bypassPermissions",
                max_turns=50,
            ),
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        got_assistant_text = True
                        result_text += block.text
            elif isinstance(message, ResultMessage):
                if (
                    not got_assistant_text
                    and hasattr(message, "result")
                    and message.result
                ):
                    result_text = str(message.result)
                # Capture conductor token usage
                if message.usage:
                    trace(
                        "CONDUCTOR",
                        f"USAGE conductor raw_keys={list(message.usage.keys())} usage={message.usage}",
                    )
                    _accumulate_usage(
                        "conductor", message.usage, message.total_cost_usd
                    )
                if message.model_usage:
                    trace(
                        "CONDUCTOR",
                        f"USAGE conductor model_usage_keys={list(message.model_usage.keys())} model_usage={message.model_usage}",
                    )
                    _accumulate_usage("conductor", message.model_usage)
    except asyncio.TimeoutError:
        trace("CONDUCTOR", "TIMEOUT")
        return {
            "status": "conductor_error",
            "error": "timeout",
            "suggested_theses": [],
            "should_stop": False,
        }
    except Exception as exc:
        trace("CONDUCTOR", f"ERROR: {exc}")
        print(f"CONDUCTOR error: {exc}")
        return {
            "status": "conductor_error",
            "error": str(exc),
            "suggested_theses": [],
            "should_stop": False,
        }

    parsed = _parse_json(result_text)
    trace_agent_response("research-conductor", trace_id, result_text, parsed)

    if parsed:
        theses = parsed.get("suggested_theses", [])
        if parsed.get("should_stop"):
            trace("CONDUCTOR", "recommends STOP")
            return parsed
        if theses and isinstance(theses[0], dict):
            t = theses[0]
            if t.get("thesis_id") and (
                t.get("config_changes") or t.get("requires_code_change")
            ):
                trace("CONDUCTOR", f"OK thesis={t['thesis_id']}")
                return parsed
        trace("CONDUCTOR", f"validate failed: {result_text[:200]}")
    else:
        trace("CONDUCTOR", f"parse failed: {result_text[:200]}")

    return None


def run_research_conductor_sync(
    trades_file: str,
    experiment_results: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str = "ema",
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
) -> dict[str, Any] | None:
    return asyncio.run(
        run_research_conductor(
            trades_file,
            experiment_results,
            latest_outcome,
            research_round,
            family_name,
            strategy_events_file=strategy_events_file,
            diagnostics_file=diagnostics_file,
            rejection_feedback=rejection_feedback,
        )
    )
