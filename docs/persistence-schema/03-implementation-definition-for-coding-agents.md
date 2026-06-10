# Implementation Definition for Coding Agents

This document defines how a coding agent should implement the required canonical SQLite persistence from the current codebase.

## Goal

Replace dual or partial durable persistence with a single canonical SQLite store that can answer:
- what experiment ran,
- what thesis reached execution,
- what job and round it belonged to,
- what happened,
- and what thesis attempts were rejected before execution.

---

## Canonical persistence contract

### Canonical
- SQLite database

### Derived / export
- `current.md`
- status summaries

### Operational but non-canonical
- controller `state.json`
- artifact files on disk

---

## Core entity model

Use this mental model:

- `strategy_family` = broad strategy type (`ema`, `orb`)
- `job` = one controller research session
- `research_round` = one conductor cycle inside a job
- `research_thesis_attempt` = one proposed thesis inside a round
- `experiment` = one executed backtest for a selected thesis

Important rule:
- a round is not a thesis
- a round can contain multiple thesis attempts when validator rejection retries happen

---

## Required implementation constraints

1. SQLite must become the only durable authority.
2. Do not invent data during migration.
3. Do not map `asi.hypothesis` to canonical hypothesis text.
4. Do not persist experiment statuses that current code does not produce.
5. Persist validator rejection metadata as thesis-attempt history, not experiment history.
6. Artifact references must stay as paths, not blobs.
7. Timestamps must be normalized to ISO-8601 UTC.

---

## Required tables

Implement these tables only:
1. `backtest_runs`
2. `research_rounds`
3. `research_thesis_attempts`
4. `baseline_checkpoints`

---

## Required write behavior for research rounds

When a research round starts:
1. create a `research_rounds` row with `outcome = 'in_progress'`
2. if conductor proposes a thesis that is rejected, persist a
   `research_thesis_attempts` row for that retry with `validator_status`
   recording the rejected attempt outcome
3. if conductor retries with a new thesis in the same round, create a new
   attempt row with the next `attempt_number`
4. if one thesis is accepted for execution, persist that attempt row with
   `selected_for_execution = 1`
5. keep rejection JSON as an artifact; SQLite stores attempt-level status and
   failure summary only
6. only then create a `backtest_runs` row when a backtest actually runs

This preserves the real workflow:
- one round
- many thesis attempts
- maybe one executed experiment

---

## Recommended implementation order

### Phase 1 — Introduce SQLite schema
- add SQLite storage layer
- create the four required tables
- add deterministic migration-safe ids where needed

### Phase 2 — Canonical write path
- write backtest runs directly to SQLite
- write research rounds directly to SQLite
- write thesis attempts directly to SQLite
- write baseline checkpoints directly to SQLite

### Phase 3 — Read path cutover
- change conductor/controller reads to query SQLite
- stop relying on any legacy export format for authoritative history

### Phase 4 — Export compatibility
- if compatibility exports are ever reintroduced, they must be generated from SQLite only
- mark them as derived in code comments and interfaces

---

## Migration guidance

### Historical import priority
When multiple legacy sources exist, prefer:
1. canonical SQLite row
2. artifact file payloads only for recoverable references

### Historical limitation to document explicitly
Old data will not always let you reconstruct every rejected thesis attempt inside a round, because current code does not durably store each retry attempt separately.

### Never backfill from transient state when avoidable
Do not backfill canonical experiment history from:
- in-memory controller context
- current `next_action`
- temporary trace state

---

## Agent checklist before implementing

- [ ] Verify current code write path for every target column
- [ ] Mark every missing field as `NEW_WRITE_PATH_REQUIRED` or remove it
- [ ] Ensure `asi.hypothesis` is not misused
- [ ] Preserve `keep` / `discard` semantics unless expanding the write path deliberately
- [ ] Separate round from thesis-attempt in both schema and code
- [ ] Persist validator rejection reason at thesis-attempt level
- [ ] Define deterministic ID generation for new primary keys
- [ ] Add migration tests before changing writes

---

## Non-goals for first implementation

Do not try to solve all of these in the first migration:
- retroactive historical reasoning persistence for old research rounds
- retroactive drift payload persistence for old checkpoints
- perfect reconstruction of old rejected thesis retries that were never durably stored

Start with a correct canonical core.
