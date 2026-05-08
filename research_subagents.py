from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
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
ANALYST_SOURCE_EXCLUDED_FILES = {
    "__init__.py",
    "contract.py",
    "defaults.py",
    "prompt.py",
    "research.py",
    "validate.py",
}


def _resolve_tool_max_chars(value: object, *, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1_000, min(parsed, 20_000))


def _compact_tool_output(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    suffix = (
        f"\n... (truncated tool output, {len(text)} total chars, "
        f"sha256={digest}; use targeted run_python or typed artifact tools if more detail is needed)"
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
    return (
        "AUTORESEARCH_DATA_ROOT is unset in this process.\n"
        "Raw market data is unavailable unless the market data manifest below exposes "
        "an exact existing universe_path.\n"
        "Do NOT probe repo-local data directories or guess paths such as "
        f"{str(_ROOT / 'data')}."
    )


def _load_runtime_config_for_artifact(artifact_file: str) -> tuple[Path | None, dict]:
    artifact_path = Path(artifact_file).expanduser()
    config_hash = artifact_path.parent.name
    if not config_hash:
        return None, {}
    for ancestor in artifact_path.parents:
        candidate = ancestor / "experiments" / config_hash / "runtime_config.json"
        if not candidate.exists():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return candidate, {}
        runtime = raw.get("runtime_config") if isinstance(raw, dict) else None
        if isinstance(runtime, dict):
            return candidate, runtime
        return candidate, raw if isinstance(raw, dict) else {}
    return None, {}


def _market_data_manifest(trades_file: str) -> str:
    config_path, runtime_config = _load_runtime_config_for_artifact(trades_file)
    data_universe = runtime_config.get("data_universe")
    provenance = runtime_config.get("data_provenance")
    data_root = os.environ.get("AUTORESEARCH_DATA_ROOT")

    lines = ["MARKET DATA MANIFEST:"]
    if config_path is not None:
        lines.append(f"- runtime_config: {config_path}")
    else:
        lines.append("- runtime_config: not found from artifact path")
    lines.append(f"- data_universe: {data_universe or 'unknown'}")

    universe_path = ""
    manifest_path = ""
    if isinstance(provenance, dict):
        universe_path = str(provenance.get("universe_path") or "")
        manifest_path = str(provenance.get("manifest_path") or "")
    if not universe_path and data_root and data_universe:
        universe_path = str(Path(data_root) / "universes" / str(data_universe))
    if not manifest_path and universe_path:
        manifest_path = str(Path(universe_path) / "manifest.json")
    if universe_path:
        lines.append(f"- universe_path: {universe_path}")
    if manifest_path:
        lines.append(f"- manifest_path: {manifest_path}")

    if universe_path:
        for label in ("open", "high", "low", "close", "volume"):
            path = Path(universe_path) / f"{label}.parquet"
            status = "exists" if path.exists() else "expected"
            lines.append(f"- {label}: {path} ({status})")
        lines.append(
            "- Do NOT run recursive filesystem discovery such as glob('/root/**') "
            "or searches for open.parquet; use the paths above."
        )
    else:
        lines.append("- No exact universe path resolved.")
        lines.append(
            "- Raw market data is unavailable for this analyst call; use trades.csv, "
            "strategy_events.parquet, diagnostics.json, and source code only."
        )
        lines.append(
            "- Do NOT probe repo-local data directories, recursive filesystem paths, "
            "or guessed locations for OHLCV files."
        )
    return "\n".join(lines)


def _family_name_from_artifact_path(path: str) -> str:
    for part in Path(path).expanduser().parts:
        if part.endswith("_autoresearch-runs"):
            return part.removesuffix("_autoresearch-runs")
    return ""


def _discover_strategy_source_files(family_name: str) -> dict[str, str]:
    if not family_name:
        return {}
    strategy_dir = _ROOT / "strategies" / family_name
    if not strategy_dir.exists():
        return {}
    sources: dict[str, str] = {}
    for path in sorted(strategy_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name in ANALYST_SOURCE_EXCLUDED_FILES:
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            continue
        key = path.relative_to(strategy_dir).as_posix()
        sources[key] = str(path)
    return sources


def _analysis_manifest(
    *,
    trades_file: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    family_name: str = "",
) -> dict[str, object]:
    config_path, runtime_config = _load_runtime_config_for_artifact(trades_file)
    resolved_family = (
        family_name
        or str(runtime_config.get("strategy_family") or "")
        or str(runtime_config.get("family") or "")
        or _family_name_from_artifact_path(trades_file)
    )
    artifacts = {
        "trades_csv": trades_file,
    }
    if strategy_events_file:
        artifacts["strategy_events_parquet"] = strategy_events_file
    if diagnostics_file:
        artifacts["diagnostics_json"] = diagnostics_file
    if config_path is not None:
        artifacts["runtime_config_json"] = str(config_path)

    data_files: dict[str, str] = {}
    data_universe = runtime_config.get("data_universe")
    provenance = runtime_config.get("data_provenance")
    universe_path = ""
    if isinstance(provenance, dict):
        universe_path = str(provenance.get("universe_path") or "")
    data_root = os.environ.get("AUTORESEARCH_DATA_ROOT")
    if not universe_path and data_root and data_universe:
        universe_path = str(Path(data_root) / "universes" / str(data_universe))
    if universe_path:
        for label in ("open", "high", "low", "close", "volume"):
            path = Path(universe_path) / f"{label}.parquet"
            if path.exists():
                data_files[label] = str(path)

    return {
        "family_name": resolved_family,
        "artifacts": artifacts,
        "strategy_sources": _discover_strategy_source_files(resolved_family),
        "market_data_files": data_files,
    }


def _manifest_prompt_block(manifest: dict[str, object]) -> str:
    return "ANALYSIS MANIFEST:\n" + json.dumps(manifest, indent=2, sort_keys=True)


def _analysis_python_prelude(manifest: dict[str, object]) -> str:
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else {}
    strategy_sources = manifest.get("strategy_sources") if isinstance(manifest, dict) else {}
    market_data_files = manifest.get("market_data_files") if isinstance(manifest, dict) else {}
    return "\n".join(
        [
            "from pathlib import Path",
            f"ANALYSIS_ARTIFACTS = {artifacts!r}",
            f"STRATEGY_SOURCE_FILES = {strategy_sources!r}",
            f"MARKET_DATA_FILES = {market_data_files!r}",
            "TRADES_FILE = Path(ANALYSIS_ARTIFACTS['trades_csv'])",
            "EVENTS_FILE = Path(ANALYSIS_ARTIFACTS['strategy_events_parquet']) "
            "if 'strategy_events_parquet' in ANALYSIS_ARTIFACTS else None",
            "DIAGNOSTICS_FILE = Path(ANALYSIS_ARTIFACTS['diagnostics_json']) "
            "if 'diagnostics_json' in ANALYSIS_ARTIFACTS else None",
            "RUNTIME_CONFIG_FILE = Path(ANALYSIS_ARTIFACTS['runtime_config_json']) "
            "if 'runtime_config_json' in ANALYSIS_ARTIFACTS else None",
            "",
        ]
    )


async def _call_analyst(
    trades_file: str,
    focus_question: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    family_name: str = "",
    reflexion_feedback: str = "",
) -> str:
    from agents import Agent as OAIAgent
    from agents import RunConfig as OAIRunConfig
    from agents import Runner as OAIRunner
    from agents import function_tool
    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

    _ensure_oauth_proxy()
    current_trace_id = f"analyst-{focus_question[:40].replace(' ', '_')}"
    manifest = _analysis_manifest(
        trades_file=trades_file,
        strategy_events_file=strategy_events_file,
        diagnostics_file=diagnostics_file,
        family_name=family_name,
    )
    manifest_prompt = _manifest_prompt_block(manifest)

    @function_tool
    def list_analysis_artifacts() -> str:
        """Return the exact artifacts, strategy source files, and market data paths available."""
        output = json.dumps(manifest, indent=2, sort_keys=True)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "list_analysis_artifacts",
            "",
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "list_analysis_artifacts",
            output,
            status="ok",
            duration_ms=0,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return output

    @function_tool
    def read_artifact(kind: str, max_chars: int = ANALYST_READ_FILE_MAX_CHARS) -> str:
        """Read a manifest artifact by kind, such as diagnostics_json or runtime_config_json.

        Args:
            kind: Artifact key from ANALYSIS MANIFEST artifacts.
            max_chars: Maximum characters to return. Defaults to a compact audit-safe limit.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        max_chars = _resolve_tool_max_chars(max_chars, default=ANALYST_READ_FILE_MAX_CHARS)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "read_artifact",
            kind,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        try:
            artifacts = manifest.get("artifacts")
            if not isinstance(artifacts, dict) or kind not in artifacts:
                raise KeyError(f"Unknown artifact kind: {kind}")
            with open(str(artifacts[kind])) as f:
                content = f.read()
            output, truncated = _compact_tool_output(content, max_chars=max_chars)
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "read_artifact",
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
    def read_strategy_source(source_name: str, max_chars: int = ANALYST_READ_FILE_MAX_CHARS) -> str:
        """Read a strategy source file by name from ANALYSIS MANIFEST strategy_sources.

        Args:
            source_name: Source key from strategy_sources, for example strategy.py or signals.py.
            max_chars: Maximum characters to return.
        """
        started = monotonic()
        output = ""
        status = "ok"
        error_type = ""
        truncated = False
        max_chars = _resolve_tool_max_chars(max_chars, default=ANALYST_READ_FILE_MAX_CHARS)
        trace_agent_tool_call(
            "analyst",
            current_trace_id,
            "read_strategy_source",
            source_name,
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        try:
            sources = manifest.get("strategy_sources")
            if not isinstance(sources, dict) or source_name not in sources:
                raise KeyError(f"Unknown strategy source: {source_name}")
            with open(str(sources[source_name])) as f:
                content = f.read()
            output, truncated = _compact_tool_output(content, max_chars=max_chars)
        except Exception as e:
            status = "error"
            error_type = e.__class__.__name__
            output = f"ERROR: {e}"
        trace_agent_tool_result(
            "analyst",
            current_trace_id,
            "read_strategy_source",
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
            executable_code = _analysis_python_prelude(manifest) + "\n" + code
            result = subprocess.run(
                [sys.executable, "-c", executable_code],
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
5. Optional raw OHLCV data, only when the manifest below exposes exact paths:
{_analyst_data_root_guidance()}
{_market_data_manifest(trades_file)}
{manifest_prompt}
   If no exact universe_path is resolved, do not use raw OHLCV or search for it.

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
1. ALWAYS start with read_artifact("diagnostics_json") if present.
2. Use run_python to execute pandas analysis code on trades and/or events.
3. When the focus question requires market context (volatility, volume,
   trend, gaps, range characteristics), use raw OHLCV only if the manifest
   provides exact paths. If not, state that raw OHLCV is unavailable and
   answer from trades/events/diagnostics/source code.
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
- Use raw OHLCV only from exact manifest paths. Never guess or probe data directories.
- Do NOT guess source paths. Use read_strategy_source with names from ANALYSIS MANIFEST.
- Do NOT read large source/data files into the chat unless strictly necessary.
  Prefer targeted run_python summaries and print compact tables only.
- In run_python, these variables are already defined: TRADES_FILE, EVENTS_FILE,
  DIAGNOSTICS_FILE, RUNTIME_CONFIG_FILE, ANALYSIS_ARTIFACTS,
  STRATEGY_SOURCE_FILES, MARKET_DATA_FILES.

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
    if reflexion_feedback:
        user_parts.append(
            "AGENT REFLEXION FEEDBACK FROM THE PRIOR ROUND:\n"
            f"{reflexion_feedback}\n"
            "Apply this lesson only if it is relevant to the focus question. Do not repeat "
            "the same tool/path/prompt failure."
        )
    user_parts.append(
        "Load artifacts using read_artifact/read_strategy_source and perform analysis using run_python."
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
        tools=[list_analysis_artifacts, read_artifact, read_strategy_source, run_python],
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
            trace_id=current_trace_id,
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


async def _call_web_researcher(query: str, context: str, reflexion_feedback: str = "") -> str:
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
    if reflexion_feedback:
        user_prompt += (
            "\n\nAGENT REFLEXION FEEDBACK FROM THE PRIOR ROUND:\n"
            f"{reflexion_feedback}\n"
            "Apply this lesson to improve source selection and specificity."
        )
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
            _accumulate_usage(
                "web_researcher",
                usage,
                provider="openai",
                model=_CONDUCTOR_MODEL,
                trace_id=trace_id,
            )
        trace(
            "CONDUCTOR",
            "web_search codex_cli completed",
            {
                "exit_code": metadata.get("exit_code"),
                "output_len": metadata.get("output_len"),
                "usage_source": metadata.get("usage_source", ""),
            },
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        parsed = _parse_json(output)
        if parsed:
            n_findings = len(parsed.get("findings", []))
            trace(
                "CONDUCTOR",
                "web_search OK",
                {"findings": n_findings},
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
            "web_search parse failed",
            {
                "output_type": type(output).__name__,
                "output_len": len(output),
                "output_excerpt": output[:200],
            },
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: could not parse: {output[:500]}"
    except WebResearchCliError as exc:
        accumulate_agents_sdk_result_usage(
            "web_researcher",
            None,
            provider="openai",
            model=_CONDUCTOR_MODEL,
            trace_id=trace_id,
        )
        trace(
            "CONDUCTOR",
            "web_search ERROR",
            {"error_type": exc.__class__.__name__, "error": str(exc)},
            model_provider="openai",
            model_name=_CONDUCTOR_MODEL,
        )
        return f"WEB_SEARCH ERROR: {exc}"
