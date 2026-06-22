# Global Theme-Archetype Library

Recurring theme patterns that appear across many countries. Seeding (`theme-seeding`) checks each archetype against the country — so seeds start from proven patterns, not only free recall (anti-anchoring, anti-omission).

Protocol (append-only, structure, update cycle): `REGISTRY-PROTOCOL.md`.
Archetype-specific promotion bar: a pattern recurs in **≥2 countries** with cited instances → BASELINE. One-country patterns sit in CANDIDATE.
Lens column = a **controlled-vocabulary reference**: every value MUST be an existing lens id in `lens-registry.md` (no free-text lenses).

Use: for each archetype, ask "does this country have a candidate match?" If yes → seed a candidate theme (give it the country's specifics). If no → note why not. `theme-seeding` proposes only; `admission-bar` decides PASS/WATCH/FAIL from live evidence.

## BASELINE ARCHETYPES (pattern → lens → instances)
Lens values below are single `lens-registry.md` ids (primary lens of the archetype).
| Archetype | Lens (registry id) | Example instances (≥2 countries) |
|-----------|--------------------|----------------------------------|
| Classical antiquity | archaeology | Italy (Rome), Greece, Turkey, Egypt |
| Ancient-civilisation archaeology | archaeology | Egypt (pharaonic), Mexico (Maya/Aztec), Peru (Inca), Italy (Etruscan) |
| Old-masters / Renaissance art | art | Italy (Florence), Netherlands, Spain |
| Sacred / pilgrimage circuit | religion/pilgrimage | Italy (Catholic), India (temple trails), Japan (Kumano Kodo) |
| Spiritual / sacred-river | religion/pilgrimage | India (Varanasi–Ganges), Tibet |
| Wine region | wine | Italy (Tuscany/Piedmont), France (Bordeaux/Burgundy), Spain, Argentina |
| Food region | food | Italy (Emilia-Romagna), Mexico (Oaxaca), Japan, Thailand |
| Wildlife / safari circuit | wildlife/nature | Kenya (Mara/Amboseli), Tanzania, India (tiger), Italy (Apennine) |
| Layered-civilisations | history | Italy (Sicily), Turkey, Spain (Andalucía) |
| Colonial / old-town heritage | architecture | Mexico, Spain, Portugal, India (Goa) |
| Imperial / dynastic capitals | history | Japan (Kyoto/Nara), China (Beijing/Xi'an), Turkey (Istanbul) |
| Tradition & living-culture | living-culture | Japan (Kyoto), India (Rajasthan) |
| Design / contemporary culture | design | Japan (Tokyo), Italy (Milan) |
| Desert / nomadic culture | living-culture | Egypt (Western Desert), Morocco, Jordan |
| Mountain / alpine nature & culture | wildlife/nature | Italy (Dolomites), Nepal, Switzerland |
| Volcanology / geology | geology/volcanology | Italy (Etna), Iceland, Indonesia |
| Jewish / diaspora heritage | ethnic heritage | Italy, Spain (Sefarad), Poland, Portugal |
| Military / battlefield | military | Italy (WWII Husky, WW1 Alpine), France (Normandy), Belgium (WW1) |
| Opera / classical music | music | Italy (Verona/Verdi), Austria, Germany |

## CANDIDATE ARCHETYPES (seen in 1 country; promote at ≥2)
| Archetype | Lens (registry id) | Instance |
|-----------|--------------------|----------|
| _(none yet — append as single-country patterns surface)_ | | |

## PROMOTION LOG
- (seed) Baseline archetypes established from the initial cross-country pattern scan.

## UPDATE
Mechanics: `REGISTRY-PROTOCOL.md`. Archetype-specific bar = recurs in ≥2 countries with cited instances (CANDIDATE→BASELINE). New single-country pattern → append to CANDIDATE. Archetypes are prompts, not quotas — seed a theme only if the country genuinely has it (else `thin/none`).
