# 06 — Corpus & Persistence

AGENT SPEC. The durable knowledge layer: defines WHERE findings are written, the row/ID schema they use, and the reconciliation that proves convergence is real. The corpus is the seed for deeper rounds (`04`), the input to ranking (`07`), and the object the freshness loop (`08`) refreshes.

INPUT (read these committed files — never the orchestrator's context):
- `<country>_theme_map_v0.md` — seed (from `02`).
- `axes.md` — per-country axis ledger (from `03`).
- `axes-registry.md` — global axis registry at the playbook root (baseline axes + candidate watchlist + cross-country promotions).
- Prior `corpus/round<N>_<cluster>.md` files (when resuming/appending).

OUTPUT (every artifact WRITTEN to a committed file — verbatim, never relayed):
- `<country>_theme_map_v0.md` — seed (audit trail).
- `<country>_theme_map_v<N>.md` — reshaped after each round (decisions).
- `<country>_theme_map_FINAL.md` — converged structure + closing reconciliation.
- `axes.md` — per-country axis ledger (active axes, promotions, pending) — see `03`.
- `corpus/round<N>_<cluster>.md` — raw inventories, written BY the subagents.
- `rankings/<theme-id>.md` — ranked Top-5 per theme (produced in `07`).
- A verification-debt section/file — UNVERIFIED rows + fetch-blocked URLs with HTTP status.

NEXT: `07` (ranking) reads `corpus/round<N>_*.md` and the verification-debt queue; `08` (freshness) refreshes rows by `last_checked`; `04` (discovery) appends new rounds. `02`/`03` produced the inputs.

MEMORY INVARIANT: nothing the method depends on lives in session memory. Findings live ONLY in the files above; if a finding is in the orchestrator's context only, the next session starts cold and relayed summaries lose detail. Persist verbatim. A fresh session reproduces the same corpus, rankings, and convergence verdict from the files alone.

COMPOUNDING: corpus rounds and theme-map versions are APPEND-only (read prior → run round → APPEND new file, never overwrite). Axis findings flow `axes.md` (per-country ledger) → promote to `axes-registry.md` (global) so future countries inherit them (`03`). The per-country `axes.md` inherits from the registry and records this country's deviations; promotions flow back up to the registry. Append, don't overwrite, is what lets a future run *improve upon* rather than repeat.

(During method development these lived under `.context/`; for delivery, keep them in the repo `travel/` tree or a per-country subfolder.)

## PROCEDURE

1. **Subagents write their own files (the key pattern).** Each discovery/verification agent uses its file-write tool to save raw findings **directly** to `corpus/round<N>_<cluster>.md`, and returns to the orchestrator only a 2-line verdict + the file path. Rationale: orchestrator context stays lean (scales to 50 countries), the save is verbatim (no relay loss), parallel agents never collide (one file each). See `09`.

2. **Write every operator as a row** using the schema below. One row per operator.

3. **Tag each row's `format-class`** — `fixed-departure group` | `private/bespoke/year-round` | `hybrid/course`. This affects rankability (`07`): a private/bespoke or year-round product can't be admitted on the "dated departure" basis the same way a fixed-departure tour can.

4. **Set each row's `status`** per the status rules below.

5. **Assign theme IDs** per the ID convention below (IDs are assigned at seed time in `02`; here you preserve them and extend on split).

6. **Carry the verification debt as a concrete artifact.** Keep a dedicated section/file listing every UNVERIFIED row and every **fetch-blocked URL with its HTTP status**. This is the priority queue for verification (`07`) and the freshness pass (`08`) — never let it silently drop.

7. **On a 403/404 fetch block, do NOT drop the row** (snippet-as-secondary-source): harvest the date/price/guide from the **search-result snippet**, record it with the claim's source noted (e.g. "date confirmed in search snippet, page 403"), keep the row at `status: UNVERIFIED` with the HTTP status logged.

8. **Close every axis-proof corpus file with a written de-dup/exclusion note** (the three de-dup guards below), stating what was dropped and why.

9. **APPEND, don't overwrite.** Each discovery round writes a NEW `round<N>` file. Theme-map versions are kept (v0, v1, … FINAL) as an audit trail of how the structure evolved.

10. **If discovery surfaced a new axis** → APPEND it to `axes.md`; if it generalizes beyond this country → PROMOTE to `axes-registry.md` (`03`).

11. **Before declaring done, run the closing reconciliation** (rules below) and write it into `<country>_theme_map_FINAL.md`.

## ROW SCHEMA (every operator row)
`Operator | Channel | Tour name | Expert (named + credential) | Format-class | current-season departure? | price | group size | URL | last_checked: YYYY-MM-DD | status: verified | UNVERIFIED | stale`

## DECISION RULES

**Status:**
- status=verified IFF named guide + dated departure + price all confirmed from a live page.
- status=UNVERIFIED IFF operator/tour is real but a named per-departure guide and/or date is not yet confirmed (e.g. annual catalogue, or a page that 403'd).
- status=stale IFF `last_checked` is older than the refresh window (`08`).
- A snippet-sourced row stays UNVERIFIED. NEVER promote it to `verified` without an unblocked live-page confirmation — a snippet is weaker than the live page.

**Theme-ID convention:**
- Format: `<2-letter country code>-<two-digit number>` (e.g. `IT-01`).
- Assign IDs at seed time (`02`) and NEVER renumber on reshape — stable IDs are the audit trail and the `rankings/<theme-id>.md` filename.
- On SPLIT → keep the parent number, append lowercase letters: `IT-05a` (Umbria art) / `IT-05b` (St Francis).
- On fold/demote → the ID keeps its place in the DEMOTED audit trail.
- New themes found in later rounds get the next free number (don't reuse). Without this, parallel agents/sessions invent conflicting IDs and ranking files collide.

**De-dup guards (each axis-proof file must end with these):**
- Aggregators/resellers excluded — count the underlying operator, not the marketplace (e.g. studienreisen.de excluded).
- Absorbed sub-brands collapsed into the parent (Dr. Tigges → Gebeco).
- Prior-captured operators excluded (already in the cumulative known list, see `09`).

**Closing reconciliation (`*_theme_map_FINAL.md` MUST):**
1. List every corpus round, INCLUDING the LANGUAGE and AUTHORITY-INDEX axis rounds — not just the early cluster rounds.
2. State convergence as two-level: "theme-converged at round N; operator-converged after the axis-proof rounds."
- Skipping this silently reproduces the false-convergence lesson L7 (`10`) exists to prevent — the Italy FINAL initially said "converged after 4 rounds" while round-5 axis operators sat unmerged. Always reconcile before declaring done.

## EXAMPLE (Italy)

Per-country layout produced:
```
italy/italy_theme_map_v0.md      ← seed (19 themes)
italy/italy_theme_map_v<N>.md    ← reshaped each round
italy/italy_theme_map_FINAL.md   ← 35 themes, converged
italy/axes.md                    ← per-country axis ledger
italy/corpus/round<N>_<cluster>.md
italy/rankings/IT-01.md …
```
403/404 fallback applied verbatim in the Italy run:
- VolcanoAdventures page 403 → date kept from search snippet, row UNVERIFIED.
- ACE Palladio page 404 → operator kept, marked UNVERIFIED.
- Peter Sommer, Smithsonian → direct fetches 403'd → URLs logged with HTTP status in the verification-debt queue.

De-dup applied: studienreisen.de excluded (aggregator); Dr. Tigges collapsed into Gebeco (absorbed sub-brand).

Reconciliation caught: Italy FINAL initially declared "converged after 4 rounds" while round-5 LANGUAGE/AUTHORITY-INDEX axis operators sat unmerged → corrected to the two-level statement.

## ANTI-PATTERNS (fail the step if true)
- Overwriting corpus rounds instead of appending (destroys the audit trail).
- Renumbering theme IDs on reshape (breaks `rankings/<theme-id>.md` and cross-refs).
- Dropping a 403/404 row instead of keeping it UNVERIFIED with snippet evidence.
- Double-counting aggregators or absorbed sub-brands as separate operators.
- Promoting a snippet-sourced row to `verified` without an unblocked live-page confirmation.
- Shipping a FINAL map that omits the language/authority axis rounds (false convergence).
- Findings that live only in orchestrator context instead of a committed file (breaks the memory invariant; next session starts cold).
- A new axis found in discovery and not appended to `axes.md` / promoted to `axes-registry.md` (no compounding).
