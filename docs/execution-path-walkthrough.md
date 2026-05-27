# How the Code Runs — Plain-English Walkthrough
A non-technical tour of what happens when you start a **fresh autoresearch job**: from typing the command on your laptop to the first backtest result landing in the database. Each step is one line by default; only steps with a real gotcha or design choice get more.
## The big picture
```
   YOUR LAPTOP                              THE VPS (rented server in the cloud)
   ┌──────────────────────┐                 ┌──────────────────────────────────┐
   │ you type:            │                 │                                  │
   │ python vps_runner.py │  ── SSH ──►     │  autoresearch_controller.py      │
   │   --strategy ema     │                 │     (the "brain" / state machine)│
   │   --git-ref main     │                 │           │                      │
   └──────────────────────┘                 │           ▼                      │
                                            │   autoresearch.next.json  (state)│
                                            │           │                      │
                                            │           ▼                      │
                                            │   research → backtests           │
                                            │           │                      │
                                            │           ▼                      │
                                            │   results in SQLite              │
                                            └──────────────────────────────────┘
```

Two machines. Your laptop **drives**. The VPS does the **work**.

* * *
## Block 1 — On your laptop: launching the run (steps 1–5)
- **Step 1 — You run the command:** `python vps_runner.py --strategy ema --git-ref main`
  
  - `--strategy ema` — which family of trading strategies to research (`ema`, `orb`, …).
    
  - `--git-ref main` — which version of the code to deploy. Accepts a branch (`main`), a tag (`v1.2.3`), or a commit ID (`c968654`). Branch = "latest code at deploy time"; commit ID = "exact frozen snapshot, never changes" — use the latter for reproducible runs.
    
- **Step 2 — Load the** `.env` **file** — opens `./.env` and copies `KEY=VALUE` lines into the program's env vars. Shell wins over file (so `export FOO=...` in your terminal beats the file value).
  
- **Step 3 — Parse what you typed** — `argparse` turns flags into structured values and rejects invalid choices (`--strategy ema5` fails here, before any network call).
  
- **Step 4 — Build a** `VPSConfig` **from env vars** — bundles the 5 required vars (`AUTORESEARCH_VPS_HOST`, `_USER`, `_KEY`, `_DIR`, `AUTORESEARCH_GIT_REPO`) into one object. Missing → hard error.
  
- **Step 5 — Open an SSH connection to the VPS** using `paramiko`. Verifies the server identity against `known_hosts`.
  
  - Gotcha: first run on a brand-new VPS often fails because its host key isn't in `known_hosts` yet — add it once with `ssh-keyscan`.
    

* * *
## Block 2 — On the VPS: preparing the workspace (steps 6–10)
- **Step 6 — Safety probe: "anything already running?"** Since this is a fresh job, the probe finds nothing active → safe to proceed.
  
- **Step 7 — Check the VPS's current code version** with `git status`. On a fresh VPS there's nothing checked out yet, so the runner falls through to Step 8.
  
- **Step 8 — Fetch & check out the requested code** — `git clone/fetch`, `git checkout` to a detached HEAD on the requested commit, then `pip install` to refresh Python dependencies. Slow on first deploy of a branch; skipped on subsequent runs if the SHA hasn't moved.
  
- **Step 9 — Upload a curated env file to the VPS** as `.env.autoresearch`. The runner only forwards keys whose name starts with one of the allowed prefixes; everything else (`PATH`, `HOME`, ssh-agent vars) stays on your laptop.
  
  - **API keys** (without these the agents can't make LLM calls):
    
    - `ANTHROPIC_*` — Claude API key + config; research sub-agents call Claude.
      
    - `OPENAI_*` — OpenAI API key + config; other sub-agents call GPT.
      
  - **Config knobs** (no secrets, just settings):
    
    - `AUTORESEARCH_*` — the project's own flags (data root, VPS dir, feature toggles).
      
    - `CLAUDE_CODE_*` — Claude Agent SDK settings.
      
  - **Observability / tracing** (entirely optional — only forwarded if set):
    
    - `OTEL_*`, `OPENTELEMETRY_*` — OpenTelemetry collector settings.
      
    - `TRACELOOP_*` — Traceloop SDK (an LLM observability provider).
      
    - `OPENINFERENCE_*` — OpenInference / Arize observability.
      
  - The prefix list is an **allowlist**: unset vars never get forwarded. To wire in a new SDK, append its prefix to `REMOTE_RUNTIME_ENV_PREFIXES` in `vps_runner.py:45`.
    
- **Step 10 — Run the "prepare" command remotely** — SSH-runs `autoresearch_controller.py --prepare-launch-state-only`. This creates the initial state file via `_fresh_launch_state` (`autoresearch_controller.py:348`) and prints back `AUTORESEARCH_PREPARE_RESULT {"ok": true, "job": 1, "state": "running", ...}` confirming the VPS is ready. The fresh state has no queued config yet — the controller picks the baseline once the loop starts.
  

* * *
## Block 3 — The controller starts (steps 11–15)
After prepare succeeds, the runner SSH-fires the real "go" command: `python autoresearch_controller.py --family ema --run-current-state`

- **Step 11 — Controller boots** — loads the strategy family object (e.g. EMA). From now on, terminal output streams from the VPS.
  
- **Step 12 — Resolve paths** — figures out where state and artifacts go:
  
  - `autoresearch.next.json` — the **state file** (the "brain").
    
  - `autoresearch.current.md` — human-readable view of what's running.
    
  - `runtime/jobs/<job-id>/…` — per-job folder for theses, builder requests, traces.
    
- **Step 13 — Read the state file** — loads `autoresearch.next.json`. For a fresh job it was initialized in Step 10 with `state: "running"`, `research_round: 0`, and **no queued config** (`autoresearch_controller.py:348` → `_fresh_launch_state`). The baseline config gets picked later by `plan_next_action` when the loop starts.
  
  - The four possible states across a job's lifetime:
    
    - `running` — controller can proceed (our starting point for a fresh job — picks the baseline first, then variants).
      
    - `blocked` — needs research to proceed (kicks in after the baseline has run and before variants are produced).
      
    - `halted` — something went wrong; **you** need to look.
      
    - `finished` — job complete.
      
- **Step 14 — Normalize launch state** — fresh job confirmed; job id stays at 1, state stays `running`, written back to disk.
  
- **Step 15 — Enter the main loop** — `_run_controller_loop()` calls `execute_once()` in a `while True` until a terminal state. This is the heartbeat.
  

* * *
## {==Block 4 — One iteration: `execute_once()` (steps 16–20)==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
{==For a fresh job the very first iteration runs the **baseline backtest** (a hardcoded "Brand-new job policy" in `autoresearch_planning.py:302`). Research only starts on iteration 2+, once the baseline has produced a comparison point.{>>You're right, I was wrong. Grounded in the code now:==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}

1. {==`_fresh_launch_state(job)` in `autoresearch_controller.py:348` returns `state: "running"` (not `"blocked"`), with no queued config.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
2. {==On the first `execute_once`, the controller calls `resolve_next_action` → `reconcile_state` → `plan_next_action` in `autoresearch_planning.py:289`.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
3. {==`plan_next_action` line 302-309 has an explicit rule: **"Brand-new job policy: baseline always runs first for the family."** When `results` is empty, it sets `next_action` to the baseline config.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
4. {==So the first iteration runs the **baseline backtest** (the reference strategy for that family — e.g. the standard EMA(9,21) setup). This produces the first row in the SQLite results DB.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
5. {==**Research only starts on iteration 2+**, after the baseline result exists to compare against.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  

{==Corrected flow for Block 4 of the doc would be:==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}

- {==Step 16: Resolve next action — for a fresh job with empty results, returns the baseline as `next_action`.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
- {==Step 17: **Baseline backtest** runs first (jump to the experiment branch / Step 20).==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
- {==After baseline has a result, the next iteration takes branch 3 (research) and produces a thesis. THEN compile/queue/backtest the variants.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  

{==Other corrections needed: Step 10's prepare-result example should show `"state": "running"` not `"blocked"`. Step 13 should say the fresh state is `running` with no queued config (baseline gets picked up by `plan_next_action`, not by the prepare step). Step 14: fresh job state is `running`.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}

{==Applied to the body now.<<}{id="r11" by="AI" at="2026-05-26T15:30:00.000Z" re="c10"}==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}

- {==**Step 16 — Resolve next action** — the controller asks itself, "what should I do?" Call chain: `resolve_next_action` → `reconcile_state` → `plan_next_action` (`autoresearch_planning.py:289`).==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
  - {==Priority order inside `plan_next_action`:==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
    
    1. {==Forced baseline rerun pending (e.g. code change) → re-validate reference strategy.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
      
    2. {==**No prior results yet** (brand-new job) → return the baseline config as `next_action`. This is the "Brand-new job policy" at `autoresearch_planning.py:302`.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
      
    3. {==Otherwise → call `select_research_next_action` which either runs research or queues an already-produced variant.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
      
  - {==**First iteration of a fresh job:** results list is empty → branch 2 fires → next_action is the **baseline backtest**. Jump to Step 20 (experiment branch).==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
    
- {==**Step 17 — Research branch: run the research conductor** (iteration 2+ only, after the baseline has a result) — spawns AI sub-agents in parallel to produce a new trading hypothesis (a "thesis").==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
  - {==Cost note: this step is **expensive** — it spends OpenAI/Anthropic tokens. If your run looks pricey, look here first.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
    
  - {==The sub-agents:==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
    
    - {==Web researcher — market regime / news context.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
      
    - {==Data analyst — recent price-action patterns.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
      
    - {==Doctrine reviewer — checks the idea against project rules.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
      
  - {==Output: a written thesis like "EMA(9,21) crossovers work better when volatility is below threshold X."==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
    
- {==**Step 18 — Compile the thesis into variants** — `compiler_pipeline.py` turns the prose thesis into concrete Python parameter sets (e.g. EMA periods 5, 7, 9). One thesis usually fans out to 5–20 variants.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
- {==**Step 19 — Queue the variants** — each variant is written as a JSON file in `runtime/jobs/1/builder_requests/`. State transitions from `blocked` → `running` once at least one variant is queued.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}
  
- {==**Step 20 — Experiment branch: run the backtest** (next iteration) — pulls a queued config and runs `autoresearch_experiment.py`. This is the step that produces the actual performance numbers.==}{>>you are wrong. did you even read the code base? first step is backtest. read the code. ground your truth. make the changes<<}{id="c10" by="user" at="2026-05-26T15:22:21.897Z"}{>>rewrite this block 4 in simple and shot terms in plain english.<<}{id="c11" by="user" at="2026-05-26T15:33:46.984Z"}{>>Proposed simpler Block 4 (file/function names stripped, code-grounded order kept). Say `update` and I'll replace the current Block 4 with this:
  

* * *

**Block 4 — One iteration of the loop (steps 16–20)**

A fresh job runs the baseline first; research only kicks in once the baseline has produced a result to compare against.

- **Step 16 — Pick what to do next.** The controller asks: "what should I run?" It checks, in order:
  
  1. Is a forced baseline rerun pending (e.g. code changed)? → run that.
    
  2. Are there no results yet (brand-new job)? → run the **baseline strategy** (the family's reference setup, e.g. plain EMA 9/21).
    
  3. Otherwise → run research, or run a variant that's already queued.
    
  
  _Fresh job, first iteration: branch 2 fires → baseline goes next._
  
- **Step 17 — Run research** (iteration 2+, once a baseline result exists). The controller spawns AI agents in parallel to propose a new trading idea:
  
  - Web researcher — looks at market news / regime.
    
  - Data analyst — looks at recent price patterns.
    
  - Doctrine reviewer — checks the idea against project rules.
    
  
  Output: a written hypothesis (a "thesis"), e.g. _"EMA crossovers do better when volatility is below X."_ **This is the expensive step** — it spends LLM tokens. Highest cost driver in any run.
  
- **Step 18 — Turn the thesis into testable variants.** The compiler converts the prose hypothesis into concrete parameter sets to backtest (e.g. EMA periods 5, 7, 9). One thesis usually fans out to 5–20 variants.
  
- **Step 19 — Queue the variants.** Each variant becomes a small "to-do" file. Next iterations pick them up one by one.
  
- **Step 20 — Run a backtest.** The controller pulls a queued config (the baseline on iteration 1, a variant later) and simulates the strategy on historical data. This is the step that produces the performance numbers — Sharpe, drawdown, win-rate, etc.<<}{id="r12" by="AI" at="2026-05-26T15:45:00.000Z" re="c11"}
  

* * *
## Block 5 — Wrap-up: data flow back to you (steps 21–25)
- **Step 21 — Backtest engine runs** — loads parquet files from `AUTORESEARCH_DATA_ROOT` (e.g. `universes/nasdaq8/<ticker>.parquet`) and simulates the strategy bar-by-bar using numba-compiled kernels for speed.
  
- **Step 22 — Metrics computed** — Sharpe ratio, max drawdown, total return, win rate, exposure. These answer "is this strategy any good?"
  
- **Step 23 — Persist results in three places:**
  
  - `ema_backtest_runs.db` (SQLite) — one row per backtest with all metrics → **use this for analysis**.
    
  - `trace_exports/<job>/` — full JSONL event trace of the round → **use this when debugging "what happened?"**.
    
  - `autoresearch.next.json` — updated state so the next iteration knows where to pick up.
    
- **Step 24 — Loop or terminate** — if state becomes `finished` / `halted` / `interrupted` → exit. Otherwise → back to Step 16 for the next variant. Optional Discord webhook fires a notification.
  
- **Step 25 — Output streams back to your laptop** over the SSH connection from Step 5.
  
  - Gotcha: if your laptop sleeps or you close the terminal, the **runner** dies but the **VPS controller is unaffected** (separate process) — you lose the live stream, the run continues. Re-attach by SSHing in and tailing the log file.
    

* * *
## Data flow — one consolidated diagram
```
   YOUR .env (laptop)                                  VPS filesystem
   ─────────────────                                   ─────────────────────────
   AUTORESEARCH_VPS_*  ──┐                             /your-vps-dir/
   AUTORESEARCH_GIT_*    │                              ├── (git checkout of code)
   AUTORESEARCH_DATA_*   ├── SSH upload ───────────►   ├── .env.autoresearch
   ANTHROPIC_API_KEY     │                              ├── autoresearch.next.json  ◄── state
   OPENAI_API_KEY      ──┘                              ├── autoresearch.current.md ◄── human view
                                                        ├── ema_backtest_runs.db    ◄── results
                                                        ├── trace_exports/          ◄── traces
                                                        └── runtime/jobs/1/         ◄── this job
                                                              ├── builder_requests/
                                                              └── thesis/
                              ◄── stdout streams back ──┘

   MARKET DATA (mounted on VPS at AUTORESEARCH_DATA_ROOT)
   ──────────────────────────────────
   universes/nasdaq8/<ticker>.parquet  ──► read by backtest engine in Step 21
```

* * *
## Glossary
- **VPS** — a rented Linux computer in the cloud; runs 24/7.
  
- **SSH** — secure way to send commands from your laptop to the VPS.
  
- **Env var** — `KEY=value` setting kept outside the code so secrets aren't committed.
  
- **Git ref** — name for a code version: a branch (`main`), tag (`v1.2.3`), or commit ID.
  
- **State machine** — software always in exactly one state (running / blocked / halted / finished), with rules for moving between them.
  
- **Thesis** — a written hypothesis ("EMA crossovers work better in trending markets") that becomes the next batch of experiments.
  
- **Backtest** — simulating a trading strategy on past market data to see how it would have performed.
  
- **Metric** — a summary number describing a backtest result (Sharpe ratio, max drawdown, …).
  
- **SQLite** — a tiny database stored as a single file; used here to record every backtest.
  
- **Detached HEAD** — Git mode where you're sitting on a specific commit without tracking any branch.
  
- **Argparse** — Python's built-in command-line argument parser.
  
- **OpenTelemetry / Traceloop / OpenInference** — observability tools that record what the AI agents do (prompts, responses, latency, cost) for a dashboard. All optional.
