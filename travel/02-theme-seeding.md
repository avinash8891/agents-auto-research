# 02 — Theme Seeding (v0)

## Purpose
Produce a first-pass theme map for the country from training knowledge, BEFORE discovery. The seed is deliberately provisional — discovery will split/merge/add/demote it. Its job is to give the discovery loop a structured starting grid, not to be correct.

## The seed is a grid to SEARCH, not an answer to confirm
The seed comes from training memory, so it carries the anchoring risk that sank the early Italy run (L1/L7): treating the seed as the field and just confirming it. Guard against it:
- The seed lists **themes/cells to search**, NOT a candidate-operator list. Don't pre-name "the operators" and go verify them.
- Discovery must **extend past the seed** (new themes, splits, whole missing lenses — "nature" was a systemic miss precisely because it wasn't seeded). A round that only confirms the seed has failed.
- Expect the seed to be wrong at the edges; that's the point.

## Method
Enumerate themes across three of the five matrix axes (the other two — language, authority — are discovery tools, see `03`):
- **Region**: list every first-level admin region; ensure none is left without a candidate theme or an explicit "thin/none" note.
- **Lens**: for each region, ask which lenses it is genuinely best known for (history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic heritage · military · music · wildlife · geology · gardens · maritime · literary · crafts).
- **Channel** (sanity check): does at least one provider type plausibly sell this as an expert-led trip? If not, mark it a watch/leisure candidate.

## Rules
- **Assign a theme ID to each seed theme now** — `<2-letter country code>-NN` (e.g. `IT-01`), per the convention in `06`. IDs are assigned at seed time and never renumbered on reshape; splits append letters (`IT-05a/b`).
- **Single-lens only** (see `00`): each seed theme is one coherent subject. It may span eras or regions, but never bundle multiple lenses — those split into separate themes. Multi-lens trips are a composition layer (`11`), not a seed theme.
- Non-overlapping by tour product (see `00`).
- Split a region into multiple themes only when the *tours* differ (art-historian-led vs sommelier-led).
- Mark each seed theme: **Strong / Medium / Thin** (expected depth of expert-led market) and note candidate splits/merges/cross-cuts.
- Allow **cross-regional themes** when operators sell them as one trip (e.g. Etruscan Italy, Magna Graecia, a Caravaggio trail) — but only if they don't double-count a region's flagship.
- Keep every theme to a single trip < 21 days.

## Worked example (Italy v0)
19 seed themes + minors across Centre/South/Islands/North, with leisure-flags (Amalfi, Lakes, Cinque Terre) and explicit open questions (split Sicily? is Etruscan standalone? is Magna Graecia a cross-cut?). See `italy/italy_theme_map_v0.md` for the full v0; discovery later took it to 35 (see `italy/italy_theme_map_FINAL.md` and `10-lessons-log.md`) — proof the seed was meant to be outgrown.

## Output
`<country>_theme_map_v0.md`: themes grouped by macro-region, each with one-line capture statement, strength rating, and the open questions discovery must resolve. This file is the audit trail of where you started — keep it.
