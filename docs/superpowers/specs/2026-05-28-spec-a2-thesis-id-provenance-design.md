# Spec A2 — Thesis-ID Provenance: System-Generated, Not LLM-Generated

**Date:** 2026-05-28
**Status:** Design — ready for writing-plans
**Reference:** Spec A (`2026-05-28-spec-a-context-snapshot-design.md`) §6.1; Spec A1 (`2026-05-28-spec-a1-experiment-id-cleanup-design.md`)
**Depends on:** Spec A1 (which fixes `thesis_id`-as-experiment-key misuse — that fix is a prerequisite; A2 builds on it)
**Blocks:** none
**Parallel with:** Spec B / C / D (does not touch their concerns)

---

## 1. Goal

**Stop the LLM from generating `thesis_id`. Have the system assign it deterministically at thesis-accept time.**

Today the conductor LLM emits `thesis_id` as part of its JSON output. The prompts (`agent_prompts.py:121`, `research_prompts.py:119`) ask for "short snake_case, unique, never reuse" — a contract that's enforced only by:

- prompt instruction (the LLM may ignore),
- a post-hoc validator rule `_check_thesis_id_not_repeated` (rejects after the fact),
- a `or "none"` fallback when the LLM emits nothing (which then collides with every other failed attempt — see §3 evidence).

The fix: identifier assignment moves to the system at the moment the validator accepts a thesis. The LLM keeps producing the *description* (hypothesis, mechanism, alternatives_considered, etc.) — that's the creative work. Identity is a registry concern, not a prompt-following concern.

## 2. Non-goals

- **Renaming, re-keying, or migrating the DB column `thesis_id`** — column stays. Its *values* change format starting from the cutover date. Pre-cutover rows keep their old LLM-generated values.
- **Removing the `thesis_id` field from any agent-facing prompt or block** — the conductor still sees `thesis_id` in `LAST RESEARCH ROUND — WHAT WAS TESTED` (§5.1 of Spec A) and in `prior_lever_outcomes`. It's just that, post-A2, those values are system-assigned strings the conductor *reads* but never *invents*.
- **Spec A1's renames** — independent; A1 ships first.
- **Changing the JSON envelope shape the conductor emits.** The conductor still emits `suggested_theses[0]`. We *delete* the `thesis_id` field from that envelope (the LLM no longer fills it) and *add* a `proposal_label` field if humans want a free-form handle in logs.

## 2.1 No backward compatibility — hard cutover

Mirroring Spec A1's §2.1: old code paths (LLM-emitted `thesis_id`, the `or "none"` fallback, the `_check_thesis_id_not_repeated` validator rule) are **removed in the same commit** that introduces system assignment. No deprecation aliases.

**Implication for production data:**

- Pre-cutover rows in `research_thesis_attempts` and `backtest_runs` retain their old `thesis_id` values (LLM-generated snake_case, or `"none"` for rejected pre-flight attempts). These are read-only history.
- Post-cutover rows carry system-assigned ids in the new format (§5).
- Mixed-format queries are expected and acceptable — id format is *temporal*, not normalized.

## 3. Background — production evidence

Pulled via SSH from VPS DBs (`/root/autoresearch-beirut-v1-20260527-035858/ema_backtest_runs.db` and `/root/autoresearch-2026-05-02/ema_backtest_runs.db`) on 2026-05-28:

- 3 `research_thesis_attempts` rows total across both DBs.
- **All 3 have `thesis_id = "none"`** — fallback string emitted by `autoresearch_research.py:1111` etc. when the LLM didn't produce a thesis_id.
- All 3 have `validator_status = "rejected"` and `thesis_details_json = "{}"`.
- 2 `backtest_runs` rows — both `is_baseline=1` with hardcoded `thesis_id="ema_base"`.

Concrete consequences observed:

1. **Three distinct rejected attempts collapse to one identifier** (`"none"`) in the DB. Any analytics that group by `thesis_id` see "1 rejection" instead of "3 rejections."
2. **No real conductor-generated thesis_ids exist in production yet** — so the format the LLM would emit is unverified. The fixtures (`close_confirmed_break_entry_gate`, `ema_pullback_v3`) are author-invented test data, not production exemplars.
3. The `_check_thesis_id_not_repeated` rule has never been exercised against a real collision because no two LLM-emitted thesis_ids have made it past the validator.

## 4. Why LLM-generated thesis_ids are wrong (design diagnosis)

| Problem | Symptom in code / production |
|---|---|
| **Non-determinism** — same prompt may emit `htf_gate_v3` vs `htf_trend_gate_v3` for what is semantically one thesis | No symptom yet; production has zero real theses. Predicted from LLM behavior. |
| **Uniqueness enforced retroactively, not by construction** | `_check_thesis_id_not_repeated` validator rule exists *only* to catch this. System-assigned ids make the rule unnecessary. |
| **Fallback `"none"` collides** | `autoresearch_research.py:1111, 1185, 1476, 1719` — 4 separate fallback sites. VPS shows 3 attempts all sharing `thesis_id="none"`. |
| **No format contract — schema accepts any string** | `research_types.py:142`: `thesis_id: str`. Validator has no regex. Prompt says "short snake_case" but it's not enforced. |
| **Identifier carries description** | Test fixtures: `tighten_min_stop_distance_pct_floor` describes the change *inside* the id. Renaming the parameter or refactoring the description would force an id change too — id becomes coupled to language. |
| **Spec A §6.1 fragility** | Spec A §6.1 binds `prior_lever_outcomes[].prior_thesis_id` to the snapshot's `thesis_ids` set. With LLM-generated ids, the model is asked to cite an identifier it previously hallucinated — fragile by design. With system-assigned ids, the model cites identifiers from the snapshot (where they're rendered) → grounded. |

## 5. Proposed id format

**Pick one of three options. Recommendation: composite round-attempt id.**

### 5.1 Option A — composite of round + attempt (recommended)

```
thesis_id = f"{research_round_id}-attempt-{attempt_number}"
# e.g. "job-12-round-5-attempt-1"
```

**Pros:**

- Uniqueness by construction. The DB already keys `research_thesis_attempts` by `(research_round_id, attempt_number)`; this just elevates that pair to a string id. Zero risk of collision.
- Trivially derivable from the row's primary key — no extra storage needed if a query needs to reconstruct it.
- Human-readable enough for log skimming (`job-12-round-5-attempt-1` immediately tells you which round and which attempt).
- Self-orderable lexicographically *within* a job/round.

**Cons:**

- Longer than a hash or counter (~25 chars).
- Ids are tied to the round/attempt at which the thesis was first accepted — re-proposing the same thesis in a later round yields a different id (which is arguably *correct*: a thesis is a per-round artifact, not a global registry entry).

### 5.2 Option B — content hash

```
thesis_id = "thesis-" + sha1(canonical_json(hypothesis, mechanism, sorted(config_changes)))[:10]
# e.g. "thesis-a3f8c2b1d9"
```

**Pros:**

- Same-content theses dedupe naturally (proposing the identical thesis twice gets the same id).
- Compact (~17 chars).

**Cons:**

- Two near-identical theses (one-character tweak in the hypothesis) get *different* ids — defeats the "same idea, same id" intent for any non-trivial revision.
- Hash collisions are mathematically possible (vanishingly small at 10 chars / 40 bits, but non-zero). System now needs collision handling.
- Opaque — `thesis-a3f8c2b1d9` is unintelligible in logs.
- Choosing what fields go into the hash is a contract that breaks when the schema evolves (a new field gets added → all existing theses re-hash).

### 5.3 Option C — sequential counter per family

```
thesis_id = f"{family}-thesis-{N}"  # N is a per-family monotonic counter
# e.g. "ema-thesis-47"
```

**Pros:**

- Short, monotonic, no collision.
- "Latest thesis" trivially identifiable.

**Cons:**

- Requires a counter store (DB sequence or `MAX(N) + 1` query at insert time). Race condition possible under concurrent writes — needs a transaction.
- Counter is opaque about *which round* a thesis came from. Requires a join to make sense in logs.
- Doesn't survive DB resets or family-rename cleanly.

### 5.4 Decision

**Adopt Option A.** Reasons:

- Uniqueness by construction — no counter race, no hash collision, no validator rule.
- Derivable from existing primary key — no schema change beyond a one-line value computation at insert time.
- Self-describing in logs.
- The "same thesis re-proposed = different id" property is consistent with the design that the round IS the experiment unit (Spec A §14.1).

The other two remain in this spec for reviewer transparency; if anyone objects to Option A, the alternatives are pre-vetted.

## 6. Where the assignment happens

The `thesis_id` is assigned at exactly one site: **`thesis_validator.validate_thesis_dict`**, at the moment the validator *accepts* a thesis (after all hard-reject rules pass, before returning the validated `ResearchThesis`).

Rationale:

- Earliest point where the round + attempt context is unambiguous.
- After acceptance, every downstream consumer (DB write, prompt block, MCP tool response) sees the assigned id.
- Pre-acceptance, the rejected-attempts table writer (`backtest_run_db.add_research_thesis_attempt`) already knows the `(research_round_id, attempt_number)` pair; it composes the same id for rejected rows.

```python
# Sketch — thesis_validator.py

def validate_thesis_dict(
    raw: dict,
    *,
    research_round_id: str,
    attempt_number: int,
    prior_theses: list[dict[str, Any]] | None = None,
    snapshot_thesis_ids: set[str] | None = None,
    tools_called: set[str] | None = None,
) -> ResearchThesis:
    # ... existing schema + rule validation ...

    # System-assigned id — overrides whatever the LLM emitted (it should emit nothing).
    raw = dict(raw)
    raw["thesis_id"] = f"{research_round_id}-attempt-{attempt_number}"

    thesis = ResearchThesis.model_validate(normalize_thesis_payload(raw))
    return thesis
```

For rejected attempts (written before validation completes), the writer at `backtest_run_db.add_research_thesis_attempt` derives the same id format from its own `(research_round_id, attempt_number)` parameters — so rejected and accepted rows share id format.

## 7. Schema + envelope changes

### 7.1 `ResearchThesis` (research_types.py)

`thesis_id` field stays — same type, same name. What changes: it is now **set by the validator after acceptance**, not by the LLM. The Pydantic field stays `str` (no Literal, no regex) so historical rows with the old format still validate when re-loaded from disk.

### 7.2 Conductor JSON envelope

Today (and per `agent_prompts.py:121`):
```json
{
  "suggested_theses": [
    {
      "thesis_id": "short_snake_case_name (unique, never reuse)",
      "hypothesis": "...",
      "mechanism": "...",
      ...
    }
  ]
}
```

After Spec A2:
```json
{
  "suggested_theses": [
    {
      "proposal_label": "htf-gate-v3  (optional free-form handle for logs; NOT an identifier)",
      "hypothesis": "...",
      "mechanism": "...",
      ...
    }
  ]
}
```

- `thesis_id` field **deleted** from the conductor's output envelope.
- `proposal_label` field **added** (optional, free-form, ≤40 chars, for human-readable log threading; never used as an identifier or DB key).
- All prompt strings updated to reflect: "do not emit `thesis_id` — the system assigns it after validation; you may emit `proposal_label` as a freeform handle."

### 7.3 Validator rule deletions

The following rules become unnecessary and are deleted:

- `_check_thesis_id_not_repeated` (`thesis_validator.py:668`) — uniqueness is by construction.
- The `or "none"` fallback at `autoresearch_research.py:1111, 1185, 1476, 1719` — no longer possible because the LLM doesn't emit `thesis_id` at all; the validator always assigns one.
- The `raw_response_fallback` thesis_id at `tests/test_agent_orchestrator_characterization.py:396` — replaced with a system-assigned id from the test's round/attempt context.

### 7.4 Validator rule unchanged

Spec A §6.1's `prior_lever_outcomes` content check **remains** — but is now more meaningful. With system-assigned ids, citations in `prior_lever_outcomes[].prior_thesis_id` must match ids the system actually issued and the snapshot rendered. Hallucination becomes impossible to *accidentally* succeed.

## 8. Migration plan — single PR, single commit per layer

Per Spec A1's hard-cutover style. All commits land together; partial landing is not supported.

1. **`thesis_validator.py`**: add `research_round_id` and `attempt_number` parameters to `validate_thesis_dict`. Implement the system-assignment line. Delete `_check_thesis_id_not_repeated`. Update `tests/test_thesis_validator.py` and `tests/test_validator_*.py` to pass round/attempt context (every test fixture currently constructs a thesis with a hand-typed `thesis_id` — those become "expected output," with the test providing `research_round_id="job-1-round-1"` and `attempt_number=1` to drive the system-assigned id).
2. **`research_types.py`**: no schema change; field stays `str`. Add a docstring note: "Set by `validate_thesis_dict` after acceptance; do not populate from the LLM envelope."
3. **`autoresearch_research.py`**: remove the four `or "none"` fallback sites (lines 1111, 1185, 1476, 1719). Add round/attempt parameter plumbing to every caller of `validate_thesis_dict`. Update `tests/test_autoresearch_research.py` fixtures.
4. **`agent_prompts.py`**: edit the JSON schema example at line 121 to remove `thesis_id` and add `proposal_label`. Edit rule text at line 102 to reflect that `thesis_id` is no longer a thing the LLM controls — uniqueness is automatic, the agent's rule becomes "do not propose a duplicate-*content* thesis," not "do not reuse a thesis_id." Update `tests/test_agent_prompts*.py` (if any).
5. **`research_prompts.py`**: edit the OUTPUT field documentation at line 119 to remove `thesis_id` and document `proposal_label`. Update tests.
6. **`agent_runners.py` / `agent_orchestrator.py` / `research_conductor.py`**: any site that reads `result["thesis_id"]` from the raw LLM envelope is removed; reads `result["proposal_label"]` if present (logging only). Update tests.
7. **`backtest_run_db.add_research_thesis_attempt`**: ensures the row's `thesis_id` column is set to `f"{research_round_id}-attempt-{attempt_number}"`. Callers updated to pass round/attempt instead of LLM-emitted id.
8. **`compiler_thesis_io.py`**: `generated_thesis_id` keys in compiler payloads (lines 81, 88) now read `research_thesis.thesis_id` which is the system-assigned value. No code change beyond ensuring the validator ran first.
9. **`tests/test_agent_orchestrator_characterization.py:396`**: replace `"thesis_id": "raw_response_fallback"` with a test that asserts the system *does not* propagate any LLM-emitted thesis_id, and instead assigns from context.
10. **`scripts/inspect_thesis_ids.py`** (new, optional): one-time diagnostic script that lists pre-cutover vs post-cutover thesis_ids by format prefix — confirms the cutover landed correctly when run on the VPS DBs.
11. **Final sweep — grep gates** (PR not mergeable until each returns the expected count):
    - `grep -rn 'or "none"' autoresearch_research.py` returns zero hits.
    - `grep -rn '_check_thesis_id_not_repeated' --include="*.py"` returns zero hits.
    - `grep -rn 'short_snake_case_name\|short stable identifier' --include="*.py"` returns zero hits.
    - `grep -rn 'raw_response_fallback' --include="*.py"` returns zero hits.
    - `grep -rn 'proposal_label' --include="*.py"` returns at least the expected ≥5 hits (prompt, schema, validator, tests).
12. **Documentation**: this spec marked Shipped; Spec A §6.1 cross-references updated to note that ids are now system-assigned.

**Test update is non-negotiable per step**, mirroring A1.

## 9. Risk and rollback

**Risks:**

- **Pre-cutover rows in the DB have non-conforming `thesis_id` values** (LLM-emitted snake_case, or literal `"none"`). Mitigation: query logic must not assume the id format. Specifically, `_check_thesis_id_not_repeated`'s removal means we lose detection of duplicate ids — but with system assignment, dupes are impossible going forward. Pre-cutover dupes (the three `"none"` rows) remain as historical artifacts; analytics that group by `thesis_id` and want to count distinct rejections must use `(research_round_id, attempt_number)` instead.
- **Test fixtures broken across the suite.** Every test that hand-types `thesis_id="…"` and expects it to flow through validation needs to be updated to either (a) provide the round/attempt context that drives the id, or (b) assert the system-assigned id matches the format. Mitigation: §8 step ordering puts the validator change first; other tests fail at that point and get fixed in the same commit.
- **Conductor LLM ignores the schema change and still emits `thesis_id`.** Mitigation: the validator silently discards any `thesis_id` the LLM emits (overwritten by `raw["thesis_id"] = ...`). No error — but the agent reflexion can mention "your `thesis_id` field was ignored; the system assigns ids."
- **External tools (logs, dashboards) that filter by `thesis_id="some_snake_case"`** break because new ids look like `job-N-round-M-attempt-K`. Mitigation: none — these consumers must update their queries. Audit log of known consumers in the PR description.

**Rollback:**

Revert the PR. Pre-cutover code reverts to LLM-emitted ids. Post-cutover DB rows with system-assigned ids remain readable (the schema accepts any string) but no new ones get written. If business-critical, a follow-up script can re-emit LLM-style ids for the post-cutover rows by calling the conductor with the stored hypothesis/mechanism — but this is exotic and not in scope.

## 10. Success criteria

**Hard-cutover grep gate (PR not mergeable until each pass):**

- `grep -rn 'or "none"' autoresearch_research.py` returns zero hits.
- `grep -rn '_check_thesis_id_not_repeated' --include="*.py"` returns zero hits.
- `grep -rn 'short_snake_case_name\|short stable identifier' --include="*.py"` returns zero hits.
- `grep -rn '"thesis_id"\s*:\s*"raw_response_fallback"' --include="*.py"` returns zero hits.
- `grep -rn 'proposal_label' --include="*.py"` returns ≥5 hits (prompt definitions, schema, validator, tests, conductor envelope handler).

**Behavioral criteria (verified by new tests):**

- `tests/test_thesis_id_assignment.py` (new): `validate_thesis_dict(raw, research_round_id="job-1-round-1", attempt_number=1)` returns a `ResearchThesis` whose `thesis_id == "job-1-round-1-attempt-1"` regardless of what `raw["thesis_id"]` was (or whether it was absent entirely).
- Two LLM responses with identical hypothesis/mechanism, processed as two attempts in the same round, get two distinct system-assigned ids (`-attempt-1` and `-attempt-2`).
- LLM response with NO `thesis_id` field validates cleanly (system assigns it).
- LLM response with `thesis_id="completely_made_up"` validates cleanly; the `completely_made_up` value is silently discarded; the returned thesis has the system-assigned id.
- An attempt rejected by the validator gets the same id format in the rejection row (`job-X-round-Y-attempt-Z`) — verified by a test that triggers a hard reject and inspects the resulting DB row.

**Migration verification:**

- `scripts/inspect_thesis_ids.py` against the VPS DBs shows: pre-cutover rows (timestamp before the deploy) retain their old ids; post-cutover rows all follow the new format `job-*-round-*-attempt-*`.

**Documentation:**

- Spec A §6.1 updated to note: "Post-Spec-A2, `prior_thesis_id` citations reference system-assigned ids of the form `job-N-round-M-attempt-K`. The snapshot's thesis_ids set carries these directly — the conductor sees and cites the same string."
- Spec A1 cross-references Spec A2 (Spec A1 lands first; A2 references A1 as a dependency).
- This spec marked Shipped; PR description includes grep-gate output.

## 11. Out of scope

- Re-emitting historical thesis_ids in the new format (would require re-validating every stored thesis and rebuilding the DB; cost/benefit doesn't justify).
- Per-thesis content-deduplication (Option B's natural property) — that's a separate Spec C concern (semantic dedup).
- Renaming the DB column `thesis_id` (the column stays; values evolve).
- Introducing a separate `thesis_uuid` field alongside `thesis_id` (rejected — one identifier per concept).
- Changing how `proposal_label` is used in logs/prompts beyond the bare minimum (it's a logging affordance, not a feature surface).
