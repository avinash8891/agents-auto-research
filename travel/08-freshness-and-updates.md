# 08 — Freshness & Updates

AGENT SPEC. Keep a locked corpus correct over time. Two refresh loops (cheap VERIFY, expensive DISCOVERY) re-fetch known rows and re-discover the market on different cadences. The corpus is a point-in-time snapshot; "correct now" is wrong two months later unless refreshed. `CURRENT_SEASON` (travel-config.md) rolls forward here — this is the ONE doc that owns the season roll-per-cycle.

INPUT: the locked corpus `<country>/corpus_FINAL.md` (rows carry URL, stored fields, `last_checked`, `status`) + the UNVERIFIED / fetch-blocked list. Loop B additionally READS the global registries lens-registry.md, theme-archetypes.md, axes-registry.md and the admission bar from the admission-bar doc. Country re-ranking READS the ranking sources tagged `tier=primary` in sources-registry.md via the country-ranking doc. Read everything from these committed files — do NOT carry corpus state in session memory.
OUTPUT: updated corpus rows (new `last_checked` + `status`) written back to `<country>/corpus_FINAL.md`; a dated diff report per VERIFY pass `<country>/verify_<date>.md`; a changelog entry `<country>/ledger.md` when DISCOVERY reshapes anything; the refreshed UNVERIFIED / fetch-blocked list.
NEXT: booking/use-of-corpus and trip composition (composition doc) consume the freshest verified rows; DISCOVERY reshapes feed back through the round machinery (discovery-loop / admission-bar docs) and the ID/audit trail (corpus doc); the prompts to run both loops live in the orchestration doc.

MEMORY INVARIANT: nothing freshness depends on lives in session memory. Every URL, stored field, `last_checked`, `status`, and the UNVERIFIED list are READ from the committed corpus and the diffs/changelog are WRITTEN back to committed files. A fresh session reproduces the same VERIFY/DISCOVERY result from the files alone — the corpus + registries are the source of truth, not recall (README principle 9; REGISTRY-PROTOCOL.md source-of-truth invariant).

COMPOUNDING: the corpus is a living ledger, not a one-off deliverable. Each VERIFY pass READS rows → re-fetches → APPENDS diffs + new stamps. Each DISCOVERY pass READS registries → re-runs the critic → on any new lens/archetype/operator, PROMOTES it to the owning global registry (lens-registry.md / theme-archetypes.md / axes-registry.md) so future countries inherit it, and APPENDS a per-country changelog entry. Promotion mechanics and the promotion bar are owned by REGISTRY-PROTOCOL.md — do not restate them here. Knowledge accrues across passes and across sessions.

## CONSOLIDATION (round files → locked corpus; run once before the first VERIFY)
Merge the per-round `corpus/round<N>_<cluster>.md` files into `<country>/corpus_FINAL.md`. When merging each row, **stamp `first_seen_round`** derived mechanically from its source round-filename (`corpus` row schema) — preserves round provenance the consolidated file would otherwise lose. Dedup per `operator-aliases.md`. After this, the locked corpus is the VERIFY/DISCOVERY input.

## PROCEDURE — Loop A: VERIFY pass (cheap; `VERIFY_CADENCE`)
URLs are already known, so this is re-fetch, not discovery. Runs on `VERIFY_CADENCE` (travel-config.md).
1. READ every row's URL from the corpus.
2. Re-fetch each URL.
3. **Diff** the fetched page against the stored row. Diff dimensions (open — append on discovery; REGISTRY-PROTOCOL.md): date moved · price changed · tour withdrawn · new departure added · guide changed. (provenance: Italy build / L8.)
4. Update `last_checked` to today (`YYYY-MM-DD`). Set `status` ∈ {verified, stale, withdrawn}.
   - **Schema backfill:** while the page is fetched, fill any `unknown` schema sentinels on this row that are now derivable (the VERIFY pass is the backfill vehicle for schema migrations — `corpus` doc SCHEMA EVOLUTION).
5. Log every diff to the dated VERIFY report `<country>/verify_<date>.md`.
6. Refresh the UNVERIFIED / fetch-blocked list (URLs that failed to fetch stay listed and worked next pass).
7. Write updated rows back to `<country>/corpus_FINAL.md`. This loop is the answer to "new info after the cadence window" — it catches the changes that matter for booking.

## PROCEDURE — Loop B: DISCOVERY pass (expensive; `DISCOVERY_CADENCE`)
Runs on `DISCOVERY_CADENCE` (travel-config.md).
1. READ lens-registry.md, theme-archetypes.md, axes-registry.md.
2. Re-run the final adversarial completeness-critic + a dry-check across the baseline axes in axes-registry.md (count derived from the registry; the convergence-gate is "every axis tagged `role:convergence-gate` returns dry," not a frozen axis count — `OPERATOR_CONVERGED`, travel-config.md). Give axes tagged `role:axis-proof` their own dedicated sweep (the false-convergence gate).
3. For any slice that clears the `ADMISSION_BAR` (admission-bar doc; `ADMISSION_BAR` / `MIN_CREDENTIALED_PRODUCTS`, travel-config.md): run a full discovery round for that slice and reshape the corpus.
4. If the pass surfaces a new lens / archetype / operator-pattern not in the registries → PROMOTE it to the owning global registry per REGISTRY-PROTOCOL.md (lens → lens-registry.md, archetype → theme-archetypes.md, axis/pattern → axes-registry.md) so future countries inherit it. The per-registry promotion test (e.g. an axis must surface tours no existing axis finds; an archetype must recur across ≥2 countries) is owned by REGISTRY-PROTOCOL.md and the discovery-loop doc — apply it, don't restate it.
5. Write a changelog entry to `<country>/ledger.md` describing what DISCOVERY reshaped. This loop catches genuinely new operators, themes, and market entrants.
6. **Capture leads:** both loops read live pages — emit typed leads to `<country>/leads.md` with provenance for any tangential signal (a refetch revealing a guide change that links themes, a new sub-tour, a seasonality/access quirk; a re-discovery surfacing a channel/affinity/archetype/authority signal), per `REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING. Route each; new-coverage leads dirty the affected unit.

## PROCEDURE — Stamping (enables mechanical refresh)
1. Ensure every row carries `last_checked: YYYY-MM-DD` and `status`.
2. Any row whose `last_checked` is older than the `VERIFY_CADENCE` window auto-flags `stale`.
3. `stale` rows head the next VERIFY queue.
4. Keep the UNVERIFIED / fetch-blocked list current at all times.

## PROCEDURE — Season roll (this doc owns it)
1. `CURRENT_SEASON` (travel-config.md) is the prioritised departure window. Roll it forward in ONE place — travel-config.md — once per cycle. No other doc edits the season.
2. After a roll, products dated only to the prior season lose `FULL_PRODUCT_WEIGHT` standing on the next VERIFY pass: an out-of-season-only or now-UNVERIFIED-date product counts at `PARTIAL_PRODUCT_WEIGHT` (travel-config.md) until a current-season dated departure is re-confirmed.

## PROCEDURE — Embedding it (automation)
1. The `VERIFY_CADENCE` pass is a **scheduled cloud agent (cron routine)**: it reads corpus URLs, re-fetches, posts a diff report unattended.
2. The `DISCOVERY_CADENCE` pass is likewise schedulable.
3. Until scheduled, run both on demand using the prompts in the orchestration doc.
4. Recommended wiring: wire the `VERIFY_CADENCE` routine once the first country's rankings lock; re-rank countries (country-ranking doc) on `RERANK_CADENCE` (travel-config.md), i.e. when the new `tier=primary` ranking source in sources-registry.md lands.

## DECISION RULES
- Run Loop A (VERIFY) IFF the `VERIFY_CADENCE` window elapsed OR a booking window is imminent.
- Run Loop B (DISCOVERY) IFF the `DISCOVERY_CADENCE` window elapsed OR a trigger fires (below). Do NOT run DISCOVERY when VERIFY suffices — DISCOVERY is the expensive loop.
- A row's `status = stale` IFF `last_checked` is older than the `VERIFY_CADENCE` window → it heads the next VERIFY queue.
- A slice reshapes the corpus in Loop B IFF it clears the `ADMISSION_BAR` (admission-bar doc).
- On a new lens/archetype/operator-pattern → PROMOTE to the owning global registry per REGISTRY-PROTOCOL.md (no silent local-only knowledge).
- Re-rank countries (country-ranking doc) IFF a new `tier=primary` ranking source has landed (`RERANK_CADENCE`).
- Roll `CURRENT_SEASON` in travel-config.md once per cycle here; nowhere else.

### Triggers — run Loop B off-cadence if any holds (open — append on discovery; REGISTRY-PROTOCOL.md)
- New UNESCO inscription, or a major site/museum reopening. (provenance: Italy build)
- A new excavation or discovery that spawns tours. (provenance: Italy build)
- An anniversary / jubilee year that creates one-off departures (e.g. 2025 Catholic Jubilee, 800th St Francis 2026). (provenance: Italy build)
- A major operator launch or closure. (provenance: Italy build)
- A new axis/lens/channel/archetype PROMOTED in any country (cross-country INVALIDATION, `REGISTRY-PROTOCOL.md`): prior finished countries were swept on a smaller set → mark them `dirty` and re-sweep the new entry on the next DISCOVERY pass. Eventual-consistency: lazy here by cost trade-off, not immediate. (provenance: L19)

## EXAMPLE (input → output)
Input: locked `italy/italy_corpus_FINAL.md` with rows stamped `last_checked: 2026-06-22` (Italy roster cited per-country in `italy/` artifacts; this global doc stays example-light).
- Loop A (one `VERIFY_CADENCE` step on): re-fetch each row's URL. Row for an Etruscan-circuit operator shows a price increase and a withdrawn September departure; another operator added a new October departure. → update those rows' fields, advance `last_checked`, set `status: verified` (price/date rows) and `status: withdrawn` (the pulled departure); log all three diffs to the dated `italy/italy_verify_<YYYY-MM-DD>.md`. A fetch-blocked operator URL stays on the UNVERIFIED list.
- Trigger fires (2026, 800th anniversary of St Francis): run Loop B off-cadence. Re-run the completeness-critic across the baseline axes in axes-registry.md; new Franciscan-pilgrimage one-off departures clear the `ADMISSION_BAR` (admission-bar doc) → run a full round for the Umbria pilgrimage slice, reshape the corpus, and write a changelog entry to `italy/italy_changelog.md`. If "Sacred / pilgrimage circuit" were a not-yet-registered archetype, PROMOTE it to theme-archetypes.md per REGISTRY-PROTOCOL.md so the next country inherits it.

## ANTI-PATTERNS (checks — fail the step if true)
(open — append the check when a new lesson lands; tag Lnn. This block is a VIEW of 10-lessons-log.md; REGISTRY-PROTOCOL.md "Anti-patterns are a view of the lessons-log".)
- Treating the corpus as a static one-off deliverable (it rots — dates/prices/departures churn). (L8)
- Running the expensive DISCOVERY loop when the cheap VERIFY loop suffices. (L8)
- Re-fetching without diffing/stamping (no record of what changed or when last checked). (L8)
- Letting `stale` or UNVERIFIED rows accumulate unworked between passes. (L8)
- Holding corpus state in session memory instead of reading/writing the committed corpus file (violates the memory invariant — a fresh session could not reproduce the refresh). (L15)
- Discovering a new lens/archetype/operator in Loop B and not promoting it to the owning global registry (no compounding). (L15)
- Rolling `CURRENT_SEASON` in any doc other than travel-config.md, or in more than one place per cycle (drift). (L16)
