# 02 — Theme Seeding (v0)

## Purpose
Produce a first-pass theme map for the country from training knowledge, BEFORE discovery. The seed is deliberately provisional — discovery will split/merge/add/demote it. Its job is to give the discovery loop a structured starting grid, not to be correct.

## Method
Enumerate themes across three of the five matrix axes (the other two — language, authority — are discovery tools, see `03`):
- **Region**: list every first-level admin region; ensure none is left without a candidate theme or an explicit "thin/none" note.
- **Lens**: for each region, ask which lenses it is genuinely best known for (history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic heritage · military · music · wildlife · geology · gardens · maritime · literary · crafts).
- **Channel** (sanity check): does at least one provider type plausibly sell this as an expert-led trip? If not, mark it a watch/leisure candidate.

## Rules
- Non-overlapping by tour product (see `00`).
- Split a region into multiple themes only when the *tours* differ (art-historian-led vs sommelier-led).
- Mark each seed theme: **Strong / Medium / Thin** (expected depth of expert-led market) and note candidate splits/merges/cross-cuts.
- Allow **cross-regional themes** when operators sell them as one trip (e.g. Etruscan Italy, Magna Graecia, a Caravaggio trail) — but only if they don't double-count a region's flagship.
- Keep every theme to a single trip < 21 days.

## Worked example (Italy v0)
19 seed themes + minors across Centre/South/Islands/North, with leisure-flags (Amalfi, Lakes, Cinque Terre) and explicit open questions (split Sicily? is Etruscan standalone? is Magna Graecia a cross-cut?). See the project's `.context/italy_theme_map_v0.md` for the full v0; discovery later took it to 35 (see `10-lessons-log.md`).

## Output
`<country>_theme_map_v0.md`: themes grouped by macro-region, each with one-line capture statement, strength rating, and the open questions discovery must resolve. This file is the audit trail of where you started — keep it.
