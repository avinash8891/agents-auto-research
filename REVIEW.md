# Engineering Review — What Has Been Built

_Review date: 2026-07-10. Scope: the two artifacts in this workspace —
`agents-auto-research` (this repo) and the travel research playbook (currently
on branch `origin/travel-research-playbook`, migrating into the
`travel-rresearch-playbook` repo). This is an assessment; it makes no functional
code changes. Findings are grounded in a read of the code at the cited
`file:line` locations._

---

## 1. Executive summary

Two substantial things have been built here, and both are instances of the same
meta-pattern: **an autonomous, agent-driven research loop with an auditable
corpus and hard admission gates.**

1. **`agents-auto-research`** — an autonomous quantitative-research system for
   trading strategies. ~64k lines of Python across 207 files, 81 test files, 70
   merged PRs, a green CI pipeline (ruff/isort/black + bandit + prompt-drift +
   pytest/coverage), and a documented, actively-worked debt backlog. It plans a
   thesis, researches it with an LLM conductor + sub-agents, compiles any
   missing strategy primitives, backtests, evaluates, and iterates. Two
   pluggable strategy families ship today: **EMA** and **ORB**.

2. **The travel research playbook** — ~60 markdown/spec files defining a
   reproducible methodology for discovering and ranking expert-led tours, with
   piloted corpora for Italy and Japan. It is documentation-as-system: the same
   discipline (append-only registries, coverage matrices, earned convergence,
   independent verification) expressed as a playbook rather than code.

**Overall maturity: early-to-mid production.** The happy paths are solid and the
engineering discipline (CLAUDE.md rules, characterization tests, trace
instrumentation, root-cause bug tracking) is unusually rigorous for a project
this size. The risk is concentrated in a few large modules, error-path edge
cases, and a coverage gate that currently sits well below the project's own
stated bar.

---

## 2. `agents-auto-research`

### 2.1 What it does (end-to-end)

The controller (`autoresearch_controller.py`) runs a state machine per strategy
family. States observed: `running`, `blocked`, `building`, `halted`, `finished`,
`interrupted`. The main loop (`autoresearch_controller.py:482`) polls
`execute_once()` and exits on a terminal state.

The decision waterfall lives in `autoresearch_planning.py:264`:

1. No results yet → serve the baseline config (`running`).
2. Model plateau + zero screening rate → run walk-forward validation.
3. Otherwise → `blocked` with a `research` next-action.

A research round (`autoresearch_research.py:1268`) drives the LLM conductor,
runs the thesis through staged validation (Stage-1 rejection, compile
rejection), retries with per-stage budgets, and on success hands a validated
config to the compiler and then the backtest. Results are logged to SQLite and
evaluated (`autoresearch_experiment.py:891`), which feeds the next decision.

### 2.2 Architecture

The README and `CLAUDE.md` both describe a clean five-plane architecture
(entry → orchestration → agent → compiler → persistence), and the code largely
honors it. Strengths:

- **Clear entry point and wiring.** The controller delegates to planning,
  research, experiment, and orchestration modules through explicit aliases.
- **Pluggable strategies.** `strategies/base.py:131` provides an `@register`
  decorator populating a `STRATEGIES` dict; each family owns its contract,
  validation, signals, exits, and research spec. A `_demo` skeleton
  (`strategies/_demo/`) documents the contract a new family must satisfy.
- **Pure, well-isolated helpers.** `autoresearch_state.py` is side-effect-light
  (timestamp coercion, dedup, markdown rendering) — good hygiene.

Weaknesses:

- **Two god-modules.** `autoresearch_research.py` (2216 lines) and
  `autoresearch_experiment.py` (1393 lines) carry too much. Research owns
  conductor invocation, validation, rejection feedback, retry budgeting, and
  compiler dispatch; experiment owns command execution, metric parsing, trade
  analysis, and DB serialization. They share state field names
  (`research_round`, `thesis_id`, `runtime_config`) and there is no single
  "state owner" — mutations are scattered across module boundaries.
- **Wide record builders.** `_build_asi_dict` / `_build_db_record`
  (`autoresearch_experiment.py:645`) pull fields from several dicts with
  silent `{}`/`""` fallbacks; an upstream field rename would drop data quietly
  rather than fail loudly.

### 2.3 LLM / agent integration

- **Framework:** OpenAI Agents SDK (`from agents import Agent, Runner,
  function_tool`, e.g. `research_conductor.py:9`). Default model
  `gpt-5.2` (`autoresearch_constants.py:54`).
- **Routing:** all LLM calls go through a local OAuth proxy at
  `http://127.0.0.1:10531/v1` (`agent_infra.py`), which reads a token from
  `CLAUDE_CODE_OAUTH_TOKEN` → `~/.openai_oauth_token` → `~/.claude_oauth_token`.
  The proxy's health is checked at startup with a socket probe; unavailability
  is a fatal, clearly-messaged error. _Note the cross-provider naming: an
  OpenAI-SDK client, dummy `api_key="unused"`, auth delegated to a proxy that
  can be backed by a Claude OAuth token. It is internally consistent but worth
  a one-line comment for future readers._
- **Sub-agents:** a conductor (16 Pydantic-validated MCP tools) plus analyst
  and web-researcher sub-agents invoked as async callees. Tool args are
  validated at call time (`research_conductor.py:100`) and validation errors are
  returned to the model as text rather than raised.
- **Observability is a genuine strength.** OpenTelemetry via Traceloop
  (`trace_sdk.py`), per-call and per-round token accounting with a tiktoken
  fallback when the SDK reports zero (`agent_sdk_token_usage.py`), per-thesis
  rollups, budget-warning thresholds, and a post-run `scripts/token_audit.py`.
- **Robustness gaps:** retries have **no exponential backoff** (immediate
  re-hit of the proxy), the proxy is only health-checked at startup (a
  mid-run death surfaces as ambiguous transport errors), and some string tool
  fields are unbounded in length.

### 2.4 Compiler & backtest

- **"Builds missing primitives" without `eval`/`exec`.** The builder
  (`compiler_builder.py:1502`) copies the source tree into an isolated
  `builder_request/workspace/` (excluding `.git`/`.env`/data), invokes an
  external `codex` CLI agent over stdin, then validates the generated config in
  a **fresh Python subprocess** before anything runs. There is no string
  interpolation into executable code and no dynamic import/pickle. This is a
  sound sandboxing approach.
- **Residual builder risk:** the agent can write arbitrary Python into
  non-config files; the parent validates config schema + test existence but
  gates promotion on a manual review flag (`promotion_status: queued_review`)
  and snapshots only `st_size`/`st_mtime_ns`, not a content hash — a second run
  could overwrite a promoted file without a visible diff.
- **Backtest store** (`backtest_run_db.py`, 1599 lines): SQLite with JSON blobs
  for metrics/config, ISO-8601 UTC timestamps (legacy epoch-ms coerced on
  load), explicit verdict tracking. Trade simulation is JIT-compiled with numba
  (`numba_kernels.py`, `@njit(cache=True)`). Schema evolution is unversioned and
  metric-dict merging is last-write-wins — both fine for append-only runs but
  brittle under change.

### 2.5 Testing & engineering discipline

- **81 test files, behavior-first.** Sampled tests assert concrete
  counts/values and use characterization fixtures rather than structural
  null-checks — consistent with the project's own "Real tests only" rule
  (`CLAUDE.md` §G). Internal logic is exercised end-to-end; only the proxy and
  streamed runner are mocked at the service boundary.
- **CI** (`.github/workflows/ci.yml`) runs pre-commit (ruff/isort/black),
  bandit (high-severity), a prompt-drift check, and pytest with coverage on
  every push and PR. Recent `main` runs are green.
- **Debt is tracked, not hidden.** `TECH_DEBT_AUDIT.md` (dated 2026-05-04),
  `BUGS.md` / `bugs167.md`, and `FIX_PLAN.md` show a systematic root-cause
  effort — 147 tracked bugs (134 fixed, 13 needing repro) grouped G01–G11.
  `docs/superpowers/` holds 14 design specs and fix plans. This is a mature
  remediation culture.
- **The one hard number that stands out:** the coverage gate is
  `fail_under = 45` (`pyproject.toml:143`), while `CLAUDE.md` calls for 80% and
  the audit references a 70% intent. The gate was lowered to unblock CI and the
  promised ratchet has not landed. `research_conductor.py` was measured at 61%.
  _Caveat: `TECH_DEBT_AUDIT.md` is dated 2026-05-04 and is partly stale — it
  still references an older `gpt-5.5` model and marks several findings
  "→ FIXED"; treat its line-cited findings as needing re-verification against
  current code._

---

## 3. The travel research playbook

Built as a separate agentic-research methodology (~60 files under `travel/` on
branch `origin/travel-research-playbook`, being migrated into the currently-empty
`travel-rresearch-playbook` repo). It is a **specification for systematic,
auditable ranking** of expert-led tours — not a website.

Structure: 11 numbered methodology docs (`00-overview-and-principles.md` …
`11-trip-composition.md`), a `REGISTRY-PROTOCOL.md` single-source-of-truth,
file-backed controlled vocabularies (axes / lens / channel / sources
registries), per-country proof corpora (Italy + Japan piloted, with round-by-
round discovery logs and ranked outputs like `IT-01`, `IT-07`, `JP-01`), and a
quality apparatus (audit checklist, review panels in JS, a dated fix-plan).

Its governing principle — _"convergence is earned, not asserted"_ — mirrors the
trading system's admission gates: empty discovery cells raise
COVERAGE-LIMITATION flags rather than being padded. Maturity: **proven at pilot
scale** (Italy + Japan); the scale-out mechanics (dirty-propagation fixed-point,
typed-leads bus, corpus consolidation, multi-lens trip composition) are
specified but not yet operational.

---

## 4. Cross-cutting observations

- **The workspace's real thesis is the loop, not the domain.** Trading and
  travel are two applications of one idea: constrain an LLM research process
  with explicit contracts, an append-only auditable corpus, and hard admission
  bars so that "done" means "exhausted the evidence," not "the model stopped."
  That is the most reusable asset here.
- **Discipline is a differentiator.** `CLAUDE.md`'s failure-mode rules
  (wire-first, one-home-per-concept, no-patch-on-patch, real-tests-only,
  terminal-state-bookkeeping-before-validation) are enforced in the code and the
  tests, not just aspirational.
- **The migration is mid-flight.** The travel content lives on a branch and in a
  second, still-empty repo; until it lands, `travel-rresearch-playbook` reads as
  empty to anyone browsing the workspace.

---

## 5. Prioritized recommendations

**High**
1. **Ratchet the coverage gate.** Raise `fail_under` from 45 toward the stated
   70/80% (`pyproject.toml:143`), starting by closing `research_conductor.py`
   (~61%). The infrastructure to enforce it already exists in CI.
2. **Refresh `TECH_DEBT_AUDIT.md`.** It is two months stale and cites an old
   model; re-verify each `file:line` finding so the backlog is trustworthy.
3. **Complete the travel migration.** Land `travel/` into
   `travel-rresearch-playbook` (branch `origin/avinash8891/move-travel-folder`
   already stages this) so the repo is no longer empty.

**Medium**
4. **Split the two god-modules.** Separate thesis generation from execution in
   `autoresearch_research.py` / `autoresearch_experiment.py`, and funnel state
   writes through a single owner to satisfy the "terminal-state bookkeeping
   before validation" rule uniformly.
5. **Harden the builder promotion path.** Content-hash promoted files and
   enforce the review gate in CI rather than via a status flag.
6. **Add resilience to the agent layer.** Exponential backoff on retries and a
   runtime proxy-liveness check.

**Low**
7. **Add field-presence validation** to `_build_asi_dict` / `_build_db_record`
   so upstream renames fail loudly.
8. **Document the OAuth-proxy provider indirection** inline in `agent_infra.py`.
9. **Version the backtest DB schema** to make future migrations explicit.

---

## 6. Bottom line

What has been built is a genuinely capable autonomous-research platform with a
second, documentation-native instance of the same method. The core loops work
end-to-end, the observability and testing discipline are above average, and the
team already tracks its own debt honestly. The gap between the project's stated
quality bar and its current coverage gate is the single clearest thing to close;
after that, the main investments are structural (decompose the large modules)
and completion-oriented (finish the travel-repo migration).
