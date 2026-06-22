# 06 — Corpus & Persistence

## Why
The corpus is the durable knowledge base. It is the seed for deeper rounds, the input to ranking, and the object the freshness loop refreshes. If findings live only in the orchestrator's context, the next session starts cold and relayed summaries lose detail. Persist verbatim.

## Where (per country)
```
<country>_theme_map_v0.md      ← seed (audit trail)
<country>_theme_map_v<N>.md    ← reshaped after each round (decisions)
<country>_theme_map_FINAL.md   ← converged structure
corpus/round<N>_<cluster>.md   ← raw inventories, written BY the subagents
rankings/<theme-id>.md         ← ranked Top-5 per theme (step 07)
```
(During method development these lived under `.context/`; for delivery, keep them in the repo `travel/` tree or a per-country subfolder.)

## Subagents write their own files (the key pattern)
Each discovery/verification agent uses its file-write tool to save raw findings **directly** to its corpus file, and returns to the orchestrator only a 2-line verdict + the file path. Benefits: orchestrator context stays lean (scales to 50 countries), the save is verbatim (no relay loss), and parallel agents never collide (one file each). See `09`.

## Row schema (every operator row)
`Operator | Channel | Tour name | Expert (named + credential) | Format-class | current-season departure? | price | group size | URL | last_checked: YYYY-MM-DD | status: verified | UNVERIFIED | stale`

**Format-class** (not just day/multi-day — it affects rankability, see `07`): `fixed-departure group` | `private/bespoke/year-round` | `hybrid/course`. A private/bespoke or year-round product can't be admitted on the "dated departure" basis the same way a fixed-departure tour can.

- **status=verified**: named guide + dated departure + price confirmed from a live page.
- **status=UNVERIFIED**: operator/tour real, but a named per-departure guide and/or date not yet confirmed (e.g. annual catalogue, or a page that 403'd).
- **status=stale**: `last_checked` older than the refresh window (`08`).

## Carry the verification debt (a concrete artifact)
Keep a dedicated section/file listing every UNVERIFIED row and every **fetch-blocked URL with its HTTP status** (some operator sites 403/404 direct fetches — e.g. Peter Sommer, Smithsonian in the Italy run). These are the priority queue for verification (`07`) and the freshness pass (`08`) — never let them silently drop.

**403/404 fallback (snippet-as-secondary-source):** when an operator page blocks fetching, do NOT drop the row. Harvest the date/price/guide from the **search-result snippet**, record it with the claim's source noted (e.g. "date confirmed in search snippet, page 403"), and keep the row at **status: UNVERIFIED** with the HTTP status logged. The Italy run did exactly this (VolcanoAdventures 403 → date kept from snippet; ACE Palladio 404 → operator kept, marked UNVERIFIED). A snippet is weaker than the live page — never promote such a row to `verified` without an unblocked confirmation.

## Theme-ID convention
- Format: `<2-letter country code>-<two-digit number>` (e.g. `IT-01`).
- **Assign IDs at seed time (`02`) and NEVER renumber on reshape** — stable IDs are the audit trail and the `rankings/<theme-id>.md` filename.
- On **SPLIT**, keep the parent number and append lowercase letters: `IT-05a` (Umbria art) / `IT-05b` (St Francis). On **fold/demote**, the ID keeps its place in the DEMOTED audit trail.
- New themes found in later rounds get the next free number (don't reuse). Without this convention, parallel agents/sessions invent conflicting IDs and ranking files collide.

## De-dup guards (every axis-proof file ends with these)
When merging axis findings, each corpus file must close with a written **de-dup/exclusion note** stating what was dropped and why:
- **Aggregators/resellers excluded** — count the underlying operator, not the marketplace (e.g. studienreisen.de excluded).
- **Absorbed sub-brands collapsed** into the parent (Dr. Tigges → Gebeco).
- **Prior-captured operators excluded** (already in the cumulative known list, see `09`).

## Closing reconciliation (prevents shipping a false convergence)
The `*_theme_map_FINAL.md` MUST:
1. **List every corpus round**, including the LANGUAGE and AUTHORITY-INDEX axis rounds — not just the early cluster rounds.
2. **State convergence as two-level**: "theme-converged at round N; operator-converged after the axis-proof rounds." 
Skipping this silently reproduces the exact false convergence lesson L7 (`10`) exists to prevent — the Italy FINAL initially said "converged after 4 rounds" while round-5 axis operators sat unmerged. Always reconcile before declaring done.

## Anti-patterns
- Overwriting corpus rounds instead of appending (destroys the audit trail).
- Renumbering theme IDs on reshape (breaks `rankings/<theme-id>.md` and cross-refs).
- Dropping a 403/404 row instead of keeping it UNVERIFIED with snippet evidence.
- Double-counting aggregators or absorbed sub-brands as separate operators.
- Shipping a FINAL map that omits the language/authority axis rounds (false convergence).

## Append, don't overwrite
Each discovery round appends a new `round<N>` file. Theme-map versions are kept (v0, v1, … FINAL) as an audit trail of how the structure evolved. This is what lets a future run *improve upon* rather than repeat.
