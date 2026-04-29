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
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from trace_logger import trace, trace_agent_prompt, trace_agent_response

# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------

_ROUND_USAGE: dict[str, dict[str, float]] = {}


def _accumulate_usage(agent_type: str, usage: dict[str, Any] | None, cost_usd: float | None = None) -> None:
    """Accumulate token usage for the current round."""
    if agent_type not in _ROUND_USAGE:
        _ROUND_USAGE[agent_type] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "calls": 0}
    entry = _ROUND_USAGE[agent_type]
    entry["calls"] += 1
    if usage:
        entry["input_tokens"] += usage.get("input_tokens") or usage.get("input") or 0
        entry["output_tokens"] += usage.get("output_tokens") or usage.get("output") or 0
        entry["total_tokens"] += usage.get("total_tokens") or usage.get("total") or 0
    if cost_usd:
        entry["cost_usd"] += cost_usd


def reset_round_usage() -> None:
    """Reset usage counters at the start of a new round."""
    _ROUND_USAGE.clear()


def get_round_usage() -> dict[str, Any]:
    """Get accumulated usage for the current round."""
    total = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "calls": 0}
    for agent_usage in _ROUND_USAGE.values():
        for k in total:
            total[k] += agent_usage[k]
    return {"by_agent": dict(_ROUND_USAGE), "total": total}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parent
_PALACE_DIR = str(_ROOT / "palace")

# ---------------------------------------------------------------------------
# Native mempalace access — uses only documented public APIs:
#   mempalace.palace.get_collection  (ChromaDB collection access)
#   mempalace.searcher.search_memories  (semantic search)
#   mempalace.layers.MemoryStack  (status)
# ---------------------------------------------------------------------------


def _palace_add(wing: str, room: str, content: str, added_by: str = "conductor") -> dict:
    """Add a drawer to the palace via palace.get_collection + ChromaDB upsert."""
    import hashlib
    from datetime import datetime
    try:
        from mempalace.palace import get_collection
        col = get_collection(_PALACE_DIR, create=True)
        drawer_id = (
            f"drawer_{wing}_{room}_"
            f"{hashlib.sha256((wing + room + content).encode()).hexdigest()[:24]}"
        )
        col.upsert(
            ids=[drawer_id],
            documents=[content],
            metadatas=[{
                "wing": wing,
                "room": room,
                "source_file": "",
                "chunk_index": 0,
                "added_by": added_by,
                "filed_at": datetime.now().isoformat(),
            }],
        )
        return {"success": True, "drawer_id": drawer_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _palace_search(query: str, wing: str | None = None, room: str | None = None,
                   n_results: int = 10) -> list[dict]:
    """Search the palace via mempalace.searcher.search_memories."""
    try:
        from mempalace.searcher import search_memories
        result = search_memories(
            query=query,
            palace_path=_PALACE_DIR,
            wing=wing,
            room=room,
            n_results=n_results,
        )
        return result.get("results", [])
    except Exception as exc:
        return [{"error": str(exc)}]


def _palace_status() -> dict:
    """Get palace overview via mempalace.layers.MemoryStack."""
    try:
        from mempalace.layers import MemoryStack
        stack = MemoryStack(palace_path=_PALACE_DIR)
        return stack.status()
    except Exception as exc:
        return {"error": str(exc)}

# ---------------------------------------------------------------------------
# OAuth proxy for Codex agents
# ---------------------------------------------------------------------------

_OAUTH_PROXY_PORT = 10531
_OAUTH_PROXY_URL = f"http://127.0.0.1:{_OAUTH_PROXY_PORT}/v1"


def _ensure_oauth_proxy(timeout_seconds: float = 5.0) -> None:
    import socket
    import time

    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while True:
        try:
            with socket.create_connection(("127.0.0.1", _OAUTH_PROXY_PORT), timeout=1):
                return
        except OSError as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    raise RuntimeError(
        f"openai-oauth proxy is not listening at {_OAUTH_PROXY_URL}. "
        "Start openai-oauth.service before running research jobs. "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    # Strip markdown code fences if present (use find, not index, to avoid crash).
    for fence in ("```json", "```"):
        start = text.find(fence)
        if start != -1:
            start += len(fence)
            end = text.find("```", start)
            if end != -1:
                text = text[start:end].strip()
            else:
                # Opening fence without closing fence — strip fence and continue.
                text = text[start:].strip()
            break
    brace = text.find("{")
    if brace == -1:
        return None
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# Codex analyst agent (called by the MCP tool)
# ---------------------------------------------------------------------------

async def _call_analyst(
    trades_file: str,
    focus_question: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
) -> str:
    from openai import AsyncOpenAI
    from agents import Agent as OAIAgent, Runner as OAIRunner, function_tool
    from agents import RunConfig as OAIRunConfig
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

    _ensure_oauth_proxy()

    @function_tool
    def read_file(file_path: str) -> str:
        """Read a file from the local filesystem.

        Args:
            file_path: Absolute path to the file to read.
        """
        try:
            with open(file_path) as f:
                content = f.read()
            if len(content) > 50000:
                return content[:50000] + f"\n... (truncated, {len(content)} total chars)"
            return content
        except Exception as e:
            return f"ERROR: {e}"

    @function_tool
    def run_python(code: str) -> str:
        """Execute Python code locally and return stdout + stderr.

        Args:
            code: Python code to execute. Use print() for output.
        """
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=60,
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            if result.returncode != 0:
                output += f"\nEXIT CODE: {result.returncode}"
            if len(output) > 30000:
                output = output[:30000] + "\n... (truncated)"
            return output
        except subprocess.TimeoutExpired:
            return "ERROR: Code execution timed out (60s limit)"
        except Exception as e:
            return f"ERROR: {e}"

    analyst_prompt = f"""You are a quantitative trading analyst. You receive:
1. A path to a CSV file containing raw trades from a backtest
2. A FOCUS QUESTION from the research conductor
3. A strategy_events.parquet with every signal the strategy considered (accepted AND rejected)
4. A diagnostics.json with event counts and rejection breakdown
5. Access to RAW OHLCV DATA at: {str(_ROOT / 'data')}/
   Structure: data/raw/{{SYMBOL}}/{{YEAR}}.parquet (5-minute OHLCV bars, one file per year)
   Also: data/open.parquet, data/high.parquet, data/low.parquet, data/close.parquet (wide format, all symbols)
   You can load any symbol's price history to compute market context (ATR, volume, gaps, etc.).

You MUST use ALL provided files. Trades alone show what happened;
strategy_events show what DIDN'T happen and WHY. Diagnostics give
the high-level rejection breakdown before you dig into details.

RAW TRADES CSV SCHEMA (one row per completed trade):
  entry_date, exit_date, direction, entry_price, exit_price, stop, target,
  pnl_pct, exit_reason, symbol

STRATEGY EVENTS PARQUET SCHEMA (one row per decision point, read with pd.read_parquet()):
  timestamp, symbol, direction, event_type, reason, entry_price, stop_price

  event_type values: raw_setup, accepted_signal, rejected_signal, order_rejected, executed_trade
  reason values: strategy-specific (read diagnostics.json for the actual reasons
                 used in this experiment)

DIAGNOSTICS JSON: quick summary with event_counts and rejection_breakdown.

WORKFLOW:
1. ALWAYS start by reading diagnostics.json for the rejection breakdown.
2. Use run_python to execute pandas analysis code on trades and/or events.
3. When the focus question requires market context (volatility, volume,
   trend, gaps, range characteristics), load the relevant symbol data from
   the raw OHLCV directory and compute what you need.
4. Focus effort on the FOCUS QUESTION. Go deep, not wide.
5. When you find a pattern, quantify it with exact numbers and sample sizes.

CRITICAL RULES:
- PF = sum(pnl_pct where pnl_pct > 0) / abs(sum(pnl_pct where pnl_pct <= 0))
- Only flag patterns with >50 trades per bucket
- Cite exact numbers
- Do NOT invent data
- Do NOT repeat analyses the focus question doesn't ask for
- If the focus question asks about market structure, USE the raw OHLCV data

OUTPUT FORMAT:
Return ONLY a JSON object:
{{
  "focus_answer": "direct answer to the focus question with exact numbers",
  "key_anomalies": [
    {{
      "pattern": "one-line description",
      "numbers": "exact computed values",
      "sample_size": "trades in bucket",
      "suggested_exploit": "specific structural change",
      "confidence": "high/medium/low"
    }}
  ],
  "rejection_insights": [
    {{
      "reason": "rejection reason from strategy_events",
      "count": "number rejected",
      "pattern": "when/where rejections cluster",
      "implication": "what this means for the strategy"
    }}
  ],
  "overall_diagnosis": "2-3 sentence summary",
  "discovery_questions": ["questions needing more data"]
}}

Be brutally honest."""

    user_parts = [
        f"FOCUS QUESTION: {focus_question}",
        f"RAW TRADES FILE: {trades_file}",
    ]
    if strategy_events_file:
        user_parts.append(f"STRATEGY EVENTS FILE: {strategy_events_file}")
    if diagnostics_file:
        user_parts.append(f"DIAGNOSTICS FILE: {diagnostics_file}")
    user_parts.append(
        "Load the files and perform your analysis using the run_python and read_file tools."
        " Start with diagnostics.json if available for an overview."
    )
    user_prompt = "\n\n".join(user_parts)

    client = AsyncOpenAI(api_key="unused", base_url=_OAUTH_PROXY_URL)
    model = OpenAIChatCompletionsModel(model="gpt-5.5", openai_client=client)
    agent = OAIAgent(
        name="codex-diagnostic-analyst",
        instructions=analyst_prompt,
        tools=[read_file, run_python],
        model=model,
    )

    trace("CONDUCTOR", f"analyst dispatch focus='{focus_question[:80]}'")
    try:
        result = OAIRunner.run_streamed(
            agent, user_prompt,
            run_config=OAIRunConfig(tracing_disabled=True),
            max_turns=25,
        )
        async for _ in result.stream_events():
            pass
        # Track analyst token usage
        for resp in result.raw_responses:
            u = resp.usage
            _accumulate_usage("analyst", {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "total_tokens": u.total_tokens})
        output = result.final_output or ""
        parsed = _parse_json(output)
        if parsed:
            n_anomalies = len(parsed.get('key_anomalies', []))
            trace("CONDUCTOR", f"analyst OK anomalies={n_anomalies}")
            trace_agent_response(
                "analyst",
                f"analyst-{focus_question[:40].replace(' ', '_')}",
                output,
                parsed,
            )
            return json.dumps(parsed, indent=2)
        trace("CONDUCTOR", f"analyst parse failed: {output[:200]}")
        return f"ANALYST ERROR: could not parse response: {output[:500]}"
    except Exception as exc:
        trace("CONDUCTOR", f"analyst ERROR: {exc}")
        return f"ANALYST ERROR: {exc}"


async def _call_web_researcher(query: str, context: str) -> str:
    from openai import AsyncOpenAI
    from agents import Agent as OAIAgent, Runner as OAIRunner, WebSearchTool
    from agents import RunConfig as OAIRunConfig, ModelSettings as OAIModelSettings
    from agents.models.openai_provider import OpenAIProvider

    _ensure_oauth_proxy()

    web_prompt = """You are a research agent specializing in quantitative trading strategies.
Your ONLY job is to find and report external evidence for the specific question asked.

1. Run targeted web searches.
2. Prefer primary sources: academic papers > practitioner research > blogs.
3. Read sources in full. Extract specific claims and data points.
4. Be skeptical.

OUTPUT FORMAT:
Return a JSON object:
{
  "findings": [
    {
      "topic": "short label",
      "finding": "specific claim with attribution",
      "source": "URL or null",
      "source_quality": "academic/practitioner/blog/forum",
      "actionable_idea": "specific structural change this suggests"
    }
  ],
  "summary": "2-3 sentence synthesis"
}
Return ONLY the JSON object."""

    user_prompt = f"RESEARCH QUESTION: {query}\n\nCONTEXT: {context}"

    client = AsyncOpenAI(api_key="unused", base_url=_OAUTH_PROXY_URL)
    provider = OpenAIProvider(openai_client=client)
    agent = OAIAgent(
        name="web-researcher",
        instructions=web_prompt,
        tools=[WebSearchTool()],
        model="gpt-5.5",
    )

    trace("CONDUCTOR", f"web_search dispatch query='{query[:80]}'")
    try:
        result = OAIRunner.run_streamed(
            agent, user_prompt,
            run_config=OAIRunConfig(
                model_provider=provider,
                model_settings=OAIModelSettings(store=False),
                tracing_disabled=True,
            ),
        )
        async for _ in result.stream_events():
            pass
        # Track web researcher token usage
        for resp in result.raw_responses:
            u = resp.usage
            _accumulate_usage("web_researcher", {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "total_tokens": u.total_tokens})
        output = result.final_output or ""
        parsed = _parse_json(output)
        if parsed:
            n_findings = len(parsed.get('findings', []))
            trace("CONDUCTOR", f"web_search OK findings={n_findings}")
            trace_agent_response(
                "web-researcher",
                f"web-{query[:40].replace(' ', '_')}",
                output,
                parsed,
            )
            return json.dumps(parsed, indent=2)
        trace("CONDUCTOR", f"web_search parse failed: {output[:200]}")
        return f"WEB_SEARCH ERROR: could not parse: {output[:500]}"
    except Exception as exc:
        trace("CONDUCTOR", f"web_search ERROR: {exc}")
        return f"WEB_SEARCH ERROR: {exc}"


# ---------------------------------------------------------------------------
# In-process MCP server: exposes analyze_trades + web_search to Claude SDK
# ---------------------------------------------------------------------------

def _build_research_tools_mcp(
    trades_file: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
):
    """Create an in-process MCP server with research tools."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("research-tools")

    @mcp.tool()
    async def analyze_trades(focus_question: str) -> str:
        """Dispatch an independent analyst to analyze the strategy run.

        The analyst has access to:
        - trades.csv: completed/executed trades and outcomes
        - strategy_events.parquet: every setup the strategy considered, including rejected ones
        - diagnostics.json: event counts and rejection breakdown summary

        Give it a SPECIFIC focus question. For rejected signals, ask about
        rejection reasons found in diagnostics.json.

        Args:
            focus_question: What specific pattern to investigate.
        """
        if not trades_file:
            return "ERROR: No trades file available for this round."
        return await _call_analyst(
            trades_file, focus_question,
            strategy_events_file=strategy_events_file,
            diagnostics_file=diagnostics_file,
        )

    @mcp.tool()
    async def web_search(query: str, context: str = "") -> str:
        """Search the web for external evidence about trading strategies.

        Args:
            query: Specific search query.
            context: Brief context about why you're searching.
        """
        return await _call_web_researcher(query, context)

    @mcp.tool()
    async def save_finding(
        finding: str,
        finding_type: str,
        status: str,
        evidence: str,
        scope: str,
        expires_if: str,
    ) -> str:
        """Save a structured research finding to persistent memory.

        Every finding MUST have all fields. The system will reject saves
        that lack proper classification.

        Args:
            finding: The factual observation (e.g. "Tuesdays have PF=1.7 vs Friday PF=2.7 across 3017 trades")
            finding_type: One of: observation, hypothesis, validated_finding, rejected_finding, open_question, implementation_note
            status: One of: unvalidated, validated, rejected, stale
            evidence: Which round/experiment produced this (e.g. "round_003, thesis short_ema_8")
            scope: What data this applies to (e.g. "train_2020-2023", "full_sample", "SPY_only")
            expires_if: Condition that invalidates this (e.g. "fails on validation split", "baseline drift >5%")
        """
        VALID_TYPES = {"observation", "hypothesis", "validated_finding",
                       "rejected_finding", "open_question", "implementation_note"}
        VALID_STATUSES = {"unvalidated", "validated", "rejected", "stale"}

        trace("CONDUCTOR", f"save_finding type={finding_type} status={status} finding='{finding[:80]}'")

        if finding_type not in VALID_TYPES:
            trace("CONDUCTOR", f"save_finding REJECTED: bad type '{finding_type}'")
            return f"REJECTED: finding_type must be one of {VALID_TYPES}, got '{finding_type}'"
        if status not in VALID_STATUSES:
            trace("CONDUCTOR", f"save_finding REJECTED: bad status '{status}'")
            return f"REJECTED: status must be one of {VALID_STATUSES}, got '{status}''"
        if not evidence.strip():
            return "REJECTED: evidence cannot be empty — cite which round/experiment"
        if not scope.strip():
            return "REJECTED: scope cannot be empty — specify what data period this applies to"
        if not expires_if.strip():
            return "REJECTED: expires_if cannot be empty — what would invalidate this?"

        # Format as structured content for mempalace
        content = (
            f"TYPE:{finding_type} | STATUS:{status} | "
            f"EVIDENCE:{evidence} | SCOPE:{scope} | "
            f"EXPIRES_IF:{expires_if}\n"
            f"{finding}"
        )

        result = _palace_add(
            wing="research_findings",
            room=finding_type,
            content=content,
        )
        if result.get("success"):
            trace("CONDUCTOR", f"save_finding OK: {finding_type}/{status}")
            return f"SAVED: {finding_type}/{status} — {finding[:80]}"

        # Palace write failed — log error and fall back to JSONL
        trace("CONDUCTOR", f"save_finding palace error: {result}")
        findings_log = _ROOT / "research_findings.jsonl"
        import json as _json
        entry = {
            "finding": finding,
            "type": finding_type,
            "status": status,
            "evidence": evidence,
            "scope": scope,
            "expires_if": expires_if,
            "timestamp": __import__("time").time(),
        }
        with open(findings_log, "a") as f:
            f.write(_json.dumps(entry) + "\n")
        trace("CONDUCTOR", f"save_finding OK (local fallback): {finding_type}/{status}")
        return f"SAVED (local): {finding_type}/{status} — {finding[:80]}"

    @mcp.tool()
    async def search_findings(query: str, finding_type: str = "") -> str:
        """Search persistent memory for previously saved findings.

        Args:
            query: What to search for (e.g. "gap filter", "Tuesday PF").
            finding_type: Optional filter by type (observation, validated_finding, etc.).
        """
        room = finding_type if finding_type else None
        results = _palace_search(
            query=query,
            wing="research_findings",
            room=room,
            n_results=10,
        )
        if not results:
            return "No findings found."
        if len(results) == 1 and "error" in results[0]:
            return f"SEARCH ERROR: {results[0]['error']}"
        lines = []
        for r in results:
            text = r.get("text", "")[:300]
            room_name = r.get("room", "")
            dist = r.get("distance", "?")
            lines.append(f"[{room_name}] (dist={dist}) {text}")
        return "\n---\n".join(lines)

    @mcp.tool()
    async def memory_status() -> str:
        """Get an overview of the persistent memory palace."""
        info = _palace_status()
        if "error" in info:
            return f"STATUS ERROR: {info['error']}"
        return json.dumps(info, indent=2, default=str)

    @mcp.tool()
    async def list_past_theses() -> str:
        """List ALL previously proposed theses with their outcomes.

        Returns every thesis ever proposed — compiled (ran as experiment),
        rejected by validator, halted (needs code), or failed.
        Check this BEFORE proposing a new thesis to avoid duplicates.
        """
        jsonl_files = sorted(_ROOT.glob("*_autoresearch.jsonl"))
        entries = []
        for jf in jsonl_files:
            for line in jf.read_text().splitlines():
                try:
                    e = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if e.get("type") != "research_round":
                    continue
                entries.append({
                    "thesis_id": e.get("thesis_id", "unknown"),
                    "outcome": e.get("outcome", "unknown"),
                    "mechanism_dimension": e.get("mechanism_dimension", ""),
                    "config_changes": e.get("config_changes", {}),
                    "hypothesis": e.get("hypothesis", "")[:150],
                    "rejection_reason": e.get("rejection_reason", "")[:100],
                    "round": e.get("round"),
                    "job": e.get("job"),
                })
        if not entries:
            return "No previous theses found."
        return json.dumps(entries, indent=2)

    return mcp


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

    strategy_desc = STRATEGY_DESCRIPTIONS.get(family_name, f"Strategy family: {family_name}")

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
    outcome_lines = json.dumps(latest_outcome, indent=2) if latest_outcome else "(no results yet)"

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

    trace("CONDUCTOR", f"START round={research_round} trades={'YES' if trades_file else 'NO'}")
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
                if not got_assistant_text and hasattr(message, "result") and message.result:
                    result_text = str(message.result)
                # Capture conductor token usage
                if message.usage:
                    trace("CONDUCTOR", f"USAGE conductor raw_keys={list(message.usage.keys())} usage={message.usage}")
                    _accumulate_usage("conductor", message.usage, message.total_cost_usd)
                if message.model_usage:
                    trace("CONDUCTOR", f"USAGE conductor model_usage_keys={list(message.model_usage.keys())} model_usage={message.model_usage}")
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
            if t.get("thesis_id") and (t.get("config_changes") or t.get("requires_code_change")):
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
    return asyncio.run(run_research_conductor(
        trades_file, experiment_results, latest_outcome,
        research_round, family_name,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        rejection_feedback=rejection_feedback,
    ))
