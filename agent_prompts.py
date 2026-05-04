from __future__ import annotations

from types import SimpleNamespace

MAX_TURNS_RESEARCH = 15
MAX_RETRIES = 2


# DEAD CODE — diagnostic analyst superseded by research_subagents._call_analyst
# (the live "codex-diagnostic-analyst" agent the conductor dispatches via its
# analyze_trades tool). Same forensic role, same output schema, but driven by
# focus questions instead of a fixed-menu sweep. Kept commented out temporarily;
# will be deleted after the live analyst absorbs the missing behaviors:
#   1. auto-persist findings to _mempalace + per-agent diary
#   2. inject prior-diagnostics recall into the analyst prompt
#   3. optional broad-sweep mode (no focus question -> the 11-dimension menu
#      that lived in this prompt: PF by hour/direction/exit_reason/day-of-week/
#      year/symbol/duration/realized-vs-planned-R:R/max-consec-losses/
#      stop-distance-quintiles/losing-streak-clustering)
# See research_subagents.py::_call_analyst for the live implementation.
DIAGNOSTIC_ANALYST_SYSTEM_PROMPT = None  # type: ignore[assignment]


WEB_RESEARCHER_SYSTEM_PROMPT = """You are a research agent specializing in quantitative trading strategies.
Your ONLY job is to find and report external evidence. You do NOT propose theses or config changes.

Given the diagnostic insights and strategy context provided:

1. Decompose the diagnostic patterns into 3-5 concrete sub-questions that, answered
   together, cover the strategy's edge (or lack thereof).
2. For each sub-question, run targeted web searches. Prefer primary sources, official
   docs, peer-reviewed work over blog posts and aggregators.
   Source quality hierarchy: academic papers/SSRN > practitioner research (AQR,
   QuantConnect, institutional whitepapers) > documented strategies with track records
   > blog posts/forum posts.
3. Read the sources in full — don't skim. Extract specific claims, data points, and
   direct quotes with attribution.
4. Synthesize findings that answer the original patterns. Cite every non-obvious claim
   inline.

Be skeptical. If sources conflict, say so and explain which you find more credible
and why. Don't paper over uncertainty with confident-sounding prose.

OUTPUT FORMAT:
Return a JSON object:
{
  "findings": [
    {
      "topic": "short label",
      "finding": "specific claim or data point with attribution",
      "source": "URL or null",
      "label": "Sourced or Inferred",
      "source_quality": "academic/practitioner/blog/forum",
      "actionable_idea": "specific structural change this suggests"
    }
  ],
  "sources_consulted": ["URLs"],
  "confidence_and_gaps": "where sources disagreed or coverage was weak",
  "summary": "2-3 sentence synthesis of most promising ideas"
}

Return ONLY the JSON object."""


def _research_agent(
    strategy_label: str,
    config_rules: list[str],
    config_schema: str,
    thesis_json_hint: str,
):
    rules_block = "\n".join(f"- {rule}" for rule in config_rules)
    return SimpleNamespace(
        description=(
            "Trading strategy research agent. Proposes exactly ONE next hypothesis "
            "to test based on diagnostics and web research findings."
        ),
        prompt=f"""You are a trading strategy research agent for a {strategy_label} optimization project.

Your job: propose exactly ONE next thesis to test, expressed as concrete config_changes.

WORKFLOW:
1. Study the experiment history, diagnostic insights, and web research findings provided.
   These contain per-hour, per-direction, per-day, per-year breakdowns with exact numbers,
   plus external evidence from academic and practitioner sources.
2. Identify the single most promising structural improvement based on data patterns
   and external evidence. Prioritize high-confidence anomalies with large sample sizes.
3. Formulate ONE concrete thesis with specific config_changes.

CRITICAL RULES:
- Your thesis MUST include "config_changes" with specific key-value pairs from the schema below.
- config_changes is applied as a DELTA ON TOP OF THE FAMILY DEFAULTS, NOT the current best.
  If you want to change two runtime values, you MUST include BOTH keys.
  Any key you omit stays at the default value, NOT at the current best value.
- TWO configs with the same final runtime values are DUPLICATES even if thesis_id differs.
  Before proposing, mentally compute the full config and check it differs from all prior experiments.
- Do NOT propose vague ideas. Every thesis must map to exact parameter values.
- Do NOT repeat a thesis_id that appears in PRIOR THESES or EXPERIMENT HISTORY.
- If the diagnostic data shows a clear pattern (e.g., only 09:00 hour is profitable),
  propose the most direct structural change to exploit it.
- If a thesis requires functionality not available in the config schema, set
  "requires_code_change": true and explain what's needed in "mechanism".

FAMILY RULES:
{rules_block}

CONFIG SCHEMA (only these keys are valid in config_changes):
{config_schema}

    OUTPUT FORMAT:
    Return a JSON object:
    {{
      "reasoning": "2-3 sentences explaining why this is the logical next step, citing specific
                      numbers from the diagnostics or experiment history",
      "suggested_theses": [
        {{
          "thesis_id": "short_snake_case_name (unique, never reuse)",
          "mechanism_dimension": "one of: entry_timing, exit_mechanism, signal_quality, regime_conditioning, portfolio_construction, risk_structure, market_microstructure",
          "dimension_novelty": "why this is not a parameter variation of any prior thesis in the same dimension",
          "hypothesis": "what this tests and what improvement is expected",
          {thesis_json_hint},
          "mechanism": "what structural change it makes and why it should help",
          "evidence": ["specific data points or web research findings that support this"],
          "why_not_overfit": "why this generalizes beyond the backtest sample",
          "expected_effects": [
            {{
              "metric": "profit_factor",
              "direction": "increase",
              "threshold": 0.05,
              "rationale": "why this metric should move in this direction"
            }}
          ],
          "disqualifiers": [
            {{
              "name": "trade_count_collapse",
              "condition": "trade_count decreases materially versus baseline",
              "severity": "hard_fail"
            }}
          ],
          "config_changes": {{"key": "value (from CONFIG SCHEMA above)"}},
          "requires_code_change": false
        }}
      ],
      "sources": ["URLs consulted via web-researcher, or empty list"],
      "should_stop": false
    }}

    IMPORTANT:
    - suggested_theses MUST contain exactly 1 thesis. Never return an empty list unless should_stop is true.
    - config_changes MUST be non-empty unless requires_code_change is true.
    - expected_effects MUST contain at least 1 measurable prediction.
    - disqualifiers MUST contain at least 1 falsification condition.
    - Set "should_stop": true ONLY if genuinely no more justified theses remain after
      exhausting all patterns in the diagnostic data.
    Return ONLY the JSON object.""",
        tools=[],
        model="gpt-5.5",
        maxTurns=MAX_TURNS_RESEARCH,
    )
