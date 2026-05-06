from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from time import monotonic

from agent_sdk_token_usage import accumulate_agents_sdk_result_usage
from agent_token_usage import _accumulate_usage
from research_paths import (
    _CONDUCTOR_MODEL,
    _OAUTH_PROXY_URL,
    _ROOT,
    _ensure_oauth_proxy,
    _extract_runner_output_text,
    _get_openai_client,
    _parse_json,
)
from trace_sdk import (
    trace,
    trace_agent_prompt,
    trace_agent_response,
    trace_agent_tool_call,
    trace_agent_tool_result,
)
from web_research_cli import WebResearchCliError, run_codex_web_research

ANALYST_READ_FILE_MAX_CHARS = 12_000
ANALYST_RUN_PYTHON_MAX_CHARS = 12_000


def _compact_tool_output(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    suffix = (
        f"\n... (truncated tool output, {len(text)} total chars, "
        f"sha256={digest}; use targeted run_python/read_file if more detail is needed)"
    )
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix, True


def _analyst_data_root_guidance() -> str:
    data_root = os.environ.get("AUTORESEARCH_DATA_ROOT")
    if data_root:
        return (
            f"Market data root: AUTORESEARCH_DATA_ROOT={data_root}\n"
            f"Universe data lives under: {data_root}/universes/{{DATA_UNIVERSE}}/\n"
            "Typical wide-format files: open.parquet, high.parquet, low.parquet, "
            "close.parquet, volume.parquet.\n"
            f"Do NOT probe {str(_ROOT / 'data')} unless AUTORESEARCH_DATA_ROOT is unset."
        )
    fallback = str(_ROOT / "data")
    return (
        "AUTORESEARCH_DATA_ROOT is unset in this process.\n"
        f"Fallback repo-local data path, if present: {fallback}\n"
        "Before reading market data, check config/result artifacts for data_universe and "
        "validate that the chosen path exists."
    )


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
    current_trace_id = f"analyst-{focus_question[:40].replace(' ', '_')}"

    @function_tool
    def read_file(file_path: str, max_chars: int = ANALYST_READ_FILE_MAX_CHARS) -> str:
        """Read a file from the local filesystem.

        Args:
            file_path: Absolute path to the file to read.
            max_chars: Maximum characters to return. Defaults to a compact audit-safe limit.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        max_chars = max(1_000, min(int(max_chars or ANALYST_READ_FILE_MAX_CHARS), 20_000))
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "read_file",
            file_path,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        try:
            with open(file_path) as f:
                content = f.read()
            output, truncated = _compact_tool_output(content, max_chars=max_chars)
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "read_file",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def run_python(code: str) -> str:
        """Execute Python code locally and return stdout + stderr.

        Args:
            code: Python code to execute. Use print() for output.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "run_python",
            code,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
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
                status = "error"
                error_type = "NonZeroExit"
                output += f"\nEXIT CODE: {result.returncode}"
            output, truncated = _compact_tool_output(output, max_chars=ANALYST_RUN_PYTHON_MAX_CHARS)
        except subprocess.TimeoutExpired:
            status = "error"
            error_type = "TimeoutExpired"
            output = "ERROR: Code execution timed out (60s limit)"
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "run_python",
            output,
            status=status,
            error_type=error_type,
            truncated=truncated,
            duration_ms=int((monotonic() - started) * 1000),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    analyst_prompt = f"""You are a quantitative trading analyst. You receive:
1. A path to a CSV file containing raw trades from a backtest
2. A FOCUS QUESTION from the research conductor
3. A strategy_events.parquet with every signal the strategy considered (accepted AND rejected)
4. A diagnostics.json with event counts and rejection breakdown
5. Access to RAW OHLCV DATA through the configured data root:
{_analyst_data_root_guidance()}
   You can load price history to compute market context (ATR, volume, gaps, etc.).

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
   AUTORESEARCH_DATA_ROOT/universes/{{DATA_UNIVERSE}}/ and compute what you need.
4. Focus effort on the FOCUS QUESTION. Go deep, not wide.
5. When you find a pattern, quantify it with exact numbers and sample sizes.
6. Each run_python call is stateless. Put imports, path definitions, file reads,
   and calculations in the same run_python call. Do not rely on variables from
   earlier tool calls.

CRITICAL RULES:
- PF = sum(pnl_pct where pnl_pct > 0) / abs(sum(pnl_pct where pnl_pct <= 0))
- Only flag patterns with >50 trades per bucket
- Cite exact numbers
- Do NOT invent data
- Do NOT repeat analyses the focus question doesn't ask for
- If the focus question asks about market structure, USE the raw OHLCV data
- Do NOT read large source/data files into the chat unless strictly necessary.
  Prefer targeted run_python summaries and print compact tables only.

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
    current_trace_id = trace_agent_prompt(
        "analyst",
        user_prompt,
        analyst_prompt,
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )

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
        output = _extract_runner_output_text(result)
        accumulate_agents_sdk_result_usage(
            "analyst",
            result,
            provider="openai",
            model=_CONDUCTOR_MODEL,
            input_text=f"{analyst_prompt}\n\n{user_prompt}",
            output_text=output,
        )
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
                current_trace_id,
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
    trace_id = trace_agent_prompt(
        "web-researcher",
        user_prompt,
        web_prompt,
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )

    trace(
        "CONDUCTOR",
        f"web_search dispatch query='{query[:80]}' api=codex_cli_web_search",
        model_provider="openai",
        model_name=_CONDUCTOR_MODEL,
    )
    try:
        output, metadata = await asyncio.to_thread(
            run_codex_web_research,
            user_prompt,
            instructions=web_prompt,
            model=_CONDUCTOR_MODEL,
        )
        usage = metadata.get("usage")
        if isinstance(usage, dict):
            usage = {**usage, "usage_source": metadata.get("usage_source", "")}
            _accumulate_usage("web_researcher", usage, provider="openai", model=_CONDUCTOR_MODEL)
        trace(
            "CONDUCTOR",
            (
                "web_search codex_cli completed "
                f"exit={metadata.get('exit_code')} output_len={metadata.get('output_len')}"
            ),
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
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
                trace_id,
                output,
                parsed,
                model_provider="openai",
                model_name=_CONDUCTOR_MODEL,
            )
            return json.dumps(parsed, indent=2)
        trace(
            "CONDUCTOR",
            f"web_search parse failed type={type(output).__name__} len={len(output)} excerpt={output[:200]!r}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: could not parse: {output[:500]}"
    except WebResearchCliError as exc:
        accumulate_agents_sdk_result_usage(
            "web_researcher", None, provider="openai", model=_CONDUCTOR_MODEL
        )
        trace(
            "CONDUCTOR",
            f"web_search ERROR: {exc}",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: {exc}"
