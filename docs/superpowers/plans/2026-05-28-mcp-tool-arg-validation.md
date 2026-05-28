# MCP Tool Arg Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Pydantic arg-model validation to all MCP tools in `_build_research_tools_mcp`, enforced at startup so new tools can't be registered without a model.

**Architecture:** One new file `research_tools_schema.py` holds a Pydantic `BaseModel` per tool. A `_dispatch` helper in `research_tools_mcp.py` validates kwargs against the model before running tool logic, returning a `"VALIDATION ERROR: ..."` string on failure. `_TOOL_MODELS` maps tool name → model; a startup check raises `TypeError` if any registered tool is missing.

**Tech Stack:** Python 3.11+, Pydantic v2, `mcp.server.fastmcp.FastMCP`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `research_tools_schema.py` | Create | One `BaseModel` per MCP tool; shared enum literals |
| `research_tools_mcp.py` | Modify | Import models, add `_dispatch`, add `_TOOL_MODELS` enforcement |
| `tests/test_research_tools_schema.py` | Create | Validation boundary tests — no tool logic mocked |

---

### Task 1: Create `research_tools_schema.py` with all arg models

**Files:**
- Create: `research_tools_schema.py`

- [ ] **Step 1: Write the failing test first**

```python
# tests/test_research_tools_schema.py
from pydantic import ValidationError
import pytest
from research_tools_schema import (
    AnalyzeTradesArgs,
    WebSearchArgs,
    SaveFindingArgs,
    SearchFindingsArgs,
    ListPastThesesArgs,
    GetPastThesisArgs,
    ListExperimentResultsArgs,
    GetExperimentResultArgs,
    ListRejectionsArgs,
    GetRejectionArgs,
    RejectionPatternSummaryArgs,
    MemoryStatusArgs,
)


def test_imports_exist():
    """All model classes are importable."""
    assert AnalyzeTradesArgs
    assert WebSearchArgs
    assert SaveFindingArgs
    assert SearchFindingsArgs
    assert ListPastThesesArgs
    assert GetPastThesisArgs
    assert ListExperimentResultsArgs
    assert GetExperimentResultArgs
    assert ListRejectionsArgs
    assert GetRejectionArgs
    assert RejectionPatternSummaryArgs
    assert MemoryStatusArgs
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_research_tools_schema.py::test_imports_exist -v
```
Expected: `FAILED` — `ModuleNotFoundError: No module named 'research_tools_schema'`

- [ ] **Step 3: Create `research_tools_schema.py`**

```python
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field
from typing_extensions import Annotated

FINDING_TYPES = (
    "observation",
    "hypothesis",
    "validated_finding",
    "rejected_finding",
    "open_question",
    "implementation_note",
)

FINDING_STATUSES = ("unvalidated", "validated", "rejected", "stale")

RESULT_ORDER_VALUES = ("latest", "best")

FindingType = Literal[
    "observation",
    "hypothesis",
    "validated_finding",
    "rejected_finding",
    "open_question",
    "implementation_note",
]

FindingStatus = Literal["unvalidated", "validated", "rejected", "stale"]

ResultOrder = Literal["latest", "best"]

NonEmptyStr = Annotated[str, Field(min_length=1)]


class AnalyzeTradesArgs(BaseModel):
    focus_question: NonEmptyStr


class WebSearchArgs(BaseModel):
    query: NonEmptyStr
    context: str = ""


class SaveFindingArgs(BaseModel):
    finding: NonEmptyStr
    finding_type: FindingType
    status: FindingStatus
    evidence: NonEmptyStr
    scope: NonEmptyStr
    expires_if: NonEmptyStr


class SearchFindingsArgs(BaseModel):
    query: NonEmptyStr
    finding_type: Literal[
        "",
        "observation",
        "hypothesis",
        "validated_finding",
        "rejected_finding",
        "open_question",
        "implementation_note",
    ] = ""


class ListPastThesesArgs(BaseModel):
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class GetPastThesisArgs(BaseModel):
    thesis_id: NonEmptyStr


class ListExperimentResultsArgs(BaseModel):
    order: ResultOrder = "latest"
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=50)] = 10


class GetExperimentResultArgs(BaseModel):
    thesis_id: NonEmptyStr
    detail: bool = False


class ListRejectionsArgs(BaseModel):
    round_number: Optional[Annotated[int, Field(ge=0)]] = None
    rejection_code: Optional[NonEmptyStr] = None
    limit: Annotated[int, Field(ge=1, le=100)] = 25


class GetRejectionArgs(BaseModel):
    round_number: Annotated[int, Field(ge=0)]
    thesis_id: NonEmptyStr


class RejectionPatternSummaryArgs(BaseModel):
    window_rounds: Annotated[int, Field(ge=1, le=50)] = 10


class MemoryStatusArgs(BaseModel):
    pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_research_tools_schema.py::test_imports_exist -v
```
Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add research_tools_schema.py tests/test_research_tools_schema.py
git commit -m "feat: add MCP tool arg model schema"
```

---

### Task 2: Write validation boundary tests for all models

**Files:**
- Modify: `tests/test_research_tools_schema.py`

- [ ] **Step 1: Add all validation tests**

Append to `tests/test_research_tools_schema.py`:

```python
# --- AnalyzeTradesArgs ---

def test_analyze_trades_valid():
    AnalyzeTradesArgs(focus_question="Why do gaps fail on Fridays?")


def test_analyze_trades_empty_question():
    with pytest.raises(ValidationError, match="focus_question"):
        AnalyzeTradesArgs(focus_question="")


# --- WebSearchArgs ---

def test_web_search_valid():
    WebSearchArgs(query="ORB strategy gap filter")


def test_web_search_valid_with_context():
    WebSearchArgs(query="VWAP reversion", context="Investigating Tuesday bias")


def test_web_search_empty_query():
    with pytest.raises(ValidationError, match="query"):
        WebSearchArgs(query="")


# --- SaveFindingArgs ---

def test_save_finding_valid():
    SaveFindingArgs(
        finding="Tuesday PF=1.7 vs Friday PF=2.7 across 3017 trades",
        finding_type="observation",
        status="unvalidated",
        evidence="round_003, thesis entry_window_test",
        scope="train_2020-2023",
        expires_if="fails on validation split",
    )


def test_save_finding_invalid_type():
    with pytest.raises(ValidationError, match="finding_type"):
        SaveFindingArgs(
            finding="some finding",
            finding_type="fact",
            status="unvalidated",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_invalid_status():
    with pytest.raises(ValidationError, match="status"):
        SaveFindingArgs(
            finding="some finding",
            finding_type="observation",
            status="pending",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_empty_finding():
    with pytest.raises(ValidationError, match="finding"):
        SaveFindingArgs(
            finding="",
            finding_type="observation",
            status="unvalidated",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_all_valid_types():
    for ft in ("observation", "hypothesis", "validated_finding",
               "rejected_finding", "open_question", "implementation_note"):
        SaveFindingArgs(
            finding="test",
            finding_type=ft,
            status="unvalidated",
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


def test_save_finding_all_valid_statuses():
    for s in ("unvalidated", "validated", "rejected", "stale"):
        SaveFindingArgs(
            finding="test",
            finding_type="observation",
            status=s,
            evidence="round_001",
            scope="full_sample",
            expires_if="never",
        )


# --- SearchFindingsArgs ---

def test_search_findings_valid_no_type():
    SearchFindingsArgs(query="gap filter")


def test_search_findings_valid_with_type():
    SearchFindingsArgs(query="gap filter", finding_type="validated_finding")


def test_search_findings_empty_string_type_allowed():
    SearchFindingsArgs(query="gap filter", finding_type="")


def test_search_findings_invalid_type():
    with pytest.raises(ValidationError, match="finding_type"):
        SearchFindingsArgs(query="gap filter", finding_type="bad_type")


def test_search_findings_empty_query():
    with pytest.raises(ValidationError, match="query"):
        SearchFindingsArgs(query="")


# --- ListPastThesesArgs ---

def test_list_past_theses_defaults():
    args = ListPastThesesArgs()
    assert args.offset == 0
    assert args.limit == 25


def test_list_past_theses_valid():
    ListPastThesesArgs(offset=10, limit=50)


def test_list_past_theses_negative_offset():
    with pytest.raises(ValidationError, match="offset"):
        ListPastThesesArgs(offset=-1)


def test_list_past_theses_limit_zero():
    with pytest.raises(ValidationError, match="limit"):
        ListPastThesesArgs(limit=0)


def test_list_past_theses_limit_over_max():
    with pytest.raises(ValidationError, match="limit"):
        ListPastThesesArgs(limit=101)


# --- GetPastThesisArgs ---

def test_get_past_thesis_valid():
    GetPastThesisArgs(thesis_id="ema_gap_filter_v2")


def test_get_past_thesis_empty_id():
    with pytest.raises(ValidationError, match="thesis_id"):
        GetPastThesisArgs(thesis_id="")


# --- ListExperimentResultsArgs ---

def test_list_experiment_results_defaults():
    args = ListExperimentResultsArgs()
    assert args.order == "latest"
    assert args.offset == 0
    assert args.limit == 10


def test_list_experiment_results_valid():
    ListExperimentResultsArgs(order="best", offset=5, limit=20)


def test_list_experiment_results_invalid_order():
    with pytest.raises(ValidationError, match="order"):
        ListExperimentResultsArgs(order="worst")


def test_list_experiment_results_limit_over_max():
    with pytest.raises(ValidationError, match="limit"):
        ListExperimentResultsArgs(limit=51)


# --- GetExperimentResultArgs ---

def test_get_experiment_result_valid():
    GetExperimentResultArgs(thesis_id="ema_gap_filter_v2")


def test_get_experiment_result_with_detail():
    GetExperimentResultArgs(thesis_id="ema_gap_filter_v2", detail=True)


def test_get_experiment_result_empty_id():
    with pytest.raises(ValidationError, match="thesis_id"):
        GetExperimentResultArgs(thesis_id="")


# --- ListRejectionsArgs ---

def test_list_rejections_defaults():
    args = ListRejectionsArgs()
    assert args.round_number is None
    assert args.rejection_code is None
    assert args.limit == 25


def test_list_rejections_valid():
    ListRejectionsArgs(round_number=3, rejection_code="thesis_quality_theme_cluster_fixation", limit=10)


def test_list_rejections_empty_rejection_code():
    with pytest.raises(ValidationError, match="rejection_code"):
        ListRejectionsArgs(rejection_code="")


def test_list_rejections_negative_round():
    with pytest.raises(ValidationError, match="round_number"):
        ListRejectionsArgs(round_number=-1)


def test_list_rejections_limit_over_max():
    with pytest.raises(ValidationError, match="limit"):
        ListRejectionsArgs(limit=101)


# --- GetRejectionArgs ---

def test_get_rejection_valid():
    GetRejectionArgs(round_number=3, thesis_id="ema_gap_filter_v2")


def test_get_rejection_negative_round():
    with pytest.raises(ValidationError, match="round_number"):
        GetRejectionArgs(round_number=-1, thesis_id="ema_gap_filter_v2")


def test_get_rejection_empty_thesis_id():
    with pytest.raises(ValidationError, match="thesis_id"):
        GetRejectionArgs(round_number=3, thesis_id="")


# --- RejectionPatternSummaryArgs ---

def test_rejection_pattern_summary_default():
    args = RejectionPatternSummaryArgs()
    assert args.window_rounds == 10


def test_rejection_pattern_summary_valid():
    RejectionPatternSummaryArgs(window_rounds=20)


def test_rejection_pattern_summary_zero():
    with pytest.raises(ValidationError, match="window_rounds"):
        RejectionPatternSummaryArgs(window_rounds=0)


def test_rejection_pattern_summary_over_max():
    with pytest.raises(ValidationError, match="window_rounds"):
        RejectionPatternSummaryArgs(window_rounds=51)


# --- MemoryStatusArgs ---

def test_memory_status_no_args():
    MemoryStatusArgs()
```

- [ ] **Step 2: Run tests to verify they all pass**

```bash
pytest tests/test_research_tools_schema.py -v
```
Expected: all tests `PASSED`

- [ ] **Step 3: Commit**

```bash
git add tests/test_research_tools_schema.py
git commit -m "test: add MCP tool arg validation boundary tests"
```

---

### Task 3: Add `_dispatch` helper and `_TOOL_MODELS` enforcement to `research_tools_mcp.py`

**Files:**
- Modify: `research_tools_mcp.py`

- [ ] **Step 1: Write the failing tests for `_dispatch` and startup enforcement**

Append to `tests/test_research_tools_schema.py`:

```python
# --- _dispatch helper ---

from research_tools_mcp import _dispatch
from research_tools_schema import WebSearchArgs


def test_dispatch_valid_returns_none():
    result = _dispatch(WebSearchArgs, {"query": "ORB gap filter"})
    assert result is None


def test_dispatch_invalid_returns_validation_error_string():
    result = _dispatch(WebSearchArgs, {"query": ""})
    assert isinstance(result, str)
    assert result.startswith("VALIDATION ERROR:")


def test_dispatch_missing_required_field():
    result = _dispatch(WebSearchArgs, {})
    assert isinstance(result, str)
    assert result.startswith("VALIDATION ERROR:")


# --- startup enforcement ---

from unittest.mock import patch


def test_build_raises_if_tool_missing_from_tool_models(tmp_path):
    """A tool registered via @mcp.tool() but absent from _TOOL_MODELS raises TypeError."""
    from mcp.server.fastmcp import FastMCP
    from research_tools_mcp import _TOOL_MODELS
    import research_tools_mcp as rtm

    mcp = FastMCP("test-enforcement")

    @mcp.tool()
    async def unregistered_tool(x: str) -> str:
        return x

    with pytest.raises(TypeError, match="unregistered_tool"):
        rtm._enforce_tool_models(mcp, _TOOL_MODELS)
```

- [ ] **Step 2: Run to verify these tests fail**

```bash
pytest tests/test_research_tools_schema.py::test_dispatch_valid_returns_none tests/test_research_tools_schema.py::test_build_raises_if_tool_missing_from_tool_models -v
```
Expected: `FAILED` — `ImportError: cannot import name '_dispatch'`

- [ ] **Step 3: Add imports and `_dispatch` + `_enforce_tool_models` to `research_tools_mcp.py`**

Add at the top of `research_tools_mcp.py`, after existing imports:

```python
from pydantic import BaseModel, ValidationError

from research_tools_schema import (
    AnalyzeTradesArgs,
    GetExperimentResultArgs,
    GetPastThesisArgs,
    GetRejectionArgs,
    ListExperimentResultsArgs,
    ListPastThesesArgs,
    ListRejectionsArgs,
    MemoryStatusArgs,
    RejectionPatternSummaryArgs,
    SaveFindingArgs,
    SearchFindingsArgs,
    WebSearchArgs,
)

_TOOL_MODELS: dict[str, type[BaseModel]] = {
    "analyze_trades": AnalyzeTradesArgs,
    "web_search": WebSearchArgs,
    "save_finding": SaveFindingArgs,
    "search_findings": SearchFindingsArgs,
    "memory_status": MemoryStatusArgs,
    "list_past_theses": ListPastThesesArgs,
    "get_past_thesis": GetPastThesisArgs,
    "list_experiment_results": ListExperimentResultsArgs,
    "get_experiment_result": GetExperimentResultArgs,
    "list_rejections": ListRejectionsArgs,
    "get_rejection": GetRejectionArgs,
    "rejection_pattern_summary": RejectionPatternSummaryArgs,
}


def _dispatch(model_cls: type[BaseModel], kwargs: dict) -> str | None:
    """Validate kwargs against model_cls. Returns error string on failure, None on success."""
    try:
        model_cls(**kwargs)
        return None
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        parts = []
        for e in errors:
            loc = ".".join(str(x) for x in e["loc"]) if e["loc"] else "input"
            parts.append(f"{loc}: {e['msg']}")
        return "VALIDATION ERROR: " + "; ".join(parts)


def _enforce_tool_models(mcp, tool_models: dict[str, type[BaseModel]]) -> None:
    """Raise TypeError if any registered tool lacks an entry in tool_models."""
    registered = set(mcp._tool_manager._tools.keys())
    modeled = set(tool_models.keys())
    missing = registered - modeled
    if missing:
        raise TypeError(
            f"MCP tool(s) registered without an arg model in _TOOL_MODELS: {sorted(missing)}. "
            "Add a Pydantic model to research_tools_schema.py and register it in _TOOL_MODELS."
        )
```

- [ ] **Step 4: Run the new tests to verify they pass**

```bash
pytest tests/test_research_tools_schema.py::test_dispatch_valid_returns_none tests/test_research_tools_schema.py::test_dispatch_invalid_returns_validation_error_string tests/test_research_tools_schema.py::test_dispatch_missing_required_field -v
```
Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add research_tools_mcp.py tests/test_research_tools_schema.py
git commit -m "feat: add _dispatch helper and _TOOL_MODELS to research_tools_mcp"
```

---

### Task 4: Wire `_dispatch` into each tool function and call `_enforce_tool_models` at build time

**Files:**
- Modify: `research_tools_mcp.py`

- [ ] **Step 1: Update each tool function to call `_dispatch` at the top**

In `_build_research_tools_mcp`, update each tool as follows. The pattern is identical for every tool — add two lines at the top of the async function body:

```python
    @mcp.tool()
    async def analyze_trades(focus_question: str) -> str:
        err = _dispatch(AnalyzeTradesArgs, {"focus_question": focus_question})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def web_search(query: str, context: str = "") -> str:
        err = _dispatch(WebSearchArgs, {"query": query, "context": context})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def save_finding(
        finding: str,
        finding_type: str,
        status: str,
        evidence: str,
        scope: str,
        expires_if: str,
    ) -> str:
        err = _dispatch(SaveFindingArgs, {
            "finding": finding,
            "finding_type": finding_type,
            "status": status,
            "evidence": evidence,
            "scope": scope,
            "expires_if": expires_if,
        })
        if err:
            return err
        # ... existing body unchanged (trace + save_research_finding calls) ...

    @mcp.tool()
    async def search_findings(query: str, finding_type: str = "") -> str:
        err = _dispatch(SearchFindingsArgs, {"query": query, "finding_type": finding_type})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def memory_status() -> str:
        err = _dispatch(MemoryStatusArgs, {})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def list_past_theses(offset: int = 0, limit: int = 25) -> str:
        err = _dispatch(ListPastThesesArgs, {"offset": offset, "limit": limit})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def get_past_thesis(thesis_id: str) -> str:
        err = _dispatch(GetPastThesisArgs, {"thesis_id": thesis_id})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def list_experiment_results(
        order: str = "latest", offset: int = 0, limit: int = 10
    ) -> str:
        err = _dispatch(ListExperimentResultsArgs, {"order": order, "offset": offset, "limit": limit})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def get_experiment_result(thesis_id: str, detail: bool = False) -> str:
        err = _dispatch(GetExperimentResultArgs, {"thesis_id": thesis_id, "detail": detail})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def list_rejections(
        round_number: int | None = None,
        rejection_code: str | None = None,
        limit: int = 25,
    ) -> str:
        err = _dispatch(ListRejectionsArgs, {
            "round_number": round_number,
            "rejection_code": rejection_code,
            "limit": limit,
        })
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def get_rejection(round_number: int, thesis_id: str) -> str:
        err = _dispatch(GetRejectionArgs, {"round_number": round_number, "thesis_id": thesis_id})
        if err:
            return err
        # ... existing body unchanged ...

    @mcp.tool()
    async def rejection_pattern_summary(window_rounds: int = 10) -> str:
        err = _dispatch(RejectionPatternSummaryArgs, {"window_rounds": window_rounds})
        if err:
            return err
        # ... existing body unchanged ...
```

After the `track(mcp, ...)` call at the bottom of `_build_research_tools_mcp`, add:

```python
    _enforce_tool_models(mcp, _TOOL_MODELS)

    return mcp
```

- [ ] **Step 2: Run the full enforcement test**

```bash
pytest tests/test_research_tools_schema.py::test_build_raises_if_tool_missing_from_tool_models -v
```
Expected: `PASSED`

- [ ] **Step 3: Run the full test file**

```bash
pytest tests/test_research_tools_schema.py -v
```
Expected: all tests `PASSED`

- [ ] **Step 4: Run the full test suite via CI**

```bash
git add research_tools_mcp.py
git commit -m "feat: wire _dispatch into all MCP tools; enforce _TOOL_MODELS at build time"
git push origin HEAD
gh run watch --exit-status
```
Expected: CI green. Paste the run URL here before marking done.

---

## Self-Review

**Spec coverage:**
- ✅ `research_tools_schema.py` with one model per tool — Task 1
- ✅ Pydantic validation rules per tool (all 11 tools) — Task 1 + Task 2
- ✅ `_dispatch` helper returning `"VALIDATION ERROR:"` string — Task 3
- ✅ `_TOOL_MODELS` registration enforcement at startup (`TypeError`) — Task 3 + Task 4
- ✅ Tests: valid args pass, invalid args return error string, missing model raises — Task 2 + Task 3
- ✅ Error format consistent with existing `"REJECTED:"` pattern — `_dispatch` returns `"VALIDATION ERROR:"`

**Placeholder scan:** No TBDs. All code blocks are complete.

**Type consistency:** `_dispatch(model_cls, kwargs)` signature is consistent across Task 3 (definition) and Task 4 (call sites). `_enforce_tool_models(mcp, _TOOL_MODELS)` matches across Task 3 (definition) and Task 4 (call site).
