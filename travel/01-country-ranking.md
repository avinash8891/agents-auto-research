# 01 — Country Ranking (the top-N)

AGENT SPEC. Build/refresh the ordered list of top-N most-visited countries; it sets the work order and the arrivals rank cited in every output.

INPUT: none (live web sources) + current `country_ranking.md` if it exists.
OUTPUT: `country_ranking.md` — stamped table, columns: `rank · country · arrivals · metric · data-year · source · contested? · in-scope?`.
NEXT: `02` seeds themes for each `in-scope` country.

## N IS A DIAL (not a constant)
- "50" in the brief is an example target, not fixed scope.
- **Current scope: TOP 10 only** — fine-tune the method on 10, scale only when 100% confident.
- Then grow 50 → 100 → 150. Never hardcode 50; treat N as a parameter.

## PROCEDURE
1. Pull latest **UN Tourism (UNWTO) Barometer / World Tourism rankings** from a live source.
2. Cross-check ≥3 sources (UN Tourism, Wikipedia "World Tourism rankings" collation, Statista) — secondary sources lag and disagree.
3. Use metric = **international tourist arrivals (overnight)**. Not receipts, not same-day. Record the metric on every row.
4. Apply the **country definition** (DECISION RULES) consistently — decide SAR/territory handling (e.g. HK/Macau vs China).
5. Order by arrivals; take the top-N for current scope; mark each row `in-scope` (within N) or not.
6. Where a rank is contested (≈#7–15 band and the N-boundary), record BOTH values + set `contested?`.
7. **Merge ADDITIVELY** into `country_ranking.md`: add newly-qualifying countries; NEVER remove a country already present (even if it dropped out of top-N). Coverage only grows.
8. Stamp each row with `data-year` + `source` + `last_checked`. Write file. Stop.

## DECISION RULES
- METRIC = international tourist arrivals (overnight) only; never mix with receipts/same-day across rows.
- COUNTRY DEFINITION = UN Tourism's own entity list; SAR/territory inclusion rule stated once and applied to all (HK/Macau materially change the tail).
- ADDITIVE REFRESH = add new; never remove existing. Raising N later = pure append.
- CONTESTED = if sources disagree, record both + flag; never resolve silently.
- N-BOUNDARY = the rank-N cutoff itself churns year to year; list borderline countries so scope changes are visible.
- RANK CITATION = always carry `data-year` ("rank 5, 2024 data") so outputs don't drift on refresh.
- NO HAND-TYPING = populate from a live pull; never guess a rank or arrivals number.
- METRIC CAVEATS = arrivals aren't perfectly comparable across countries: land-border-heavy counts (Mexico, Turkey), historical China HK/Macau inclusion, EU methodology differences. Note known quirks beside the affected rows; don't treat a number as gospel.

## EXAMPLE (2024 data, captured 2026-06 — illustration only)
Top 10 (arrivals): 1 France 102.0M · 2 Spain 93.8M · 3 USA 72.4M · 4 Turkey 60.6M · 5 Italy 57.8M · 6 Mexico 45.0M · 7 UK ~39M / Germany 37.5M (contested) · 8 Germany / Japan 36.9M (contested) · 9 Greece 36.0M · 10 Thailand 35.5M.
Flag: ranks 7–10 swap between UK/Germany/Japan/Greece depending on arrivals vs overnight-stays — lock against the official Barometer before publishing.
Sources: UN Tourism Barometer; en.wikipedia.org/wiki/World_Tourism_rankings; statista.com (arrivals by country).

## ANTI-PATTERNS (failure checks)
- Quoting a single secondary blog as authoritative.
- Mixing metrics (arrivals vs receipts) across rows.
- Resolving a contested rank silently.
- Hardcoding N=50 (it's a dial; current scope is 10).
- Removing a country on refresh because it slipped out of top-N (refresh is additive).
- Counting territories/SARs inconsistently across runs.
- Hand-typing/guessing ranks instead of a live pull.

## REFRESH
Annually when the new UN Tourism Barometer lands (`08`), additively.
