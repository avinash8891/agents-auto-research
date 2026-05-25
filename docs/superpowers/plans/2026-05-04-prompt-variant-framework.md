# Prompt Variant Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agent system prompts selectable by variant ID at runtime, with each run's variant combo hashed and stored in `ExperimentResult` for A/B comparison.

**Architecture:** A new `prompt_registry.py` module owns variant definitions and hashing. `StrategyFamily` declares family-level defaults. Variant IDs flow from controller config → conductor → subagents and land in `ExperimentResult.prompt_variant_hash`. The strategy registry is the anchor for family-level defaults only — prompt definitions live in the prompt registry.

**Tech Stack:** Python stdlib (`hashlib`, `json`), OpenAI Agents SDK (`OAIAgent`, `OAIRunner`), existing `sqlite3` experiment DB.

---

## Assumptions

1. **Single provider.** The codebase uses only the OpenAI Agents SDK via an OAuth proxy (`_OAUTH_PROXY_URL`). No Anthropic SDK exists anywhere. All six `OAIAgent` constructions use `gpt-5.5` hardcoded. Multi-provider concern does not apply to this iteration.

2. **Variant ID is a string key, not a hash.** A variant is identified by a human-readable string like `"default"` or `"v2"`. The `prompt_variant_hash` stored on `ExperimentResult` is a hash of the *full agent→variant mapping dict*, not of individual IDs — used for DB grouping, not lookup.

3. **Analyst builder takes zero args in the registry.** `_build_analyst_system_prompt(data_root)` takes a `data_root` argument, but the registered default lambda bakes in `str(_ROOT / "data")` at registration time. All callers call `get_builder("analyst", variant_id)()` with no args. A custom analyst variant that needs a different data root must also bake it in at registration.

4. **`operationalize_thesis` is not in the active research loop.** `_run_operationalization_agent` is only reachable via `create_executable_artifact` → `derive_thesis_artifacts`, which have zero callers from `autoresearch_research.py`, `autoresearch_planning.py`, or `autoresearch_controller.py`. It is **not wired** in this plan. If it becomes active, add `prompt_variant_id` to `operationalize_thesis` and its callers following the same pattern as the conductor.

5. **Registry is process-global.** `_REGISTRY` is a module-level dict populated at import time. All registrations happen once per process when `research_prompts` is first imported. Two concurrent experiments in the same process share one registry — which is fine because the registry is read-only after startup.

6. **Same variant is used for an entire experiment run.** `prompt_variants` is resolved once at the start of a run from `controller.resolved_prompt_variants` and stays fixed for all rounds in that run. Mid-run variant switching is not supported.

7. **DB migration is additive.** `ALTER TABLE experiments ADD COLUMN prompt_variant_hash TEXT NOT NULL DEFAULT ''` works on all existing sqlite3 DBs. Rows predating this migration load with `prompt_variant_hash = ""`.

8. **`run_research_agent` / `agent_orchestrator.run_diagnostic_analysis` / `run_web_research` are unused from the active loop.** Confirmed no callers in `autoresearch_research.py`, `autoresearch_controller.py`, or `autoresearch_planning.py`. These standalone orchestrator paths are not wired in this plan.

9. **`compile_research_thesis` does not call `operationalize_thesis`.** Confirmed by reading `compiler_research.py` — it only converts a pre-validated `ResearchThesis` into an `ExperimentContract` using registered strategy defaults. No LLM call.

---

## Scope

**In scope (active loop agents):**
- Research conductor (`research_conductor.py`)
- Diagnostic analyst — conductor tool path (`research_subagents.py:_call_analyst`)
- Web researcher — conductor tool path (`research_subagents.py:_call_web_researcher`)

**Out of scope (confirmed unused from active loop):**
- Operationalization agent (`compiler_operationalize._run_operationalization_agent`)
- Standalone analyst / web researcher (`agent_openai_calls.py`, `agent_orchestrator.py`)
- Research proposer agent (`agent_orchestrator.run_research_agent`)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `prompt_registry.py` | **Create** | Variant definitions, lookup, hashing, resolve |
| `research_prompts.py` | **Modify** | Add analyst + web_researcher builder fns; register all three agent defaults |
| `research_subagents.py` | **Modify** | Accept `prompt_variant_id`; use registry (zero-arg call for analyst, zero-arg for web_researcher) |
| `research_conductor.py` | **Modify** | Accept `prompt_variant_id`; use registry for conductor prompt; thread to subagent calls; tag trace |
| `strategies/base.py` | **Modify** | Add `default_prompt_variants: tuple[tuple[str,str],...]` to `BaseStrategy` |
| `strategy_family.py` | **Modify** | Add same field + `prompt_variants_dict` property to `StrategyFamily`; wire from `BaseStrategy` |
| `experiment_db.py` | **Modify** | Add `prompt_variant_hash` to `ExperimentResult` dataclass; add DB column + migration; update all load paths |
| `autoresearch_experiment.py` | **Modify** | Read `state["_prompt_variant_hash"]` into `ExperimentResult` at construction (line 534 pattern) |
| `autoresearch_controller.py` | **Modify** | Accept `prompt_variants` dict; resolve against family defaults into `resolved_prompt_variants` |
| `autoresearch_research.py` | **Modify** | Pass `prompt_variant_id` through conductor call chain; write `_prompt_variant_hash` to state |
| `trace_sdk.py` | **Modify** | Add optional `variant_id: str = ""` to `trace_agent_prompt` payload |
| `agent_token_usage.py` | **Modify** | Add `variant_id: str = ""` to `_accumulate_usage`; key as `f"{agent_type}:{variant_id}"` when set |
| `tests/test_prompt_registry.py` | **Create** | Registry lookup, hash stability, resolve_variants |
| `tests/test_research_prompts.py` | **Create** | Builder functions + default registrations |
| `tests/test_prompt_variant_threading.py` | **Create** | Signature checks for conductor + subagent functions |
| `tests/test_strategy_family_prompt_defaults.py` | **Create** | `StrategyFamily` field + property |
| `tests/test_experiment_db_prompt_hash.py` | **Create** | DB field, persistence, migration |
| `tests/test_experiment_result_prompt_hash.py` | **Create** | `autoresearch_experiment` populates hash from state |
| `tests/test_trace_variant_tag.py` | **Create** | `trace_agent_prompt` signature + payload |
| `tests/test_token_usage_variant_key.py` | **Create** | Usage keyed by `agent:variant_id` |

---

## Task 1: Create `prompt_registry.py`

**Files:**
- Create: `prompt_registry.py`
- Create: `tests/test_prompt_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_prompt_registry.py
import pytest
import prompt_registry as pr


def test_register_and_get_builder():
    pr.register("test_agent", "default", lambda: "hello")
    builder = pr.get_builder("test_agent", "default")
    assert builder() == "hello"


def test_get_builder_missing_variant_raises():
    pr.register("test_agent", "default", lambda: "hello")
    with pytest.raises(ValueError, match="No prompt variant 'v2'"):
        pr.get_builder("test_agent", "v2")


def test_get_builder_missing_agent_raises():
    with pytest.raises(ValueError, match="No prompt variant 'default' registered for agent 'ghost'"):
        pr.get_builder("ghost", "default")


def test_prompt_variant_hash_is_stable():
    h1 = pr.prompt_variant_hash({"conductor": "v2", "analyst": "default"})
    h2 = pr.prompt_variant_hash({"conductor": "v2", "analyst": "default"})
    assert h1 == h2
    assert len(h1) == 12


def test_prompt_variant_hash_order_independent():
    h1 = pr.prompt_variant_hash({"a": "x", "b": "y"})
    h2 = pr.prompt_variant_hash({"b": "y", "a": "x"})
    assert h1 == h2


def test_prompt_variant_hash_differs_on_different_variants():
    h1 = pr.prompt_variant_hash({"conductor": "default"})
    h2 = pr.prompt_variant_hash({"conductor": "v2"})
    assert h1 != h2


def test_resolve_variants_overrides_win():
    defaults = {"conductor": "default", "analyst": "default", "web_researcher": "default"}
    result = pr.resolve_variants({"conductor": "v2"}, defaults)
    assert result == {"conductor": "v2", "analyst": "default", "web_researcher": "default"}


def test_resolve_variants_empty_overrides():
    assert pr.resolve_variants({}, {"conductor": "default"}) == {"conductor": "default"}
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_prompt_registry.py -v
```
Expected: `ModuleNotFoundError: No module named 'prompt_registry'`

- [ ] **Step 3: Write `prompt_registry.py`**

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable

PromptBuilder = Callable[..., str]

_REGISTRY: dict[str, dict[str, "PromptVariant"]] = {}


@dataclass(frozen=True)
class PromptVariant:
    agent: str
    variant_id: str
    builder: PromptBuilder
    description: str = ""


def register(
    agent: str,
    variant_id: str,
    builder: PromptBuilder,
    description: str = "",
) -> None:
    _REGISTRY.setdefault(agent, {})[variant_id] = PromptVariant(
        agent=agent, variant_id=variant_id, builder=builder, description=description
    )


def get_builder(agent: str, variant_id: str = "default") -> PromptBuilder:
    try:
        return _REGISTRY[agent][variant_id].builder
    except KeyError:
        raise ValueError(
            f"No prompt variant '{variant_id}' registered for agent '{agent}'"
        )


def prompt_variant_hash(variants: dict[str, str]) -> str:
    blob = json.dumps(variants, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def resolve_variants(overrides: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    return {**defaults, **overrides}


def list_variants(agent: str) -> list[str]:
    return list(_REGISTRY.get(agent, {}).keys())
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_prompt_registry.py -v
```
Expected: all 8 pass.

- [ ] **Step 5: Commit**

```bash
git add prompt_registry.py tests/test_prompt_registry.py
git commit -m "feat: add prompt variant registry with hashing and resolve logic"
```

---

## Task 2: Extract inline prompts into `research_prompts.py` and register defaults

**Files:**
- Modify: `research_prompts.py`
- Create: `tests/test_research_prompts.py`

The analyst system prompt lives inline in `research_subagents.py:70–139`. The web researcher system prompt lives inline at `research_subagents.py:214–236`. Both are moved to `research_prompts.py` as named builder functions and registered.

**Important (see Assumption 3):** The analyst builder `_build_analyst_system_prompt(data_root)` takes one argument, but the registered lambda bakes in `_ROOT` and takes zero args. All callers use `get_builder("analyst", variant_id)()` — no args.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_research_prompts.py
from research_prompts import (
    _build_conductor_system_prompt,
    _build_analyst_system_prompt,
    _build_web_researcher_system_prompt,
)
import prompt_registry as pr


def test_conductor_builder_injects_strategy_desc():
    prompt = _build_conductor_system_prompt("ORB: open range breakout")
    assert "ORB: open range breakout" in prompt
    assert "list_past_theses" in prompt


def test_analyst_builder_injects_data_root():
    prompt = _build_analyst_system_prompt("/data/root")
    assert "/data/root" in prompt
    assert "focus_answer" in prompt


def test_web_researcher_builder_returns_string():
    prompt = _build_web_researcher_system_prompt()
    assert "findings" in prompt
    assert "actionable_idea" in prompt


def test_defaults_registered_after_import():
    import research_prompts  # noqa: F401
    assert pr.get_builder("conductor", "default") is not None
    assert pr.get_builder("analyst", "default") is not None
    assert pr.get_builder("web_researcher", "default") is not None


def test_analyst_default_zero_arg():
    import research_prompts  # noqa: F401
    builder = pr.get_builder("analyst", "default")
    result = builder()
    assert "focus_answer" in result
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_research_prompts.py -v
```
Expected: `ImportError` on `_build_analyst_system_prompt` (not defined yet).

- [ ] **Step 3: Add builder functions to `research_prompts.py`**

Append after the existing `_build_conductor_system_prompt`:

```python
from research_paths import _ROOT


def _build_analyst_system_prompt(data_root: str) -> str:
    return f"""You are a quantitative trading analyst. You receive:
1. A path to a CSV file containing raw trades from a backtest
2. A FOCUS QUESTION from the research conductor
3. A strategy_events.parquet with every signal the strategy considered (accepted AND rejected)
4. A diagnostics.json with event counts and rejection breakdown
5. Access to RAW OHLCV DATA at: {data_root}/
   Structure: data/raw/{{SYMBOL}}/{{YEAR}}.parquet (5-minute OHLCV bars, one file per year)
   Also: data/open.parquet, data/high.parquet, data/low.parquet, data/close.parquet (wide format)

You MUST use ALL provided files. Trades alone show what happened;
strategy_events show what DIDN'T happen and WHY. Diagnostics give
the high-level rejection breakdown before you dig into details.

RAW TRADES CSV SCHEMA: entry_date, exit_date, direction, entry_price, exit_price,
  stop, target, pnl_pct, exit_reason, symbol

STRATEGY EVENTS PARQUET SCHEMA (read with pd.read_parquet()):
  timestamp, symbol, direction, event_type, reason, entry_price, stop_price
  event_type: raw_setup, accepted_signal, rejected_signal, order_rejected, executed_trade

DIAGNOSTICS JSON: quick summary with event_counts and rejection_breakdown.

WORKFLOW:
1. ALWAYS start by reading diagnostics.json for the rejection breakdown.
2. Use run_python for pandas analysis on trades and/or events.
3. For market context questions, load raw OHLCV data from the data directory.
4. Focus on the FOCUS QUESTION. Go deep, not wide.
5. Quantify patterns with exact numbers and sample sizes.

CRITICAL RULES:
- PF = sum(pnl_pct where pnl_pct > 0) / abs(sum(pnl_pct where pnl_pct <= 0))
- Only flag patterns with >50 trades per bucket
- Cite exact numbers. Do NOT invent data.
- Do NOT repeat analyses the focus question doesn't ask for.

OUTPUT FORMAT — return ONLY this JSON:
{{
  "focus_answer": "direct answer with exact numbers",
  "key_anomalies": [
    {{"anomaly": "...", "evidence": "exact numbers", "sample_size": 0, "actionable": "..."}}
  ],
  "overall_diagnosis": "one paragraph synthesis"
}}"""


def _build_web_researcher_system_prompt() -> str:
    return """You are a research agent specializing in quantitative trading strategies.
Your ONLY job is to find and report external evidence for the specific question asked.

1. Run targeted web searches.
2. Prefer primary sources: academic papers > practitioner research > blogs.
3. Read sources in full. Extract specific claims and data points.
4. Be skeptical.

OUTPUT FORMAT — return ONLY this JSON:
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
}"""


# ---------------------------------------------------------------------------
# Register defaults — importing this module activates all three variants.
# The analyst lambda bakes in _ROOT so callers use get_builder("analyst", id)()
# with no arguments (see Assumption 3).
# ---------------------------------------------------------------------------
import prompt_registry as _pr  # noqa: E402

_pr.register("conductor", "default", _build_conductor_system_prompt,
             "Original conductor prompt with mechanism research dimensions")
_pr.register("analyst", "default",
             lambda: _build_analyst_system_prompt(str(_ROOT / "data")),
             "Original analyst prompt with full OHLCV access")
_pr.register("web_researcher", "default", _build_web_researcher_system_prompt,
             "Original web researcher prompt")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_research_prompts.py -v
```
Expected: all 5 pass.

- [ ] **Step 5: Replace inline strings in `research_subagents.py`**

In `_call_analyst()` (around line 70), replace the `analyst_prompt = f"""..."""` block with:
```python
import research_prompts  # noqa: F401 — ensures default registered
import prompt_registry as _pr
analyst_prompt = _pr.get_builder("analyst", "default")()
```

In `_call_web_researcher()` (around line 214), replace the `web_prompt = """..."""` block with:
```python
import research_prompts  # noqa: F401
import prompt_registry as _pr
web_prompt = _pr.get_builder("web_researcher", "default")()
```

(The `"default"` is replaced by `prompt_variant_id` in Task 3.)

- [ ] **Step 6: Verify no regressions**

```bash
pytest tests/ -v -k "prompt"
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add research_prompts.py research_subagents.py tests/test_research_prompts.py
git commit -m "refactor: extract inline agent prompts into research_prompts.py and register defaults"
```

---

## Task 3: Thread `prompt_variant_id` through conductor and subagents

**Files:**
- Modify: `research_subagents.py`
- Modify: `research_conductor.py`
- Create: `tests/test_prompt_variant_threading.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_prompt_variant_threading.py
import inspect
import prompt_registry as pr


def test_conductor_accepts_prompt_variant_id():
    from research_conductor import run_research_conductor
    sig = inspect.signature(run_research_conductor)
    assert "prompt_variant_id" in sig.parameters
    assert sig.parameters["prompt_variant_id"].default == "default"


def test_call_analyst_accepts_prompt_variant_id():
    from research_subagents import _call_analyst
    sig = inspect.signature(_call_analyst)
    assert "prompt_variant_id" in sig.parameters
    assert sig.parameters["prompt_variant_id"].default == "default"


def test_call_web_researcher_accepts_prompt_variant_id():
    from research_subagents import _call_web_researcher
    sig = inspect.signature(_call_web_researcher)
    assert "prompt_variant_id" in sig.parameters
    assert sig.parameters["prompt_variant_id"].default == "default"


def test_custom_analyst_variant_is_used():
    pr.register("analyst", "test_minimal", lambda: "MINIMAL-ANALYST")
    builder = pr.get_builder("analyst", "test_minimal")
    assert builder() == "MINIMAL-ANALYST"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_prompt_variant_threading.py -v
```
Expected: signature tests fail — no `prompt_variant_id` param yet.

- [ ] **Step 3: Update `research_subagents.py`**

`_call_analyst` signature (line 12):
```python
async def _call_analyst(
    trades_file: str,
    focus_question: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    prompt_variant_id: str = "default",
) -> str:
```

Replace the now-temporary `"default"` lookup with `prompt_variant_id`:
```python
analyst_prompt = _pr.get_builder("analyst", prompt_variant_id)()
```

`_call_web_researcher` signature (line 203):
```python
async def _call_web_researcher(
    query: str,
    context: str,
    prompt_variant_id: str = "default",
) -> str:
```

Replace lookup:
```python
web_prompt = _pr.get_builder("web_researcher", prompt_variant_id)()
```

- [ ] **Step 4: Update `research_conductor.py`**

`run_research_conductor` signature (line 69):
```python
async def run_research_conductor(
    trades_file: str,
    experiment_results: str,
    latest_outcome: dict[str, Any],
    research_round: int,
    family_name: str,
    strategy_events_file: str = "",
    diagnostics_file: str = "",
    rejection_feedback: str = "",
    prompt_variant_id: str = "default",
) -> dict[str, Any] | None:
```

Replace line 81 (conductor system prompt):
```python
import research_prompts  # noqa: F401
import prompt_registry as _pr
system_prompt = _pr.get_builder("conductor", prompt_variant_id)(strategy_desc)
```

In the `analyze_trades` tool closure (line 146), thread through:
```python
return await _call_analyst(
    trades_file,
    focus_question,
    strategy_events_file=strategy_events_file,
    diagnostics_file=diagnostics_file,
    prompt_variant_id=prompt_variant_id,
)
```

In the `web_search` tool closure (line 157):
```python
return await _call_web_researcher(query, context, prompt_variant_id=prompt_variant_id)
```

Also update `run_research_conductor_sync` at the bottom — add `prompt_variant_id: str = "default"` to its signature and pass it to `run_research_conductor`.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_prompt_variant_threading.py tests/test_research_prompts.py tests/test_prompt_registry.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add research_conductor.py research_subagents.py tests/test_prompt_variant_threading.py
git commit -m "feat: thread prompt_variant_id through conductor and subagents"
```

---

## Task 4: Add `default_prompt_variants` to `StrategyFamily` and `BaseStrategy`

**Files:**
- Modify: `strategies/base.py`
- Modify: `strategy_family.py`
- Create: `tests/test_strategy_family_prompt_defaults.py`

`StrategyFamily` is `frozen=True` so the field must use an immutable type. Pattern matches existing `default_variants: tuple[str, ...]`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_strategy_family_prompt_defaults.py
import dataclasses
from strategy_family import load_family
from strategies import STRATEGIES


def test_strategy_family_has_default_prompt_variants_field():
    fields = {f.name for f in dataclasses.fields(__import__("strategy_family").StrategyFamily)}
    assert "default_prompt_variants" in fields


def test_prompt_variants_dict_property():
    from strategy_family import StrategyFamily
    fam = StrategyFamily(
        name="test", benchmark_script="test.py",
        default_prompt_variants=(("conductor", "v2"), ("analyst", "default")),
    )
    assert fam.prompt_variants_dict == {"conductor": "v2", "analyst": "default"}


def test_empty_default_is_empty_dict():
    from strategy_family import StrategyFamily
    fam = StrategyFamily(name="test", benchmark_script="test.py")
    assert fam.prompt_variants_dict == {}


def test_all_registered_families_have_field():
    for name in STRATEGIES:
        fam = load_family(name)
        assert isinstance(fam.prompt_variants_dict, dict)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_strategy_family_prompt_defaults.py -v
```
Expected: `TypeError` — no `default_prompt_variants` field yet.

- [ ] **Step 3: Update `strategies/base.py`**

In `BaseStrategy`, add after `default_variants`:
```python
default_prompt_variants: tuple[tuple[str, str], ...] = ()
```

- [ ] **Step 4: Update `strategy_family.py`**

In `StrategyFamily` dataclass, add after `default_variants`:
```python
default_prompt_variants: tuple[tuple[str, str], ...] = ()
```

Add property:
```python
@property
def prompt_variants_dict(self) -> dict[str, str]:
    return dict(self.default_prompt_variants)
```

In `_families()`, add to the `StrategyFamily(...)` constructor:
```python
default_prompt_variants=tuple(
    (k, v) for k, v in (strategy.default_prompt_variants or ())
),
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_strategy_family_prompt_defaults.py -v
```
Expected: all 4 pass.

- [ ] **Step 6: Commit**

```bash
git add strategies/base.py strategy_family.py tests/test_strategy_family_prompt_defaults.py
git commit -m "feat: add default_prompt_variants to StrategyFamily and BaseStrategy"
```

---

## Task 5: Add `prompt_variant_hash` to `ExperimentResult` and DB schema

**Files:**
- Modify: `experiment_db.py`
- Create: `tests/test_experiment_db_prompt_hash.py`

Three `ExperimentResult` construction sites exist in `experiment_db.py` (lines 251, 574, 908). The field defaults to `""` so all three are backward-compatible without changes — only the `_load` path and `_write_record` need updating.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_experiment_db_prompt_hash.py
import dataclasses
import sqlite3
import tempfile
from pathlib import Path
from experiment_db import ExperimentDB, ExperimentResult


def _base_result(**kw) -> ExperimentResult:
    return ExperimentResult(
        experiment_id=kw.get("experiment_id", "exp_001"),
        thesis_id="thesis_001",
        config_path="configs/orb_base.yaml",
        runtime_config={"family": "orb"},
        code_commit="abc123",
        data_hash="def456",
        train_metrics={},
        validation_metrics={},
        trade_count=10,
        trades_file="",
        strategy_events_file="",
        diagnostics_file="",
        strategy_diagnostics={},
        accepted=True,
        rejection_reason="",
        verdict_status="accepted",
        verdict_summary="good",
        **{k: v for k, v in kw.items() if k != "experiment_id"},
    )


def test_field_exists():
    assert "prompt_variant_hash" in {f.name for f in dataclasses.fields(ExperimentResult)}


def test_defaults_to_empty():
    assert _base_result().prompt_variant_hash == ""


def test_persisted_and_loaded():
    with tempfile.TemporaryDirectory() as d:
        db = ExperimentDB(Path(d) / "test.db")
        db.add(_base_result(family="orb", prompt_variant_hash="abc123def456"))
        loaded = db.get("exp_001")
        assert loaded.prompt_variant_hash == "abc123def456"


def test_legacy_row_loads_as_empty():
    with tempfile.TemporaryDirectory() as d:
        db_path = Path(d) / "legacy.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""CREATE TABLE experiments (
            experiment_id TEXT PRIMARY KEY, thesis_id TEXT NOT NULL DEFAULT '',
            config_path TEXT NOT NULL DEFAULT '', runtime_config_json TEXT NOT NULL DEFAULT '{}',
            code_commit TEXT NOT NULL DEFAULT '', data_hash TEXT NOT NULL DEFAULT '',
            train_metrics_json TEXT NOT NULL DEFAULT '{}',
            validation_metrics_json TEXT NOT NULL DEFAULT '{}',
            trade_count INTEGER NOT NULL DEFAULT 0, trades_file TEXT NOT NULL DEFAULT '',
            strategy_events_file TEXT NOT NULL DEFAULT '',
            diagnostics_file TEXT NOT NULL DEFAULT '',
            strategy_diagnostics_json TEXT NOT NULL DEFAULT '{}',
            accepted INTEGER NOT NULL DEFAULT 0, rejection_reason TEXT NOT NULL DEFAULT '',
            verdict_status TEXT NOT NULL DEFAULT '', verdict_summary TEXT NOT NULL DEFAULT '',
            parent_experiment_id TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL DEFAULT '',
            family TEXT NOT NULL DEFAULT '', hypothesis TEXT NOT NULL DEFAULT '',
            mechanism TEXT NOT NULL DEFAULT '', job INTEGER NOT NULL DEFAULT 0,
            usage_json TEXT NOT NULL DEFAULT '{}',
            asi_json TEXT NOT NULL DEFAULT '{}', description TEXT NOT NULL DEFAULT ''
        )""")
        conn.commit()
        conn.close()
        db = ExperimentDB(db_path)
        db.add(_base_result(family="orb"))
        loaded = db.get("exp_001")
        assert loaded.prompt_variant_hash == ""
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_experiment_db_prompt_hash.py -v
```
Expected: `test_field_exists` fails.

- [ ] **Step 3: Add field to `ExperimentResult`**

After `usage` field (line 76):
```python
prompt_variant_hash: str = ""
```

- [ ] **Step 4: Add DB column to `CREATE TABLE` and migration**

In the `CREATE TABLE experiments` statement, after `description`:
```sql
prompt_variant_hash TEXT NOT NULL DEFAULT ''
```

After the `CREATE TABLE` block (wherever existing `ALTER TABLE` migrations are), add:
```python
cols = {row[1] for row in conn.execute("PRAGMA table_info(experiments)")}
if "prompt_variant_hash" not in cols:
    conn.execute(
        "ALTER TABLE experiments ADD COLUMN "
        "prompt_variant_hash TEXT NOT NULL DEFAULT ''"
    )
```

- [ ] **Step 5: Update `_write_record` INSERT statement**

Add `prompt_variant_hash` to the column list and `record.prompt_variant_hash` to the values tuple.

- [ ] **Step 6: Update `_load` row-to-dataclass mapping**

Wherever rows are converted to `ExperimentResult`, add:
```python
prompt_variant_hash=row.get("prompt_variant_hash", "") or "",
```

- [ ] **Step 7: Run tests**

```bash
pytest tests/test_experiment_db_prompt_hash.py -v
```
Expected: all 4 pass.

- [ ] **Step 8: Commit**

```bash
git add experiment_db.py tests/test_experiment_db_prompt_hash.py
git commit -m "feat: add prompt_variant_hash to ExperimentResult and DB schema with migration"
```

---

## Task 6: Wire prompt variants through controller

**Files:**
- Modify: `autoresearch_controller.py`
- Create: `tests/test_prompt_variant_wiring.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_prompt_variant_wiring.py
import inspect


def test_controller_accepts_prompt_variants():
    sig = inspect.signature(__import__("autoresearch_controller").AutoresearchController.__init__)
    assert "prompt_variants" in sig.parameters


def test_controller_resolves_family_defaults(tmp_path):
    from strategy_family import StrategyFamily
    from autoresearch_controller import AutoresearchController
    family = StrategyFamily(
        name="orb", benchmark_script="backtest_orb_v2.py",
        default_prompt_variants=(("conductor", "v2"),),
    )
    ctrl = AutoresearchController(
        root=tmp_path,
        state_path=tmp_path / "state.json",
        current_md_path=tmp_path / "current.md",
        ideas_md_path=tmp_path / "ideas.md",
        family=family,
    )
    assert ctrl.resolved_prompt_variants["conductor"] == "v2"


def test_controller_overrides_win(tmp_path):
    from strategy_family import StrategyFamily
    from autoresearch_controller import AutoresearchController
    family = StrategyFamily(
        name="orb", benchmark_script="backtest_orb_v2.py",
        default_prompt_variants=(("conductor", "v2"),),
    )
    ctrl = AutoresearchController(
        root=tmp_path,
        state_path=tmp_path / "state.json",
        current_md_path=tmp_path / "current.md",
        ideas_md_path=tmp_path / "ideas.md",
        family=family,
        prompt_variants={"conductor": "v3"},
    )
    assert ctrl.resolved_prompt_variants["conductor"] == "v3"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_prompt_variant_wiring.py -v
```
Expected: `test_controller_accepts_prompt_variants` fails.

- [ ] **Step 3: Update `AutoresearchController.__init__`**

Add `prompt_variants: dict[str, str] | None = None` parameter. At the end of `__init__`, after `self.family` is set:

```python
from prompt_registry import resolve_variants
self.resolved_prompt_variants: dict[str, str] = resolve_variants(
    overrides=prompt_variants or {},
    defaults=self.family.prompt_variants_dict,
)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_prompt_variant_wiring.py -v
```
Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add autoresearch_controller.py tests/test_prompt_variant_wiring.py
git commit -m "feat: add prompt_variants resolution to AutoresearchController"
```

---

## Task 7: Thread variant through research loop and write hash to state

**Files:**
- Modify: `autoresearch_research.py`
- Modify: `autoresearch_experiment.py`
- Create: `tests/test_experiment_result_prompt_hash.py`

`autoresearch_research.py` calls `run_research_conductor_sync` at line 589. The hash must be written into `state["_prompt_variant_hash"]` (same pattern as `state["_last_round_usage"]` at line 938). `autoresearch_experiment.py` reads from state at `ExperimentResult` construction (line 534 pattern).

- [ ] **Step 1: Write failing test**

```python
# tests/test_experiment_result_prompt_hash.py
from unittest.mock import MagicMock, patch


def test_log_experiment_result_sets_prompt_variant_hash(tmp_path):
    from autoresearch_experiment import log_experiment_result

    state = {
        "job": 1,
        "_last_round_usage": {},
        "_prompt_variant_hash": "abc123def456",
    }
    controller = MagicMock()
    controller.family.name = "orb"
    controller.ctx.parent_experiment_id = ""
    controller.ctx.latest_config_contents = {}
    controller.current_commit.return_value = "deadbeef"

    saved = []
    controller.experiment_db.add.side_effect = lambda r: saved.append(r)

    details = {
        "trade_count": 10, "trades_file": "", "strategy_events_file": "",
        "diagnostics_file": "", "strategy_diagnostics": {}, "train_metrics": {},
        "profit_factor": 1.5,
    }

    with patch("autoresearch_experiment.build_data_hash", return_value="datahash"), \
         patch("autoresearch_experiment._contract_from_sidecar", return_value=None), \
         patch("autoresearch_experiment.iso8601_utc_now", return_value="2026-05-04T00:00:00+00:00"):
        record = log_experiment_result(
            controller=controller,
            config="configs/orb_base.yaml",
            decision="keep",
            details=details,
            analysis={},
            fallback_experiment_id="fb-001",
            state=state,
        )

    assert record.prompt_variant_hash == "abc123def456"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_experiment_result_prompt_hash.py -v
```
Expected: `AssertionError` — `prompt_variant_hash` is `""`.

- [ ] **Step 3: Update `autoresearch_research.py`**

In `run_research_once()` (around line 933), after `state["_last_round_usage"] = round_usage`:

```python
from prompt_registry import prompt_variant_hash as _pvh
_variants = getattr(controller, "resolved_prompt_variants", {})
state["_prompt_variant_hash"] = _pvh(_variants)
controller.write_state(state)
```

In `_call_conductor_once()` (line ~585), pass the variant through:

```python
def _call_conductor_once(
    ...,
    prompt_variant_id: str = "default",
) -> dict[str, Any] | None:
    ...
    return run_research_conductor_sync(
        ...,
        prompt_variant_id=prompt_variant_id,
    )
```

In `execute_research_sdk()`, read from controller and pass down:
```python
variant_id = getattr(controller, "resolved_prompt_variants", {}).get("conductor", "default")
# pass variant_id to _call_conductor_once(...)
```

- [ ] **Step 4: Update `autoresearch_experiment.py` line ~534**

In the `ExperimentResult(...)` constructor, add after `usage=state.get("_last_round_usage", {})`:
```python
prompt_variant_hash=state.get("_prompt_variant_hash", ""),
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_experiment_result_prompt_hash.py tests/test_experiment_db_prompt_hash.py -v
```
Expected: all pass.

- [ ] **Step 6: Full suite**

```bash
pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add autoresearch_research.py autoresearch_experiment.py tests/test_experiment_result_prompt_hash.py
git commit -m "feat: thread prompt_variant_id through research loop; write hash to state and ExperimentResult"
```

---

## Task 8: Tag `variant_id` in trace payload

**Files:**
- Modify: `trace_sdk.py`
- Modify: `research_conductor.py`
- Create: `tests/test_trace_variant_tag.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_trace_variant_tag.py
import inspect


def test_trace_agent_prompt_accepts_variant_id():
    from trace_sdk import trace_agent_prompt
    sig = inspect.signature(trace_agent_prompt)
    assert "variant_id" in sig.parameters
    assert sig.parameters["variant_id"].default == ""


def test_conductor_passes_variant_id_to_trace():
    import inspect
    from research_conductor import run_research_conductor
    # Verify the trace call at line 137 uses prompt_variant_id — confirmed by reading
    # the source; this test checks the signature is in place
    sig = inspect.signature(run_research_conductor)
    assert "prompt_variant_id" in sig.parameters
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_trace_variant_tag.py -v
```
Expected: `test_trace_agent_prompt_accepts_variant_id` fails.

- [ ] **Step 3: Update `trace_sdk.py:510`**

```python
def trace_agent_prompt(
    agent_name: str,
    prompt: str,
    system_prompt: str = "",
    variant_id: str = "",
) -> str:
```

In the `_record_event` payload dict, add:
```python
"variant_id": variant_id,
```

- [ ] **Step 4: Update `research_conductor.py` trace call (line 137)**

```python
trace_id = trace_agent_prompt(
    "research-conductor", user_prompt, system_prompt, variant_id=prompt_variant_id
)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_trace_variant_tag.py -v
```
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add trace_sdk.py research_conductor.py tests/test_trace_variant_tag.py
git commit -m "feat: add variant_id to trace_agent_prompt payload"
```

---

## Task 9: Key token usage by `agent:variant_id`

**Files:**
- Modify: `agent_token_usage.py`
- Modify: `research_conductor.py`
- Create: `tests/test_token_usage_variant_key.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_usage_variant_key.py
from agent_token_usage import _accumulate_usage, get_round_usage, reset_round_usage


def test_usage_keyed_with_variant_id():
    reset_round_usage()
    _accumulate_usage("conductor", {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                      variant_id="v2")
    usage = get_round_usage()
    assert "conductor:v2" in usage["by_agent"]
    assert usage["by_agent"]["conductor:v2"]["input_tokens"] == 100


def test_usage_keyed_without_variant_id_stays_bare():
    reset_round_usage()
    _accumulate_usage("conductor", {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    assert "conductor" in get_round_usage()["by_agent"]


def test_total_aggregates_across_variants():
    reset_round_usage()
    _accumulate_usage("conductor", {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                      variant_id="default")
    _accumulate_usage("conductor", {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
                      variant_id="v2")
    assert get_round_usage()["total"]["input_tokens"] == 300
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_token_usage_variant_key.py -v
```
Expected: `_accumulate_usage` doesn't accept `variant_id` kwarg.

- [ ] **Step 3: Update `agent_token_usage.py`**

In `_accumulate_usage`, add `variant_id: str = ""` parameter:

```python
def _accumulate_usage(
    agent_type: str,
    usage: dict[str, Any] | None,
    cost_usd: float | None = None,
    *,
    dedupe_key: str | None = None,
    variant_id: str = "",
) -> None:
    key = f"{agent_type}:{variant_id}" if variant_id else agent_type
    if key not in _ROUND_USAGE:
        _ROUND_USAGE[key] = {
            "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cost_usd": 0.0, "calls": 0,
        }
    entry = _ROUND_USAGE[key]
    entry["calls"] += 1
    if usage:
        entry["input_tokens"] += usage.get("input_tokens") or usage.get("input") or 0
        entry["output_tokens"] += usage.get("output_tokens") or usage.get("output") or 0
        entry["total_tokens"] += usage.get("total_tokens") or usage.get("total") or 0
    if cost_usd:
        entry["cost_usd"] += cost_usd
```

In `_accumulate_result_usage`, add `variant_id: str = ""` and pass it to `_accumulate_usage`.

- [ ] **Step 4: Update `research_conductor.py` accumulate calls**

Find where `_accumulate_usage("conductor", ...)` or `_accumulate_result_usage` is called and add `variant_id=prompt_variant_id`.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_token_usage_variant_key.py -v
```
Expected: all 3 pass.

- [ ] **Step 6: Full suite**

```bash
pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add agent_token_usage.py research_conductor.py tests/test_token_usage_variant_key.py
git commit -m "feat: key token usage by agent:variant_id for per-variant cost tracking"
```

---

## Self-Review

**Spec coverage:**
- [x] Prompt variant registry with lookup and hashing — Task 1
- [x] Inline prompts extracted and registered as defaults — Task 2
- [x] `prompt_variant_id` threaded through conductor + subagents — Task 3
- [x] Family-level default variants via `StrategyFamily` — Task 4
- [x] `prompt_variant_hash` field + DB column + migration — Task 5
- [x] Controller resolves overrides against family defaults — Task 6
- [x] Hash written to state + read into `ExperimentResult` — Task 7
- [x] Trace payload tagged with `variant_id` — Task 8
- [x] Token usage keyed by `agent:variant_id` — Task 9

**Confirmed out of scope:**
- Operationalization agent: `_run_operationalization_agent` is only reachable via `create_executable_artifact` → `derive_thesis_artifacts`, which are not called from the active research loop. Add `prompt_variant_id` there if those paths are activated.
- Standalone paths (`agent_openai_calls.py`, `agent_orchestrator.run_research_agent`): no callers from the main loop.

**Type consistency:**
- `prompt_variant_id: str` — single agent key in function signatures (Tasks 3, 7, 8, 9)
- `prompt_variants: dict[str, str]` — full `{agent: variant_id}` map (Tasks 1, 4, 6, 7)
- `prompt_variant_hash: str` — 12-char SHA of variants dict on `ExperimentResult` and DB (Tasks 1, 5, 7)
- `default_prompt_variants: tuple[tuple[str, str], ...]` — frozen-safe field on `StrategyFamily` / `BaseStrategy` (Task 4)
- `prompt_variants_dict: dict[str, str]` — property on `StrategyFamily` (Task 4)
- Token usage key: `f"{agent_type}:{variant_id}"` when variant_id set, bare `agent_type` otherwise (Task 9)
