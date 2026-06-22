# 01 — Country Ranking (the top-N)

AGENT SPEC. Build/refresh the ordered list of most-visited countries up to `CURRENT_SCOPE_N` (`config`); it sets the work order and the arrivals rank cited in every output.

INPUT: none (live web sources) + current `country_ranking.md` if it exists.
OUTPUT: `country_ranking.md` — stamped table, columns: `rank · country · arrivals · metric · data-year · source · contested? · in-scope?`.
NEXT: `theme-seeding` seeds themes for each `in-scope` country.

## N IS A DIAL (not a constant)
- Scope is `CURRENT_SCOPE_N` (`config`); `TARGET_SCALE` ("50" in the brief) is an illustrative target, not fixed scope.
- **Run at `CURRENT_SCOPE_N` now** — fine-tune the method on it, scale only when 100% confident.
- Then grow along `GROWTH_LADDER` (`config`). Never hardcode `TARGET_SCALE`; treat N as a parameter.

## PROCEDURE
1. Pull the latest ranking from `sources-registry` — rank on the `tier=primary` source.
2. Cross-check against the `tier=secondary` sources in `sources-registry` (count per `sources-registry` RULES) — secondary sources lag and disagree.
3. Use metric = **international tourist arrivals (overnight)**. Not receipts, not same-day. Record the metric on every row.
4. Apply the **country definition** (DECISION RULES) consistently — decide SAR/territory handling (e.g. HK/Macau vs China).
5. Order by arrivals; take the top `CURRENT_SCOPE_N` for current scope; mark each row `in-scope` (within N) or not.
6. Where a rank is contested (the band straddling the N-boundary), record BOTH values + set `contested?`.
7. **Merge ADDITIVELY** into `country_ranking.md`: add newly-qualifying countries; NEVER remove a country already present (even if it dropped out of top-N). Coverage only grows.
8. Stamp each row with `data-year` + `source` + `last_checked`. Write file. Stop.

## DECISION RULES
- METRIC = international tourist arrivals (overnight) only; never mix with receipts/same-day across rows.
- COUNTRY DEFINITION = the `tier=primary` source's own entity list; SAR/territory inclusion rule stated once and applied to all (HK/Macau materially change the tail).
- ADDITIVE REFRESH = add new; never remove existing. Raising N along `GROWTH_LADDER` later = pure append.
- CONTESTED = if sources disagree, record both + flag; never resolve silently. A `lag` other than low on the source (`sources-registry`) → treat the number as provisional, flag contested.
- N-BOUNDARY = the `CURRENT_SCOPE_N` cutoff itself churns year to year; list borderline countries so scope changes are visible.
- RANK CITATION = always carry `data-year` ("rank 5, 2024 data") so outputs don't drift on refresh.
- NO HAND-TYPING = populate from a live pull; never guess a rank or arrivals number.
- METRIC CAVEATS = arrivals aren't perfectly comparable across countries: land-border-heavy counts (Mexico, Turkey), historical China HK/Macau inclusion, EU methodology differences. Note known quirks beside the affected rows; don't treat a number as gospel.

## EXAMPLE (2024 data, captured 2026-06 — illustration only)
Top `CURRENT_SCOPE_N` (arrivals): 1 France 102.0M · 2 Spain 93.8M · 3 USA 72.4M · 4 Turkey 60.6M · 5 Italy 57.8M · 6 Mexico 45.0M · 7 UK ~39M / Germany 37.5M (contested) · 8 Germany / Japan 36.9M (contested) · 9 Greece 36.0M · 10 Thailand 35.5M.
Flag: the lower ranks swap between UK/Germany/Japan/Greece depending on arrivals vs overnight-stays — lock against the `tier=primary` source (`sources-registry`) before publishing.
Sources: see `sources-registry` (do not re-list inline).

## ANTI-PATTERNS (failure checks)
- Quoting a single secondary source as authoritative.
- Mixing metrics (arrivals vs receipts) across rows.
- Resolving a contested rank silently.
- Hardcoding N=`TARGET_SCALE` (it's a dial; current scope is `CURRENT_SCOPE_N`).
- Removing a country on refresh because it slipped out of top-N (refresh is additive).
- Counting territories/SARs inconsistently across runs.
- Hand-typing/guessing ranks instead of a live pull.

## REFRESH
On `RERANK_CADENCE` (`config`) when the new `tier=primary` Barometer lands (`freshness`), additively.
