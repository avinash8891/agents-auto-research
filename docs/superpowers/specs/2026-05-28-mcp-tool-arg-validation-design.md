# MCP Tool Arg Validation — Design Spec

**Date:** 2026-05-28
**Scope:** `research_tools_mcp.py` — all tools in `_build_research_tools_mcp`

---

## Problem

MCP tools exposed to AI agents fire immediately with whatever args the agent sends. Only `save_finding` validates args today; the other 10 tools do not. Bad args cause expensive downstream failures (API calls, DB reads) that could have been caught at the boundary. New tools added in the future will also skip validation unless the pattern enforces it.

---

## Solution

Introduce a Pydantic-model-per-tool registration pattern enforced at startup. Every tool must declare an arg model. If a tool is registered without one, `_build_research_tools_mcp` raises `TypeError` immediately — silent omission is impossible.

---

## Architecture

**New file:** `research_tools_schema.py`
- One Pydantic `BaseModel` subclass per tool, named `<ToolName>Args`
- Shared enum constants: `FINDING_TYPES`, `FINDING_STATUSES`, `RESULT_ORDER_VALUES`

**Modified file:** `research_tools_mcp.py`
- Import all `*Args` models from `research_tools_schema`
- Add `_dispatch(model, kwargs) -> str | None` helper: validates kwargs against model, returns `"VALIDATION ERROR: ..."` string on failure, `None` on success
- Each tool function calls `_dispatch` at the top; returns the error string immediately if not `None`
- `_build_research_tools_mcp` maintains a `_TOOL_MODELS` dict mapping tool name → model class; raises `TypeError` at build time if any registered tool is missing an entry

---

## Validation Rules Per Tool

| Tool | Arg | Rule |
|---|---|---|
| `analyze_trades` | `focus_question` | non-empty string |
| `web_search` | `query` | non-empty string |
| `save_finding` | `finding_type` | `Literal["observation","hypothesis","validated_finding","rejected_finding","open_question","implementation_note"]` |
| `save_finding` | `status` | `Literal["unvalidated","validated","rejected","stale"]` |
| `save_finding` | `finding`, `evidence`, `scope`, `expires_if` | non-empty strings |
| `search_findings` | `query` | non-empty string |
| `search_findings` | `finding_type` | empty string OR valid finding type literal |
| `list_past_theses` | `offset` | ≥ 0 |
| `list_past_theses` | `limit` | 1–100 |
| `get_past_thesis` | `thesis_id` | non-empty string |
| `list_experiment_results` | `order` | `Literal["latest","best"]` |
| `list_experiment_results` | `offset` | ≥ 0 |
| `list_experiment_results` | `limit` | 1–50 |
| `get_experiment_result` | `thesis_id` | non-empty string |
| `list_rejections` | `limit` | 1–100 |
| `list_rejections` | `rejection_code` | non-empty string if provided (None allowed) |
| `get_rejection` | `round_number` | ≥ 0 |
| `get_rejection` | `thesis_id` | non-empty string |
| `rejection_pattern_summary` | `window_rounds` | 1–50 |
| `memory_status` | *(none)* | empty model — still registered |

---

## Data Flow

```
Agent call → FastMCP → tool function
                            ↓
                       _dispatch(ModelClass, kwargs)
                            ↓ fail → return "VALIDATION ERROR: <field>: <reason>"
                            ↓ pass → execute tool logic, return result
```

---

## Error Format

Validation errors return a string starting with `"VALIDATION ERROR:"` — consistent with `save_finding`'s existing `"REJECTED:"` pattern. The message names the failing field and expected values so the agent can self-correct without a retry loop.

Example: `"VALIDATION ERROR: finding_type must be one of: observation, hypothesis, validated_finding, rejected_finding, open_question, implementation_note — got 'fact'"`

Pydantic `ValidationError` is always caught inside `_dispatch`. It never propagates as an exception — MCP tools must return strings.

---

## Registration Enforcement

```python
_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "analyze_trades": AnalyzeTradesArgs,
    "web_search": WebSearchArgs,
    # ... all tools ...
}

# inside _build_research_tools_mcp, after all @mcp.tool() decorators:
for tool in mcp.list_tools():
    if tool.name not in _TOOL_MODELS:
        raise TypeError(f"MCP tool '{tool.name}' registered without an arg model in _TOOL_MODELS")
```

New tools added via `@mcp.tool()` without a `_TOOL_MODELS` entry will fail at startup.

---

## Testing

**File:** `tests/test_research_tools_schema.py`

- One test per tool: valid args → `_dispatch` returns `None`
- One test per tool: invalid args → `_dispatch` returns string starting with `"VALIDATION ERROR:"`
- One test: `_build_research_tools_mcp` raises `TypeError` if a tool is missing from `_TOOL_MODELS`
- Tests do not mock tool logic — they test the validation boundary only

---

## Files Touched

| File | Change |
|---|---|
| `research_tools_schema.py` | New — arg models |
| `research_tools_mcp.py` | Add `_dispatch`, `_TOOL_MODELS`, enforce at build time |
| `tests/test_research_tools_schema.py` | New — validation tests |
