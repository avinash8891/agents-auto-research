# 02 — Theme Seeding (v0)

AGENT SPEC. Produce a provisional theme map from training knowledge, before discovery. Output is a grid to SEARCH, not an answer to confirm.

Seeding uses **3 of the 5 matrix axes** — region × lens × channel-sanity-check. The other two (language, authority-index) are **discovery-only** tools (`03`/`04`), not used at seed time.

INPUT: one country (from `01` in-scope list) + the global registries `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md` (read them — do NOT seed from memory).
OUTPUT: `<country>_theme_map_v0.md` — table of seed themes (ID, region(s), lens, capture line, strength, open-questions) + a **seed-completeness diff** section.
NEXT: `03` (coverage matrix) + `04` (discovery loop) consume this file.

MEMORY INVARIANT (steps 01–03): nothing the method depends on lives in session memory. Country list, lenses, theme-archetypes, axes, seed themes — all READ from committed files and APPENDED back. A fresh session reproduces the same seed from the files alone. Training knowledge proposes; the registries + corpus are the source of truth.

## PROCEDURE (start = country)
1. Take the country. READ `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md`.
2. List every first-level admin region.
3. For each region, determine what it is genuinely best known for → one or more **lenses** taken from `lens-registry.md` (baseline + candidates). Do not use an inline/remembered lens list — the registry is the source of truth.
4. **Archetype walk**: go through `theme-archetypes.md` and ask, for this country, "does it have a strong version of this archetype?" Instantiate matches as candidate themes; note misses. (Catches patterns free-recall skips — e.g. "wine region", "wildlife circuit", "pilgrimage circuit".)
5. Emit one **candidate theme per distinct subject** (single-lens). A region with two distinct lenses emits two themes.
6. Emit **cross-regional themes** for subjects operators sell as one trip spanning regions.
7. **Channel sanity-check**: for each candidate theme, confirm ≥1 provider channel (`03`, A–H) plausibly sells it **expert-led**. If none plausibly does, mark it `watch/leisure` — not a theme.
8. Assign each theme an **ID** = `<2-letter country code>-NN` (`IT-01`, `IT-02`…). Sequential. Never renumbered later.
9. Assign each theme a **strength guess**: Strong | Medium | Thin (expected depth of expert-led market).
10. Record **open questions** per theme (candidate splits/merges/cross-cuts discovery must resolve, e.g. "split Sicily?", "is Etruscan standalone?") in the `open-questions` column.
11. For any region that yields no theme, emit an explicit `thin/none` row (gap stays visible).
12. **Seed-completeness diff (enumerate-and-diff at seed time):** list every BASELINE LENS (`lens-registry.md`) and every ARCHETYPE walked (`theme-archetypes.md`); for each, point to the seed theme that covers it OR a one-line `thin/none` justification. A baseline lens/archetype with neither = an unjustified gap → fix before writing. This is the check that would have caught "nature" at seed instead of discovery round 2.
13. If the country surfaces a new lens or archetype not in the registries → APPEND it there (promotion), so future countries inherit it.
14. Write all rows + the completeness-diff section to `<country>_theme_map_v0.md` (group by macro-region; kept as the audit trail — `06`). Stop. Discovery (`04`) reshapes from here.

## DECISION RULES
- SINGLE-LENS: a theme is one coherent subject. May span eras (Sicily: Greek→Roman→Arab-Norman→Baroque) or regions (Etruscan: Lazio+Tuscany+Umbria). MUST NOT bundle different lenses. Different lenses → different themes.
- SPLIT a region into N themes IFF the *tours differ by expert type* (art-historian-led ≠ sommelier-led).
- CROSS-REGIONAL theme allowed IFF operators sell it as one trip AND it does not double-count a region's flagship theme.
- MULTI-LENS trip ≠ seed theme. It is a composition-layer artifact (`11`), never seeded here.
- DURATION: every theme must fit one trip < 21 days.
- ID STABILITY: assign at seed time; on later SPLIT, keep parent number + append letter (`IT-05a`/`IT-05b`); never renumber (`06`).

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
- Seeding lenses/archetypes from memory instead of reading the registries (violates the memory invariant; reintroduces the omission risk that missed "nature").
- Skipping the archetype walk or the seed-completeness diff (these catch systemic misses at seed).
- A baseline lens/archetype left with neither a theme nor a `thin/none` justification.
- Discovering a new lens/archetype and not appending it to the global registry (no compounding).
- Seed contains a candidate-operator list (it must list themes/cells to SEARCH, not operators to confirm — anchoring, L1/L7).
- A theme bundles >1 lens to look comprehensive.
- A region is silently omitted (must be a theme or a `thin/none` row).
- A theme ID is reused or renumbered.
- A round of discovery only confirms the seed and adds nothing (the seed was wrong at the edges by design — extend it).
