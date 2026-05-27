# New Job — Simple Walkthrough
A plain-English tour of what happens when you start a brand-new auto-research job from scratch. No function names, just the files that do the work.

You start it with one command on your laptop:

```
python vps_runner.py --strategy ema --git-ref main --fresh-job
```

Everything below happens by itself after you press Enter.

* * *
## 1. Your laptop wakes up
- `vps_runner.py` is the program that runs on your laptop. It is the _launcher_.
  
  - It first reads `.env` to learn things like the VPS address, your SSH key, and the Git repo URL.
    
  - It looks inside the `strategies/` folder to learn which strategies exist (so it knows `ema` is a real choice and `foo` is not).
    
  - It checks the flags you passed: a strategy name and a Git version.
    

* * *
## 2. Your laptop calls the VPS
- The launcher opens an SSH connection to the VPS (the rented server in the cloud).
  
- Before doing anything destructive, it asks the VPS: _"are you already busy with a round right now?"_
  
  - If yes, it refuses and exits. You'd have to pass `--force` to override.
    
- It asks the VPS: _"what Git version do you currently have?"_
  
  - If the VPS is already on the version you asked for, it skips the next step to save time.
    

* * *
## 3. The VPS gets the latest code
- The launcher tells the VPS to do a `git fetch` + checkout of the version you asked for.
  
- The VPS reinstalls Python dependencies (if needed).
  
- The launcher then uploads two things over SSH:
  
  - The **runtime env file** (so the VPS knows API keys, paths, webhook URLs).
    
  - The **Codex auth file** (so the VPS can talk to the OpenAI Codex agent).
    

* * *
## 4. The VPS prepares a fresh job
- The launcher runs a short _prepare_ step on the VPS.
  
- This step lives in `autoresearch_controller.py` — the brain of the whole system.
  
- Because you said `--fresh-job`, the controller:
  
  - Picks the next job number (last job + 1).
    
  - Wipes any leftover "halted" or "resume" markers from the prior job — a clean slate.
    
  - Writes a new state file: `ema_autoresearch.next.json` (for ema; orb has its own).
    
  - Prints a one-line JSON summary so your laptop knows the prep succeeded.
    

* * *
## 5. The VPS starts the main loop
- Same brain — `autoresearch_controller.py` — but now in "loop forever until done" mode.
  
- Every pass through the loop does the same thing:
  
  - Ask `autoresearch_orchestration.py` — the _decision desk_ — "what's next?"
    
  - Do that one thing.
    
  - Update the state file and go round again.
    

* * *
## 6. First iteration — the baseline backtest always runs first
This is the rule baked into `autoresearch_planning.py`: **a brand-new job with no results runs the baseline backtest first. No research, no thesis, no AI agents yet.**

- The decision desk looks at the result history. For a fresh job it's empty.
  
- Because it's empty, `autoresearch_planning.py` picks the strategy's **baseline config file** from the `configs/` folder (e.g. `configs/ema_base.yaml` or `configs/orb_base.yaml`). The exact filename comes from `strategy_family.py`'s `baseline_config_path`, which always points inside `configs/`, never inside `strategies/`.
  
- It writes a `next_action` into `ema_autoresearch.next.json` that says: _"run a backtest of the baseline config, label it round 0."_
  
- Control goes straight to `autoresearch_experiment.py` — the _test bench_.
  

* * *
## 7. The baseline backtest runs
- `autoresearch_experiment.py` runs a real backtest of the baseline config against historical market data.
  
  - The market data lives in the folder pointed at by `AUTORESEARCH_DATA_ROOT` in your `.env`.
    
- When it finishes, the test bench:
  
  - Computes metrics (Sharpe, drawdown, win rate, etc.).
    
  - Saves the result row to `ema_backtest_runs.db`.
    
  - Writes trace files into `trace_exports/` under `runtime/jobs/job-N/research/round-0-baseline/backtest`.
    
  - Updates `ema_autoresearch.current.md` so a human can read the current best result at a glance.
    
- Now the result history is **not empty** anymore. The loop goes round.
  

* * *
## 8. Second iteration — now research can happen
- The decision desk sees there is at least one result in the history, so the "baseline first" rule no longer applies.
  
- It calls into `autoresearch_planning.py` again to pick the next variant. If there's nothing in the queue, it marks the state as `blocked` with a `research_required` blocker.
  
- Before doing anything else, planning reads any **rejection artifacts** sitting in the run folder from earlier failed backtests (saved by `rejection_artifact.py`). Those say _"don't propose this idea again, here's why it failed."_ The next round starts from that prior knowledge.
  
- Seeing the blocker, `autoresearch_controller.py` hands off to `autoresearch_research.py` — the research round.
  
  - `research_conductor.py` is the _project manager_ for AI agents.
    
  - The conductor spawns specialist agents defined in `research_subagents.py`:
    
    - one reads web pages,
      
    - one reads the existing code,
      
    - one looks at past backtest results,
      
    - one keeps long-term memory in `research_memory.py`.
      
  - Each agent's prompt comes from `agent_prompts.py`.
    
  - The actual AI calls go through `agent_openai_calls.py` (with retries) and every call's token cost is recorded by `agent_token_usage.py` into `trace_exports/`.
    
- `thesis_validator.py` then checks the thesis: is it well-formed, does it actually differ from prior rejected ones, does it touch the right config keys? Invalid theses get sent back for another round.
  
- The research round produces a written **thesis** — a one-paragraph idea like _"try EMA with a 21-period window and a 0.5% stop-loss."_
  

* * *
## 9. The thesis becomes a real strategy config, then runs
- `autoresearch_planning.py` takes the thesis and decides which strategy variant(s) to queue.
  
- `compiler_pipeline.py` is the _workshop_ that turns the thesis into runnable code. It runs several small phases in order:
  
  - `compiler_research.py` deepens technical details the thesis was vague about.
    
  - `compiler_builder.py` writes any missing **Python primitives** (new indicators, new rules) into the strategy code folder (`strategies/ema/` or `strategies/orb/`).
    
  - `compiler_validate.py` + `compiler_implementation_verify.py` smoke-check what was just written — does it compile, do the contracts agree?
    
  - `compiler_operationalize.py` emits the runnable **YAML variant** into `configs/variants/` (e.g. `configs/variants/orb_trailing_stop.yaml`).
    
  - `compiler_thesis_io.py` reads and saves the thesis text to disk for the audit trail.
    
- The loop comes back to `autoresearch_experiment.py` and runs the variant backtest.
  
  - `autoresearch_experiment.py` invokes the backtest subprocess, then `experiment_evaluator.py` turns the metrics into a clear _accept/reject_ verdict the controller uses.
    
  - `eval_harness.py` separately runs the strategy against a fixed **holdout** task set so improvements aren't just curve-fitting.
    
  - Results land in `ema_backtest_runs.db`.
    

* * *
## 9.5 The improvement pass — easy to miss, but always there
Before the loop accepts the result and moves on, a gated **post-round improvement pass** can polish the candidate code. This is its own little sub-loop:

- `improvement_flags.py` decides which of the polishing subsystems are enabled for this round.
  
- `improvement_halo.py` shells out to the external `halo` CLI to get code-improvement recommendations.
  
- `improvement_reflexion.py` writes a structured "what to change next" report based on the just-completed round's trace.
  
- `improvement_recursive_improve.py` can run a self-edit loop on the candidate strategy.
  
- `improvement_halo_apply.py` actually applies the accepted recommendations to the next round's config.
  
- `improvement_ratchet.py` enforces that quality only goes up — bad rounds don't replace good ones as the new baseline.
  

If you set a Discord webhook in `.env`, the controller sends a short summary message — fail-open, a broken webhook never stops the run.

The loop goes round again: decision desk → next variant or another research round → backtest → improvement pass → repeat.

* * *
## 10. When does it stop?
The loop only exits when one of these happens:

- `finished` — all planned variants ran and the goal metric was hit.
  
- `halted` — something needs a human (e.g. the compiler couldn't write valid code).
  
- `interrupted` — a fatal error occurred.
  
- `blocked` **for too long** — research kept asking for more research with no progress; the loop gives up after a configured number of tries.
  

When the loop exits on the VPS, the SSH connection on your laptop returns. The launcher prints the exit code and you're done.

* * *
## The cast of files, one line each
| File | Plain-English role |
| --- | --- |
| `vps_runner.py` | Laptop-side launcher; ships code to the VPS and starts it |
| `.env` | Your settings — VPS address, keys, webhook URLs |
| `strategies/` | The **Python code** for each strategy (signals, exits, validators) |
| `configs/` | The **YAML configs** the backtest actually consumes (`ema_base.yaml`, `orb_base.yaml`, plus `configs/variants/`) |
| `strategy_family.py` | Per-strategy metadata (baseline config path, default variants) |
| `autoresearch_controller.py` | The brain — the loop that drives everything |
| `autoresearch_orchestration.py` | The decision desk — picks the next action |
| `autoresearch_planning.py` | Turns a thesis into a queue of variants; baseline-first on iteration 0 |
| `autoresearch_research.py` | Runs a research round |
| `research_conductor.py` | Project manager for AI agents |
| `research_subagents.py` | The specialist AI agents |
| `research_memory.py` | Long-term memory across runs (MemPalace-backed) |
| `agent_openai_calls.py` | The phone line to the AI model |
| `agent_token_usage.py` | Tracks every cent spent on AI calls |
| `thesis_validator.py` | Checks a proposed thesis is well-formed and not a re-run of a rejection |
| `rejection_artifact.py` | Saves "why this thesis failed" so the next round doesn't repeat it |
| `compiler_pipeline.py` | Workshop — turns ideas into runnable code |
| `autoresearch_experiment.py` | Test bench — runs the actual backtest |
| `experiment_evaluator.py` | Turns backtest metrics into an accept/reject verdict |
| `eval_harness.py` | Holdout evaluation — guards against curve-fitting |
| `backtest_run_db.py` / `ema_backtest_runs.db` | Storage for backtest results |
| `improvement_halo.py` | Post-round code-improvement suggestions from the external `halo` CLI |
| `improvement_halo_apply.py` | Applies accepted halo suggestions to the next round |
| `improvement_ratchet.py` | Quality-only-goes-up gate between rounds |
| `trace_sdk.py` / `trace_exports/` | The flight recorder — every step is logged here |
| `scripts/token_audit.py` | After-the-fact cost report: tokens by model / agent / job |
| `ema_autoresearch.next.json` | "What state are we in right now?" |
| `ema_autoresearch.current.md` | Human-readable summary of best result so far |
