# Global Axis Registry

The shared, evolving list of discovery axes. **Seeds every new country.** Per-country deviations live in `travel/<country>/axes.md` (see `coverage-matrix`, `corpus`). Mechanics (append-only, structure, update cycle): `REGISTRY-PROTOCOL.md`.

Axis = an independent dimension where failing to search it causes systematic (not random) misses.
Promotion bar = provably surfaces tours no existing axis finds (cite the tours).

Protocol (append-only, structure, update cycle): `REGISTRY-PROTOCOL.md`. Consuming docs filter axes by the `stage`/`role` tags below — they never hardcode which axes apply where, nor assert an axis count (derive it from this file).

## TAG VOCABULARY (multi-valued, growable)
- `stage`: `seed` (used at theme seeding `02`) · `discovery` (run in discovery sweeps `04`).
- `role`: `axis-proof` (gets its own dedicated sweep; the false-convergence gate) · `convergence-gate` (must be dry to declare operator-convergence) · `saturation-weight` (weighted at ranking saturation `07`).
New tag values may be added as the method grows.

## BASELINE AXES (active for all countries)
| id | axis | what | stage | role |
|----|------|------|-------|------|
| `channel` | Channel | provider type — sub-types in `channel-registry.md` (count derived there) | seed, discovery | convergence-gate |
| `lens` | Lens | subject type — vocabulary in `lens-registry.md` | seed, discovery | convergence-gate |
| `region` | Region | every first-level admin region as a checklist | seed, discovery | convergence-gate |
| `language` | Language | native + relevant study-travel source languages (per-country set is data in `<country>/axes.md`) | discovery | axis-proof, convergence-gate |
| `authority-index` | Authority-index | directories that list quality operators (awards, AITO/Virtuoso, university-alumni & museum travel partners, UNESCO) | discovery | axis-proof, convergence-gate |

Note: `channel` carries `stage:seed` only as the advisory sanity-check (`02`); its full sweep is `stage:discovery`.

## CANDIDATE WATCHLIST (test per country; promote on evidence)
| id | what it would catch | proposed tags | status | evidence so far |
|----|--------------------|---------------|--------|-----------------|
| `format-vehicle` | expedition-cruise, rail, gulet/sailing, walking-active culture operators | discovery; +composition | candidate | Peter Sommer gulets found by luck (Italy), not via an axis — suggests real |
| `affinity-audience` | alumni, religious, women-only, accessible/seniors, family operators | discovery; +composition | candidate | pilgrimage surfaced on Italy via lens/channel; affinity may be its own dimension |
| `media-creator` | TV/author experts selling private tours | discovery | candidate | Darius Arya found via his own site (Italy), no channel covered him |
| `season-time` | biennial / winter-only departures | discovery; +freshness | candidate | none yet |
| `price-tier` | ultra-luxury bespoke vs accessible expert-led | discovery; +ranking | candidate | partially covered by `luxury-bespoke` channel; unproven as standalone |

A promoted candidate MUST declare its `stage`/`role` tags in the PROMOTION LOG so the gates/sweeps pick it up automatically.

## PROMOTION LOG (axis → baseline, with evidence)
- (Italy R5) Language + Authority-index promoted from candidate to baseline — together surfaced 26 operators 4 prior English/operator-keyword rounds missed.

## UPDATE
Mechanics: `REGISTRY-PROTOCOL.md`. Axis-specific evidence to promote = the candidate provably surfaces tours no existing axis finds (cite them). The axis-completeness critic (`04`) tests candidates per country; results append to `travel/<country>/axes.md`; a pass promotes here with declared `stage`/`role` tags.
