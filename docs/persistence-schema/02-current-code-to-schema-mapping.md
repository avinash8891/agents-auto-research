# Current Code to Canonical Schema Mapping

This document maps current code fields to the required canonical SQLite schema only.

## Mapping rules

1. If a source field maps incorrectly or ambiguously, this document says so explicitly.
2. If a target column has no current write path, it is labeled `NO_CURRENT_WRITE_PATH`.
3. If a target column requires derivation, it is labeled `DERIVED`.
4. If a field exists only in runtime state and not durable history, do not treat it as persisted data.
5. Distinguish `research_round` from `research_thesis_attempt`.

---

## Exported backtest-run entry -> `backtest_runs`

| Current field | Canonical target | Status | Notes |
|---|---|---|---|
| `job` | `backtest_runs.job_id` | direct | |
| `run_id` | `backtest_runs.trace_run_id` | direct | |
| `commit` | `backtest_runs.code_commit` | direct | |
| `metric` | `backtest_runs.primary_metric_value` | direct | |
| config header `metricName` | `backtest_runs.primary_metric_name` | direct | source is config header |
| `metrics` | `backtest_runs.metrics_json` | direct | |
| `status` | `backtest_runs.decision_status` | direct | currently only `keep` / `discard` |
| `timestamp` | `backtest_runs.created_at_utc` | direct | |
| `asi.config` | `backtest_runs.config_path` | direct | |
| `asi.trade_analysis` | `backtest_runs.trade_analysis_json` | direct | |
| `asi.thesis_id` | `backtest_runs.thesis_id` | direct | |

---

## Current `BacktestRunRecord` -> `backtest_runs`

| Current field | Canonical target | Status | Notes |
|---|---|---|---|
| `run_id` | `backtest_runs.run_id` | direct | |
| `thesis_id` | `backtest_runs.thesis_id` | direct | |
| `config_path` | `backtest_runs.config_path` | direct | |
| `runtime_config` | `backtest_runs.runtime_config_json` | direct | |
| `code_commit` | `backtest_runs.code_commit` | direct | |
| `data_hash` | `backtest_runs.data_hash` | direct | |
| `train_metrics` + `validation_metrics` | `backtest_runs.metrics_json` | direct_or_merge | current code splits them in JSON DB, canonical schema does not |
| `strategy_diagnostics` | `backtest_runs.strategy_diagnostics_json` | direct | |
| `accepted` | `backtest_runs.decision_status` | DERIVED | `True -> keep`, `False -> discard` |
| `verdict_status` | `backtest_runs.verdict_status` | direct | |
| `verdict_summary` | `backtest_runs.verdict_summary` | direct | |
| `timestamp` | `backtest_runs.created_at_utc` | direct | |
| `family` | `backtest_runs.strategy_family` | direct | |
| `job` | `backtest_runs.job_id` | direct | |

### Important mapping limits
- `backtest_runs.run_id` does not exist in exported entries and comes from `BacktestRunRecord` / DB path.
- `asi.hypothesis` must not be treated as canonical hypothesis text; current code sets it to `Path(config).stem`.

---

## Research round current data -> `research_rounds`

Current persisted fields from `log_research_round()`:

| Current field | Canonical target | Status | Notes |
|---|---|---|---|
| `job` | `research_rounds.job_id` | direct | |
| `round` | `research_rounds.round_number` | direct | |
| `run_id` | `research_rounds.run_id` | direct | |
| `outcome` | `research_rounds.outcome` | direct | |
| `timestamp` | `research_rounds.created_at_utc` | direct | |

### Missing from current persisted research-round rows
| Canonical target | Status | Why |
|---|---|---|
| `research_rounds.research_round_id` | NO_CURRENT_WRITE_PATH | no id generator exists |

---

## Validator retry flow -> `research_thesis_attempts`

Current behavior:
- one research round may retry multiple times
- each retry may propose a different thesis
- current durable persistence does not store each attempt as a separate record

Canonical consequence:
- `research_thesis_attempts` must be added with a new write path

| Canonical target | Status | Notes |
|---|---|---|
| `research_thesis_attempts.thesis_attempt_id` | NO_CURRENT_WRITE_PATH | synthetic id required |
| `research_thesis_attempts.research_round_id` | NO_CURRENT_WRITE_PATH | round FK required |
| `research_thesis_attempts.attempt_number` | NO_CURRENT_WRITE_PATH | retry ordinal required |
| `research_thesis_attempts.strategy_family` | available_per_attempt | thesis metadata/runtime has it |
| `research_thesis_attempts.validator_status` | NO_CURRENT_WRITE_PATH | not durably persisted today |
| `research_thesis_attempts.selected_for_execution` | NO_CURRENT_WRITE_PATH | not durably persisted today |
| `research_thesis_attempts.created_at_utc` | NO_CURRENT_WRITE_PATH | attempt timestamp not durably persisted today |

### Historical limitation
Current historical data cannot fully recover every rejected thesis attempt inside a round, because the code does not durably store each retry attempt separately.

---

## Baseline checkpoint JSON-file tracker -> `baseline_checkpoints`

| Current field | Canonical target | Status | Notes |
|---|---|---|---|
| `code_commit` | `baseline_checkpoints.code_commit` | direct | |
| `data_hash` | `baseline_checkpoints.data_hash` | direct | |
| `config_hash` | `baseline_checkpoints.config_hash` | direct | |
| `metrics` | `baseline_checkpoints.metrics_json` | direct | |
| `timestamp` | `baseline_checkpoints.created_at_utc` | direct | |

### Missing from current checkpoint rows
| Canonical target | Status | Why |
|---|---|---|
| `baseline_checkpoints.checkpoint_id` | NO_CURRENT_WRITE_PATH | no synthetic id exists |
| `baseline_checkpoints.strategy_family` | DERIVED_OR_NEW_FIELD | not present on dataclass |

---

## Migration notes for coding agents

1. Never populate canonical hypothesis text from `asi.hypothesis`.
2. Never assume backtest-run rows already support `blocked`/`failed` statuses.
3. If a column is marked `NO_CURRENT_WRITE_PATH`, migration can only backfill it if another durable source exists.
4. Historical `research_rounds` preserve round outcome, not full thesis-attempt history.
5. Future writes must persist each thesis attempt separately when validator retries happen.
