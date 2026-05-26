# How This System Runs — In Plain English

This project is an **automated trading-strategy research assistant**. You start it once with one command, and it keeps cycling: think → research → write code → test → learn → repeat.

Below is the full path it takes from the moment you press Enter, broken into blocks of 5 steps. Ask anything inline.

---

## Block 1 — Starting Up (Steps 1–5)

1. **You run a command.** Something like `python autoresearch_controller.py --family ema`. The word "family" just means *which kind of strategy* (e.g. "ema" = a moving-average style, "orb" = an opening-range-breakout style).

2. **The program wakes up and reads its instructions.** It opens its own config files to learn things like "where is the market data stored?" and "where do I save my notes?"

3. **It loads the strategy family.** Think of this like picking the right recipe book off the shelf — EMA rules vs. ORB rules.

4. **It checks its memory.** There is a small file (the "state file") that remembers what it was doing last time — like a bookmark. Did it finish? Crash? Halt halfway?

5. **It decides: fresh start or resume?** If you told it `--fresh-job`, it starts a brand new investigation. Otherwise it picks up where it left off.

---

## Block 2 — The Main Loop Begins (Steps 6–10)

6. **It enters a `while True` loop.** This is the heartbeat. It will keep doing one "iteration" after another until it either finishes successfully, gets stuck, or hits a terminal error.

7. **Each iteration is called `execute_once`.** One iteration = one small unit of progress (e.g. "run one research round" OR "run one backtest").

8. **First sub-step: figure out the next action.** It asks itself: "Given my notes, what should I do next?" Possible answers: *do more research*, *try a new strategy variant*, *re-run the baseline*, *stop because we're done*.

9. **The answer comes back as a "state".** Common states: `running` (I have something to test), `blocked` (I need more info before I can proceed), `finished` (done), `halted` (something went wrong, human needed).

10. **Based on the state, it branches.** If `blocked` → go research. If `running` → go test. If anything else → stop the loop cleanly.

---

## Block 3 — The "Research" Branch (Steps 11–15)

11. **A blocker is like a missing puzzle piece.** Example: "I want to test a new EMA crossover rule but I don't know what parameters to try yet." That's a research blocker.

12. **It calls the "research conductor."** This is the part that talks to AI sub-agents (like little specialists: a web-searcher, a code-reader, a data-analyst).

13. **Each sub-agent does one job.** One might Google market behavior. Another might read past experiment notes. Another might look at price data. They each return a short written finding.

14. **The conductor stitches their findings into a "thesis."** A thesis is basically: *"I believe X will improve performance, and here's why."*

15. **The thesis gets compiled into runnable code.** The "compiler" turns the english-language idea into actual Python strategy code that can be backtested. Once that's done, the blocker is cleared and the state flips to `running`.

---

## Block 4 — The "Experiment" Branch (Steps 16–20)

16. **It picks the config to test.** A "config" is just a settings file: which EMA periods, which stocks, which dates, etc.

17. **It runs the backtest.** This simulates the strategy day-by-day over historical market data and records every fake trade.

18. **It calculates metrics.** Sharpe ratio, return, drawdown, win rate, etc. — the report card.

19. **It logs the result.** Everything is saved into a SQLite database (`ema_backtest_runs.db` or similar) plus a markdown summary you can read.

20. **It evaluates: is this better than the baseline?** "Baseline" = the best result it has seen so far. If the new one is better, it becomes the new baseline. If not, it gets filed away as a failed attempt — still useful for learning.

---

## Block 5 — Deciding What's Next (Steps 21–25)

21. **Back to the top of the loop.** `execute_once` finishes, control returns to the `while True` loop.

22. **It re-reads the state file.** The previous step may have written new info (e.g. "baseline updated", "queue now has 3 untested variants").

23. **It checks for terminal conditions.** Has it hit the max number of rounds? Has it stopped improving for too long? Has a human pressed stop? If yes → exit.

24. **It checks for "stuck" conditions.** If it keeps asking for the same research over and over without progress, it gives up rather than spinning forever.

25. **Otherwise: loop again.** Back to step 7. This continues for minutes to hours, depending on the strategy family and how much budget (API calls, time) is allowed.

---

## Block 6 — Side Channels Running in Parallel (Steps 26–30)

26. **Logging.** Every step writes a structured log line with a UTC timestamp. You can tail these in real time to watch progress.

27. **Tracing.** A more detailed event stream is written to `trace_exports/` — useful for debugging exactly which sub-agent said what.

28. **Token accounting.** Every AI call records how many tokens it used and what it cost. You can run `python scripts/token_audit.py` afterwards to see spending.

29. **Discord notifications (optional).** If a webhook is configured, it pings you when big things happen — new baseline, halt, finish.

30. **Crash safety.** Because the state file is the single source of truth, if the process dies the next run can resume cleanly from the last saved state.

---

## TL;DR Picture

```
   YOU
    │ "go research EMA"
    ▼
┌───────────────────────┐
│  Controller (boss)    │ ◄────── reads state file (memory)
└──────────┬────────────┘
           │ what next?
   ┌───────┴────────┐
   │                │
   ▼                ▼
RESEARCH        EXPERIMENT
(AI agents      (backtest +
 + compiler)     score)
   │                │
   └───────┬────────┘
           ▼
     writes state file
           │
           └──► loops back to boss
```

Ask any question inline by writing it directly under whichever step you want clarified.
