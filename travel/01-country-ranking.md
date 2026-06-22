# 01 — Country Ranking (the top-N)

## Purpose
Establish and lock the ordered list of the **top-N most-visited countries** by **international tourist arrivals**, latest **UN Tourism (UNWTO)** data. This sets the arrivals rank cited in every output and the work order.

## N is a dial, not a constant
"50" in the brief is an example target, not a fixed scope. N grows as the method proves out:
- **Current scope: TOP 10 only.** Deliberately small — we fine-tune the approach on 10 first and do not scale until 100% confident it works.
- **Then 50 → 100 → 150** once the protocol is trusted end-to-end.
Treat N as a parameter everywhere; never hardcode 50.

## Method
1. Pull the latest UN Tourism Barometer / World Tourism rankings. Cross-check at least 3 sources (UN Tourism, Wikipedia "World Tourism rankings" collation, Statista) because secondary sources lag and disagree.
2. Use **international tourist arrivals** (overnight visitors) as the metric — not receipts, not same-day. State the metric explicitly.
3. Where sources disagree on a rank (common in the #7–15 band and at the N-boundary — arrivals vs overnight-stays methodology), record BOTH and flag the contested rank rather than picking silently.
4. **Define "country" up front (reproducibility).** Use UN Tourism's own entity list; decide and state whether SARs/territories count separately — e.g. **Hong Kong and Macau** often rank apart from China, which changes the tail of the list. Apply the rule consistently.
5. **The N-boundary churns.** Countries near rank N swap year to year, and crossing the line decides whether a country is researched at all. Flag the borderline countries explicitly so scope changes are visible.

## Refresh is ADDITIVE (never shrink coverage)
On every run/refresh: fetch the current top-N, and **ADD any newly-qualifying countries**. **Never remove** a country already in the corpus, even if it drops out of the current top-N. Coverage only grows — a country once researched stays researched (its data just gets refreshed per `08`). This means raising N later is purely additive: re-run, append the new entrants, keep everything prior.

## Metric caveats (don't take a number as gospel)
Arrivals aren't perfectly comparable across countries: land-border-heavy counts (Mexico, Turkey), historical China HK/Macau inclusion, EU methodology differences. Note known quirks beside the affected rows. Cite each rank with its **data-year** ("rank 5, 2024 data") so outputs don't drift silently when ranks shift on refresh.

## Worked result (2024 data, captured 2026-06) — illustration only
Top 10 (arrivals):
1. France 102.0M · 2. Spain 93.8M · 3. United States 72.4M · 4. Turkey 60.6M · 5. Italy 57.8M · 6. Mexico 45.0M · 7. UK ~39M / Germany 37.5M (contested) · 8. Germany / Japan 36.9M (contested) · 9. Greece 36.0M · 10. Thailand 35.5M.

Flag: ranks 7–10 swap between UK / Germany / Japan / Greece depending on arrivals vs overnight-stays. Lock against the official UN Tourism Barometer before final publication.

Sources: UN Tourism Barometer; en.wikipedia.org/wiki/World_Tourism_rankings; statista.com (arrivals by country).

## Anti-patterns
- Quoting a single secondary blog as authoritative.
- Mixing metrics (arrivals vs receipts) across countries.
- Silently resolving a contested rank.
- **Hardcoding N=50** (it's a dial; current scope is 10).
- **Removing a country on refresh** because it slipped out of top-N (refresh is additive — never shrink).
- Counting territories/SARs inconsistently across runs.

## Output
The canonical list lives in a stamped data file `country_ranking.md` (not inline here — this doc holds the rules). Columns: rank · country · arrivals · metric · data-year · source · `contested?` · `in-scope?`. Populate from a live UN Tourism pull (never hand-typed/guessed). Refresh annually when the new Barometer lands (`08`), additively.
