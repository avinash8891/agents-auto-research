# 01 — Country Ranking (the 50)

## Purpose
Establish and lock the ordered list of the 50 most-visited countries by **international tourist arrivals**, latest **UN Tourism (UNWTO)** data. This sets the arrivals rank cited in every output and the work order.

## Method
1. Pull the latest UN Tourism Barometer / World Tourism rankings. Cross-check at least 3 sources (UN Tourism, Wikipedia "World Tourism rankings" collation, Statista) because secondary sources lag and disagree.
2. Use **international tourist arrivals** (overnight visitors) as the metric — not receipts, not same-day. State the metric explicitly.
3. Where sources disagree on a rank (common in the #7–15 band — arrivals vs overnight-stays methodology), record BOTH and flag the contested rank rather than picking silently.

## Worked result (2024 data, captured 2026-06)
Top 10 (arrivals):
1. France 102.0M · 2. Spain 93.8M · 3. United States 72.4M · 4. Turkey 60.6M · 5. Italy 57.8M · 6. Mexico 45.0M · 7. UK ~39M / Germany 37.5M (contested) · 8. Germany / Japan 36.9M (contested) · 9. Greece 36.0M · 10. Thailand 35.5M.

Flag: ranks 7–10 swap between UK / Germany / Japan / Greece depending on arrivals vs overnight-stays. Lock against the official UN Tourism Barometer before final publication.

Sources: UN Tourism Barometer; en.wikipedia.org/wiki/World_Tourism_rankings; statista.com (arrivals by country).

## Anti-patterns
- Quoting a single secondary blog as authoritative.
- Mixing metrics (arrivals vs receipts) across countries.
- Silently resolving a contested rank.

## Output
A stamped table: rank · country · arrivals · metric · source · `contested?` flag. Refresh annually when the new Barometer lands (see `08-freshness-and-updates.md`).
