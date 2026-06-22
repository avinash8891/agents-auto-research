# Travel-Config — named dials (single source of truth)

All tunables live here once. Docs reference the NAME, never the literal. Change a value here → it propagates. No magic numbers in prose anywhere else.

## SCOPE
- `CURRENT_SCOPE_N` = 10 — countries researched now (pilot; prove the method before scaling).
- `GROWTH_LADDER` = 10 → 50 → 100 → 150 — raise only when confident; refresh is additive (`01`).
- `TARGET_SCALE` = example target, NOT a fixed contract. "50" anywhere is illustrative.

## RANKING / ADMISSION
- `RANK_DEPTH` = 5 — Top-N list length. **Ceiling, not a quota** (list fewer if fewer clear the bar; never pad).
- `ADMISSION_BAR` = 2.0 — credited products required to admit a theme.
- `MIN_CREDENTIALED_PRODUCTS` = 2 — also the registry promotion bar (`REGISTRY-PROTOCOL.md`).
- `FULL_PRODUCT_WEIGHT` = 1.0 — a product with named guide AND confirmed current-season dated departure.
- `PARTIAL_PRODUCT_WEIGHT` = 0.5 — UNVERIFIED-date or unnamed-guide product. A near-miss total below `ADMISSION_BAR` (e.g. one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT`) fails → THIN-NOTE.

## TRIP / THEME SHAPE
- `MAX_TRIP_DAYS` = 21 — a theme/trip must fit one trip under this.
- `MIN_LENSES_PER_TRIP` = 2, `MAX_LENSES_PER_TRIP` = 4 — composition bounds (`11`).

## SEASON / CADENCE (roll forward per run)
- `CURRENT_SEASON` = 2026–2027 — the prioritised departure window. Roll in ONE place per cycle (`08`).
- `VERIFY_CADENCE` = monthly + before each booking window.
- `DISCOVERY_CADENCE` = quarterly or on-trigger.
- `RERANK_CADENCE` = annual (when the new UN Tourism Barometer lands).

## IDENTIFIERS
- `THEME_ID_GRAMMAR` = `<CC>-NN` — 2-letter country code + sequential number; SPLIT keeps parent number + lowercase suffix (`IT-05a`/`IT-05b`); assigned at seed, never renumbered (`06`).
- `THEME_ID_OVERFLOW` = if a country exceeds 99 themes, widen to 3 digits (`IT-100`); never recycle a retired ID.

## CONVERGENCE
- `THEME_CONVERGED` = a fresh adversarial completeness-critic admits 0 themes clearing `ADMISSION_BAR`.
- `OPERATOR_CONVERGED` = every BASELINE axis (`axes-registry.md`) returns dry for the theme.
- Country DONE = both, across all baseline axes (count derived from the registry, never asserted as a literal).

## NOTES
- Numbers here are tunables, not law — but changing one is a config edit, not a prose hunt.
- Anything that is an enumeration (axes, lenses, archetypes, channels, sources, aliases) is NOT here — it lives in its own registry under `REGISTRY-PROTOCOL.md`.
