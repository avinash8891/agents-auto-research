from __future__ import annotations

import subprocess
from typing import Any

import agent_definitions
import agent_infra
from agent_runners import _validate_output
from trace_logger import trace, trace_agent_prompt, trace_agent_response


async def _run_web_research_openai(
    prompt: str,
    retries: int = agent_definitions.MAX_RETRIES,
) -> dict[str, Any] | None:
    """Run web research using OpenAI Agents SDK via openai-oauth proxy."""
    from agents import Agent as OAIAgent
    from agents import ModelSettings as OAIModelSettings
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import WebSearchTool
    from agents.models.openai_provider import OpenAIProvider
    from openai import AsyncOpenAI

    agent_infra._ensure_oauth_proxy()

    client = AsyncOpenAI(api_key="unused", base_url=agent_infra._OAUTH_PROXY_URL)
    provider = OpenAIProvider(openai_client=client)

    agent = OAIAgent(
        name="web-researcher",
        instructions=agent_definitions.WEB_RESEARCHER_SYSTEM_PROMPT,
        tools=[WebSearchTool()],
        model="gpt-5.5",
    )

    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(
            "openai-web-researcher", prompt, agent_definitions.WEB_RESEARCHER_SYSTEM_PROMPT
        )
        trace(
            "OPENAI_AGENT",
            f"web-researcher attempt={attempt}/{retries} model=gpt-5.5 api=responses",
        )
        try:
            result = OAIRunner.run_streamed(
                agent,
                prompt,
                run_config=OAIRunConfig(
                    model_provider=provider,
                    model_settings=OAIModelSettings(store=False),
                    tracing_disabled=True,
                ),
            )
            async for _event in result.stream_events():
                pass

            output = result.final_output or ""
            parsed = agent_infra._parse_json(output)
            trace_agent_response("openai-web-researcher", trace_id, output, parsed)
            if parsed is not None and _validate_output("web-researcher", parsed):
                trace("OPENAI_AGENT", "web-researcher VALIDATED OK")
                return parsed
            trace("OPENAI_AGENT", "web-researcher validate FAILED")
            print(f"WEB_RESEARCH parse/validate failed (attempt {attempt}): {output[:200]}")
        except Exception as exc:
            trace("OPENAI_AGENT", f"web-researcher ERROR: {exc}")
            print(f"WEB_RESEARCH error (attempt {attempt}): {exc}")

        if attempt < retries:
            prompt = f"RETRY: Return valid JSON with 'findings' array and 'summary'. {prompt}"

    return None


DIAGNOSTIC_ANALYST_PROMPT = """You are a quantitative trading analyst. You receive:
1. A path to a CSV file containing raw trades from a backtest
2. The strategy config (what settings are applied)
3. The backtest results summary

Your job: load the raw trades, run your own analysis code, and find patterns
that explain the strategy's performance.

RAW TRADES CSV SCHEMA (one row per trade):
  entry_date    - datetime, when the trade was entered
  exit_date     - datetime, when the trade was exited
  direction     - str, "long" or "short"
  entry_price   - float, entry price (includes slippage)
  exit_price    - float, exit price (includes slippage)
  stop          - float, stop loss price
  target        - float, target price
  pnl_pct       - float, PnL as fraction of entry price (0.01 = 1%)
  exit_reason   - str, "stop_loss", "target", or "timeout"
  symbol        - str, ticker symbol (e.g. "AAPL")

WORKFLOW:
1. Use run_python to execute pandas analysis code. Use read_file to inspect the CSV
   if needed. The file path is given in the user prompt.
2. Perform AT MINIMUM these analyses:
   a. PF by entry hour (split 09:30 vs 09:35 vs later)
   b. PF by direction
   c. PF by exit_reason (counts + mean pnl)
   d. PF by day of week
   e. PF by year
   f. PF by symbol (top 10 best, top 10 worst by PF, min 5 trades each)
   g. Trade duration (winners vs losers in minutes)
   h. Realized R:R vs planned (avg win pnl / avg loss pnl)
   i. Max consecutive losses
   j. Stop distance analysis (stop dist from entry vs PF by quintile)
   k. Losing streak clustering by date range
3. Go BEYOND the minimum. Look for anything predefined slices miss.
   Examples: per-symbol PF variance, exit_reason by hour, seasonal patterns,
   hold duration vs PnL correlation, gap between planned target and realized gain.
4. Cross-reference findings with the strategy config provided. If config says
   short_only but you see long trades, flag it. If cutoff is 10:00 but trades
   appear after 10:00, flag it. Verify the config is correctly applied.

CRITICAL RULES:
- PF = sum(pnl_pct where pnl_pct > 0) / abs(sum(pnl_pct where pnl_pct <= 0))
- Only flag patterns with >100 trades per bucket
- Cite exact numbers from your code output
- Do NOT invent data
- Run ALL analysis in a SINGLE run_python call to save time

OUTPUT FORMAT:
After analysis, return ONLY a JSON object:
{
  "key_anomalies": [
    {
      "pattern": "one-line description",
      "numbers": "exact computed values",
      "sample_size": "trades in bucket",
      "suggested_exploit": "specific structural change",
      "confidence": "high/medium/low"
    }
  ],
  "overall_diagnosis": "2-3 sentence summary",
  "discovery_questions": ["questions needing more data"]
}

Be brutally honest."""


async def _run_diagnostic_analyst_openai(
    prompt: str,
    retries: int = agent_definitions.MAX_RETRIES,
) -> dict[str, Any] | None:
    """Run diagnostic analyst using OpenAI Agents SDK with local function tools."""
    import sys as _sys

    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import function_tool
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    agent_infra._ensure_oauth_proxy()

    @function_tool
    def read_file(file_path: str) -> str:
        """Read a file from the local filesystem and return its contents."""
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
        """Execute Python code locally and return stdout + stderr."""
        try:
            result = subprocess.run(
                [_sys.executable, "-c", code],
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

    client = AsyncOpenAI(api_key="unused", base_url=agent_infra._OAUTH_PROXY_URL)
    model = OpenAIChatCompletionsModel(model="gpt-5.5", openai_client=client)

    agent = OAIAgent(
        name="codex-diagnostic-analyst",
        instructions=DIAGNOSTIC_ANALYST_PROMPT,
        tools=[read_file, run_python],
        model=model,
    )

    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt("codex-analyst", prompt, DIAGNOSTIC_ANALYST_PROMPT)
        trace(
            "OPENAI_AGENT",
            f"codex-analyst attempt={attempt}/{retries} model=gpt-5.5 api=chatcmpl",
        )
        try:
            result = OAIRunner.run_streamed(
                agent,
                prompt,
                run_config=OAIRunConfig(
                    tracing_disabled=True,
                ),
            )
            async for _event in result.stream_events():
                pass

            output = result.final_output or ""
            parsed = agent_infra._parse_json(output)
            trace_agent_response("codex-analyst", trace_id, output, parsed)
            if parsed is not None and _validate_output("diagnostic-analyst", parsed):
                trace("OPENAI_AGENT", "codex-analyst VALIDATED OK")
                return parsed
            trace("OPENAI_AGENT", "codex-analyst validate FAILED")
            print(f"CODEX_ANALYST parse/validate failed (attempt {attempt}): {output[:200]}")
        except Exception as exc:
            trace("OPENAI_AGENT", f"codex-analyst ERROR: {exc}")
            print(f"CODEX_ANALYST error (attempt {attempt}): {exc}")

        if attempt < retries:
            prompt = (
                f"RETRY: Return valid JSON with key_anomalies array and overall_diagnosis. {prompt}"
            )

    return None
