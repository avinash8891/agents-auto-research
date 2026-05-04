# CLAUDE.md

Operating discipline for Claude Code in this repository. Project-specific context (schemas, architecture, tasks, acceptance criteria) lives in the project's spec file, not here. Read this file at session start. It wins on conflict unless resolved explicitly.

## Non-negotiable rules

1. **Announce intent** before each unit of work: what, files touched, tests, expected user-visible output.
2. **No secrets, auth headers, or raw request/response payloads** in code, logs, errors, or output. Read from env or secret manager. Never print, never commit.
3. **Search before writing.** Grep the codebase for the concept before any new function >15 lines. Report findings.
4. **Stop at each task boundary.** Commit with clear tag, push, wait for "continue." No auto-chaining.
5. **Validate at every layer boundary**, including raw external inputs — one source-specific model per source before normalization.
6. **Tests run and pass.** Paste actual test-runner output into the completion message.
7. **No scope creep.** The spec is authoritative. "While I was here" additions banned. "In blast radius" is not a license.
8. **Single source of truth for persistent state.** No shadow copies, no cross-task intermediate stores. Raw inputs and final outputs OK.

## Failure modes + counter-mechanisms

- **A. Wire-first.** First file touched = the entry point, with the signature that will call the new code. Task not done until a real invocation exercises it end-to-end. Before commit, grep for imports of the new module — must return at least one caller outside its own package, or the module is unwired.
- **B. One home per concept.** Each external service client, each shared utility, each data-model file — one location, declared up front. Nobody re-instantiates locally. Data models live in one file; adding or changing a field is announced, not silent.
- **C. No patch-on-patch.** Bug → failing test first, then fix. Read the whole function before touching it — don't parachute a guard around the failing call. Before removing or renaming anything (function, table, field, env var), grep every caller in one pass — "unused in this file" ≠ "unused globally." Commit subject identifies the class: `fix` (root cause), `patch` (symptom workaround — opens follow-up issue), `refactor` (no behavior change), `feat` (new capability). Commit message names the wrong assumption ("code assumed X, but X is false because Y"). Delete-before-add bias: fix by removing lines when possible. If a patch adds >5 lines in one file or >15 total, stop and reconsider — probably a symptom not a root cause. Three-strikes rule: if a function accumulates three branches for specific edge cases, STOP and refactor — the data model is wrong, not the branches. Trust the framework: don't re-validate what pydantic/the DB/the type system already validates.
- **D. Read-before-accept.** Plain-language diff summary in domain language before every commit. Flag trust-point lines (table names, fields, endpoints, regex, filter conditions) for user spot-check.
- **E. One commit, one deliverable.** Stated in one sentence up front. No related cleanup, no uncalled-for refactors. If a file outside the stated deliverable needs to change, ask first.
- **F. No confident-wrong.** Every external API or library claim verified via docs lookup, signature pasted in announcement. Uncertainty stated explicitly. User corrections override training — don't argue, update.
- **G. Real tests only.** Never mock internal code; mock external services using captured real fixtures (redacted), not hand-written shapes. One integration test per task against real data. Assertions exercise behavior, not structure — `assert len(events) == 2401` beats `assert events is not None`. If the test would pass with the function body replaced by a plausible constant, the test is worthless. When an assertion fails, the code or fixture is wrong — not the assertion. Don't relax `==` to `>=`, widen ranges, or loosen types to turn red tests green.
- **H. Log before feature.** Structured stdlib logging, UTC timestamps. Every ERROR line answers "what do I do about this?"
- **I. Quarantine bad external data.** Source-specific validation row-by-row. Malformed rows → quarantine file + log, continue. >1% failure rate = STOP and show user. When a new edge case surfaces in production data, add it to the test fixture first, then fix the code.
- **J. UTC in persistent state.** All stored timestamps UTC with timezone. Conversion to local time happens at display edges only. Naive datetimes banned from storage and comparisons.
- **K. Evidence with claims.** "It works" requires pasted output / row counts for mutations / request+status+response excerpts. "Tests pass" alone is not evidence. Ambiguous success reported as ambiguous ("47/48 passing" is not "tests pass"). User can demand verification at any point — respond with artifacts, not restated claims.
- **L. Agent findings are hypotheses.** Before acting on a review agent's claim ("dead code," "unused import," "missing reference," "broken ref"), grep or read to verify. Fixing imaginary bugs introduces real ones.

## Error policy

- Deterministic errors (schema, validation, logic, SQL): propagate loud. Never swallow.
- External flakiness (5xx, rate-limit, timeout): catch, log, degrade. Never kill the whole run.
- Status integrity: a run or step is "ok" only if every required sub-step succeeded. Partial success with a silent skip = failed run, reported as failed.

## Hygiene

- `get_logger` must NOT set `propagate = False` — pytest caplog captures via the root logger; blocking propagation makes all `caplog` assertions return empty strings. No duplicate output risk in production (no root handler attached outside tests).
- `PYTEST_CURRENT_TEST` is NOT set during pytest module import/collection — only during test execution. Module-level code cannot use it as an import guard. Use it only inside functions.
- No new dependency without justification in commit message. Stdlib → existing deps → new dep, in that order.
- Hardcoded tunable numbers (thresholds, limits, batch sizes, weights) = config smell. Put them in config, validated on load.
- Size-down tactics: data over logic (lookup tables beat if-chains), stdlib first, no wrapper-only-renames, no premature abstraction, no scaffolding comments, delete dead code on contact (including commented-out blocks — git preserves what was there).

## Commands

```bash
# Tests
pytest                                        # run full suite
pytest --cov --cov-report=term-missing        # coverage for tracked source modules (gate: 80%)
pytest tests/test_agent_token_usage.py -v     # single file

# Lint / format
pre-commit run --all-files                    # run ruff, isort, black, cubic-review on all files

# Run research (local)
python autoresearch_controller.py --family ema   # run EMA strategy family
python autoresearch_controller.py --family orb   # run ORB strategy family

# Deploy & run on VPS
python vps_runner.py --strategy ema --git-ref main       # deploy branch/tag
python vps_runner.py --strategy ema --git-sha <40-char>  # deploy exact commit

# Experiment tracking CLI
python autoresearch_cli.py init   --session-path <path>
python autoresearch_cli.py log    --session-path <path> --metric sharpe --value 1.23
python autoresearch_cli.py status --session-path <path>

# Token audit (post-run)
python scripts/token_audit.py --by model       # group by model id
python scripts/token_audit.py --by agent       # group by agent name
python scripts/token_audit.py --by job         # group by trace job
python scripts/token_audit.py --by hour        # time-bucketed view
python scripts/token_audit.py --since 2026-05-01  # filter by UTC date
```

## Environment

Copy `.env.example` → `.env`. Required vars:

| Var | Purpose |
|-----|---------|
| `AUTORESEARCH_DATA_ROOT` | Local path to market data (`universes/nasdaq8/`, etc.) |
| `AUTORESEARCH_VPS_HOST/USER/KEY` | SSH target for `vps_runner.py` |
| `AUTORESEARCH_DISCORD_WEBHOOK_ORB/EMA` | Run notifications (optional, fail-open) |
| `AUTORESEARCH_GIT_REPO` | Git repo URL cloned on VPS during deploy |

## Architecture

```
autoresearch_controller.py   ← main entry point; state machine per strategy family
  ├─ autoresearch_planning.py        ← thesis generation
  ├─ autoresearch_research.py        ← per-thesis research rounds
  ├─ autoresearch_experiment.py      ← experiment logging + metric evaluation
  ├─ autoresearch_orchestration.py   ← state-transition helpers (resume, baseline rerun, next action)
  └─ compiler_pipeline.py            ← top-level compiler orchestrator
       ├─ compiler_builder.py            ← builds missing strategy primitives
       ├─ compiler_operationalize.py     ← operationalizes compiled strategy code
       └─ compiler_thesis_io.py          ← thesis read/write helpers

research_conductor.py        ← drives sub-agent calls (web, code, data)
research_subagents.py        ← individual agent definitions
research_memory.py           ← MemPalace integration for cross-session research state
research_tools_mcp.py        ← MCP tools exposed to research agents

agent_orchestrator.py        ← public orchestrator API (diagnostics, web research, thesis)
agent_runners.py             ← agent runner implementations
agent_formatters.py          ← response formatting for agent outputs
agent_infra.py               ← shared agent infrastructure (clients, retry)
agent_prompts.py             ← prompt templates for agent calls
agent_openai_calls.py        ← OpenAI API wrapper with retry + token tracking
agent_token_usage.py         ← per-call token audit; emits trace events
trace_sdk.py                 ← trace event schema + writer
trace_adapters/              ← per-adapter trace transformers (halo, recursive_improve, reflexio)

autoresearch_cli.py          ← SQLite-backed experiment tracking CLI
experiment_db.py             ← ExperimentDB (SQLite schema + queries)
autoresearch_state.py        ← state file read/write helpers

strategies/ema/              ← EMA strategy (registered as "ema")
strategies/orb/              ← Opening Range Breakout (registered as "orb")
strategies/base.py           ← @register decorator + STRATEGIES dict
strategies/validate_utils.py ← shared type-check helpers (_is_int_value, _is_number_value)
strategies/_demo/            ← skeleton for adding a new strategy family

vps_runner.py                ← deploys git ref to VPS + launches controller
scripts/token_audit.py       ← post-run token cost analysis (--by model/agent)
trace_exports/               ← per-round trace artifacts (gitignored raw outputs)
prompts.db                   ← runtime SQLite; accumulated prompt history (gitignored)
ema_experiments.db           ← runtime SQLite; EMA experiment records (gitignored)
```

## Known gaps

- `strategies/validate_utils` is NOT in `pyproject.toml` py-modules — add it before cutting a release build.

## Violations

Cite by number or letter (`violates rule 4` or `violates C`), self-correct before proceeding, state what changed. If a rule seems wrong for a specific case, flag the conflict and propose a resolution — never silently ignore.
