# Global Tags Registry

Single home for the small controlled vocabularies used as row/theme tags, so writer (`corpus`) and consumer (`ranking`) docs never spell values independently (they had already drifted: `private-bespoke-year-round` vs `private/bespoke/year-round`). Protocol: `REGISTRY-PROTOCOL.md`. Docs reference a tag set by name; values live here only.

## row.status
| value | meaning |
|-------|---------|
| `verified` | named guide + confirmed `CURRENT_SEASON` dated departure + price, all from a live page |
| `UNVERIFIED` | operator/tour real, but guide and/or dated departure unconfirmed (incl. 403/404 kept via snippet) |
| `stale` | `last_checked` older than `VERIFY_CADENCE` window (`travel-config.md`) |

## format-class (load-bearing for rankability in `ranking`)
| value | rankability note |
|-------|------------------|
| `fixed-departure-group` | rankable on a dated departure directly |
| `private-bespoke` | private/bespoke/year-round; can't be admitted on the "dated departure" basis the same way; flag when mixed in a Top-`RANK_DEPTH` |
| `day-format` | day tour / stackable; not a single immersive itinerary; flag when mixed |
| `hybrid-course` | course/residency hybrid, not a classic escorted tour; flag when mixed |

Mixing format-classes in one ranked list → flag explicitly (`ranking`).

## theme.seed-tag (advisory, from `theme-seeding`)
| value | meaning |
|-------|---------|
| `theme` | a real candidate theme |
| `watch/leisure` | advisory tag — likely no expert-led market; discovery + `admission-bar` decide on live evidence and OVERRIDE this tag; never used to kill a theme at seed |

## theme.strength (seed guess, from `theme-seeding`)
| value | meaning |
|-------|---------|
| `Strong` / `Medium` / `Thin` | expected depth of the expert-led market (a guess; discovery confirms) |

## admission.disqualifiers (open — append on discovery; used by `admission-bar`)
Open enumerations (`REGISTRY-PROTOCOL.md`) of what does NOT count toward the admission bar. Append a case when discovered, with provenance.
- **non-qualifying product** = not a sold leisure tour: university course · master-class · lecture-residency · retreat · maker-workshop · day-activity · pilgrimage-without-study-content. (provenance: Italy R4)
- **non-qualifying expert** = not a credentialed theme-fit scholar: title-only (name unpublished) · generic licensed city guide · artisan/trifolao (unless the craft IS the theme). (provenance: Italy R4)
- **non-qualifying operator** = aggregator/marketplace reselling others (count the underlying operator; see `operator-aliases.md`).

## PROMOTION LOG
- (seed) Baseline tag vocabularies (row.status, format-class, theme.seed-tag, theme.strength) established.
- (Italy R4) admission.disqualifiers seeded (non-qualifying product/operator/expert).

## UPDATE
Mechanics: `REGISTRY-PROTOCOL.md`. Append a value when a new state is genuinely needed; never let two docs invent divergent spellings. The tag/value sets here are open enumerations — append-on-discovery with provenance (log additions above).
