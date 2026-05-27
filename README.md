# agents-auto-research

Autonomous quantitative research loop: proposes hypotheses, runs backtests, validates results, and iterates — driven by an LLM research conductor and a suite of sub-agents.

## Overview

The system runs an agentic research loop for trading strategy families (currently EMA and ORB). Each loop iteration:

1. **Plans** — selects the next thesis to test based on prior experiment results.
2. **Researches** — the conductor agent proposes a structured thesis (config changes, expected effects, disqualifiers) informed by trade analysis, web search, and cross-session memory.
3. **Compiles** — builder agents generate and validate any missing strategy primitives for the thesis config.
4. **Backtests** — the validated config is run through the backtest engine and results are evaluated.
5. **Iterates** — findings feed into the next round until a stopping criterion is met or the operator halts.

## Architecture

The system is organized into five planes:

```
┌─────────────────────────────────────────────────────────┐
│  Entry points                                           │
│  autoresearch_controller.py   — state machine, main loop│
│  vps_runner.py                — remote deploy + launch  │
│  autoresearch_cli.py          — experiment tracking CLI │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Orchestration plane                                    │
│  autoresearch_orchestration.py  — round-level dispatch  │
│  autoresearch_planning.py       — thesis generation     │
│  autoresearch_research.py       — per-thesis research   │
│  autoresearch_experiment.py     — metric evaluation     │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Agent plane                                            │
│  research_conductor.py    — conductor (OAI Agents SDK)  │
│  research_subagents.py    — analyst + web researcher    │
│  agent_runners.py         — SDK runner wrapper          │
│  agent_openai_calls.py    — low-level OAI API calls     │
│  agent_infra.py           — shared infra (proxy, retry) │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Compiler plane                                         │
│  compiler_pipeline.py         — top-level orchestrator  │
│  compiler_builder.py          — builds missing prims    │
│  compiler_operationalize.py   — operationalizes code    │
│  compiler_thesis_io.py        — thesis read/write       │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Persistence plane                                      │
│  backtest_run_db.py      — SQLite experiment records      │
│  research_memory.py    — MemPalace cross-session memory │
│  trace_sdk.py          — OpenTelemetry trace events     │
│  autoresearch_state.py — JSON state file helpers        │
└─────────────────────────────────────────────────────────┘
```

### Strategy families

Each strategy family (e.g. `ema`, `orb`) is registered under `strategies/` via the `@register` decorator. The family object provides:
- `description_for_research` — natural-language description fed to the conductor
- `benchmark_command(config_path)` — shell command to run a backtest
- `validate_runtime_config_scope(config)` — validates a runtime config dict
- `research_spec` — allowed config keys, schema, and constraint rules

### VPS runner

`vps_runner.py` deploys a git ref (branch, tag, or git SHA) to a remote host via SSH, then launches `autoresearch_controller.py` in a tmux session. Use `--git-sha` for reproducible deploys pinned to an exact commit.

## Environment variables

Copy `.env.example` → `.env`. Required:

| Variable | Purpose |
|---|---|
| `AUTORESEARCH_DATA_ROOT` | Local path to market data (`universes/nasdaq8/`, etc.) |
| `AUTORESEARCH_VPS_HOST` | SSH hostname for remote deploy |
| `AUTORESEARCH_VPS_USER` | SSH username |
| `AUTORESEARCH_VPS_KEY` | Path to SSH private key |
| `AUTORESEARCH_DISCORD_WEBHOOK_ORB` | Run notifications for ORB family (optional) |
| `AUTORESEARCH_DISCORD_WEBHOOK_EMA` | Run notifications for EMA family (optional) |

### OpenAI OAuth proxy

Research jobs route all LLM calls through a local OAuth proxy at `http://127.0.0.1:10531/v1`. The proxy must be reachable on whichever machine runs the research process (VPS or local).

**VPS deploys** (via `vps_runner.py`): `openai-oauth.service` is already running on the VPS as a systemd service — nothing extra needed.

**Local runs**: start the proxy manually or via `systemctl start openai-oauth.service` before invoking `autoresearch_controller.py`.

The proxy reads an OAuth token from (in order):
1. `CLAUDE_CODE_OAUTH_TOKEN` env var
2. `~/.openai_oauth_token` (on the machine running the research process)
3. `~/.claude_oauth_token` (legacy fallback)

If you need to run manually on the VPS as root, the token lives at `/home/researcher/.openai_oauth_token`. Copy it to `/root/.openai_oauth_token` first.

## Development

Install the repo hooks once and let them run before each commit:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Run tests:

```bash
pytest                                        # full suite
pytest --cov --cov-report=term-missing        # with coverage
pytest tests/test_research_conductor_paths.py -v  # single file
```
