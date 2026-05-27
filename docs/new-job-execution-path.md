# A New Job, From Scratch — Every Step
Every step that runs when you start a brand-new job. Nothing skipped, nothing assumed. Each step cites the real file and line number.

You start it by typing this on your laptop:

```
python vps_runner.py --strategy ema --git-ref main
```

That's the only thing you do. Everything below happens on its own.

* * *
## Part 1 — Python starts on your laptop
1. **Your shell starts the Python program** `vps_runner.py`.
  
2. **Python reads the file from top to bottom** and runs the `import` lines.
  
3. **One of those imports is** `from strategies import STRATEGIES` (`vps_runner.py:32`).
  
4. **That import opens the** `strategies/` **folder** and finds every sub-folder (like `strategies/ema/`, `strategies/orb/`).
  
5. **It runs each strategy's setup code.** Each strategy's setup calls a `@register("ema")` decorator that adds itself to a global dictionary called `STRATEGIES` (`strategies/__init__.py:9`, `strategies/base.py:131`).
  
6. **After this finishes, the program knows the list of valid strategies** ("ema", "orb", etc.) without you telling it.
  

* * *
## Part 2 — Reading your settings
7. **Python jumps to** `main()` (`vps_runner.py:1042`).
  
8. `_load_local_env_file()` **opens** `./.env` (`vps_runner.py:90`).
  
9. **It reads the file one line at a time.**
  
10. **It skips blank lines and** `#` **comment lines.**
  
11. **It strips the word** `export` **if present.**
  
12. **It splits each line on the first** `=` into a key and a value.
  
13. **It rejects keys that aren't valid variable names** (must start with letter/underscore).
  
14. **If a key is already set in the shell, the file is ignored for that key** — shell wins.
  
15. **It strips outer quotes from the value.**
  
16. **It copies the result into** `os.environ` so the rest of the program can read it.
  

* * *
## Part 3 — Reading your command
17. `argparse` **builds the list of allowed flags** (`vps_runner.py:1006`).
  
18. **The** `--strategy` **choices come from the** `STRATEGIES` **dictionary** built in Part 1 — that's why "ema" works but "foo" doesn't.
  
19. **It parses your command line.** Wrong strategy name → hard error here, before any network call.
  
20. **It checks** `--fresh-job` **and** `--resume-current-job` **aren't both set.**
  
21. **It saves** `strategy_name = "ema"` **and** `git_ref = "main"`**.**
  

* * *
## Part 4 — Loading the strategy
22. `load_family("ema")` **runs** (`vps_runner.py:1051`, `strategy_family.py:91`).
  
23. **A cached function builds a** `StrategyFamily` **object for every registered strategy** on first call (`strategy_family.py:73`).
  
24. **That object holds metadata: baseline config filename, variant prefix, default variant list, Discord webhook env-var name, etc.**
  
25. `load_family` **returns the one named "ema".**
  
26. **If the name was wrong, it raises** `Unknown strategy family`**.**
  

* * *
## Part 5 — Building the VPS config
27. `config_from_env(git_ref="main")` **runs** (`vps_runner.py:131`).
  
28. **It checks that 5 environment variables are set:** `AUTORESEARCH_VPS_HOST`, `_USER`, `_KEY`, `_DIR`, `AUTORESEARCH_GIT_REPO`. Missing → error.
  
29. **It reads** `AUTORESEARCH_VPS_DIR` (the folder path on the VPS).
  
30. **It validates that folder path:**
  

- refuses the old legacy path `/root/orb-research`
  
- must be absolute and normalized
  
- must not be `/`, `/root`, `/home`, `/srv`, `/tmp`, `/var`, `/opt`
  
- must not contain `..`
  
- must only contain safe characters
  
- must end in a folder name containing "autoresearch" or "auto-research"
  

31. **It validates** `--git-ref` to make sure it's a real branch/tag/SHA (no refspecs, no `..`, no backslashes, no `@{` syntax).
  
32. **It reads** `AUTORESEARCH_DATA_ROOT` if set, expands `~` to `/root` or `/home/<user>`, and validates the path.
  
33. **It returns a** `VPSConfig` **object** holding host, user, key file path, remote dir, git repo URL, git ref, and data root.
  

* * *
## Part 6 — SSH connection to the VPS
34. `connect_verified_ssh_client(vps_config)` **runs** (`vps_runner.py:825`).
  
35. **It creates a paramiko SSH client.**
  
36. **It loads the system's** `~/.ssh/known_hosts` **file.**
  
37. **If** `AUTORESEARCH_KNOWN_HOSTS` **is set, it also loads that file.**
  
38. **It sets a "reject unknown hosts" policy** — first-time VPS without a recorded host key → connection refused.
  
39. **It calls** `.connect(host, username, key_filename)` — SSH handshake happens, your key is used to authenticate.
  

* * *
## Part 7 — "Is anything already running?" (active-run probe)
40. `build_activity_probe_command(...)` **builds a shell command** that will run on the VPS (`vps_runner.py:404`).
  
41. **The command first checks if the repo or repo-cache folder exists on the VPS.** If neither does, it prints `AUTORESEARCH_ACTIVE_RUN {"active": false, "reason": "missing_checkout"}`.
  
42. **Otherwise it launches a small embedded Python snippet** that does:
  

- run `ps -eo command=` to list every running process
  
- look for any line containing `autoresearch_controller.py --family ema`
  
- open `ema_autoresearch.next.json` (the state file) if it exists
  
- check whether the state shows a research round in progress, a builder running, or an experiment running
  
- print `AUTORESEARCH_ACTIVE_RUN <json>` with all those flags
  

43. **Your laptop runs this over SSH.**
  
44. `parse_activity_probe` **extracts the JSON from the marker line** (`vps_runner.py:510`).
  
45. **If** `active == true`**, your laptop refuses to deploy and exits with code 2.** `--force` would skip this.
  

* * *
## Part 8 — "What code is the VPS on right now?" (git status)
46. `build_git_status_command(...)` **builds a shell command** (`vps_runner.py:366`).
  
47. **The command checks if** `<remote_dir>/repo-cache/.git` **exists.** If not, it falls back to checking `<remote_dir>/.git`. If neither, prints `AUTORESEARCH_CURRENT_SHA missing` and exits.
  
48. **It runs** `git fetch --prune origin <ref>` to pull the latest from GitHub.
  
49. **It runs** `git rev-parse --verify FETCH_HEAD^{commit}` to turn "main" into a real 40-char SHA.
  
50. **It prints two markers:** `AUTORESEARCH_CURRENT_SHA <sha>` (what's on the VPS now) and `AUTORESEARCH_RESOLVED_SHA <sha>` (what you asked for).
  
51. **Your laptop parses both SHAs** via `parse_current_sha` and `parse_resolved_sha`.
  
52. `_should_skip_git_prepare` **compares them.** If equal → skip the next step.
  

* * *
## Part 9 — Deploying the code (git prepare)
53. `build_git_prepare_command(...)` **builds the deploy command** (`vps_runner.py:322`).
  
54. **The command makes sure the VPS folders exist:** the remote dir, its parent, the `releases/` folder.
  
55. **If** `repo-cache/.git` **doesn't exist yet, it runs** `git clone --no-checkout <repo> repo-cache`**.** First-deploy on a fresh VPS is slow here.
  
56. **It points** `origin` **at the configured repo URL.**
  
57. **It fetches and resolves the SHA** (same logic as the status command).
  
58. **It checks if** `releases/<sha>/` **already exists.** If yes, skip the next step.
  
59. **Otherwise it** `git archive` **+** `tar -x` the SHA into `releases/<sha>.tmp.<pid>`, then atomically renames to `releases/<sha>/`.
  
60. **It updates the** `current` **symlink** to point at `releases/<sha>/`.
  
61. **It prints** `AUTORESEARCH_RESOLVED_SHA <sha>` so your laptop knows the exact commit deployed.
  

* * *
## Part 10 — Uploading runtime env and Codex auth
62. `materialize_remote_runtime_env(...)` **runs** (`vps_runner.py:659`).
  
63. **It scans your local env vars for ones starting with** `AUTORESEARCH_`, `ANTHROPIC_`, `CLAUDE_CODE_`, `OPENAI_`, `OPENINFERENCE_`, `OPENTELEMETRY_`, `OTEL_`, `TRACELOOP_`.
  
64. **It keeps only the ones in a fixed whitelist** (`REMOTE_RUNTIME_ENV_PERSISTED_KEYS`) — so accidental local vars don't leak.
  
65. **It fills in defaults** (e.g. `AUTORESEARCH_IMPROVEMENT_REFLEXION=1`).
  
66. **It builds a** `.env.autoresearch` **file** with `export KEY=VALUE` lines.
  
67. **Over SFTP, it makes the remote dir if missing, uploads the file, chmods to 600.**
  
68. `materialize_remote_codex_auth(...)` **runs next** (`vps_runner.py:694`).
  
69. **It reads** `~/.codex/auth.json` **from your laptop.** Missing or invalid → skipped (fail-open).
  
70. **It uploads to** `~<vps_user>/.codex/auth.json` **over SFTP** so the agents on the VPS can call Codex.
  

* * *
## Part 11 — The remote "prepare" command
71. `build_remote_prepare_command(...)` **builds a long shell command** (`vps_runner.py:586`).
  
72. **The command does these things in order on the VPS:** a. `set -e` — fail on any error. b. If `.env.autoresearch` exists, `source` it so all those env vars are loaded. c. Export `AUTORESEARCH_RESOLVED_SHA`, `AUTORESEARCH_RUNTIME_ROOT`, `CODEX_HOME`. d. Run an embedded Python snippet that creates symlinks inside `releases/<sha>/` pointing back to shared folders `.venv/`, `runtime/`, `logs/` in the remote root. This way every release shares one Python venv and one runtime state folder. e. `cd releases/<sha>/`. f. Export `AUTORESEARCH_DATA_ROOT` if you set it. g. Compute a SHA-256 fingerprint of `pyproject.toml`. h. If `.venv/.autoresearch-deps.sha256` exists and doesn't match → `rm -rf .venv`. i. If `.venv/bin/python` doesn't exist → `python3 -m venv .venv`. j. If the fingerprint file is missing or stale → `pip install -e .` and write the new fingerprint. k. Export `AUTORESEARCH_TRACE_MODE=transaction` (so prep steps are traced). l. Run `python autoresearch_controller.py --family ema --fresh-job --prepare-launch-state-only`.
  
73. **Your laptop sends the whole thing via SSH and streams the output.**
  

* * *
## Part 12 — The controller starts (prepare phase)
The Python process on the VPS, started by step 72l, now runs.

74. **Python imports the same auto-discovery code from Part 1** — same `STRATEGIES` dict built.
  
75. `main()` **in** `autoresearch_controller.py` **runs** (`autoresearch_controller.py:1121`).
  
76. **It parses its CLI args** — picks up `--family ema`, `--fresh-job`, `--prepare-launch-state-only`.
  
77. `load_family("ema")` **returns the same metadata object** (same code as Part 4).
  
78. `_resolve_runtime_root(ROOT)` **figures out the runtime folder.** When `AUTORESEARCH_RUNTIME_ROOT` is set (it is, by step 72c), that wins over the repo folder.
  
79. `default_controller_paths(...)` **computes the file paths:**
  

- state file: `<runtime_root>/ema_autoresearch.next.json`
  
- human-readable status: `<runtime_root>/ema_autoresearch.current.md`
  
- jobs folder: `<runtime_root>/runtime/jobs`
  

80. **An** `AutoresearchController` **object is created** (`autoresearch_controller.py:592`).
  

- Resolves and stores all paths.
  
- Validates that a family was passed.
  
- Calls `_clear_runtime_paths` to point per-job paths at the bare jobs folder (no job picked yet).
  
- Opens `ema_backtest_runs.db` (SQLite) — creates the file and tables if missing.
  
- Opens `ema_baseline_checkpoints.json` (creates if missing).
  
- Creates an empty `RunContext` for per-iteration scratch state.
  

81. `controller.read_state()` **opens the state file.** Brand-new install → file doesn't exist → returns `{}`.
  
82. **Because** `--fresh-job` **was passed, the "fresh job" branch runs:**
  

- `_next_fresh_job_for_launch` picks the next job id (`autoresearch_controller.py:359`).
  
- It takes the max of: previous job in state file, max job id in the SQLite db, max `job-N` folder in `runtime/jobs/`.
  
- Adds 1 (or 1 if everything is empty). This becomes job number 1 on a brand-new install.
  

83. `normalize_controller_launch_state(prior_state, fresh_job=1)` **runs** (`autoresearch_controller.py:371`).
  
84. **It builds the fresh starting state:**
  

```json
{"state": "running", "job": 1, "research_round": 0, "job_usage": null, "heartbeat": {}}
```

85. `controller.write_state(state)` **writes that JSON to disk.** A new file appears at `ema_autoresearch.next.json`.
  
86. **Writing the state also calls** `_set_runtime_paths_for_job(1)` which creates the path objects (folders not made yet):
  

- `<runtime>/runtime/jobs/job-1/`
  
- `<runtime>/runtime/jobs/job-1/research/`
  
- `<runtime>/runtime/jobs/job-1/builder-requests/`
  

87. `--prepare-launch-state-only` **was set, so:** `_emit_prepare_result(state, 1)` prints a marker line: `AUTORESEARCH_PREPARE_RESULT {"ok": true, "job": 1, "state": "running", ...}` and the process exits with code 0.
  

* * *
## Part 13 — Laptop confirms prepare succeeded
88. `_stream_remote_prepare_command` **watches for the success marker** (`vps_runner.py:933`).
  
89. **As soon as it sees** `AUTORESEARCH_PREPARE_RESULT`, it starts a 5-second grace timer.
  
90. **If the process doesn't exit within the grace window, the laptop force-closes the SSH channel.** This is on purpose — sometimes the prepare process lingers; the marker is the real signal.
  
91. `parse_prepare_result` **reads the JSON** and confirms `ok == true`. Otherwise → error and exit.
  

* * *
## Part 14 — Launching the real run
92. `build_remote_run_command(...)` **builds the run command** (`vps_runner.py:610`).
  
93. **It re-uses the same bootstrap (source env, cd to release dir, set up venv) but skips the release symlink setup** (already done in prepare).
  
94. **It unsets** `AUTORESEARCH_TRACE_MODE` so the real run uses its normal trace mode.
  
95. **It appends** `python autoresearch_controller.py --family ema --run-current-state`**.**
  
96. **Your laptop sends this over SSH with no timeout** (controller runs can be long).
  
97. `_stream_remote_command` **reads stdout and stderr in 4 KB chunks** and prints them to your terminal as they arrive.
  

* * *
## Part 15 — The controller loop starts
Back on the VPS, a new Python process starts.

98. **Imports +** `main()` **+** `load_family()` **run again** (same as Part 12, fresh process).
  
99. **A new** `AutoresearchController` **object is built.** It re-opens the state file written in step 85 — sees `{"state": "running", "job": 1, ...}`.
  
100. **Because** `--run-current-state` **was passed,** `_validate_current_executable_state` **runs** (`autoresearch_controller.py:1135`). It confirms the state file has a valid `job >= 1` and a runnable state.
  
101. `_run_controller_loop(controller, family_name="ema", job=1)` **starts** (`autoresearch_controller.py:485`).
  
102. **It calls** `set_family("ema", job=1)` **on the trace SDK** so every trace event is tagged.
  
103. **It enters** `while True:` **— the main loop.**
  

* * *
## Part 16 — One iteration: `execute_once`
Each tick of the loop runs `controller.execute_once()` (`autoresearch_controller.py:1065`).

104. **Trace event "LOOP === execute_once START ===" is written.**
  
105. `_ensure_job_metadata()` **runs:** - reads the state file again - confirms `job >= 1` - sets `research_round = 0` and `job_usage = None` if missing - re-binds per-job paths to `runtime/jobs/job-1/...`
  
106. `_resolve_next_action()` **runs.** This forwards to `autoresearch_orchestration.resolve_next_action` (`autoresearch_orchestration.py:749`).
  
107. `_check_baseline_rerun()` **runs.** On a fresh job it returns `None`.
  
108. **The orchestrator sees** `state == "running"` **and** `results` **is empty** (the SQLite db has no rows yet for job 1).
  
109. **It calls** `controller.reconcile_state()` (`autoresearch_controller.py:880`).
  
110. `reconcile_state` **does:** - read all SQLite rows (empty) - dedupe them (no-op) - read results scoped to job 1 (empty) - find best result (none) - reset `heartbeat` fields - call `plan_next_action(state, results, ...)` to decide what to do
  
111. `plan_next_action` **(in** `autoresearch_planning.py:289`**):** - sees `results` is empty - calls `_baseline_branch(...)` which checks if `configs/base.yaml` exists in the repo - if yes, it builds a state saying "run the baseline backtest" and returns it
  
112. **The new state looks like:**`json { "state": "running", "job": 1, "research_round": 0, "selected_config_path": "configs/base.yaml", "selected_thesis_id": "baseline", "backtest_target_path": "runtime/jobs/job-1/research/round-0-baseline/backtest", "next_action": {"type": "run_experiment", "selected_thesis_id": "baseline", ...} }`
  
113. `write_state` **saves it.** `write_current_md` writes a human-readable summary.
  
114. **Back in** `execute_once`**,** `state["state"]` **is** `"running"`**, so it skips the research branch and calls** `_run_experiment(state)` (`autoresearch_controller.py:1118`).
  

* * *
## Part 17 — The first backtest (baseline)
### 17a — The controller hands the work to the experiment file
115. **The controller hands off to** `autoresearch_experiment.py` (`autoresearch_controller.py:1062` → `autoresearch_experiment.py:1327`).
  
116. **The experiment file needs three things from the state file:** - which config file to run (`configs/base.yaml` for the baseline) - which folder to write results into (`runtime/jobs/job-1/research/round-0-baseline/backtest`) - which strategy ("ema")
  
### 17b — Building the shell command
117. **`strategy_family.py` builds a single shell command string** (`strategy_family.py:47`):

    ```
    .venv/bin/python -m backtest.runner --strategy ema --config configs/base.yaml --output-dir runtime/jobs/job-1/research/round-0-baseline/backtest
    ```

118. **The** `python` **path comes from the** `AUTORESEARCH_PYTHON_BIN` **env var** (set by the VPS bootstrap in Part 11).
  
### 17c — Launching the backtest as a separate program
119. `autoresearch_experiment.py` **launches that command as a child Python process** (`autoresearch_experiment.py:155`).
  
120. **The controller waits.** It captures everything the child prints.
  
121. **A timeout is enforced.** If the backtest hangs, the child is killed.
  
### 17d — What the child program does
The child program starts at `backtest/runner.py`. It does these things in order:

122. **Reads its own CLI args** — strategy, config path, output dir.
  
123. **Loads the config YAML file** (`backtest/runtime_config.py`). The YAML is validated and the data universe is resolved.
  
124. **Looks up the strategy** in the in-memory registry (same `STRATEGIES` dict from Part 1). For "ema" this gives the EMA strategy object.
  
125. **Asks the strategy to run** — control jumps into `strategies/ema/strategy.py`.
  
### 17e — Inside the EMA backtest (the actual simulation)
This all happens in `strategies/ema/strategy.py`.

126. **A** `strategy_event_logger.py` **event recorder is created.** It will log every signal seen, every rejection, every trade.
  
127. **Historical market data is loaded** via `backtest/data_universe.py`: - reads parquet files from `<AUTORESEARCH_DATA_ROOT>/universes/<name>/` - the files are `open.parquet`, `high.parquet`, `low.parquet`, `close.parquet`, `volume.parquet` - one column per stock symbol, one row per timestamp - filtered to the `validation_start` → `validation_end` date range from the config
  
128. **If the data frame is empty, return zeroed metrics and stop.**
  
129. {==**The strategy reads all its knobs from the config:** EMA length, long/short timeframes, direction bias, gap filters, stop-distance limits, max trades per day, entry cutoff time.==}{>>where does it get this information from?<<}{id="c1" by="user" at="2026-05-26T16:30:24.423Z"} {>>From the YAML file passed as `--config` on the command line. For the baseline that's `configs/base.yaml` in the repo. Each variant later uses a YAML at `configs/variants/<prefix><slug>.yaml`, or a generated one at `runtime/jobs/job-N/research/round-M/selected_config.json`. The YAML is loaded by `backtest/runtime_config.py` (step 123 above) and arrives at the strategy as a plain Python dict, then `strategies/ema/strategy.py` pulls out each named knob by key.<<}{id="c3" by="AI" at="2026-05-26T16:42:00.000Z" re="c1"}
  
130. **It loops over every stock symbol.** For each symbol: - **resample raw bars** to the long timeframe and the short timeframe - **compute the EMA line** and **detect signals** (`strategies/ema/signals.py`) - **apply each filter in order**; every rejection is logged with a reason:
  

- entry-cutoff filter (no entries after a fixed time of day)
  
- gap filter (skip days with overnight gaps in either direction)
  
- gap-exclude filter (skip days with small gaps)
  
- min/max stop-distance filters (skip if stop is too tight or too wide) - **simulate trades** via `strategies/ema/exits.py`
  

131. **The trade simulator walks the data bar by bar.** For each signal it: - applies slippage to the entry price (`slippage_pct` from the config) - rejects if the risk is zero, inverted (stop on wrong side), or has bad prices - tracks the stop price and the target price (target = entry + `rr_ratio` × risk) - scans forward bars looking for whichever comes first: stop hit, target hit, or `max_hold_bars` reached - if `trail_after_r` is set, switches to a trailing stop once the trade is in profit by that many R multiples - records the trade's `pnl_pct` (percent gain or loss) and `exit_reason` (stop / target / trailing / time)
  
132. **Trades from all symbols are collected into one list.**
  
133. **The list is turned into a pandas DataFrame.**
  
134. **If** `max_trades_per_day` **is set, trades beyond the daily cap are dropped** (earliest trades win; rejected ones are logged).
  
### 17f — Computing the metrics
This happens in `metrics.py`.

135. **The metrics function works on the** `pnl_pct` **column** — one number per trade.
  
136. **It computes these standard metrics:** -
  
  1. `trade_count` — how many trades happened.
    
  2. `median_expectancy` — the median pnl per trade (typical trade outcome).
    
  3. - `profit_factor` — total winning pnl ÷ total losing pnl (absolute value). Above 1.0 means the strategy made money. -
    
  4. `max_drawdown` — biggest peak-to-trough drop in the running equity curve. Computed by adding up trade pnls in order, then finding the largest gap between any prior peak and a later low. -
    
  5. `pct_profitable_windows` — fraction of trades with positive pnl. This is the "win rate". -
    
  6. `avg_sharpe_across_windows` — the Sharpe ratio. Formula: `mean(pnl) / std(pnl) × sqrt(trade_count)`. It rewards consistent gains and penalizes wild swings. Higher is better. This is the main metric the controller uses to decide if a new strategy is better than the baseline.
    
137. **It also bundles diagnostics** (per-symbol and per-direction breakdowns) and **exit-reason counts** (how many trades exited via stop vs target vs time).
  
### 17g — Writing files to disk
This happens in `backtest/output.py`.

138. **Make the output folder if it doesn't exist.**
  
139. **Write** `trades.csv` — one row per trade with entry/exit timestamps, prices, pnl, exit reason, symbol.
  
140. **Write** `metrics.json` — the dictionary of metrics from step 136.
  
141. **Write** `strategy_events.parquet` — every event the logger captured (signals, rejections, fills). Used later for debugging "why did this trade not happen?"
  
142. **Write** `diagnostics.json` — counts of accepted/rejected signals broken down by reason.
  
143. **Build the** `result.json` **payload** (`backtest/result_schema.py`). It contains: - `family` — "ema" - `config` — path to the config file used - `config_hash` — a fingerprint of the resolved config (so two runs of the same config can be matched) - `git_sha` — the current commit SHA of the code - `timestamp` — UTC time the run finished - `metrics_file`, `trades_file`, `strategy_events_file`, `diagnostics_file` — paths to the four files above - `data_provenance` — fingerprint of the historical data used
  
144. **Write** `result.json` **atomically** to the output folder.
  

145. **Print these marker lines to stdout** so the parent process knows where the files are:

    ```
    RESULT_JSON <path>/result.json
    METRICS_FILE <path>/metrics.json
    STRATEGY_EVENTS_FILE <path>/strategy_events.parquet
    DIAGNOSTICS_FILE <path>/diagnostics.json
    ```

146. **The child program exits with code 0.**
  
### 17h — The controller reads the result back
147. **The controller now has the captured stdout from the child.**
  
148. `autoresearch_experiment.py` **searches the stdout for the** `RESULT_JSON` **line.**
  
149. **It opens that file and loads the JSON.**
  
150. **If the path is missing, the JSON is malformed, or the metrics-file pointer is bad → it raises an error.** The run fails loudly. No silent passes.
  
151. **It also opens** `metrics.json` to read the actual metric values (Sharpe, win rate, etc.).
  
### 17i — Recording the run
152. **The controller assembles a database row containing:** - an auto-incremented run id, `job=1`, `research_round=0` - the config path and config hash - the primary metric value (Sharpe by default) - file paths to all the artifacts - the git SHA and UTC timestamp
  
153. `backtest_run_db.py` **writes the row to** `ema_backtest_runs.db` **(SQLite).**
  
154. `ema_baseline_checkpoints.json` **is updated** so future runs know this commit's baseline metric and can detect drift.
  
### 17j — Deciding what comes next
155. **The controller compares the new metric to the current best:** - on the very first run, the baseline IS the best - on later runs, "better" means higher Sharpe (or whichever direction the strategy declares)
  
156. **It updates** `current_best` **in the state file** and saves the state.
  
157. **Control returns to the main loop**, which decides what to do next tick.
  

* * *
## Part 18 — The loop turns again
123. **The** `while True:` **loop in** `_run_controller_loop` **continues.**
  
124. **It reads the state file.** State is still `"running"` (or has been updated by the experiment).
  
125. **Not in** `{finished, interrupted, halted}` **→ keep looping.** Not `"blocked"` → don't trigger the research-required watchdog.
  
126. **Goes back to step 104 —** `execute_once` **runs again.**
  

This time `results` has one row (the baseline). So:

127. `reconcile_state` **→** `plan_next_action` doesn't take the baseline branch anymore.
  
128. **It calls** `select_research_next_action` (`autoresearch_planning.py:220`).
  
129. **No thesis queue, no combinations yet → returns** `_blocked_for_research_state` with a `research_required` blocker and `next_action = {"type": "research"}`.
  
130. **State is now** `"blocked"`**.** `execute_once` sees the research blocker and calls `_run_research(state)` (`autoresearch_controller.py:1083`).
  
131. `autoresearch_research.run_research` **runs:** - creates the research round folder `runtime/jobs/job-1/research/round-1/` - starts the research conductor, which dispatches sub-agents (web search, code reader, data agent) - the agents generate a thesis (a written rationale for a new strategy variant) - the thesis is passed to `compiler_pipeline` which either reuses existing primitives or invokes `compiler_builder` to write new strategy code - the result is operationalized into a runnable `selected_config.json` file - the state is updated to `"running"` with `next_action = {"type": "run_experiment", ...}` pointing at the new config
  
132. `execute_once` **returns; the loop runs again; the next experiment runs against the generated config.**
  

The loop alternates: **research → experiment → research → experiment**.

* * *
## Part 19 — How the loop ends
133. **After each iteration,** `_run_controller_loop` **re-reads the state.**
  
134. **If** `state["state"]` **is in** `{finished, interrupted, halted}`**, it returns 0** (clean exit) (`autoresearch_controller.py:502`).
  
135. **If** `state["state"]` **is** `"blocked"` **with a** `research_required` **blocker but research keeps re-blocking, a counter (**`consecutive_research_required`**) grows.** When it hits `AUTORESEARCH_MAX_CONSECUTIVE_RESEARCH_REQUIRED` (default 10), the loop returns 1 (gave up).
  
136. **If** `state["state"]` **is** `"blocked"` **for any other reason → return 1 (terminal).**
  
137. `main()` **returns the exit code; the Python process exits.**
  

* * *
## Part 20 — Laptop wraps up
138. `_stream_remote_command` **on your laptop sees the SSH channel close.**
  
139. **It reads the final exit status, flushes any remaining output.**
  
140. `client.close()` **closes the SSH connection** (`vps_runner.py:1259`).
  
141. **The laptop emits one last trace event with elapsed time and stdout/stderr sizes.**
  
142. `sys.exit(exit_code)` — your laptop process exits with the same code the VPS controller returned.
  

The job's results stay on the VPS in `ema_backtest_runs.db` and in the per-round folders under `runtime/jobs/job-1/`. You pull them down separately when you want to look at them.

* * *
## The one-tick summary
Each tick of the controller's main loop:

```
read state.json
      │
      ▼
 resolve next action
      │
      ├── no results yet?  ──► run baseline backtest ──► back to top
      │
      ├── need a new idea? ──► research agents ──► compile config ──► back to top
      │
      ├── have a config?   ──► run backtest ──► save to db ──► back to top
      │
      └── state == finished/halted/interrupted ──► exit
```

That's it. One state file. One loop. Many ticks. Until done.
