# Autoresearch Controller — Capability Scenario Matrix

Scope: the 5 major capabilities of the controller entry point (`autoresearch_controller.py`),
each exercised end-to-end through the real `execute_once()` / `_run_controller_loop()`.

- **Evaluation method:** deterministic pass/fail (pytest assertions). No scoring rubric —
  these are state-machine behaviors with exact expected outputs, so `==` assertions are the
  honest bar (CLAUDE.md rule G: assertions exercise behavior, not structure).
- **Conditions held constant:** real `ema` family (`load_family("ema")`), the shared
  `controller(tmp_path)` fixture, `AUTORESEARCH_DATA_ROOT` from `conftest.pytest_configure`,
  `AUTORESEARCH_TRACING_DISABLED=1`. Same command every run.
- **Quality bar:** every scenario passes; each assertion would fail if the capability's logic
  broke (no constant-passing tests).

| # | Capability | Scenario | Success criterion | Test |
|---|---|---|---|---|
| 1 | Fresh-job baseline backtest (iter 0) | Running state, round 0 → real subprocess backtest → result logged | exactly 1 record, `thesis_id == "ema_base"`, `accepted is True`, benchmark output file exists | `test_execute_once_runs_baseline_experiment_through_real_handlers` |
| 2 | Terminal / manual-review halt | Blocked state with `manual_review` blocker | `execute_once() == 0` and `_run_round` is never called (raises if invoked) | `test_execute_once_stops_without_experiment_for_terminal_blocked_state` |
| 3 | Walkforward dispatch | `next_action.type == "walkforward"` | walkforward handler receives the state; normal round handler never called | `test_execute_once_dispatches_walkforward_action` |
| 4 | Research-transition autonomy ledger (gap closed) | Research-blocked → `_run_research` clears to running → round dispatched | ledger records `research_transition/approved` decision then linked `transition_to_running` audit; post-research state reaches `_run_round` | `test_execute_once_records_autonomy_ledger_on_research_transition` **(new)** |
| 5 | Stuck-research safeguard | Loop sees unchanged `research_required` N times | `_run_controller_loop` returns `1` after `max+1` iterations | `test_run_controller_loop_stops_after_repeated_research_required` |

## Why only one new test

Capabilities 1–3 and 5 already have end-to-end `execute_once`/loop coverage. Capability 4 (the
ledger-recording glue at `autoresearch_controller.py:846-873`) was the sole genuine gap — the
baseline path asserts `calls == []` and never enters that branch, and `run_research` itself is
covered separately in `test_autoresearch_research.py`. Adding only the missing scenario avoids
duplicating existing coverage (CLAUDE.md rule 3 / code-reuse).

## Evidence (recorded run)

```
$ python -m pytest tests/test_autoresearch_controller_characterization.py \
    -k "execute_once_runs_baseline_experiment_through_real_handlers or \
        stops_without_experiment_for_terminal_blocked_state or \
        dispatches_walkforward_action or loop_exits_on_terminal_state or \
        stops_after_repeated_research_required or \
        records_autonomy_ledger_on_research_transition" -v
6 passed
```

Authoritative verification is the `CI` workflow on push (CLAUDE.md rule 6); the above is the
local targeted confirmation.
