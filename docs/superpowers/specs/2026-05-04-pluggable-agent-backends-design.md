# Pluggable Agent Backends and Multi-Provider Web Search

**Date:** 2026-05-04
**Status:** Design (revised after blast-radius audit)

## 1. Goal

Decouple the agent runtime and the web search provider so each agent role (research conductor, diagnostic analyst, builder) can pick its model SDK independently (Claude Agent SDK or OpenAI Agents SDK) and the web search tool can switch between Exa, Brave, Parallel, or OpenAI's hosted WebSearchTool. Switching is manual via CLI args.

Both SDKs run in their **full native form**. There is no SDK substitution, no chat-completions proxy hack, no manual Messages API loop. Cross-backend integration happens at the tool boundary using the **agent-as-a-tool pattern**.

## 2. Non-Goals

- Automatic fallback on credit exhaustion. Operator switches manually.
- LangChain or other agent frameworks. Additive later.
- Streaming output normalization beyond `final_output` extraction.
- VPS provisioning of OAuth proxy services (separate `claude-oauth.service` systemd unit). Treated as an out-of-band prerequisite.
- Refactoring the three duplicate `_run_coroutine_sync` thread wrappers into a shared util. Pre-existing tech debt; preserved as-is.

## 3. Background — Proven Reference Implementation

The architecture below is **already proven** in `backtesting-platform/evolution-v3/orb-research/research_conductor.py`. That file implements:

- A research conductor running on `claude-agent-sdk` with `model="claude-opus-4-6"`
- An in-process MCP server (`create_sdk_mcp_server`) named `research-tools`
- MCP tools the conductor calls (`mcp__research-tools__analyze_trades`, `mcp__research-tools__web_search`, etc.)
- Tool **bodies** that internally spawn full `OAIAgent` sub-agents:
  - `analyze_trades` → spawns `OAIAgent(name="codex-analyst", model="gpt-5.5", tools=[read_file, run_python])`
  - `web_search` → spawns `OAIAgent(name="web-researcher", model="gpt-5.5", tools=[WebSearchTool()])`
- Each sub-agent runs to completion via `OAIRunner.run_streamed`, with its own retry loop, tracing, and JSON parsing.
- Result strings flow back through the MCP tool return value into the Claude conductor's context.

This pattern works because each runtime stays in its native API context. Claude runs Claude. OpenAI runs OpenAI. They communicate at the tool layer through plain strings. `WebSearchTool` stays inside its native Responses API call where it functions correctly.

**Two pitfalls in the reference impl** that this spec avoids:

- The reference impl calls `_accumulate_usage("conductor", message.usage, ...)` AND `_accumulate_usage("conductor", message.model_usage)` (lines 1036–1039). Without `dedupe_key` this **double-counts** Claude tokens. This spec mandates `dedupe_key` on every accumulator call (see §10).
- The reference impl uses ad-hoc `trace_id` strings (`f"web-{query[:40]...}"`) that bypass `trace_agent_prompt`. Two simultaneous calls with the same first-40-char query collide and overwrite artifact files. This spec routes every backend call through `trace_agent_prompt` (see §11).

## 4. CLI Surface

`vps_runner.py` and `autoresearch_controller.py` accept four new optional args. `vps_runner.py` forwards them to the controller through `build_remote_command()`.

| Arg | Values | Default | Used by |
| --- | --- | --- | --- |
| `--research-backend` | `claude`, `openai` | `claude` | Research conductor's own loop |
| `--analyst-backend` | `claude`, `openai` | `openai` | Both analyst implementations |
| `--builder-backend` | `claude`, `openai` | `claude` | Compiler/builder agent |
| `--search-backend` | `exa`, `brave`, `parallel`, `openai` | `exa` | Web search tool body |

**Operational note:** the default `research-backend=claude` is a **cost-tier change** from today's behavior (production runs OpenAI conductor on `gpt-5.5`). The first migrated run pays Opus prices. Document the cutover separately from the code change.

## 5. Auth — Env Vars Only

Secrets stay out of CLI args. Required env vars depend on selected backends.

| Env var | Required when | Purpose |
| --- | --- | --- |
| `OPENAI_PROXY_URL` | any agent backend = `openai`, OR search backend = `openai` | OpenAI OAuth proxy URL |
| `CLAUDE_PROXY_URL` (a.k.a. `ANTHROPIC_BASE_URL`) | any agent backend = `claude` | Claude OAuth proxy URL (vibe-proxy) |
| `ANTHROPIC_API_KEY` | any agent backend = `claude` | Sentinel key for the proxy; SDK requires it to be present |
| `EXA_API_KEY` | search backend = `exa` | Exa direct API |
| `BRAVE_API_KEY` | search backend = `brave` | Brave Search API |
| `PARALLEL_API_KEY` | search backend = `parallel` | Parallel AI search API |

`vps_runner.py` propagates required env vars over SSH alongside existing `AUTORESEARCH_*` exports. Today `build_remote_command` exports **zero** agent env vars (proxies are started on the VPS as systemd units); this is a real new behavior.

The Claude Agent SDK Python package self-bundles the Claude Code CLI; no separate `npm install`. SDK errors (`CLINotFoundError`, `CLIConnectionError`, `ProcessError`, `CLIJSONDecodeError`) are caught in the Claude adapter.

## 6. Architecture

### 6.1 Cross-backend integration: agent-as-a-tool

A Claude conductor calling an "OpenAI sub-agent" is **not** the Claude SDK invoking OpenAI's WebSearchTool directly. It is the Claude conductor calling an MCP tool whose body spawns a separate `OAIAgent`, runs it to completion via `OAIRunner.run_streamed`, and returns its `final_output` (a JSON string) to Claude. From Claude's perspective it's just an MCP tool returning a string. From the sub-agent's perspective it's a normal OpenAI Agents SDK run.

Every cross-backend boundary follows this pattern: a tool body that runs a sub-agent in its native SDK and returns a string.

### 6.2 New package layout

```
providers/
  __init__.py
  config.py                  # ProviderConfig + validate()
  agent/
    protocol.py              # AgentBackend Protocol; AgentResult; TokenUsage
    tool_spec.py             # Tool descriptor (unified, SDK-agnostic)
    openai_backend.py        # OpenAI Agents SDK adapter; Tool → @function_tool
    claude_backend.py        # Claude Agent SDK adapter; Tool → @tool + MCP server;
                             # emits manual OTel spans (CLI subprocess opacity)
    factory.py               # get_backend(role, config) → AgentBackend
  search/
    protocol.py              # SearchResult dataclass
    exa.py                   # AsyncExa wrapper
    brave.py                 # Brave HTTP client
    parallel.py              # Parallel AI HTTP client
    openai_subagent.py       # spawns OAIAgent(tools=[WebSearchTool()]); existing logic
    factory.py               # get_search_tool_spec(backend) → Tool descriptor
```

Estimated `providers/` LOC: **~600**.

### 6.3 ProviderConfig

```python
@dataclass(frozen=True)
class ProviderConfig:
    research_backend: Literal["claude", "openai"]
    analyst_backend:  Literal["claude", "openai"]
    builder_backend:  Literal["claude", "openai"]
    search_backend:   Literal["exa", "brave", "parallel", "openai"]

    def validate(self) -> None:
        for role in ("research", "analyst", "builder"):
            _require_env_for_agent(getattr(self, f"{role}_backend"))
        _require_env_for_search(self.search_backend)
```

Built once in `autoresearch_controller.main()` from CLI args, validated, then threaded by reference. No globals (note: pre-existing `_REFINEMENT_RECORDER` singleton in `research_conductor.py:35` survives — out of scope).

### 6.4 Tool descriptor (unified)

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    json_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]
```

Adapters convert `Tool` to either OpenAI `@function_tool` or Claude `@tool` + MCP server. Tool definitions used everywhere: `analyze_trades`, `web_search`, `save_finding`, `search_findings`, `memory_status`, `read_file`, `run_python`, `list_past_theses`.

### 6.5 AgentResult / TokenUsage

```python
@dataclass(frozen=True)
class TokenUsage:
    input: int = 0
    output: int = 0
    total: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: float | None = None
    backend: str = ""
    model: str = ""

@dataclass(frozen=True)
class AgentResult:
    output: str
    usage: TokenUsage
    raw: Any
```

`backend` and `model` labels are **mandatory** in every accumulated entry — this is what answers the operator's "is Claude better than OpenAI?" question via SQL on `usage_json`.

### 6.6 OpenAI adapter

Wraps existing `OAIRunner.run_streamed` logic. Tool conversion uses `@function_tool` with name override; for stricter schemas, falls back to constructing a `FunctionTool` with `params_json_schema=tool.json_schema`. Defaults to `OpenAIChatCompletionsModel`; **only** the OpenAI search sub-agent uses `OpenAIResponsesModel` (because hosted `WebSearchTool` requires it).

### 6.7 Claude adapter

Uses `claude-agent-sdk`'s `create_sdk_mcp_server` + `@tool` + `query`. Tool conversion as in the reference impl. Schema conversion (`_schema_to_dict`) maps JSON-Schema dicts to the `{name: type}` dict the Claude `@tool` decorator expects.

**Critical: the Claude Agent SDK runs Claude Code as a CLI subprocess.** No Python-level Anthropic API calls happen. Therefore neither the OpenInference nor the Traceloop Anthropic instrumentor will produce LLM spans. **The Claude adapter MUST emit OTel spans manually** using `_event_span` from `trace_sdk.py`. This is not optional.

### 6.8 Search providers

```python
@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float | None = None
    published: str | None = None
```

Provider files (~30–60 LOC each): `exa.py` (AsyncExa), `brave.py` (httpx), `parallel.py` (httpx), `openai_subagent.py` (lifts `agent_openai_calls.py:_run_web_research_openai` verbatim).

### 6.9 Search factory

`get_search_tool_spec(backend)` returns one `Tool` descriptor whose body picks the configured provider. Both adapter formats wrap the same descriptor.

## 7. Blast Radius and Pre-Existing Tech Debt

This section enumerates impact discovered in the audit. Items are anchored to file:line. Treat as the source of truth for migration scope.

### 7.1 Two duplicate `_OAUTH_PROXY_URL` definitions

- `research_paths.py:9-10` AND `agent_infra.py:23-24` both define `_OAUTH_PROXY_PORT = 10531` and `_OAUTH_PROXY_URL`. Tests monkeypatch one or the other inconsistently.
- **Migration step:** consolidate to one module before splitting. Then split into `_OPENAI_PROXY_URL` and `_CLAUDE_PROXY_URL`. Update consumers in `agent_runners.py:16,77`, `agent_openai_calls.py:28,121,161`, `research_subagents.py:8,25,155,212,240`, `research_conductor.py:20,141,142`.
- The OAuth-token loader `_ensure_oauth_token()` (`agent_infra.py:27-48`) reads `~/.claude_oauth_token` and exports `CLAUDE_CODE_OAUTH_TOKEN` — a pre-existing tangle (proxy named "openai" but uses a Claude OAuth token). Resolve naming during the consolidation.

### 7.2 Two analyst implementations, not one

- `research_subagents.py:_call_analyst` (line 12) — used by the conductor's `analyze_trades` tool body.
- `agent_openai_calls.py:_run_diagnostic_analyst_openai` (lines 107–234) — used by `agent_orchestrator.run_diagnostic_analysis` and `autoresearch_research.py`.
- **Both** must accept `ProviderConfig` and dispatch by `analyst_backend`. The spec previously implied only one site. Two factory wirings, two test paths.

### 7.3 Tracing — Anthropic instrumentation is **already** present (and useless for Claude SDK)

- `traceloop-sdk==0.60.0` transitively installs `opentelemetry-instrumentation-anthropic==0.60.0`.
- `trace_sdk.py:366-371` whitelists `{OPENAI, OPENAI_AGENTS, REQUESTS, URLLIB3}`. Adding `Instruments.ANTHROPIC` enables it.
- **But** `claude-agent-sdk` does not call the `anthropic` Python SDK — it spawns Claude Code as a subprocess and communicates via stdio JSON. The Anthropic instrumentor sees **zero spans**.
- **Do not add `openinference-instrumentation-anthropic`** (different vendor, different span shape, would create duplicate spans alongside Traceloop's).
- **Mandatory:** the Claude adapter creates manual OTel spans for: agent run start/end, each tool-call dispatch, each result message. Use `tracer.start_as_current_span(...)` analogous to existing OpenAI manual spans.

### 7.4 Three trace-id schemes coexist today

- Structured: `trace_agent_prompt` returns `f"{hid}-{agent_name}-{seq:05d}"` (`trace_sdk.py:513`).
- Ad-hoc strings: `research_subagents.py:191,278` use `f"analyst-{focus_question[:40]...}"` and `f"web-{query[:40]...}"`.
- Stateful global: `agent_openai_calls.py:122` sets `current_trace_id = "active"` shared across tool calls.
- Two simultaneous calls with the same first-40-char prompt **collide and overwrite artifact files**.
- **Migration step:** route every backend call through `trace_agent_prompt` before adding the Claude adapter. This is a tech-debt cleanup commit that ships first.

### 7.5 Trace artifact format does not encode backend

- `_render_response_artifact` (`trace_sdk.py:224-251`) writes the response file body without a `BACKEND:` line.
- `trace_agent_prompt` event payload does not include `backend` or `model`.
- **Migration:** extend the artifact header (`BACKEND:` + `MODEL:` lines) and add `backend`/`model` keys to the JSONL event payload.

### 7.6 Token-usage schema needs cache fields, backend, and model labels

- `agent_token_usage.py:_ROUND_USAGE` initial dict — add `cache_read_tokens`, `cache_write_tokens`, `backend`, `model`.
- `agent_token_usage.py:get_round_usage` total dict — same.
- `autoresearch_research.py:107-117` `job_usage` dict — same.
- Two paths to unify: `_accumulate_result_usage` (used by `agent_runners`) and direct `_accumulate_usage` (used by `_call_analyst`, `_call_web_researcher`, `research_conductor`).
- `research_conductor.py:253-293` has duplicate inline token-usage logic — delete and replace with one `_accumulate_result_usage(role, result, dedupe_key=...)` call.
- **Always pass `dedupe_key`** in Claude paths to avoid the reference-impl double-count bug.

### 7.7 Cost-comparison skew between providers

- OpenAI Agents SDK does not always populate `total_cost_usd` (Responses API path vs ChatCompletions).
- Claude `ResultMessage.total_cost_usd` is reliable.
- Result: cost reports may show Claude as cheap-relative-to-OpenAI when OpenAI cost is just unattributed.
- **Resolution:** explicit `cost_calculation_source: "sdk" | "missing"` field per usage entry. Reports flag missing cost as a known measurement gap, not zero cost.

### 7.8 DB schema

- `experiment_db.py:experiments` and `research_rounds` tables store `usage_json TEXT NOT NULL` — JSON blob, no DDL change. New shape: `{by_agent: {<role>: {input, output, cache_read, cache_write, cost_usd, calls, backend, model}}, total: {...}}`.
- `experiment_db.py:982` reader: `row.get("usage_json", {})` — silent acceptance of new fields. No code change needed but smoke-test required.
- `experiment_db.py:session_meta` (lines 95–101) has 3 columns. **Add `provider_config_json TEXT NOT NULL DEFAULT '{}'`.** Idempotent migration on startup (PRAGMA table_info check, ALTER TABLE if missing).

### 7.9 Discord notifications are safe

- `autoresearch_research.py:notify_discord` (lines 65-90) does not `.format()` on `usage`; no format-string-failure risk. New cache fields silently flow through if added to `accumulate_job_usage` (lines 107-118).

### 7.10 Hardcoded `model="gpt-5.5"` at 7 sites

- `agent_openai_calls.py:29,162`, `agent_prompts.py:209`, `agent_runners.py:78`, `compiler_operationalize.py:162`, `research_conductor.py:143`, `research_subagents.py:156,241`.
- **Resolution:** drop `model` from `agent_def` shape. Adapter picks model from env (`OPENAI_MODEL` or `CLAUDE_MODEL`) based on the backend. `agent_runners.py:78` becomes a backend-keyed lookup, not a `getattr(agent_def, "model", ...)`.

### 7.11 Existing `research_tools_mcp.py` is dead in production

- Only `tests/test_research_conductor_characterization.py:317,378` use `_build_research_tools_mcp`.
- Production `research_conductor.py` inlines `@function_tool` definitions (lines 145-213).
- File registers `agnost_mcp` vendor telemetry (`a042226c-b858-46f3-9756-b1e675c03c13`) at lines 157-196 — this telemetry is silently dropped if migration ignores the file.
- **Decision:** delete `research_tools_mcp.py` and update its tests to use the new `Tool` descriptor pattern. Surface the lost telemetry to stakeholders before the commit lands.

### 7.12 Three duplicate `_run_coroutine_sync` thread wrappers

- `research_conductor.py:38-58`, `agent_orchestrator.py:38-58`, `compiler_operationalize.py:126-146` — three identical thread-spawning sync→async bridges.
- **Out of scope** for this refactor (pre-existing tech debt). Document the risk: anyio-based Claude SDK + nested OAIAgent dispatch through these wrappers is untested event-loop territory. Pin `anyio` to the asyncio backend explicitly in `claude_backend.py` to avoid trio-vs-asyncio interop issues.

### 7.13 CLI subprocess per Claude `query()` call

- `claude-agent-sdk` fork-execs Claude Code CLI on every `query()`. No pooling.
- Risks: file-descriptor pressure, zombie processes on interpreter shutdown, MCP server lifecycle leaks across rounds.
- **Mitigation:** the conductor's `query()` runs in the controller's main async path (not in a thread). Process-count audit deferred to integration testing.

### 7.14 Test mock surface

- `tests/test_trace_sdk.py:10,40,144,145` patches `OpenAIInstrumentor` directly. Add parallel Anthropic-instrumentor patch test.
- `tests/test_agent_orchestrator_characterization.py:51,118,253,354,388,445,475-476,580,698` — patches `_run_diagnostic_analyst_openai`, `_run_web_research_openai`, `OAIRunner.run_streamed`. Asserts `tool_type=="WebSearchTool"` and `model_type=="OpenAIResponsesModel"`. Move the model_type/tool_type assertions into `tests/test_search_providers.py` (OpenAI sub-agent path); other patches accept a `config: ProviderConfig` arg.
- `tests/test_research_conductor_characterization.py:114,176,203,319-326,380-387` — patches `rc.OAIRunner.run_streamed`. With default backend = claude, this becomes "test the OpenAI fallback" not "test the conductor." Either retain as the OpenAI-path test, or rewrite to assert the Claude adapter path.
- `tests/test_compiler_pipeline_characterization.py:303-329` — patches `agent_orchestrator._run_single_agent`. Add `config` parameter threading.

### 7.15 CI

- `.github/workflows/ci.yml:20` duplicates pyproject deps inline. Add `claude-agent-sdk`, `exa-py` (and ensure `traceloop-sdk` still pulls anthropic instrumentation transitively).
- `.github/workflows/ci.yml:24` bandit scope: `bandit -r autoresearch_*.py agent_*.py research_conductor.py compiler_pipeline.py`. **Extend to `providers/`**. New backend code must be scanned for secrets.

### 7.16 Pyproject

- `pyproject.toml:11` `packages.find` `include` list — add `"providers*"`.
- `pyproject.toml:14-70` manual `py-modules` list — `providers/` is a package, caught by `packages.find`. No entry needed there.
- `pyproject.toml:146-160` deps — add `claude-agent-sdk`, `exa-py`. Do **not** add `openinference-instrumentation-anthropic` (would conflict with traceloop's anthropic instrumentor).

### 7.17 Trace adapters / mempalace / strategies are clean

- `trace_adapters/halo.py`, `reflexio.py`, `recursive_improve.py` — provider-agnostic span shapes. No change.
- `research_memory.py` (mempalace) — provider-agnostic. No change.
- `strategies/` — no SDK imports. No change.

## 8. Threading ProviderConfig Through the Code

| File | Change |
| --- | --- |
| `vps_runner.py` | Add four CLI args; export proxy URLs and search-API keys in `build_remote_command()`. Today exports zero agent env vars. |
| `autoresearch_controller.py` | Add same four args; build `ProviderConfig`, validate, pass to controller. |
| `autoresearch_controller.py:AutoresearchController` | Store `ProviderConfig`; pass to every dispatch. |
| `research_conductor.py` | Replace OAIAgent-only conductor with `backend = get_backend("research", config)`. Delete inline duplicate token-usage block (lines 253-293). Replace inline `@function_tool` definitions with `Tool` descriptors built from a new helper. |
| `research_subagents.py` | `_call_analyst(..., config)` uses `get_backend("analyst", config)`. `_call_web_researcher` deleted (replaced by search factory). Fix ad-hoc trace_ids (lines 191, 278) to route through `trace_agent_prompt`. |
| `agent_openai_calls.py` | `_run_web_research_openai` moves verbatim to `providers/search/openai_subagent.py`. **`_run_diagnostic_analyst_openai` (lines 107-234) accepts `config` and dispatches via `get_backend("analyst", config)`** — the spec previously missed this. Fix stateful trace_id (line 122). |
| `agent_runners.py` | `_run_single_agent(..., config)` uses `get_backend(<role>, config)`. Tool-shape conversion happens here (`agent_def.tools` → `list[Tool]` → adapter format). |
| `compiler_operationalize.py` | Builder uses `get_backend("builder", config)`. Drop `model="gpt-5.5"` from agent_def. |
| `agent_prompts.py` | Drop `model` field from agent definitions. Adapter picks from env. |
| `research_paths.py` + `agent_infra.py` | Consolidate duplicate `_OAUTH_PROXY_URL`, then split into `_OPENAI_PROXY_URL` / `_CLAUDE_PROXY_URL`. Resolve OAuth-token-file naming. |
| `agent_token_usage.py` | Add `cache_read_tokens`, `cache_write_tokens`, `backend`, `model` fields. Add Claude-specific extractor. Always require `dedupe_key`. |
| `autoresearch_research.py` | Extend `job_usage` dict with cache fields. |
| `trace_sdk.py` | Add `Instruments.ANTHROPIC` to whitelist. Add `_ANTHROPIC_INSTRUMENTOR` lifecycle parallel to `_OPENAI_INSTRUMENTOR` (so `begin_round` rebinds it). Extend artifact headers with `BACKEND:` / `MODEL:`. Extend JSONL event payloads with `backend` / `model`. |
| `experiment_db.py` | Idempotent `ALTER TABLE session_meta ADD COLUMN provider_config_json TEXT NOT NULL DEFAULT '{}'` on startup. |
| `research_tools_mcp.py` | **Delete.** Update tests that import it to use new `Tool` descriptor. |

## 9. Conductor as Meta-Agent (Two-Level Dispatch)

- Conductor's own loop: `--research-backend`. Tools are `Tool` descriptors.
- Tool bodies dispatch sub-agents per their own backend:
  - `analyze_trades` → `get_backend("analyst", config)`.
  - `web_search` → search provider (Exa/Brave/Parallel direct, or OpenAI sub-agent).
- Memory tools (`save_finding`, `search_findings`, `memory_status`) — pure Python, no sub-agent.

## 10. Token-Usage Discipline (mandatory)

- Every `_accumulate_usage` / `_accumulate_result_usage` call **must** pass `dedupe_key` (default: `f"{role}-{response_id_or_object_id}"`).
- Both backend extractors return a `TokenUsage` dataclass; the legacy dict-based interface is the input format only.
- Cost extraction:
  - OpenAI: `result.raw_responses[].usage` + `total_cost_usd` (may be None on Responses API).
  - Claude: `ResultMessage.usage` (cache_creation_input_tokens, cache_read_input_tokens) + `ResultMessage.total_cost_usd`.
- Per-entry `backend` and `model` labels are non-optional.

## 11. Trace-ID Discipline (mandatory)

- Every backend invocation routes through `trace_agent_prompt(agent_name, prompt, system_prompt)` to obtain a structured `trace_id`.
- Ad-hoc `trace_id` strings (today: `research_subagents.py:191,278`, `agent_openai_calls.py:122`) are eliminated **before** the Claude adapter ships.
- Artifact filenames are `{trace_id}-prompt.txt` / `{trace_id}-response.txt`. Backend label lives inside the file body and JSONL event, not in the filename.

## 12. max_turns Sizing

- Conductor (research backend): 25.
- Analyst sub-agent: 25.
- Web-researcher sub-agent (when `search=openai`): 10.
- Direct-API search providers: no sub-agent; one HTTP call inside the tool body.

`max_turns` parameterized in `AgentBackend.run()`; threaded from call site.

## 13. Error Handling

- Search providers raise on transport/HTTP errors; `Tool.run` propagates; caller's existing retry loop handles.
- Claude adapter catches `CLINotFoundError`, `CLIConnectionError`, `ProcessError`, `CLIJSONDecodeError` → re-raise as `AgentTransportError` → caller maps to `_structured_error("transport", ...)`.
- OpenAI adapter retains existing broad-`except` (catches `ModelRefusalError` and transport errors).
- `ProviderConfig.validate()` aborts before any agent runs.

## 14. Testing

Unit tests:
- `ProviderConfig.validate` — every combo, missing-env-var paths, message formatting.
- `Tool` → `@function_tool` adapter — execution path, schema preservation.
- `Tool` → `@tool` + MCP server adapter — execution path, allowed-tools list.
- Each search provider — fixture HTTP responses → expected `SearchResult[]`.
- `openai_subagent.run_web_research` — preserves existing characterization (assertion on `OpenAIResponsesModel` / `WebSearchTool` moves here).
- Claude adapter manual-span emission — assert spans for run start/end and tool calls.
- Token-usage cache fields and `dedupe_key` enforcement.
- `experiment_db.session_meta.provider_config_json` migration is idempotent on existing dbs.

Integration (mocked SDKs):
- Conductor end-to-end with stub backends; `analyze_trades` and `web_search` reachable; `final_output` parsed.

CLI:
- `vps_runner.py` and `autoresearch_controller.py` accept new args; remote command and env exports include them.

**No internal mocking** of `OAIRunner`, `ClaudeSDKClient`, `query()`. Adapter tests check our wrapping logic only.

## 15. Migration Sequence — 8 Commits

Each commit green. Reordered to put cleanup-before-feature.

1. **Tech-debt cleanup: trace-id discipline.** Route `research_subagents.py` and `agent_openai_calls.py` ad-hoc trace_ids through `trace_agent_prompt`. No behavior change. Tests updated for new artifact filenames. (~80 LOC delta.)
2. **Tech-debt cleanup: dedup `_OAUTH_PROXY_URL`.** Consolidate `research_paths.py` and `agent_infra.py` to a single source. Resolve OAuth-token-file naming. No behavior change. (~50 LOC delta.)
3. **Token-usage schema extension.** Add `cache_read_tokens`, `cache_write_tokens`, `backend`, `model`. Delete inline duplicate in `research_conductor.py:253-293`. Mandate `dedupe_key`. (~120 LOC delta across 3 files.)
4. **Trace SDK: Anthropic instrumentor lifecycle + artifact backend label.** Add `_ANTHROPIC_INSTRUMENTOR` parallel to OpenAI's. Add `Instruments.ANTHROPIC` to whitelist. Extend artifact header and JSONL event with `backend` / `model`. Update `tests/test_trace_sdk.py`. (~150 LOC delta.)
5. **DB migration: `session_meta.provider_config_json`.** Idempotent ALTER TABLE on startup. (~40 LOC delta.)
6. **`providers/` package skeleton: ProviderConfig, Tool, AgentResult, OpenAI backend.** Verbatim re-implementation of current SDK calls. New tests, no call-site changes. (~400 LOC.)
7. **Claude backend + `claude-agent-sdk` dep + manual span emission + tests.** Pin anyio to asyncio backend. (~250 LOC.)
8. **Search package + factory + relocate `_run_web_research_openai`.** Add `exa-py` dep. Move existing characterization tests. (~200 LOC.)
9. **CLI args wired through `vps_runner.py` and `autoresearch_controller.py`. Env propagation. Default config = current effective behavior.** (~100 LOC.)
10. **Switch all call sites to factories.** `research_conductor.py`, `research_subagents.py`, `agent_openai_calls.py:_run_diagnostic_analyst_openai`, `agent_runners.py`, `compiler_operationalize.py`, `agent_prompts.py`. Drop `model` from agent_def. Delete `research_tools_mcp.py` (and update its tests). (~300 LOC delta + ~200 LOC test updates.)

That's **10 commits**, not 5. The last refinement: 1–5 are pre-requisite cleanups; 6–10 are the actual feature.

## 16. Effort Estimate

| Item | Estimate |
|---|---|
| New `providers/` LOC | ~600 |
| Modified production LOC across 14 files | ~700 |
| Test changes (4 modified + 3 new) | ~600 |
| **Total** | **~1900 LOC across ~25 files** |
| Commits | **10** (5 cleanup + 5 feature) |
| External prerequisites | VPS systemd unit for Claude OAuth proxy (out of repo) |

## 17. Files Touched (final)

**New (~600 LOC):**
- `providers/__init__.py`, `providers/config.py`
- `providers/agent/__init__.py`, `protocol.py`, `tool_spec.py`, `openai_backend.py`, `claude_backend.py`, `factory.py`
- `providers/search/__init__.py`, `protocol.py`, `exa.py`, `brave.py`, `parallel.py`, `openai_subagent.py`, `factory.py`
- `tests/test_provider_config.py`, `test_agent_backends.py`, `test_search_providers.py`, `test_token_usage_schema.py`, `test_trace_sdk_anthropic.py`

**Modified:**
- `vps_runner.py`, `autoresearch_controller.py`
- `agent_runners.py`, `agent_openai_calls.py`, `research_subagents.py`, `research_conductor.py`, `compiler_operationalize.py`, `agent_orchestrator.py`, `agent_orchestrator_helpers.py`
- `agent_token_usage.py`, `autoresearch_research.py`
- `trace_sdk.py`, `trace_adapters/__init__.py` (verify no provider assumptions)
- `research_paths.py`, `agent_infra.py`
- `agent_prompts.py`
- `experiment_db.py`
- `pyproject.toml`, `.github/workflows/ci.yml`
- `tests/test_trace_sdk.py`, `test_agent_orchestrator_characterization.py`, `test_research_conductor_characterization.py`, `test_compiler_pipeline_characterization.py`

**Deleted:**
- `research_tools_mcp.py` (and its tests rewritten to use the new `Tool` descriptor).

## 18. Risks Carried Forward

- **R1.** CLI subprocess scaling for Claude — N rounds = N spawns. Audit at integration test time.
- **R2.** Manual span emission inside Claude adapter is mandatory; no fallback. Adapter tests must assert spans exist.
- **R3.** anyio↔asyncio interop. Pin anyio to asyncio backend explicitly.
- **R4.** Three pre-existing `_run_coroutine_sync` thread wrappers + nested cross-backend dispatch — untested. Out of scope but flagged.
- **R5.** MCP server lifecycle across rounds — reference impl doesn't tear down. Memory growth risk.
- **R6.** Reference-impl token double-count bug — avoided by mandating `dedupe_key`.
- **R7.** Cost-comparison skew — tagged with `cost_calculation_source` field.
- **R8.** Default-backend cost spike (claude conductor at Opus prices). Operational note in §4.
- **R9.** `agnost_mcp` telemetry dropped when `research_tools_mcp.py` is deleted. Stakeholder check before §15 commit 10.
- **R10.** Bandit scope expansion — verify CI catches secrets in `providers/`.
- **R11.** VPS provisioning of Claude OAuth proxy is an out-of-repo prerequisite. Block deployment of any `claude` backend until the systemd unit is in place.

## 19. Open Questions (implementation-time)

- Claude proxy auth shape: API-key vs subscription session. Confirm during commit 7.
- Brave / Parallel response shape mapping to `SearchResult`. Confirm during commit 8.
- `agnost_mcp` telemetry: keep or drop. Confirm before commit 10.
- Anthropic model id (`claude-opus-4-6` vs `claude-opus-4-7` etc.) — verify the latest published id at commit time.
