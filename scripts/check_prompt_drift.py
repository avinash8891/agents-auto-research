#!/usr/bin/env python3
"""Static drift checker for the conductor system prompt.

Compares what the system prompt CLAIMS against what the code actually does:

  1. TOOLS section vs MCP registry — every tool listed in the prompt must
     correspond to a real @mcp.tool() in research_tools_mcp.py, and every
     registered tool should be documented.
  2. OUTPUT schema vs ResearchThesis — every field the validator can reject
     on must be documented in the prompt's OUTPUT section.
  3. Validator rule mentions vs rejection codes — every named rule the prompt
     references ("theme-cluster fixation", "neighboring threshold", etc.) must
     still have a corresponding rejection_code in thesis_validator.py.

Exit 1 on drift. Wire into CI to prevent prompt rot.

Usage:
    python scripts/check_prompt_drift.py
    python scripts/check_prompt_drift.py --strict   # also flag undocumented MCP tools
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research_prompts import _build_conductor_system_prompt  # noqa: E402
from research_types import ResearchThesis  # noqa: E402

# ---------------------------------------------------------------------------
# Ground truth — what the validator actually enforces.
# ---------------------------------------------------------------------------

# Fields the validator inspects directly. If absent from the prompt, the
# conductor has no idea the field is required and will omit it.
# Sourced from _validate_structural / _validate_thesis_quality in
# thesis_validator.py — keep in sync when validator rules change.
VALIDATOR_INSPECTED_FIELDS: set[str] = {
    "thesis_id",
    "hypothesis",
    "mechanism",
    "mechanism_dimension",
    "dimension_novelty",
    "causal_cluster",
    "underexplored_dimensions_considered",
    "config_changes",
    "requires_code_change",
    "requested_primitives",
    "expected_effects",
    "disqualifiers",
    "required_diagnostics",
    "falsification_or_alternative",
    "theme_keywords",
    "prior_lever_outcomes",
}

# Named rules the prompt may reference → rejection_code that must still exist
# in thesis_validator.py for the reference to be live.
PROMPT_RULE_NAMES_TO_CODES: dict[str, str] = {
    "theme-cluster fixation": "thesis_quality_theme_cluster_fixation",
    "direction whipsaw": "thesis_quality_direction_whipsaw",
    "needs_code starvation": "thesis_quality_needs_code_starvation",
    "neighboring threshold": "config_validity_neighboring_threshold",
    "neighboring-threshold": "config_validity_neighboring_threshold",
    "config-key overlap": "config_validity_config_key_overlap_real",
    "hypothesis-config misalignment": "hypothesis_config_misalignment",
    "thesis_id repeated": "structural_thesis_id_repeated",
}


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def discover_mcp_tools(path: Path) -> set[str]:
    """Parse research_tools_mcp.py and return the names of all @mcp.tool() defs.

    Uses AST rather than regex so it can't be fooled by commented-out blocks
    or string literals containing 'mcp.tool'.
    """
    tree = ast.parse(path.read_text())
    tools: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            # Match @mcp.tool() — a Call whose func is an Attribute named 'tool'
            # on a Name 'mcp'.
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "mcp"
            ):
                tools.add(node.name)
                break
    return tools


def extract_tools_from_prompt(prompt: str) -> set[str]:
    """Pull tool names out of the prompt's TOOLS section.

    Recognizes both styles currently in use:
      "- analyze_trades(focus_question)         analyst — ..."
      "list_past_theses / get_past_thesis      Full thesis history."
    """
    section_match = re.search(
        r"^TOOLS\b.*?(?=^[A-Z][A-Z _\-]{3,}$|\Z)",
        prompt,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return set()
    section = section_match.group(0)

    tools: set[str] = set()
    # Look for identifiers that appear at line start (optionally preceded by
    # "- ") and are followed by either "(" or whitespace + description.
    # Identifiers are snake_case names with optional " / other_name" pairs.
    pattern = re.compile(
        r"^\s*-?\s*([a-z_][a-z0-9_]*(?:\s*/\s*[a-z_][a-z0-9_]*)*)\b",
        re.MULTILINE,
    )
    for match in pattern.finditer(section):
        for name in match.group(1).split("/"):
            cleaned = name.strip()
            if cleaned and cleaned != "TOOLS":
                tools.add(cleaned)
    return tools


def extract_output_fields_from_prompt(prompt: str) -> set[str]:
    """Pull field names out of the prompt's OUTPUT section.

    Field lines look like:
      "  thesis_id              short stable identifier"
    """
    section_match = re.search(
        r"^OUTPUT\b.*?(?=^[A-Z][A-Z _\-]{3,}$|\Z)",
        prompt,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return set()
    section = section_match.group(0)

    fields: set[str] = set()
    # Look for indented snake_case identifiers followed by 2+ spaces and prose.
    pattern = re.compile(
        r"^\s{2,}([a-z_][a-z0-9_]*)\s{2,}\S",
        re.MULTILINE,
    )
    for match in pattern.finditer(section):
        fields.add(match.group(1))
    return fields


def discover_validator_rejection_codes(path: Path) -> set[str]:
    """Return every rejection_code literal that appears in thesis_validator.py.

    Captures both kwarg-style (`rejection_code="..."`) and the return values
    inside infer_rejection_code.
    """
    text = path.read_text()
    codes: set[str] = set()
    codes.update(re.findall(r'rejection_code\s*=\s*"([a-z0-9_]+)"', text))
    codes.update(re.findall(r'return\s+"([a-z0-9_]+)"', text))
    # Drop the catch-all sentinel
    codes.discard("unspecified_validation_error")
    return codes


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_tools(prompt: str, mcp_tools: set[str], strict: bool) -> list[str]:
    """Flag tools mentioned in the prompt that don't exist as MCP tools, and
    (in strict mode) MCP tools that aren't mentioned in the prompt."""
    findings: list[str] = []
    prompt_tools = extract_tools_from_prompt(prompt)

    # The prompt may legitimately mention tool families with a trailing wildcard
    # like "get_*"; strip a sole trailing "_" to allow that.
    normalized_prompt_tools = {name.rstrip("_") for name in prompt_tools}
    # And tool aliases the prompt uses for narration (not invocable).
    narrative_tokens = {"get", "tools", "default", "required"}
    normalized_prompt_tools -= narrative_tokens

    phantom = sorted(normalized_prompt_tools - mcp_tools - _prefix_matches(mcp_tools))
    for name in phantom:
        findings.append(
            f"TOOL_PHANTOM: prompt advertises '{name}' but no @mcp.tool() with "
            f"that name exists in research_tools_mcp.py"
        )

    if strict:
        undocumented = sorted(mcp_tools - normalized_prompt_tools)
        for name in undocumented:
            findings.append(
                f"TOOL_UNDOCUMENTED: @mcp.tool() '{name}' is registered but "
                f"not mentioned in the prompt's TOOLS section"
            )
    return findings


def _prefix_matches(mcp_tools: set[str]) -> set[str]:
    """Allow prompt entries like `get_*` or `list_*` to resolve to any MCP tool
    sharing that prefix. Returns the set of prompt-style names that should be
    accepted as covered."""
    accepted: set[str] = set()
    for tool in mcp_tools:
        accepted.add(tool.split("_")[0])
    return accepted


def check_output_fields(prompt: str) -> list[str]:
    findings: list[str] = []
    documented = extract_output_fields_from_prompt(prompt)
    model_fields = set(ResearchThesis.model_fields.keys())

    # Required-but-missing: validator inspects it, prompt doesn't document it.
    missing = sorted(VALIDATOR_INSPECTED_FIELDS - documented)
    for name in missing:
        findings.append(
            f"FIELD_MISSING_FROM_PROMPT: '{name}' is inspected by the validator "
            f"but not documented in the prompt's OUTPUT section"
        )

    # Phantom fields: prompt documents a name that isn't on the model.
    phantom = sorted(documented - model_fields)
    for name in phantom:
        findings.append(
            f"FIELD_PHANTOM_IN_PROMPT: prompt documents '{name}' but no such "
            f"field exists on ResearchThesis"
        )
    return findings


def check_rule_references(prompt: str, validator_codes: set[str]) -> list[str]:
    findings: list[str] = []
    lowered = prompt.lower()
    for rule_name, expected_code in PROMPT_RULE_NAMES_TO_CODES.items():
        if rule_name.lower() not in lowered:
            continue
        if expected_code not in validator_codes:
            findings.append(
                f"RULE_STALE: prompt references '{rule_name}' but its rejection "
                f"code '{expected_code}' no longer exists in thesis_validator.py"
            )
    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also flag MCP tools that are registered but not documented in the prompt",
    )
    parser.add_argument(
        "--family",
        default="ema",
        help="strategy family to render the prompt for (default: ema)",
    )
    args = parser.parse_args()

    prompt = _build_conductor_system_prompt(f"<{args.family} strategy description>")
    mcp_tools = discover_mcp_tools(REPO_ROOT / "research_tools_mcp.py")
    validator_codes = discover_validator_rejection_codes(REPO_ROOT / "thesis_validator.py")

    all_findings: list[str] = []
    all_findings.extend(check_tools(prompt, mcp_tools, args.strict))
    all_findings.extend(check_output_fields(prompt))
    all_findings.extend(check_rule_references(prompt, validator_codes))

    if not all_findings:
        print("OK: no prompt drift detected.")
        print(f"  MCP tools discovered:    {sorted(mcp_tools)}")
        print(f"  Validator codes counted: {len(validator_codes)}")
        return 0

    print(f"DRIFT: {len(all_findings)} finding(s)")
    for finding in all_findings:
        print(f"  - {finding}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
