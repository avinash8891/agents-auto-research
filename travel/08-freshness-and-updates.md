# 08 — Freshness & Updates

## Why
The corpus is a point-in-time snapshot. Departures sell out, dates move, prices change, tours are withdrawn, new operators launch. "Correct in June" is wrong by August unless refreshed. Two loops, different cost and cadence.

## Loop A — VERIFY pass (cheap; monthly + before each booking window)
URLs are already known, so this is re-fetch, not discovery.
1. Read every row's URL from the corpus.
2. Re-fetch each; **diff** against the stored row: date moved · price changed · tour withdrawn · new departure added · guide changed.
3. Update `last_checked`; set `status` (verified / stale / withdrawn); log diffs.
This is the answer to "new info after one month" — it catches the changes that matter for booking.

## Loop B — DISCOVERY pass (expensive; quarterly or on-trigger)
1. Re-run the final adversarial completeness-critic + a dry-check across all 5 axes.
2. Anything that clears the admission bar (`05`) → run a full round for that slice and reshape.
Catches genuinely new operators, themes, and market entrants.

## Triggers (run Loop B off-cadence)
- New UNESCO inscription or major site/museum reopening.
- A new excavation or discovery that spawns tours.
- Anniversary / jubilee years (e.g. 2025 Catholic Jubilee, 800th St Francis 2026) that create one-off departures.
- A major operator launch or closure.

## Stamping (enables mechanical refresh)
Every row carries `last_checked: YYYY-MM-DD` and `status`. Rows older than the cadence window auto-flag `stale` and head the next VERIFY queue. Keep the UNVERIFIED / fetch-blocked list current.

## Embedding it (automation)
The monthly VERIFY pass is a natural **scheduled cloud agent** (a cron routine): it reads corpus URLs, re-fetches, posts a diff report unattended. The quarterly DISCOVERY pass likewise. Until scheduled, run both on demand with the prompts in `09`. Recommended: wire the monthly VERIFY as a routine once the first country's rankings lock, and re-run country ranking (`01`) annually when the new UN Tourism Barometer lands.

## Anti-patterns
- Treating the corpus as a static one-off deliverable (it rots — dates/prices/departures churn).
- Running the expensive DISCOVERY loop when the cheap VERIFY loop suffices.
- Re-fetching without diffing/stamping (no record of what changed or when last checked).
- Letting `stale` or UNVERIFIED rows accumulate unworked between passes.

## Output
- A dated diff report per VERIFY pass (what changed).
- Updated corpus rows (new `last_checked`/`status`).
- A changelog entry when DISCOVERY reshapes anything.
