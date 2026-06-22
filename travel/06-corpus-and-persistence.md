# 06 — Corpus & Persistence

AGENT SPEC. The durable knowledge layer: defines WHERE findings are written, the row/ID schema they use, and the reconciliation that proves convergence is real. The corpus is the seed for deeper rounds (discovery-loop), the input to ranking, and the object the freshness loop refreshes. Resolve all sibling-doc slugs via doc-manifest.md.

INPUT (read these committed files — never the orchestrator's context):
- `<country>_theme_map_v0.md` — seed (from theme-seeding).
- `<country>/axes.md` — per-country axis ledger (from coverage-matrix).
- `axes-registry.md` — global axis registry (baseline axes + candidate watchlist + cross-country promotions). Axis set, stage/role tags, and count are DERIVED from this file.
- Prior `corpus/round<N>_<cluster>.md` files (when resuming/appending).

OUTPUT (every artifact WRITTEN to a committed file — verbatim, never relayed):
- `<country>_theme_map_v0.md` — seed (audit trail).
- `<country>_theme_map_v<N>.md` — reshaped after each round (decisions).
- `<country>_theme_map_FINAL.md` — converged structure + closing reconciliation.
- `<country>/axes.md` — per-country axis ledger (active axes, promotions, pending) — see coverage-matrix.
- `corpus/round<N>_<cluster>.md` — raw inventories, written BY the subagents.
- `rankings/<theme-id>.md` — ranked RANK_DEPTH list per theme (produced in ranking).
- A verification-debt section/file — UNVERIFIED rows + fetch-blocked URLs with HTTP status.

NEXT: ranking reads `corpus/round<N>_*.md` and the verification-debt queue; freshness refreshes rows by `last_checked` on VERIFY_CADENCE; discovery-loop appends new rounds. theme-seeding/coverage-matrix produced the inputs.

MEMORY INVARIANT: nothing the method depends on lives in session memory. Findings live ONLY in the files above; if a finding is in the orchestrator's context only, the next session starts cold and relayed summaries lose detail. Persist verbatim. A fresh session reproduces the same corpus, rankings, and convergence verdict from the files alone.

COMPOUNDING: corpus rounds and theme-map versions are APPEND-only (read prior → run round → APPEND new file, never overwrite). Axis findings flow `<country>/axes.md` (per-country ledger) → promote to `axes-registry.md` (global) so future countries inherit them (coverage-matrix). The per-country `axes.md` inherits from the registry and records this country's deviations; promotions flow back up to the registry. Append, don't overwrite, is what lets a future run *improve upon* rather than repeat. Registry append/promotion mechanics: REGISTRY-PROTOCOL.md.

(During method development these lived under `.context/`; for delivery, keep them in the repo `travel/` tree or a per-country subfolder — see doc-manifest.md per-country artifacts.)

## PROCEDURE

1. **Subagents write their own files (the key pattern).** Each discovery/verification agent uses its file-write tool to save raw findings **directly** to `corpus/round<N>_<cluster>.md`, and returns to the orchestrator only a 2-line verdict + the file path. Rationale: orchestrator context stays lean (scales to TARGET_SCALE), the save is verbatim (no relay loss), parallel agents never collide (one file each). See orchestration.

2. **Write every operator as a row** using the schema below. One row per operator.

3. **Tag each row's `format-class`** — values + rankability in `tags-registry.md` (`fixed-departure-group` / `private-bespoke` / `day-format` / `hybrid-course`). Only `fixed-departure-group` is admissible on the "dated departure" basis directly (ranking).

4. **Set each row's `status`** — values `tags-registry.md` row.status (`verified` / `UNVERIFIED` / `stale`); rules below.

5. **Assign theme IDs** per THEME_ID_GRAMMAR (IDs are assigned at seed time in theme-seeding; here you preserve them and extend on split).

6. **Carry the verification debt as a concrete artifact.** Keep a dedicated section/file listing every UNVERIFIED row and every **fetch-blocked URL with its HTTP status**. This is the priority queue for verification (ranking) and the freshness pass — never let it silently drop.

7. **On a 403/404 fetch block, do NOT drop the row** (snippet-as-secondary-source): harvest the date/price/guide from the **search-result snippet**, record it with the claim's source noted (e.g. "date confirmed in search snippet, page 403"), keep the row at `status: UNVERIFIED` with the HTTP status logged.

8. **Close every axis-proof corpus file with a written de-dup/exclusion note.** Axis-proof files are those produced for axes tagged `role:axis-proof` in axes-registry.md. The note applies the de-dup guards below, stating what was dropped and why.

9. **APPEND, don't overwrite.** Each discovery round writes a NEW `round<N>` file. Theme-map versions are kept (v0, v1, … FINAL) as an audit trail of how the structure evolved.

10. **If discovery surfaced a new axis** → APPEND it to `<country>/axes.md`; if it generalizes beyond this country → PROMOTE to `axes-registry.md` (coverage-matrix; promotion bar + mechanics in REGISTRY-PROTOCOL.md).

11. **Before declaring done, run the closing reconciliation** (rules below) and write it into `<country>_theme_map_FINAL.md`.

## ROW SCHEMA (every operator row)
`Operator | Channel | Tour name | Expert (named + credential) | Format-class | CURRENT_SEASON departure? | price | group size | URL | last_checked: YYYY-MM-DD | status | first_seen_round`
(Format-class + status value sets: `tags-registry.md`. Channel = a `channel-registry.md` id.)

`first_seen_round` provenance: discovery agents do NOT fill this — round provenance is implicit in the `corpus/round<N>_<cluster>.md` filename while findings stay per-round. It is stamped ONLY at consolidation (when round files merge into `<country>/corpus_FINAL.md`, see freshness), **derived mechanically from the source round-filename**. Preserves which round surfaced each operator across the per-round → consolidated transition (the one place filename provenance is otherwise lost).

Channel values are channel-registry.md stable ids (e.g. `academic-operator`, `luxury-bespoke`) — never positional letters.

## SCHEMA EVOLUTION (the schema is a versioned contract — it evolves, deliberately)
The row schema is NOT a frozen static list (it has already grown: `first_seen_round`, the `dirty` tracker) — but it is also NOT an open-append-anytime list, because consumers (`ranking`, `freshness`) read specific columns. It is a **versioned contract**: changing it is a deliberate, lesson-tracked migration, not a casual append.
Rules to add/change a column:
1. **Lesson-tracked**: a schema change is recorded in `10-lessons-log.md` (what column, why); bump the `schema-version` stamped at the top of `<country>/corpus_FINAL.md`.
2. **Consumers in lockstep**: update every reader/writer (`ranking`, `freshness`, the row schema here) in the same change — never ship a column some consumer doesn't know.
3. **BACKFILL existing rows** (no orphaned rows):
   - **Derive where possible** — mechanically fill from existing data (e.g. `first_seen_round` from the source round-filename at consolidation).
   - **Sentinel otherwise** — set the new field to `unknown` (or `n/a` if it can't apply) and queue it; do NOT leave it blank/undefined.
   - **Fill on next touch** — the VERIFY pass (`freshness`) fills `unknown` sentinels when it next re-fetches each row; a one-shot backfill migration over `<country>/corpus_FINAL.md` is the alternative for a bulk fill.
   - **Append-only safe** — backfill adds/sets the column; git preserves prior state, so no destructive rewrite.

## DECISION RULES

**Status:**
- status=verified IFF named guide + dated departure + price all confirmed from a live page.
- status=UNVERIFIED IFF operator/tour is real but a named per-departure guide and/or date is not yet confirmed (e.g. annual catalogue, or a page that 403'd).
- status=stale IFF `last_checked` is older than the refresh window (VERIFY_CADENCE; see freshness).
- A snippet-sourced row stays UNVERIFIED. NEVER promote it to `verified` without an unblocked live-page confirmation — a snippet is weaker than the live page.

These row statuses feed the credited-product weights at ranking: a verified row earns FULL_PRODUCT_WEIGHT, an UNVERIFIED row earns PARTIAL_PRODUCT_WEIGHT against the ADMISSION_BAR — owned by ranking, cross-ref only here.

**Theme-ID convention (THEME_ID_GRAMMAR):**
- Format: 2-letter country code + sequential number (e.g. `IT-01`).
- Assign IDs at seed time (theme-seeding) and NEVER renumber on reshape — stable IDs are the audit trail and the `rankings/<theme-id>.md` filename.
- On SPLIT → keep the parent number, append lowercase letters: `IT-05a` (Umbria art) / `IT-05b` (St Francis).
- On fold/demote → the ID keeps its place in the DEMOTED audit trail.
- New themes found in later rounds get the next free number (don't reuse). Per THEME_ID_OVERFLOW, a country exceeding the two-digit range widens digits rather than recycling. Without stable IDs, parallel agents/sessions invent conflicting IDs and ranking files collide.

**De-dup guards (each axis-proof file must end with these):**
- Aggregators/resellers excluded — count the underlying operator, not the marketplace.
- Absorbed sub-brands collapsed into the parent.
- Prior-captured operators excluded (already in the cumulative known list, see orchestration).
- For the first two guards, apply operator-aliases.md (parent/sub-brand absorptions + aggregator exclusions) rather than inline exemplars; append a new row there whenever a fresh absorption/aggregator surfaces.

**Closing reconciliation (`*_theme_map_FINAL.md` MUST):**
1. List every corpus round, INCLUDING the rounds for axes tagged `role:axis-proof` in axes-registry.md — not just the early cluster rounds.
2. State convergence as two-level: "theme-converged at round N; operator-converged after the axis-proof rounds." Operator-convergence requires every axis tagged `role:convergence-gate` to return dry — not a frozen round count (per OPERATOR_CONVERGED, axes-registry).
3. The per-axis dry tracker carries a `dirty` state per (theme, axis): a promotion (`REGISTRY-PROTOCOL.md` INVALIDATION) sets earlier themes `dirty` on the new axis; a `dirty` cell is NOT dry. FINAL may only declare DONE when zero cells are `dirty` (per DONE, `travel-config.md`).
- Skipping this silently reproduces the false-convergence lesson L7 (pinned in lessons) that the reconciliation exists to prevent — the Italy FINAL initially said "converged after 4 rounds" while a later axis-proof round's operators sat unmerged. Always reconcile before declaring done.

## EXAMPLE (Italy)

Per-country layout produced (full theme roster lives in the italy/ files; this doc stays example-light):
```
italy/italy_theme_map_v0.md      ← seed
italy/italy_theme_map_v<N>.md    ← reshaped each round
italy/italy_theme_map_FINAL.md   ← converged
italy/axes.md                    ← per-country axis ledger
italy/corpus/round<N>_<cluster>.md
italy/rankings/IT-01.md …
```
403/404 fallback applied verbatim in the Italy run:
- VolcanoAdventures page 403 → date kept from search snippet, row UNVERIFIED.
- ACE Palladio page 404 → operator kept, marked UNVERIFIED.
- Peter Sommer, Smithsonian → direct fetches 403'd → URLs logged with HTTP status in the verification-debt queue.

De-dup applied via operator-aliases.md: aggregator excluded; absorbed sub-brand collapsed into its parent.

Reconciliation caught: Italy FINAL initially declared "converged after 4 rounds" while the round-5 axis-proof (language / authority-index) operators sat unmerged → corrected to the two-level statement.

## ANTI-PATTERNS (fail the step if true)
(VIEW of the lessons-log — open — append the check when a new lesson lands; tag Lnn. Source of truth is 10-lessons-log.md; REGISTRY-PROTOCOL.md "Anti-patterns are a view of the lessons-log".)
- Overwriting corpus rounds instead of appending (destroys the audit trail). (L4)
- Renumbering theme IDs on reshape (breaks `rankings/<theme-id>.md` and cross-refs).
- Dropping a 403/404 row instead of keeping it UNVERIFIED with snippet evidence. (L9)
- Double-counting aggregators or absorbed sub-brands as separate operators (apply operator-aliases.md). (L9)
- Promoting a snippet-sourced row to `verified` without an unblocked live-page confirmation. (L9)
- Shipping a FINAL map that omits the `role:axis-proof` axis rounds (false convergence). (L10, L7)
- Findings that live only in orchestrator context instead of a committed file (breaks the memory invariant; next session starts cold). (L4, L5)
- A new axis found in discovery and not appended to `<country>/axes.md` / promoted to `axes-registry.md` (no compounding). (L14)
