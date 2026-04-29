"""Agent orchestrator using Claude Agent SDK + OpenAI Agents SDK.

Three stateless sub-agents:
  1. diagnostic_analyst (Claude SDK) u2014 reads trade breakdowns, finds anomalies
  2. web_researcher (OpenAI Agents SDK + Codex auth) u2014 web search for evidence
  3. research_agent (Claude SDK) u2014 proposes hypotheses

Agents are stateless workers. The orchestrator owns memory:
  - Reads from mempalace BEFORE each agent run (via subprocess CLI)
  - Injects context into the agent's prompt
  - Validates the agent's JSON output
  - Writes validated results to mempalace AFTER each run
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

import agent_definitions
import agent_infra
import agent_memory
from trace_logger import (
    trace,
    trace_agent_prompt,
    trace_agent_response,
)

# ---------------------------------------------------------------------------
# Mempalace CLI u2014 orchestrator reads/writes memory on behalf of agents
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI-based agent runner (for tools not available in SDK subprocesses)
# ---------------------------------------------------------------------------


def _run_cli_agent(
    name: str,
    system_prompt: str,
    user_prompt: str,
    retries: int = 2,
    timeout: int = agent_infra.CLI_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Run an agent via `claude --print` CLI subprocess.

    Used for agents that need deferred tools like WebSearch, which are
    not available in SDK-spawned subprocesses.
    """
    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(f"cli-{name}", user_prompt, system_prompt)
        trace("CLI_AGENT", f"{name} attempt={attempt}/{retries} timeout={timeout}s")
        try:
            result = subprocess.run(
                [
                    "claude",
                    "-p",
                    user_prompt,
                    "--model",
                    "sonnet",
                    "--system-prompt",
                    system_prompt,
                    "--max-turns",
                    "10",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                stderr = result.stderr.strip()[:200] if result.stderr else ""
                trace("CLI_AGENT", f"{name} exit={result.returncode}", {"stderr": stderr})
                print(f"CLI_AGENT {name} exit={result.returncode} (attempt {attempt}): {stderr}")
                if attempt < retries:
                    continue
                return None

            parsed = agent_infra._parse_json(output)
            trace_agent_response(f"cli-{name}", trace_id, output, parsed)
            if parsed is not None:
                if _validate_output(name, parsed):
                    trace("CLI_AGENT", f"{name} VALIDATED OK")
                    return parsed
                trace("CLI_AGENT", f"{name} quality check failed")
                print(f"CLI_AGENT {name} quality check failed (attempt {attempt})")
            else:
                print(f"CLI_AGENT {name} parse failed (attempt {attempt}): {output[:200]}")

        except subprocess.TimeoutExpired:
            trace("CLI_AGENT", f"{name} TIMEOUT after {timeout}s")
            print(f"CLI_AGENT {name} timeout after {timeout}s (attempt {attempt})")
        except Exception as exc:
            trace("CLI_AGENT", f"{name} ERROR: {exc}")
            print(f"CLI_AGENT {name} error (attempt {attempt}): {exc}")

        if attempt < retries:
            user_prompt = (
                f"RETRY: Your previous response could not be parsed as valid JSON "
                f"or was missing required fields. {user_prompt}"
            )

    return None


# ---------------------------------------------------------------------------
# Sub-agent definitions u2014 stateless, no memory access
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Public API u2014 orchestrator handles memory on behalf of agents
# ---------------------------------------------------------------------------


async def run_diagnostic_analysis(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None = None,
    baseline_results: dict[str, Any] | None = None,
    family: str = "ema",
) -> dict[str, Any] | None:
    """Run the diagnostic analyst on raw trades. Orchestrator manages memory."""
    trace(
        "ORCHESTRATOR",
        f"run_diagnostic_analysis config={config} metric={metric} trades={trades_file}",
    )
    # READ: get prior diagnostics from mempalace
    prior = agent_memory._mempalace_search("diagnostic anomalies patterns", wing="autoresearch")

    config_str = json.dumps(config_contents, indent=2) if config_contents else "unknown"
    results_str = json.dumps(
        {
            k: v
            for k, v in (baseline_results or {}).items()
            if k != "diagnostics" and k != "trades_file"
        },
        indent=2,
    )

    prompt = (
        f"Analyze the raw trades for config '{config}' "
        f"(strategy family: {family}).\n\n"
        f"STRATEGY CONFIG (what settings are applied):\n{config_str}\n\n"
        f"BACKTEST RESULTS:\n{results_str}\n\n"
        f"RAW TRADES FILE: {trades_file}\n\n"
        f"PRIOR DIAGNOSTIC FINDINGS (from earlier runs):\n{prior}\n\n"
        f"Load the CSV file and perform your analysis. "
        f"The file contains one row per trade with the schema described in your instructions."
    )
    # Use Codex SDK analyst (gpt-5.5 + local FunctionTools) instead of
    # Claude SDK analyst (which spawns a crash-prone CLI subprocess).
    result = await _run_diagnostic_analyst_openai(prompt)

    # WRITE: persist validated result to mempalace
    if result:
        summary = result.get("overall_diagnosis", "")
        anomalies = result.get("key_anomalies", [])
        content = (
            f"DIAGNOSTIC ANALYSIS: {config} PF={metric}\n"
            f"DIAGNOSIS: {summary}\n"
            f"ANOMALIES ({len(anomalies)}):\n"
            + "\n".join(
                f"  [{a.get('confidence', '?')}] {a.get('pattern', '')} "
                f"-> {a.get('suggested_exploit', '')}"
                for a in anomalies
            )
        )
        agent_memory._mempalace_write("autoresearch", f"{family}-diagnostics", content)
        agent_memory._mempalace_diary(
            "diagnostic-analyst",
            f"{family}-analysis",
            f"CONFIG:{config}|PF:{metric}|ANOMALIES:{len(anomalies)}|{summary[:100]}",
        )

    return result


async def run_web_research(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int = 1,
    family: str = "ema",
) -> dict[str, Any] | None:
    """Run the web researcher via Claude SDK (builds prompt, delegates to OpenAI for search)."""
    trace(
        "ORCHESTRATOR",
        f"run_web_research round={research_round} family={family} strategy={strategy_label}",
    )
    # READ: get prior web findings
    prior = agent_memory._mempalace_search("web research findings strategy", wing="autoresearch")

    user_prompt = (
        f"Strategy: {strategy_label}, round {research_round}\n\n"
        f"DIAGNOSTIC INSIGHTS:\n{analyst_brief}\n\n"
        f"RESULTS SUMMARY:\n{result_summary}\n\n"
        f"PRIOR WEB RESEARCH (do not repeat):\n{prior}\n\n"
        f"Search for external evidence and ideas relevant to these findings."
    )
    result = await _run_web_research_openai(user_prompt)

    # WRITE: persist validated result
    if result:
        findings = result.get("findings", [])
        summary = result.get("summary", "")
        gaps = result.get("confidence_and_gaps", "")
        content = (
            f"WEB RESEARCH round={research_round}: {summary}\n"
            + "\n".join(
                f"  [{f.get('label', '?')}/{f.get('source_quality', '?')}] "
                f"{f.get('topic', '')}: {f.get('actionable_idea', '')}"
                for f in findings
            )
            + (f"\nGAPS: {gaps}" if gaps else "")
        )
        agent_memory._mempalace_write("autoresearch", f"{family}-web-research", content)
        agent_memory._mempalace_diary(
            "web-researcher",
            f"{family}-research",
            f"ROUND:{research_round}|FINDINGS:{len(findings)}|{summary[:100]}",
        )

    return result


async def run_research_agent(
    context: dict[str, Any],
    family_name: str = "orb",
) -> dict[str, Any] | None:
    """Run the research agent. Orchestrator manages memory."""
    trace(
        "ORCHESTRATOR",
        f"run_research_agent family={family_name} round={context.get('research_round')}",
    )
    from family_research import get_family_research_spec

    spec = get_family_research_spec(family_name)
    best = context.get("current_best", {})
    history = context.get("result_history", "")
    research_round = context.get("research_round", 1)
    analyst_brief = context.get("analyst_brief", "")
    web_findings = context.get("web_findings", "")

    # READ: get prior theses from mempalace
    prior_theses = agent_memory._mempalace_search("research thesis proposed", wing="autoresearch")

    best_config = best.get("config_contents", {})
    best_config_str = json.dumps(best_config, indent=2) if best_config else "unknown"

    prompt = (
        f"Research round: {research_round}\n"
        f"Current best: {best.get('config', 'none')} at metric={best.get('metric', 'unknown')}\n\n"
        f"CURRENT BEST CONFIG (these settings are ALREADY applied — do NOT re-propose them):\n{best_config_str}\n\n"
        f"FULL EXPERIMENT HISTORY:\n{history}\n\n"
        f"DIAGNOSTIC ANALYST INSIGHTS:\n{analyst_brief}\n\n"
        f"WEB RESEARCH FINDINGS:\n{web_findings}\n\n"
        f"PRIOR THESES (from memory):\n{prior_theses}\n\n"
        f"Based on all the above, propose exactly ONE next thesis that CHANGES something from the current best config."
    )

    research_def = agent_definitions._research_agent(
        strategy_label=spec.strategy_label,
        config_rules=spec.config_rules,
        config_schema=spec.config_schema,
        thesis_json_hint=spec.thesis_json_hint,
    )

    # Main query with supervision (timeout prevents hanging on rate limits)
    for attempt in range(1, agent_definitions.MAX_RETRIES + 1):
        trace_id = trace_agent_prompt("sdk-research-agent", prompt, research_def.prompt)
        trace(
            "AGENT_SDK",
            f"research_agent attempt={attempt}/{agent_definitions.MAX_RETRIES} model={research_def.model} round={research_round}",
        )
        result_text = ""
        try:
            deadline = asyncio.get_event_loop().time() + agent_infra.SDK_TIMEOUT_SECONDS
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    system_prompt=research_def.prompt,
                    model=research_def.model,
                    allowed_tools=[],
                    permission_mode="bypassPermissions",
                    max_turns=research_def.maxTurns or agent_definitions.MAX_TURNS_RESEARCH,
                ),
            ):
                if asyncio.get_event_loop().time() > deadline:
                    raise asyncio.TimeoutError()
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            result_text += block.text
                elif isinstance(message, ResultMessage):
                    # Only use ResultMessage as fallback — it duplicates
                    # AssistantMessage text when both are emitted.
                    if not result_text and hasattr(message, "result") and message.result:
                        result_text = str(message.result)
        except asyncio.TimeoutError:
            trace("AGENT_SDK", f"research_agent TIMEOUT after {agent_infra.SDK_TIMEOUT_SECONDS}s")
            print(f"AGENT_SDK research_agent timeout (attempt {attempt})")
            if attempt < agent_definitions.MAX_RETRIES:
                continue
            return None
        except Exception as exc:
            trace("AGENT_SDK", f"research_agent ERROR: {exc}")
            print(f"AGENT_SDK research_agent error (attempt {attempt}): {exc}")
            if attempt < agent_definitions.MAX_RETRIES:
                continue
            return None

        parsed = agent_infra._parse_json(result_text)
        trace_agent_response("sdk-research-agent", trace_id, result_text, parsed)
        if parsed and _validate_output("research-agent", parsed):
            # WRITE: persist validated thesis to mempalace
            theses = parsed.get("suggested_theses", [])
            if theses:
                thesis = theses[0]
                content = (
                    f"THESIS round={research_round}: {thesis.get('thesis_id', '?')}\n"
                    f"HYPOTHESIS: {thesis.get('hypothesis', '')}\n"
                    f"MECHANISM: {thesis.get('mechanism', '')}\n"
                    f"REASONING: {parsed.get('reasoning', '')}"
                )
                agent_memory._mempalace_write("autoresearch", f"{family_name}-theses", content)
                agent_memory._mempalace_diary(
                    "research-agent",
                    f"{family_name}-thesis",
                    f"ROUND:{research_round}|THESIS:{thesis.get('thesis_id', '?')}|"
                    f"{parsed.get('reasoning', '')[:100]}",
                )
            return parsed

        print(f"AGENT_SDK research_agent quality/parse failed (attempt {attempt})")
        if attempt < agent_definitions.MAX_RETRIES:
            prompt = (
                f"RETRY: Your previous response could not be parsed or was missing "
                f"'suggested_theses'. Please return valid JSON. {prompt}"
            )

    return None


# ---------------------------------------------------------------------------
# Result formatting (moved from research_subagent.py)
# ---------------------------------------------------------------------------


def format_result_history(results: list[dict[str, Any]]) -> str:
    """Format experiment results into a readable history for prompts."""
    if not results:
        return "No experiments run yet."
    lines: list[str] = []
    for r in results:
        config = r.get("config", "unknown")
        metric = r.get("metric", "?")
        status = r.get("status", "?")
        # Show thesis name and config changes so the conductor knows
        # exactly what was tried and can avoid re-proposing failed theses.
        label = config
        thesis_id = r.get("thesis_id", "")
        config_changes = r.get("config_changes", {})
        if thesis_id:
            if config_changes:
                changes_str = ", ".join(f"{k}={v}" for k, v in config_changes.items())
                label = f"{thesis_id} ({changes_str})"
            else:
                label = thesis_id
        parts = [f"metric={metric}", f"status={status}"]
        if r.get("trade_count"):
            parts.append(f"trades={r['trade_count']}")
        if r.get("profit_factor"):
            parts.append(f"PF={r['profit_factor']}")
        if r.get("max_drawdown"):
            parts.append(f"maxDD={r['max_drawdown']}")
        if r.get("avg_sharpe_across_windows"):
            parts.append(f"sharpe={r['avg_sharpe_across_windows']}")
        if r.get("exit_mix"):
            parts.append(f"exit_mix={r['exit_mix']}")
        if r.get("regime_expectancy"):
            parts.append(f"regime={r['regime_expectancy']}")
        lines.append(f"  - {label}: {' | '.join(parts)}")
        if r.get("why"):
            lines.append(f"    WHY: {r['why']}")
        if r.get("regime_insight"):
            lines.append(f"    REGIME: {r['regime_insight']}")
        if r.get("next_thesis_suggestion"):
            lines.append(f"    NEXT: {r['next_thesis_suggestion']}")
        if r.get("insight_brief"):
            lines.append("    ANALYST INSIGHTS:")
            for bl in r["insight_brief"].split("\n")[:8]:
                lines.append(f"      {bl}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Web research via OpenAI Agents SDK + openai-oauth proxy (Codex subscription)
# ---------------------------------------------------------------------------


async def _run_web_research_openai(
    prompt: str,
    retries: int = agent_definitions.MAX_RETRIES,
) -> dict[str, Any] | None:
    """Run web research using OpenAI Agents SDK via openai-oauth proxy.

    Uses Responses API (not Chat Completions) because WebSearchTool is a
    hosted tool that requires it. store=False is fine here because web search
    is single-turn (no local function tools needing multi-turn).
    """
    from agents import Agent as OAIAgent
    from agents import ModelSettings as OAIModelSettings
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import WebSearchTool
    from agents.models.openai_provider import OpenAIProvider
    from openai import AsyncOpenAI

    agent_infra._ensure_oauth_proxy()

    client = AsyncOpenAI(
        api_key="unused",  # proxy handles auth
        base_url=agent_infra._OAUTH_PROXY_URL,
    )
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


# ---------------------------------------------------------------------------
# Diagnostic analyst via OpenAI Agents SDK (Codex + local function tools)
# ---------------------------------------------------------------------------

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
    """Run diagnostic analyst using OpenAI Agents SDK with local function tools.

    Uses Chat Completions API with FunctionTools (read_file + run_python)
    instead of the Claude SDK analyst that spawns a crash-prone CLI subprocess.
    """
    import sys as _sys

    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import function_tool
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
    from openai import AsyncOpenAI

    agent_infra._ensure_oauth_proxy()

    # --- Local function tools ---
    @function_tool
    def read_file(file_path: str) -> str:
        """Read a file from the local filesystem and return its contents.

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
            "OPENAI_AGENT", f"codex-analyst attempt={attempt}/{retries} model=gpt-5.5 api=chatcmpl"
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


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------


def analyze_diagnostics_sync(
    trades_file: str,
    config: str,
    metric: float,
    config_contents: dict[str, Any] | None = None,
    baseline_results: dict[str, Any] | None = None,
    family: str = "ema",
) -> dict[str, Any] | None:
    return asyncio.run(
        run_diagnostic_analysis(
            trades_file,
            config,
            metric,
            config_contents,
            baseline_results,
            family,
        )
    )


def run_web_research_sync(
    strategy_label: str,
    analyst_brief: str,
    result_summary: str,
    research_round: int = 1,
    family: str = "ema",
) -> dict[str, Any] | None:
    return asyncio.run(
        run_web_research(
            strategy_label,
            analyst_brief,
            result_summary,
            research_round,
            family,
        )
    )


def run_research_agent_sync(
    context: dict[str, Any],
    family_name: str = "orb",
) -> dict[str, Any] | None:
    return asyncio.run(run_research_agent(context, family_name))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _query_with_timeout(
    prompt: str,
    agent_def: AgentDefinition,
    timeout: int,
):
    """Async generator: yield text chunks from the agent with a deadline."""
    deadline = asyncio.get_event_loop().time() + timeout
    got_assistant_text = False
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt=agent_def.prompt,
            model=agent_def.model,
            allowed_tools=list(agent_def.tools or []),
            permission_mode="bypassPermissions",
            max_turns=agent_def.maxTurns or 10,
        ),
    ):
        if asyncio.get_event_loop().time() > deadline:
            raise asyncio.TimeoutError()
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    got_assistant_text = True
                    yield block.text
        elif isinstance(message, ResultMessage):
            # Only use ResultMessage as fallback u2014 it duplicates
            # AssistantMessage text when both are emitted.
            if not got_assistant_text and hasattr(message, "result") and message.result:
                yield str(message.result)


async def _run_single_agent(
    name: str,
    prompt: str,
    agent_def: AgentDefinition,
    retries: int = agent_definitions.MAX_RETRIES,
    timeout: int = agent_infra.SDK_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    """Run a single agent directly with its system prompt.

    Uses query() with the agent's prompt as system_prompt, so the model
    responds directly as the agent — no outer wrapper that summarizes.
    """
    for attempt in range(1, retries + 1):
        trace_id = trace_agent_prompt(f"sdk-{name}", prompt, agent_def.prompt)
        trace(
            "AGENT_SDK",
            f"{name} attempt={attempt}/{retries} model={agent_def.model} tools={agent_def.tools}",
        )
        result_text = ""
        try:
            async for chunk in _query_with_timeout(prompt, agent_def, timeout):
                result_text += chunk
        except asyncio.TimeoutError:
            trace("AGENT_SDK", f"{name} TIMEOUT after {timeout}s")
            print(f"AGENT_SDK {name} timeout after {timeout}s (attempt {attempt})")
            if attempt < retries:
                continue
            return None
        except Exception as exc:
            trace("AGENT_SDK", f"{name} ERROR: {exc}")
            print(f"AGENT_SDK {name} error (attempt {attempt}): {exc}")
            if attempt < retries:
                continue
            return None

        parsed = agent_infra._parse_json(result_text)
        trace_agent_response(f"sdk-{name}", trace_id, result_text, parsed)
        if parsed is not None:
            if _validate_output(name, parsed):
                trace("AGENT_SDK", f"{name} VALIDATED OK")
                return parsed
            trace("AGENT_SDK", f"{name} quality check FAILED")
            print(f"AGENT_SDK {name} quality check failed (attempt {attempt})")
        else:
            trace("AGENT_SDK", f"{name} parse FAILED")
            print(f"AGENT_SDK {name} parse failed (attempt {attempt})")

        if attempt < retries:
            prompt = (
                f"RETRY: Your previous response could not be parsed as valid JSON "
                f"or was missing required fields. {prompt}"
            )

    return None


def _validate_output(agent_name: str, parsed: dict[str, Any]) -> bool:
    """Quality check: ensure agent output has required fields and structure."""
    if agent_name == "diagnostic-analyst":
        anomalies = parsed.get("key_anomalies")
        diagnosis = parsed.get("overall_diagnosis")
        if not isinstance(anomalies, list) or not isinstance(diagnosis, str):
            return False
        if not anomalies or not diagnosis:
            return False
        # Each anomaly must have pattern + numbers
        for a in anomalies:
            if not isinstance(a, dict):
                return False
            if not a.get("pattern") or not a.get("numbers"):
                return False
        return True

    elif agent_name == "web-researcher":
        findings = parsed.get("findings")
        if not isinstance(findings, list) or not findings:
            return False
        # Each finding must have topic + finding + label
        for f in findings:
            if not isinstance(f, dict):
                return False
            if not f.get("topic") or not f.get("finding"):
                return False
        return bool(parsed.get("summary"))

    elif agent_name == "research-agent":
        # should_stop=true is valid with empty theses
        if parsed.get("should_stop") is True:
            return True
        theses = parsed.get("suggested_theses")
        if not isinstance(theses, list) or not theses:
            return False
        # The single thesis must have thesis_id + config_changes or requires_code_change
        t = theses[0]
        if not isinstance(t, dict):
            return False
        if not t.get("thesis_id") or not t.get("hypothesis"):
            return False
        # Must have non-empty config_changes OR requires_code_change
        has_changes = bool(t.get("config_changes"))
        needs_code = t.get("requires_code_change", False)
        if not has_changes and not needs_code:
            return False
        return True

    return True


def format_insight_brief(analysis: dict[str, Any]) -> str:
    """Compress analyst findings into compact brief for research agent."""
    if not analysis:
        return "(diagnostic analysis unavailable)"

    lines: list[str] = []
    overall = analysis.get("overall_diagnosis", "")
    if overall:
        lines.append(f"DIAGNOSIS: {overall}")
        lines.append("")

    anomalies = analysis.get("key_anomalies", [])
    if anomalies:
        lines.append("ANOMALIES:")
        for a in anomalies:
            conf = a.get("confidence", "low")
            if conf == "low":
                continue
            lines.append(f"  [{conf.upper()}] {a.get('pattern', '')}")
            lines.append(f"    Data: {a.get('numbers', '')} (n={a.get('sample_size', '')})")
            lines.append(f"    Exploit: {a.get('suggested_exploit', '')}")

    questions = analysis.get("discovery_questions", [])
    if questions:
        lines.append("")
        lines.append("OPEN QUESTIONS:")
        for q in questions[:3]:
            lines.append(f"  - {q}")

    return "\n".join(lines)


def format_web_findings(research: dict[str, Any]) -> str:
    """Compress web research into compact block for research agent."""
    if not research:
        return "(no web research available)"

    lines: list[str] = []
    summary = research.get("summary", "")
    if summary:
        lines.append(f"WEB RESEARCH SUMMARY: {summary}")
        lines.append("")

    findings = research.get("findings", [])
    if findings:
        lines.append("EXTERNAL FINDINGS:")
        for f in findings:
            label = f.get("label", "")
            quality = f.get("source_quality", "")
            topic = f.get("topic", "")
            finding = f.get("finding", "")
            source = f.get("source", "")
            idea = f.get("actionable_idea", "")
            tag = f"[{label}" + (f"/{quality}" if quality else "") + "]"
            lines.append(f"  {tag} {topic}: {finding}")
            if source:
                lines.append(f"    Source: {source}")
            if idea:
                lines.append(f"    Idea: {idea}")

    gaps = research.get("confidence_and_gaps", "")
    if gaps:
        lines.append("")
        lines.append(f"CONFIDENCE & GAPS: {gaps}")

    return "\n".join(lines)
