# 08 — Freshness & Updates

AGENT SPEC. Keep a locked corpus correct over time. Two refresh loops (cheap VERIFY, expensive DISCOVERY) re-fetch known rows and re-discover the market on different cadences. The corpus is a point-in-time snapshot; "correct in June" is wrong by August unless refreshed.

INPUT: the locked corpus `<country>_corpus_FINAL.md` (rows carry URL, stored fields, `last_checked`, `status`) + the UNVERIFIED / fetch-blocked list. Loop B additionally READS the global registries `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md` and the admission bar from `05`. Country re-ranking READS the UN Tourism Barometer via `01`. Read everything from these committed files — do NOT carry corpus state in session memory.
OUTPUT: updated corpus rows (new `last_checked` + `status`) written back to `<country>_corpus_FINAL.md`; a dated diff report per VERIFY pass `<country>_verify_<YYYY-MM-DD>.md`; a changelog entry `<country>_changelog.md` when DISCOVERY reshapes anything; the refreshed UNVERIFIED / fetch-blocked list.
NEXT: booking/use-of-corpus consumes the freshest verified rows; DISCOVERY reshapes feed back through the round machinery (`04`/`05`) and the ID/audit trail (`06`); the prompts to run both loops live in `09`.

MEMORY INVARIANT: nothing freshness depends on lives in session memory. Every URL, stored field, `last_checked`, `status`, and the UNVERIFIED list are READ from the committed corpus and the diffs/changelog are WRITTEN back to committed files. A fresh session reproduces the same VERIFY/DISCOVERY result from the files alone — the corpus + registries are the source of truth, not recall.

COMPOUNDING: the corpus is a living ledger, not a one-off deliverable. Each VERIFY pass READS rows → re-fetches → APPENDS diffs + new stamps. Each DISCOVERY pass READS registries → re-runs the critic → on any new lens/archetype/operator, APPENDS it to the global registry (`lens-registry.md` / `theme-archetypes.md` / `axes-registry.md`) so future countries inherit it (PROMOTE), and APPENDS a per-country changelog entry. Knowledge accrues across passes and across sessions.

## PROCEDURE — Loop A: VERIFY pass (cheap; monthly + before each booking window)
URLs are already known, so this is re-fetch, not discovery.
1. READ every row's URL from the corpus.
2. Re-fetch each URL.
3. **Diff** the fetched page against the stored row. Diff dimensions: date moved · price changed · tour withdrawn · new departure added · guide changed.
4. Update `last_checked` to today (`YYYY-MM-DD`). Set `status` ∈ {verified, stale, withdrawn}.
5. Log every diff to the dated VERIFY report `<country>_verify_<YYYY-MM-DD>.md`.
6. Refresh the UNVERIFIED / fetch-blocked list (URLs that failed to fetch stay listed and worked next pass).
7. Write updated rows back to `<country>_corpus_FINAL.md`. This loop is the answer to "new info after one month" — it catches the changes that matter for booking.

## PROCEDURE — Loop B: DISCOVERY pass (expensive; quarterly or on-trigger)
1. READ `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md`.
2. Re-run the final adversarial completeness-critic + a dry-check across all 5 matrix axes.
3. For any slice that clears the admission bar (`05`): run a full discovery round for that slice and reshape the corpus.
4. If the pass surfaces a new lens / archetype / operator-pattern not in the registries → APPEND it to the relevant global registry (PROMOTE) so future countries inherit it.
5. Write a changelog entry to `<country>_changelog.md` describing what DISCOVERY reshaped. This loop catches genuinely new operators, themes, and market entrants.

## PROCEDURE — Stamping (enables mechanical refresh)
1. Ensure every row carries `last_checked: YYYY-MM-DD` and `status`.
2. Any row whose `last_checked` is older than the cadence window auto-flags `stale`.
3. `stale` rows head the next VERIFY queue.
4. Keep the UNVERIFIED / fetch-blocked list current at all times.

## PROCEDURE — Embedding it (automation)
1. The monthly VERIFY pass is a **scheduled cloud agent (cron routine)**: it reads corpus URLs, re-fetches, posts a diff report unattended.
2. The quarterly DISCOVERY pass is likewise schedulable.
3. Until scheduled, run both on demand using the prompts in `09`.
4. Recommended wiring: wire the monthly VERIFY as a routine once the first country's rankings lock; re-run country ranking (`01`) annually when the new UN Tourism Barometer lands.

## DECISION RULES
- Run Loop A (VERIFY) IFF the cadence window elapsed (monthly) OR a booking window is imminent.
- Run Loop B (DISCOVERY) IFF the quarterly cadence elapsed OR a trigger fires (below). Do NOT run DISCOVERY when VERIFY suffices — DISCOVERY is the expensive loop.
- A row's `status = stale` IFF `last_checked` is older than the cadence window → it heads the next VERIFY queue.
- A slice reshapes the corpus in Loop B IFF it clears the admission bar in `05`.
- On a new lens/archetype/operator-pattern → APPEND to the global registry (no silent local-only knowledge).
- Re-rank countries (`01`) IFF a new UN Tourism Barometer has landed (annual).

### Triggers — run Loop B off-cadence if any holds
- New UNESCO inscription, or a major site/museum reopening.
- A new excavation or discovery that spawns tours.
- An anniversary / jubilee year that creates one-off departures (e.g. 2025 Catholic Jubilee, 800th St Francis 2026).
- A major operator launch or closure.

## EXAMPLE (input → output)
Input: locked `italy/italy_corpus_FINAL.md` with rows stamped `last_checked: 2026-06-22`.
- Loop A (monthly, 2026-07): re-fetch each row's URL. Row for an Etruscan-circuit operator shows a price increase and a withdrawn September departure; another operator added a new October departure. → update those rows' fields, set `last_checked: 2026-07-22`, `status: verified` (price/date rows) and `status: withdrawn` (the pulled departure); log all three diffs to `italy/italy_verify_2026-07-22.md`. A fetch-blocked operator URL stays on the UNVERIFIED list.
- Trigger fires (2026, 800th anniversary of St Francis): run Loop B off-cadence. Re-run the completeness-critic across the 5 axes; new Franciscan-pilgrimage one-off departures clear the `05` admission bar → run a full round for the Umbria pilgrimage slice, reshape the corpus, and write a changelog entry to `italy/italy_changelog.md`. If "pilgrimage circuit" were a not-yet-registered archetype, APPEND it to `theme-archetypes.md` so the next country inherits it.

## ANTI-PATTERNS (checks — fail the step if true)
- Treating the corpus as a static one-off deliverable (it rots — dates/prices/departures churn).
- Running the expensive DISCOVERY loop when the cheap VERIFY loop suffices.
- Re-fetching without diffing/stamping (no record of what changed or when last checked).
- Letting `stale` or UNVERIFIED rows accumulate unworked between passes.
- Holding corpus state in session memory instead of reading/writing the committed corpus file (violates the memory invariant — a fresh session could not reproduce the refresh).
- Discovering a new lens/archetype/operator in Loop B and not appending it to the global registry (no compounding).
