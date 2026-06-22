# 02 — Theme Seeding (v0)

AGENT SPEC. Produce a provisional theme map from training knowledge, before discovery. Output is a grid to SEARCH, not an answer to confirm.

Seeding uses only the **axes tagged `stage:seed`** in `axes-registry.md` — region × lens × channel sanity-check. Axes tagged `stage:discovery`-only (e.g. the `axis-proof` pair) are **discovery-only** tools (`coverage-matrix`/`discovery-loop`), not used at seed time. Filter by tag; never name axes by hand, and never assert an axis count — derive it from the registry.

INPUT: one country (from `country-ranking`'s in-scope list) + the global registries `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md` (read them — do NOT seed from memory) + any `<country>/leads.md` rows routed to seeding (theme/sub-lens/archetype hints, per `REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING — a re-seed may be triggered by leads from a prior run's verification).
OUTPUT: `<country>_theme_map_v0.md` — table of seed themes (ID, region(s), lens, capture line, strength, open-questions) + a **seed-completeness diff** section.
NEXT: `coverage-matrix` + `discovery-loop` consume this file.

MEMORY INVARIANT (steps `overview`–`coverage-matrix`): nothing the method depends on lives in session memory. Country list, lenses, theme-archetypes, axes, seed themes — all READ from committed files and APPENDED back. A fresh session reproduces the same seed from the files alone. Training knowledge proposes; the registries + corpus are the source of truth.

## PROCEDURE (start = country)
1. Take the country. READ `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md`.
2. READ/confirm the first-level admin regions from `<country>/axes.md` (the `region`-axis values); for a fresh country where the file/section is absent, first-populate it by enumerating the country's first-level admin regions (the one deterministic axis) and WRITE them there. Append any new region on discovery — do not keep the set only in memory.
3. For each region, determine what it is genuinely best known for → one or more **lenses** taken from `lens-registry.md` (baseline + candidates). Do not use an inline/remembered lens list — the registry is the source of truth.
4. **Archetype walk**: go through `theme-archetypes.md` and ask, for this country, "does it have a strong version of this archetype?" Instantiate matches as candidate themes; note misses. (Catches patterns free-recall skips — e.g. "wine region", "wildlife circuit", "pilgrimage circuit".)
5. Emit one **candidate theme per distinct subject** (single-lens). A region with two distinct lenses emits two themes.
6. Emit **cross-regional themes** for subjects operators sell as one trip spanning regions.
7. **Channel sanity-check (ADVISORY — tag, never kill)**: for each candidate theme, ask whether ≥1 provider channel (`channel-registry.md` ids) plausibly sells it expert-led. This is the `channel` axis used in its `stage:seed` advisory mode (its full sweep is `stage:discovery`); cheap triage to steer sweep effort, NOT a decision. Rules: TAG (don't delete) the obviously-pure-leisure (beaches, nightlife, shopping, generic scenery) as `watch/leisure` (`tags-registry.md` theme.seed-tag); discovery still checks tagged themes lightly. WHEN IN DOUBT, keep it a full theme. NEVER kill a theme here from memory — that is the anchoring sin (L1/L7); "Apennine wildlife" looked operator-less from memory yet discovery found Steppes/Exodus under the `special-interest` channel. The real arbiter is discovery + the admission bar (`admission-bar`) on LIVE evidence, which OVERRIDES this tag.
8. Assign each theme an **ID** per `THEME_ID_GRAMMAR` (`travel-config.md`). Sequential. Never renumbered later. Past the per-country ceiling, apply `THEME_ID_OVERFLOW`.
9. Assign each theme a **strength guess** per `tags-registry.md` theme.strength (a guess; discovery confirms).
10. Record **open questions** per theme (candidate splits/merges/cross-cuts discovery must resolve, e.g. "split Sicily?", "is Etruscan standalone?") in the `open-questions` column.
11. For any region that yields no theme, emit an explicit `thin/none` row (gap stays visible).
12. **Seed-completeness diff (enumerate-and-diff at seed time):** list every BASELINE LENS (`lens-registry.md`) and every ARCHETYPE walked (`theme-archetypes.md`); for each, point to the seed theme that covers it OR a one-line `thin/none` justification. A baseline lens/archetype with neither = an unjustified gap → fix before writing. This is the check that would have caught "nature" at seed instead of discovery round 2.
13. If the country surfaces a new lens or archetype not in the registries → APPEND it to the per-country ledger and promote per `REGISTRY-PROTOCOL.md`, so future countries inherit it.
14. Write all rows + the completeness-diff section to `<country>_theme_map_v0.md` (group by macro-region; kept as the audit trail — `corpus`). Stop. `discovery-loop` reshapes from here.

## DECISION RULES
- SINGLE-LENS: a theme is one coherent subject. May span eras (Sicily: Greek→Roman→Arab-Norman→Baroque) or regions (Etruscan: Lazio+Tuscany+Umbria). MUST NOT bundle different lenses. Different lenses → different themes.
- SPLIT a region into N themes IFF the *tours differ by expert type* (art-historian-led ≠ sommelier-led).
- CROSS-REGIONAL theme allowed IFF operators sell it as one trip AND it does not double-count a region's flagship theme.
- MULTI-LENS trip ≠ seed theme. It is a composition-layer artifact (`composition`), never seeded here. Composition bounds (`MIN_LENSES_PER_TRIP`/`MAX_LENSES_PER_TRIP`, `travel-config.md`) live there, not here.
- DURATION: every theme must fit one trip under `MAX_TRIP_DAYS` (`travel-config.md`).
- ID STABILITY: assign per `THEME_ID_GRAMMAR` at seed time; on later SPLIT follow `THEME_ID_GRAMMAR` (`travel-config.md`); never renumber (`corpus`).

## EXAMPLE (input → output rows)
Input: `Italy`. Sample output rows:
| ID | region(s) | lens | capture | strength |
|----|-----------|------|---------|----------|
| IT-01 | Lazio | history/archaeology | Imperial Rome on the ground: Forum, Ostia, Tivoli | Strong |
| IT-03 | Tuscany | art | Florentine/Tuscan Renaissance masters | Strong |
| IT-04 | Tuscany | food/wine | Chianti/Brunello wine + Tuscan cooking | Strong |
| IT-06 | Lazio+Tuscany+Umbria | archaeology | Pre-Roman Etruscan civilisation (cross-regional) | Medium |
| — | Aosta Valley | (none) | thin/none — no expert-led theme surfaced | — |
Note IT-03 vs IT-04: same region, SPLIT because the tours differ (art-historian-led vs sommelier-led) — two themes, not one.
Full Italy v0 (19 seed themes): `italy/italy_theme_map_v0.md`. Discovery later took it to 35 (`italy/italy_theme_map_FINAL.md`) — the seed is meant to be outgrown.

## ANTI-PATTERNS (checks — fail the step if true)
This block is a VIEW of `10-lessons-log.md` (open — append the check when a new lesson lands; tag `Lnn`). Each check carries the `Lnn` that surfaced it where one exists.
- Seeding lenses/archetypes from memory instead of reading the registries (violates the memory invariant; reintroduces the omission risk that missed "nature"). (L15)
- Naming axes by hand or asserting an axis count instead of filtering `axes-registry.md` by `stage`/`role` tag and deriving the count. (L16)
- Skipping the archetype walk or the seed-completeness diff (these catch systemic misses at seed). (L15)
- A baseline lens/archetype left with neither a theme nor a `thin/none` justification. (L15)
- Discovering a new lens/archetype and not appending it to the per-country ledger / promoting per `REGISTRY-PROTOCOL.md` (no compounding). (L15)
- Killing a theme at seed via the channel sanity-check (it is advisory — tag `watch/leisure` only; when in doubt keep; discovery + `admission-bar` decide on live evidence). (L1/L7)
- Seed contains a candidate-operator list (it must list themes/cells to SEARCH, not operators to confirm — anchoring, L1/L7). (L1/L7)
- A theme bundles >1 lens to look comprehensive. (L11)
- A region is silently omitted (must be a theme or a `thin/none` row). (L15)
- A theme ID is reused or renumbered (violates `THEME_ID_GRAMMAR`). (L17)
- A round of discovery only confirms the seed and adds nothing (the seed was wrong at the edges by design — extend it). (L10)
