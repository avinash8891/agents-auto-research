# Global Sources Registry

Authoritative data sources for country ranking (`country-ranking`) and verification. Tier/lag carried as tags, not prose. Protocol: `REGISTRY-PROTOCOL.md`. Docs say "prefer `tier=primary`", never re-list sources inline.

## SOURCES
| id | source | use | tier | lag |
|----|--------|-----|------|-----|
| `unwto-barometer` | UN Tourism (UNWTO) World Tourism Barometer | country arrivals ranking | primary | low |
| `wikipedia-wtr` | Wikipedia "World Tourism rankings" collation | cross-check | secondary | medium |
| `statista-arrivals` | Statista international arrivals by country | cross-check | secondary | medium |

## RULES
- Rank on `tier=primary`; corroborate with ≥2 `tier=secondary` (cross-check count from `travel-config` discipline).
- A `lag` other than low → treat the number as provisional; flag contested.

## CANDIDATE WATCHLIST (test per country; promote on evidence)
| id | source | use | why candidate |
|----|--------|-----|---------------|
| _(none yet)_ | | | append a candidate source when one surfaces (e.g. a national tourism board's own arrivals release); promote to SOURCES on corroboration |

## PROMOTION LOG
- (seed) Baseline three sources established for the country-ranking step.
