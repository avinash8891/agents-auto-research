# Country Ranking

Last checked: 2026-06-22
Metric: international tourist arrivals, overnight visitors, millions.

| rank | country | arrivals | metric | data-year | source | last_checked | contested? | in-scope? |
|------|---------|----------|--------|-----------|--------|--------------|------------|-----------|
| 5 | Italy | 57.9M | international tourist arrivals (overnight) | 2024 | UN Tourism World Tourism Barometer data via `https://www.untourism.int/un-tourism-world-tourism-barometer-data`; cross-check `https://en.wikipedia.org/wiki/World_Tourism_rankings` | 2026-06-22 | no for Italy row; tail ranks remain contested | yes |
| 9 | Japan | 36.9M | international visitor arrivals | 2024 | JNTO statistics via `https://statistics.jnto.go.jp/en/graph/`; cross-check `https://en.wikipedia.org/wiki/World_Tourism_rankings` | 2026-06-22 | yes; tail ranks 7-10 vary by source/metric | yes |

Notes:
- Step 2 scoped this run to IT-01, so only the Italy row needed by the output contract was materialized.
- The primary UN Tourism Barometer page confirms the source family; the public cross-check table gives Italy as 57.9M in 2024.
- Japan added for the first non-Western ranking run; rank is the 2024 cross-check table position, with tail-rank caveat preserved.
