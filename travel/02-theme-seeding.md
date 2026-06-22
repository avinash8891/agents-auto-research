# 02 — Theme Seeding (v0)

AGENT SPEC. Produce a provisional theme map from training knowledge, before discovery. Output is a grid to SEARCH, not an answer to confirm.

INPUT: one country (from `01` in-scope list).
OUTPUT: `<country>_theme_map_v0.md` — table of seed themes, each with ID, region(s), lens, capture line, strength, open-questions.
NEXT: `03` (coverage matrix) + `04` (discovery loop) consume this file.

## PROCEDURE (start = country)
1. Take the country.
2. List every first-level admin region.
3. For each region, determine what it is genuinely best known for → one or more **lenses** (history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic heritage · military · music · wildlife · geology · gardens · maritime · literary · crafts).
4. Emit one **candidate theme per distinct subject** (single-lens). A region with two distinct lenses emits two themes.
5. Emit **cross-regional themes** for subjects operators sell as one trip spanning regions.
6. Assign each theme an **ID** = `<2-letter country code>-NN` (`IT-01`, `IT-02`…). Sequential. Never renumbered later.
7. Assign each theme a **strength guess**: Strong | Medium | Thin (expected depth of expert-led market).
8. For any region that yields no theme, emit an explicit `thin/none` row (gap stays visible).
9. Write all rows to `<country>_theme_map_v0.md`. Stop. Discovery (`04`) reshapes from here.

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
- Seed contains a candidate-operator list (it must list themes/cells to SEARCH, not operators to confirm — anchoring, L1/L7).
- A theme bundles >1 lens to look comprehensive.
- A region is silently omitted (must be a theme or a `thin/none` row).
- A theme ID is reused or renumbered.
- A round of discovery only confirms the seed and adds nothing (the seed was wrong at the edges by design — extend it).
