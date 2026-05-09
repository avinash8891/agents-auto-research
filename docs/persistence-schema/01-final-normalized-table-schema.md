# Final Normalized Table Schema

This document defines the required canonical SQLite schema for autoresearch persistence.
It excludes optional tables and optional columns.

## Design rules

1. SQLite is the only canonical durable store.
2. SQLite-backed exports may exist for reporting, but never as source of truth.
3. Runtime controller state JSON remains operational state, not durable experiment history.
4. Artifact files remain on disk; SQLite stores references only.
5. Timestamps are ISO-8601 UTC strings.
6. Model `research_round` and `research_thesis_attempt` separately.
7. Validator rejection history belongs in `research_thesis_attempts`, not `experiments`.

---

## Required table: `experiments`

One row per executed experiment/backtest only.

| Column | Type | Source today | Notes |
|---|---|---|---|
| `experiment_id` | TEXT PRIMARY KEY | existing | canonical experiment id |
| `thesis_id` | TEXT | existing | selected thesis that reached execution |
| `strategy_family` | TEXT | existing | current `family` field |
| `job_id` | INTEGER | existing | controller job number |
| `run_id` | TEXT | existing | trace/session run id |
| `created_at_utc` | TEXT | existing | current `timestamp` |
| `decision_status` | TEXT | existing | currently `keep` / `discard` only |
| `verdict_status` | TEXT | existing | accepted/rejected/inconclusive/none |
| `verdict_summary` | TEXT | existing | evaluator summary |
| `code_commit` | TEXT | existing | current commit |
| `data_hash` | TEXT | existing | persisted today |
| `config_path` | TEXT | existing | config path |
| `runtime_config_json` | TEXT | existing | serialized runtime config |
| `primary_metric_name` | TEXT | existing | from config header/family metric definition |
| `primary_metric_value` | REAL | existing | current `metric` |
| `metrics_json` | TEXT | existing | full metrics payload |
| `trade_analysis_json` | TEXT | existing | full trade analysis payload |
| `strategy_diagnostics_json` | TEXT | existing | full diagnostics payload |

### Required indexes
- `idx_experiments_thesis_id`
- `idx_experiments_strategy_family_created_at`
- `idx_experiments_job_id`
- `idx_experiments_code_commit`
- `idx_experiments_decision_status`
- `idx_experiments_primary_metric_value`

---

## Required table: `research_rounds`

One row per conductor cycle.
A round is the container for one or more thesis attempts.

| Column | Type | Source today | Notes |
|---|---|---|---|
| `research_round_id` | TEXT PRIMARY KEY | NEW_WRITE_PATH_REQUIRED | no synthetic id exists today |
| `job_id` | INTEGER | existing | current `job` |
| `round_number` | INTEGER | existing | current `round` |
| `run_id` | TEXT | existing | trace/session run id |
| `outcome` | TEXT | existing | compiled/needs_code/rejected/stopped/etc. |
| `created_at_utc` | TEXT | existing | timestamp |

### Required indexes
- `idx_research_rounds_job_round`
- `idx_research_rounds_outcome`

---

## Required table: `research_thesis_attempts`

One row per thesis proposal/attempt inside a research round.
This is required because validator retries can produce multiple theses in the same round.

| Column | Type | Source today | Notes |
|---|---|---|---|
| `thesis_attempt_id` | TEXT PRIMARY KEY | NEW_WRITE_PATH_REQUIRED | synthetic id required |
| `research_round_id` | TEXT | NEW_WRITE_PATH_REQUIRED | FK to research_rounds |
| `attempt_number` | INTEGER | NEW_WRITE_PATH_REQUIRED | 1, 2, 3... within a round |
| `strategy_family` | TEXT | existing | from controller family / thesis metadata |
| `validator_status` | TEXT | NEW_WRITE_PATH_REQUIRED | rejected / accepted / needs_code / compiled |
| `selected_for_execution` | INTEGER | NEW_WRITE_PATH_REQUIRED | 0/1 flag |
| `created_at_utc` | TEXT | NEW_WRITE_PATH_REQUIRED | attempt timestamp |

### Required indexes
- `idx_research_thesis_attempts_round_attempt`
- `idx_research_thesis_attempts_validator_status`

---

## Required table: `baseline_checkpoints`

One row per baseline checkpoint.

| Column | Type | Source today | Notes |
|---|---|---|---|
| `checkpoint_id` | TEXT PRIMARY KEY | NEW_WRITE_PATH_REQUIRED | synthetic id required |
| `strategy_family` | TEXT | DERIVED_OR_NEW_FIELD | not in dataclass today |
| `code_commit` | TEXT | existing | persisted today |
| `data_hash` | TEXT | existing | persisted today |
| `config_hash` | TEXT | existing | persisted today |
| `metrics_json` | TEXT | existing | persisted today |
| `created_at_utc` | TEXT | existing | timestamp |

### Required indexes
- `idx_baseline_checkpoints_strategy_family_created_at`
- `idx_baseline_checkpoints_code_commit`

---

## Explicit non-goals

Do not model the following as already-existing persisted data:
- experiment statuses `blocked` / `failed`
- `asi.hypothesis` as true hypothesis text
- checkpoint drift as already persisted checkpoint data
- conductor reasoning as already persisted research-round data
- one round equaling one thesis
