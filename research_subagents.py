from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from functools import partial

from agent_token_usage import _accumulate_result_usage
from research_paths import (
    _CONDUCTOR_MODEL,
    _OAUTH_PROXY_URL,
    _ROOT,
    _ensure_oauth_proxy,
    _get_openai_client,
    _parse_json,
)
from trace_sdk import trace, trace_agent_response


async def _call_analyst(
    trades_file: str,
    focus_question: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
) -> str:
    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import function_tool
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
                capture_output=True,
                text=True,
                timeout=60,
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

    client = _get_openai_client(_OAUTH_PROXY_URL)
    model = OpenAIChatCompletionsModel(model=_CONDUCTOR_MODEL, openai_client=client)
    agent = OAIAgent(
        name="codex-diagnostic-analyst",
        instructions=analyst_prompt,
        tools=[read_file, run_python],
        model=model,
    )

    trace(
        "CONDUCTOR",
        f"analyst dispatch focus='{focus_question[:80]}'",
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
    try:
        result = OAIRunner.run_streamed(
            agent,
            user_prompt,
            run_config=OAIRunConfig(tracing_disabled=True),
            max_turns=25,
        )
        async for _ in result.stream_events():
            pass
        _accumulate_result_usage("analyst", result, provider="openai", model=_CONDUCTOR_MODEL)
        output = result.final_output or ""
        parsed = _parse_json(output)
        if parsed:
            n_anomalies = len(parsed.get("key_anomalies", []))
            trace(
                "CONDUCTOR",
                f"analyst OK anomalies={n_anomalies}",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            trace_agent_response(
                "analyst",
                f"analyst-{focus_question[:40].replace(' ', '_')}",
                output,
                parsed,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return json.dumps(parsed, indent=2)
        trace(
            "CONDUCTOR",
            f"analyst parse failed: {output[:200]}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"ANALYST ERROR: could not parse response: {output[:500]}"
    except Exception as exc:
        trace(
            "CONDUCTOR",
            f"analyst ERROR: {exc}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"ANALYST ERROR: {exc}"


async def _call_web_researcher(query: str, context: str) -> str:
    from agents import Agent as OAIAgent
    from agents import ModelSettings as OAIModelSettings
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import WebSearchTool
    from agents.models.openai_responses import OpenAIResponsesModel

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

    client = _get_openai_client(_OAUTH_PROXY_URL)
    model = OpenAIResponsesModel(model=_CONDUCTOR_MODEL, openai_client=client)
    agent = OAIAgent(
        name="web-researcher",
        instructions=web_prompt,
        tools=[WebSearchTool()],
        model=model,
    )

    trace(
        "CONDUCTOR",
        f"web_search dispatch query='{query[:80]}'",
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
    try:
        result = await asyncio.to_thread(
            partial(
                OAIRunner.run_sync,
                agent,
                user_prompt,
                run_config=OAIRunConfig(
                    model_settings=OAIModelSettings(store=False),
                    tracing_disabled=True,
                ),
            )
        )
        _accumulate_result_usage(
            "web_researcher", result, provider="openai", model=_CONDUCTOR_MODEL
        )
        output = getattr(result, "final_output", "") or ""
        parsed = _parse_json(output)
        if parsed:
            n_findings = len(parsed.get("findings", []))
            trace(
                "CONDUCTOR",
                f"web_search OK findings={n_findings}",
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            trace_agent_response(
                "web-researcher",
                f"web-{query[:40].replace(' ', '_')}",
                output,
                parsed,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return json.dumps(parsed, indent=2)
        trace(
            "CONDUCTOR",
            f"web_search parse failed: {output[:200]}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: could not parse: {output[:500]}"
    except Exception as exc:
        trace(
            "CONDUCTOR",
            f"web_search ERROR: {exc}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: {exc}"
