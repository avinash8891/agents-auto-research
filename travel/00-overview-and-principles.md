# 00 — Overview, Scope & Output Contract

AGENT SPEC. Defines the deliverable, the unit of work, ranking criteria, and the output contract. Read first — every other doc references these definitions. All tunable values are named dials in `config` (travel-config.md); enumerations live in their registries (`registry-protocol`, REGISTRY-PROTOCOL.md). This doc references names and registries, never literals. Sibling docs are cited by slug, resolved via `manifest` (doc-manifest.md).

INPUT: the named dials in `config`; the controlled vocabularies in `lens-registry`, `axes-registry`, `channel-registry`.
OUTPUT: the shared definitions, ranking criteria, and per-theme output contract consumed by every downstream step.
NEXT: `country-ranking` (build the in-scope country list), then `theme-seeding`.

SCOPE: the most-visited countries, sized to `CURRENT_SCOPE_N` (`config`; current pilot), growing along `GROWTH_LADDER` toward the illustrative `TARGET_SCALE`. See `country-ranking`.
UNIT: a **theme** (single-lens regional experience), not a country.
DELIVERABLE: a ranked **Top-`RANK_DEPTH` tours** list per theme (`config`), expert-led, depth-first, value-justified.

## DEFINITIONS
- **THEME** = one coherent subject, doable as a single trip within `MAX_TRIP_DAYS` (`config`). May span multiple **eras** (Sicily: Greek→Roman→Arab-Norman→Baroque) or **regions** (Etruscan: Lazio+Tuscany+Umbria). MUST NOT bundle multiple lenses.
- **LENS** = the subject type. The sole controlled vocabulary is `lens-registry` (lens-registry.md) — never re-list lenses here.
- **SINGLE-LENS rule**: different lenses → different themes (Sicily layered civilisations ≠ Etna food & wine).
- **TWO CONSUMPTION MODES**:
  - Group-tour ranking = single-lens (this method's unit; where expert depth lives).
  - Trip composition = multi-lens, built by combining ranked themes (`composition`); whole-trip expert depth recovered only per-segment or via a bespoke designer (the `luxury-bespoke` channel in `channel-registry`).
- **FIRST-TRIP-REPRESENTATIVE**: within a theme, favour what is iconic and representative for a first-time visitor, done with real depth. Not obscure hyper-niche sub-specialisms.
- **GRANULARITY**: big, diverse countries get many themes; small/single-note destinations get one or two. Match each theme to what the region is genuinely best known for.

## THEME DESIGN RULES
- **Non-overlapping by tour product**: a region with two genuine lenses (Tuscany = Renaissance art AND wine/food) gets two themes — different tours, no double-count.
- **Cover every region**: a region with zero themes must be explicitly tested-and-justified (`thin/none`), not assumed empty (`theme-seeding`).
- **Single-lens** (see DEFINITIONS): eras/regions may span; lenses split.

## EXAMPLE (themes = focused regional subjects, not whole countries)
- India → South Indian temple trail (Tamil Nadu) · North Indian Mughal history (Delhi–Agra–Rajasthan) · Kerala food & backwaters · Ladakh Buddhist culture · Varanasi–Ganges spiritual.
- Italy → Rome & classical antiquity · Florence/Tuscany Renaissance art · Sicily layered civilisations · Naples–Pompeii · Venetian art & lagoon · Emilia-Romagna food. (Full Italy roster lives in the per-country files under `italy/`.)
- Kenya → regional safari circuits (Masai Mara vs Amboseli/Tsavo) led by top naturalist guides — wildlife is the standout lens, not archaeology.

## RANKING CRITERIA (priority order)
1. **Expert guide fit** — genuinely expert, theme-appropriate, highly regarded leader (real historian/Egyptologist/naturalist/food or religion specialist). NOT a figurehead or generic coach guide.
2. **Depth** within the theme — beneath the surface, not a checklist.
3. **Small / authentic / locally connected** — private-departure option a plus.
4. **Value for money (tie-break)** — price is not a barrier; the best wins even if pricier. But cost MUST be justified by depth/expertise; do NOT reward luxury for its own sake. Comparable excellence → better value ranks higher. Flag premium-for-thin-substance.

CONSTANT TEST: *"best for a first-time visitor to this theme/region — deep, expert-led, authentic, with cost justified by what it delivers."*

## OUTPUT CONTRACT (per country → per theme)
Emit, per theme:
- Theme **ID** (`THEME_ID_GRAMMAR`, e.g. `IT-01`; overflow per `THEME_ID_OVERFLOW`; convention owned by `corpus`) + country (with arrivals rank + data-year) + theme/region + one-line capture statement.
- A ranked **Top `RANK_DEPTH`** — `RANK_DEPTH` is a **ceiling, not a quota** (`config`): list fewer if fewer clear `ADMISSION_BAR`; never pad. Each entry: operator · tour name · guide/expertise · group size · duration · price · value note · depth/access feature · source URL.
- **Price**: operator's listed currency (stated) + rough **USD-equivalent** (like-for-like comparison).
- **Format-class flag**: if the Top-`RANK_DEPTH` mixes fixed-departure group / private-bespoke / day-format, flag it (`ranking`).
- One line: **why #1 wins**.
- **FLAG** any tour whose leader or `CURRENT_SEASON` (`config`) departure is unverified — never guess.

## ADMISSION (owned by `admission-bar`; restated here only for the output contract)
- A theme is admitted IFF its credited-product total ≥ `ADMISSION_BAR` (`config`). A product with a named guide AND a confirmed `CURRENT_SEASON` dated departure scores `FULL_PRODUCT_WEIGHT`; an unverified-date or unnamed-guide product scores `PARTIAL_PRODUCT_WEIGHT`. A near-miss total below `ADMISSION_BAR` (e.g. one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT`) fails → THIN-NOTE, never padded to admission.
- Full admission mechanics, convergence, and the loop-until-dry rule live in `admission-bar`; do not duplicate them.

## HARD RULES
- Never invent guides, dates, prices, claims. Unverified → say so.
- Prioritise `CURRENT_SEASON` departures; the season **rolls** — re-baseline on the cadence owned by `freshness` (`VERIFY_CADENCE` / `DISCOVERY_CADENCE` / `RERANK_CADENCE` in `config`).
- Weak theme → say so + give closest strong fits; do NOT pad.
- Cite sources; every factual claim traces to a live URL.

## ANTI-PATTERNS (failure checks)
- A theme bundles >1 lens to look comprehensive.
- Padding a Top-`RANK_DEPTH` list beyond the number that clear `ADMISSION_BAR`.
- Any invented/guessed guide, date, or price.
- Rewarding price/luxury not justified by depth.
- Mixed currencies with no USD-equivalent.
- Selling a multi-lens itinerary as a single-expert group tour (that's `composition`, with the trade-off stated).
- Naming a literal (count, day-limit, price-tier, season, cadence) inline instead of its `config` dial, or re-listing a registry vocabulary (lenses, channels, axes) instead of pointing to the registry.
