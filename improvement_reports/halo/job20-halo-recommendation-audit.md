# Job 20 HALO Recommendation Audit

Source report:
`improvement_reports/halo/job20-all-rounds.real-halo-report.md`

This audit compares HALO's job-20 trace recommendations against the current codebase.

## Summary

HALO's useful recommendations are mostly about orchestration, validation, builder feedback, analyst tooling, trace quality, and token waste.

Many recommendations were implemented after job 20. The concrete follow-ups from this audit are now either implemented or explicitly deferred as not recommended yet.

## Recommendation Status

| HALO suggestion | Current status | Evaluation |
|---|---|---|
| Fail fast on missing research inputs | Implemented with scoped semantics | `research_required` remains a valid autonomous trigger, not a hard error. Missing trade artifacts are represented in conductor prompts/tools, while invalid `running` states with blockers are rejected before state writes. |
| Block controller actions while blockers remain | Implemented | The controller only transitions to experiment execution when research returns `running` with blockers cleared. The autonomy trace wording now says "blocker-cleared research next action" and records the clear-blockers constraint. |
| Add invariant tests for blocked/running transitions | Implemented | `validate_controller_state_invariants()` rejects `running` state with non-empty blockers, and characterization tests cover the invariant. |
| Canonical thesis to config schema | Implemented | `ResearchThesis -> ExperimentContract -> runtime_config` exists and is high-value. |
| Deduplicate prior theses before validation | Implemented enough for now | Config-key Jaccard overlap, dimension checks, and prior-thesis tools are active. Full semantic/cosine dedupe remains deferred unless duplicate pollution recurs. |
| Emit structured rejection reasons | Implemented | Validator rejections now emit structured `{source, code, message}` metadata and trace `validation_error` payloads use stable error codes. Builder and conductor paths also emit typed error codes/actions. |
| Add novelty/diversity constraints | Implemented enough for now | Prompt and validator enforce mechanism dimensions and novelty explanation. A full diversity scheduler remains deferred because it could over-constrain creative thesis generation. |
| Preflight file/schema checks before builder | Implemented enough for now | Builder reads thesis/contract artifacts, normalizes thesis metadata away from runtime config, validates generated config in fresh Python, and runs implementation-contract verification before activation. A separate preflight module is not needed yet. |
| Separate build validation from execution | Mostly implemented | Builder validates config and implementation before backtest activation. |
| Return structured builder error codes | Implemented | Builder errors now include stable `error_code` values such as `builder_config_validation_failed` and `builder_implementation_contract_failed`. |
| Reserve `manual_review` for ambiguous cases only | Implemented | Deterministic builder config failures route to `research_retry_required`; deterministic verifier/build failures route to `builder_failed`; ambiguous untyped failures still route to `manual_review`. |
| Harden analyst pandas/path failures | Implemented | Analyst now gets typed artifact/source tools, path-probing prevention, compact tool outputs, and tested dataframe helpers for PF buckets and safe as-of merge/sort analysis. |
| Cache intermediate dataframes | Not implemented | Not recommended yet. Agent tool calls are stateless intentionally; caching adds complexity. Better first step is reusable analysis helper functions. |
| Cap retries | Implemented | Builder retry is capped at 1, conductor validation retries are capped at 3, analyst has `max_turns=25`, Python execution has a timeout, and repeated failed analyst `run_python` calls are stopped after a small failure budget. |
| Summarize state between attempts | Implemented enough for now | Builder retries get fresh prompts with verifier failures, tool outputs are compacted, research retry feedback is carried into the next conductor call for config-validation failures, and analyst `run_python` budget exhaustion returns a bounded diagnostic instruction instead of more failed attempts. |
| Persist compact structured web research summaries | Implemented enough | Web researcher response is persisted through trace response artifact and parsed JSON. |
| Preserve prompt/response fields for HALO | Implemented | HALO adapter reads prompt/response artifacts into `llm.input_messages` and `llm.output_messages`. |
| Avoid dropped projections | Likely implemented | Current adapter writes both JSON messages and flat message fields. Current code does not show `__halo_dropped_flat_projections`. |
| Require diagnostic summary span on error | Implemented enough for now | Tool errors include `status` and `error_type`; builder, conductor, and validator now emit explicit error actions with typed payloads. |
| Standardize explicit `builder_error` / `conductor_error` labels | Implemented | Trace events now include `builder_error`, `conductor_error`, and `validation_error` actions. |
| Token/tool usage tracking | Implemented | Actual and estimated usage are separated and propagated into trace events. |
| Trim conversation history on retry | Implemented enough for now | Builder retry uses a fresh prompt with verifier failures. Conductor `get_experiment_result` now defaults to compact summaries so multi-MB experiment details do not enter SDK history unless explicitly requested with `detail=true`. Further SDK-level history surgery is deferred. |
| Add per-thesis token budgets | Implemented | Soft budget warnings now support per-agent and per-thesis scopes and emit `token_budget_warning` trace events without blocking execution. |
| Stop after small failed retry count | Implemented | Validation retries are capped at 3, builder implementation retries at 1, analyst has tool timeout/max turns, and failed `run_python` calls are stopped after the configured small budget. |

## Grounding

Relevant current code:

- `research_types.py`: structured `ResearchThesis`, `ExperimentContract`, `ExperimentVerdict`
- `compiler_research.py`: compiles validated thesis into runtime config contract
- `thesis_validator.py`: mechanism dimensions, metadata-key rejection, config-overlap dedupe, hypothesis/config alignment
- `research_prompts.py`: prior-thesis tools, experiment-result tools, mechanism dimension rules, implementation-gap check
- `compiler_implementation_verify.py`: deterministic builder implementation verification
- `compiler_builder.py`: builder prompt, verifier retry, builder Reflexion, validation before activation
- `research_subagents.py`: analyst manifest, typed artifact/source tools, path-probing prevention, compact tool outputs
- `trace_sdk.py`: prompt/response artifacts, tool call/result tracing, parent span IDs, usage events
- `trace_adapters/halo.py`: HALO JSONL export shape, prompt/response message projection, token attributes
- `agent_token_usage.py` and `agent_sdk_token_usage.py`: actual and estimated token usage propagation
- `autoresearch_controller.py` and `autoresearch_orchestration.py`: blocked-state handling, resume, builder manual-review routing

## Recommended Priority

1. Add typed error codes for validator, conductor, and builder failures.
2. Change builder failure routing so deterministic verifier/config failures become actionable `builder_failed` or `research_retry_required`, not always `manual_review`.
3. Add invariant test: state cannot be `running` with blockers.
4. Add reusable analyst helper functions for common pandas bucket/merge/sort operations.
5. Add explicit trace actions: `builder_error`, `conductor_error`, `validation_error`.
6. Add per-agent/per-thesis token budget warnings, not hard stops yet.

All six priority items above are now implemented.

## Not Recommended Now

- Do not block all `research_required` states. That is how the autonomous loop asks the conductor for the next thesis.
- Do not add dataframe caching yet. It risks complexity before proving repeated dataframe loading is the main cost.
- Do not overbuild semantic thesis dedupe yet. Current dimension, config-overlap, and past-thesis tooling is acceptable unless duplicates recur.
