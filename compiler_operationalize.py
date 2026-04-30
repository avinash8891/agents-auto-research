from __future__ import annotations

import json
from typing import Any

from strategies import STRATEGIES

AMBIGUOUS_PATTERNS = {
    "stocks_in_play": ("stocks in play", "stocks-in-play", "stocks_in_play"),
    "narrow_or": ("narrow or", "narrow-or", "narrow_or", "narrow opening range"),
    "wide_or": ("wide or", "wide-or", "wide_or", "wide opening range"),
}

# ---------------------------------------------------------------------------
# Operationalization: ambiguous thesis → exact contract
# ---------------------------------------------------------------------------


def thesis_needs_operationalization(thesis: dict[str, Any]) -> bool:
    """Check if a thesis contains ambiguous terms needing resolution."""
    haystack = " ".join(str(thesis.get(key, "")) for key in ("hypothesis", "mechanism")).lower()
    return any(term in haystack for terms in AMBIGUOUS_PATTERNS.values() for term in terms)


def finalize_thesis_config_changes(
    thesis: dict[str, Any], clarification: dict[str, Any]
) -> dict[str, Any]:
    """Merge operationalization clarification into a thesis.

    Takes the resolved contract from the clarification agent and
    produces a finalized thesis with concrete config_changes.
    """
    finalized = dict(thesis)
    finalized["operationalization_reasoning"] = clarification.get("reasoning", "")
    primitive_contract = clarification.get("resolved_contract")
    resolved_changes = clarification.get("resolved_changes") or {}
    if primitive_contract is None:
        if resolved_changes:
            finalized["primitive_contract"] = thesis.get("primitive_contract", [])
            normalized = dict(resolved_changes)
            if normalized.get("require_regimes") == ["narrow-or"]:
                normalized["require_regimes"] = ["narrow-OR"]
            finalized["config_changes"] = normalized
            finalized["requires_code_change"] = clarification.get("requires_code_change", False)
            if finalized["requires_code_change"]:
                finalized["missing_primitives"] = clarification.get("missing_primitives", [])
                finalized["code_change_idea"] = clarification.get("code_change_idea")
                finalized["config_changes"] = {}
            return finalized
        primitive_contract = []
    finalized["primitive_contract"] = primitive_contract
    family_name = finalized["strategy_family"]
    strategy = STRATEGIES[family_name]
    support = strategy.resolve_contract_support(finalized["primitive_contract"])
    renderable = True
    try:
        rendered_config = strategy.render_contract_to_runtime_config(
            finalized["primitive_contract"]
        )
    except (KeyError, TypeError, ValueError):
        renderable = False
        rendered_config = {}
    if clarification.get("requires_code_change"):
        finalized["requires_code_change"] = True
        finalized["config_changes"] = {}
        finalized["missing_primitives"] = (
            clarification.get("missing_primitives") or support["missing_primitive_types"]
        )
        finalized["code_change_idea"] = clarification.get("code_change_idea")
        return finalized

    finalized["requires_code_change"] = (not support["supported"]) or (not renderable)
    finalized["missing_primitives"] = support["missing_primitive_types"]
    finalized["config_changes"] = rendered_config if renderable and support["supported"] else {}
    if finalized["requires_code_change"] and not finalized.get("code_change_idea"):
        finalized["code_change_idea"] = clarification.get("code_change_idea")
    return finalized


def operationalize_thesis(thesis: dict[str, Any]) -> dict[str, Any]:
    """Convert a potentially ambiguous thesis into an executable contract.

    For registered strategies with config_changes, maps them to primitive_contract entries.
    For ambiguous theses (detected by AMBIGUOUS_PATTERNS), runs an SDK
    operationalization agent to resolve the ambiguity.
    For clear theses with primitive_contract, renders to runtime config directly.
    """
    # Direct config_changes → primitive_contract mapping
    print(
        f"OPERATIONALIZE: config_changes={bool(thesis.get('config_changes'))} needs_op={thesis_needs_operationalization(thesis)} has_pc={bool(thesis.get('primitive_contract'))}"
    )
    family_name = thesis["strategy_family"]
    strategy = STRATEGIES[family_name]
    if thesis.get("config_changes"):
        thesis["primitive_contract"] = thesis.get(
            "primitive_contract"
        ) or strategy.map_config_changes_to_contract(thesis["config_changes"])
        thesis["requires_code_change"] = thesis.get("requires_code_change", False)
        return thesis

    # Clear thesis — just render the contract
    if not thesis_needs_operationalization(thesis):
        thesis["primitive_contract"] = thesis.get("primitive_contract", [])
        thesis["config_changes"] = strategy.render_contract_to_runtime_config(
            thesis["primitive_contract"]
        )
        return thesis

    # Ambiguous thesis — use SDK operationalization agent
    clarification = _run_operationalization_agent(thesis)
    return finalize_thesis_config_changes(thesis, clarification)


def _run_operationalization_agent(thesis: dict[str, Any]) -> dict[str, Any]:
    """Run SDK agent to resolve ambiguous thesis into exact contract.

    Returns a clarification dict with resolved_changes/resolved_contract.
    Falls back to empty resolution if agent unavailable.
    """
    try:
        import asyncio

        from claude_agent_sdk import AgentDefinition

        from agent_orchestrator import _run_single_agent

        agent_def = AgentDefinition(
            description="Resolves ambiguous trading theses into exact executable contracts.",
            prompt=_build_operationalization_prompt(thesis),
            tools=[],
            model="sonnet",
            maxTurns=3,
        )

        result = asyncio.run(
            _run_single_agent(
                "operationalization-agent",
                f"Operationalize this thesis: {json.dumps(thesis, indent=2)}",
                agent_def,
            )
        )

        if result:
            return result

        print(f"OPERATIONALIZE: agent returned None for {thesis.get('thesis_id')}")
        return {
            "resolved_changes": {},
            "requires_code_change": True,
            "missing_primitives": ["operationalization_failed"],
            "code_change_idea": f"Agent could not operationalize '{thesis.get('thesis_id')}'",
        }

    except Exception as exc:
        print(f"OPERATIONALIZE: SDK error for {thesis.get('thesis_id')}: {exc}")
        return {
            "resolved_changes": {},
            "requires_code_change": True,
            "missing_primitives": ["operationalization_error"],
            "code_change_idea": str(exc),
        }


def _build_operationalization_prompt(thesis: dict[str, Any]) -> str:
    """Build the prompt for the operationalization agent."""
    return f"""You are a trading strategy contract resolver. Your job is to convert
ambiguous thesis descriptions into exact, executable contracts.

THESIS:
{json.dumps({
    "thesis_id": thesis.get("thesis_id"),
    "hypothesis": thesis.get("hypothesis"),
    "family": thesis.get("strategy_family", thesis.get("family")),
    "mechanism": thesis.get("mechanism"),
}, indent=2)}

TASK:
1. Identify every ambiguous term in the thesis/mechanism.
2. For each ambiguous term, resolve to an exact contract:
   - measurement variable
   - ranking method or baseline
   - lookback/window
   - operator
   - threshold/top-N
   - hard gate vs score
3. If the contract cannot be expressed with standard config keys,
   set requires_code_change=true and describe the missing primitive.

Return a JSON object:
{{
  "resolved_changes": {{"config-like fields if directly expressible": "value"}},
  "reasoning": "the exact contract and why",
  "requires_code_change": false,
  "code_change_idea": null
}}

If code change needed:
{{
  "resolved_changes": {{}},
  "reasoning": "explanation",
  "requires_code_change": true,
  "missing_primitives": ["primitive_name"],
  "code_change_idea": "what needs implementing"
}}

Return ONLY the JSON object."""
