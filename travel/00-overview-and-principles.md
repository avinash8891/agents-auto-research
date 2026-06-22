# 00 — Overview, Scope & Output Contract

AGENT SPEC. Defines the deliverable, the unit of work, ranking criteria, and the output contract. Read first — every other doc references these definitions. All tunable values are named dials in `config` (travel-config.md); enumerations live in their registries (`registry-protocol`, REGISTRY-PROTOCOL.md). This doc references names and registries, never literals. Sibling docs are cited by slug, resolved via `manifest` (doc-manifest.md).

The method constrains judgment and makes it auditable; it does not fully automate truth. Qualitative calls use the owner rubrics: theme/admission in `admission-bar`, reshape in `discovery-loop`, candidate ranking in `ranking`.

INPUT: the named dials in `config`; the controlled vocabularies in `lens-registry`, `axes-registry`, `channel-registry`.
OUTPUT: the shared definitions, ranking criteria, and per-theme output contract consumed by every downstream step.
NEXT: `country-ranking` (build the in-scope country list), then `theme-seeding`.

SCOPE: the most-visited countries, sized to `CURRENT_SCOPE_N` (`config`; current pilot), growing along `GROWTH_LADDER` toward the illustrative `TARGET_SCALE`. See `country-ranking`.
UNIT: a **theme** (single-lens regional experience), not a country.
DELIVERABLE: a ranked **Top-`RANK_DEPTH` tours** list per theme (`config`), expert-led, depth-first, value-justified.

## PROCEDURE (start = the named dials + controlled vocabularies; produce the shared contract a downstream step consumes)
1. READ the named dials in `config` and the controlled vocabularies in `lens-registry`, `axes-registry`, `channel-registry` — do NOT operate from memory.
2. For each in-scope country (sized to `CURRENT_SCOPE_N`; list built by `country-ranking`), emit the per-theme **OUTPUT CONTRACT** below — one ranked block per theme, applying the **DECISION RULES** (RANKING CRITERIA priority order, THEME DESIGN RULES, ADMISSION).
3. **FLAG** any tour whose leader or `CURRENT_SEASON` departure is unverified — never guess (see HARD RULES).
4. Hand off to `country-ranking` (build the in-scope country list), then `theme-seeding`.

## DEFINITIONS
- **THEME** = one coherent subject, doable as a single trip within `MAX_TRIP_DAYS` (`config`). May span multiple **eras** (Sicily: Greek→Roman→Arab-Norman→Baroque) or **regions** (Etruscan: Lazio+Tuscany+Umbria). MUST NOT bundle multiple lenses.
- **LENS** = the subject type. The sole controlled vocabulary is `lens-registry` (lens-registry.md) — never re-list lenses here.
- **SINGLE-LENS rule**: different lenses → different themes (Sicily layered civilisations ≠ Etna food & wine).
- **TWO CONSUMPTION MODES**:
  - Group-tour ranking = single-lens (this method's unit; where expert depth lives).
  - Trip composition = multi-lens, built by combining ranked themes (`composition`); whole-trip expert depth recovered only per-segment or via a bespoke designer (the `luxury-bespoke` channel in `channel-registry`).
- **FIRST-TRIP-REPRESENTATIVE**: resolved by the Theme Selection / Admission Rubric in `admission-bar`, not by free-form preference.
- **GRANULARITY**: resolved by `theme-seeding` plus the Theme Selection / Admission Rubric and the Reshape Rubric in `discovery-loop`; do not decide from country size alone.

## THEME DESIGN RULES
- **Non-overlapping by tour product**: a region with two genuine lenses (Tuscany = Renaissance art AND wine/food) gets two themes — different tours, no double-count.
- **Cover every region**: a region with zero themes must be explicitly tested-and-justified (`thin/none`), not assumed empty (`theme-seeding`).
- **Single-lens** (see DEFINITIONS): eras/regions may span; lenses split.

## EXAMPLE (themes = focused regional subjects, not whole countries)
- India → South Indian temple trail (Tamil Nadu) · North Indian Mughal history (Delhi–Agra–Rajasthan) · Kerala food & backwaters · Ladakh Buddhist culture · Varanasi–Ganges spiritual.
- Italy → Rome & classical antiquity · Florence/Tuscany Renaissance art · Sicily layered civilisations · Naples–Pompeii · Venetian art & lagoon · Emilia-Romagna food. (Full Italy roster lives in the per-country files under `italy/`.)
- Kenya → regional safari circuits (Masai Mara vs Amboseli/Tsavo) led by top naturalist guides — wildlife is the standout lens, not archaeology.

## RANKING CRITERIA (priority order)
1. **Expert guide fit** — resolved by the credential-fit and reputation rows in the `ranking` rubric.
2. **Depth** within the theme — resolved by the depth row in the `ranking` rubric.
3. **Small / authentic / locally connected** — private-departure option a plus. Operationalise with checkable signals sourced OFF the seller's page: in-country ownership/base, local guides employed-not-subcontracted, in-language operation.
4. **Value for money (tie-break)** — resolved by the value row in the `ranking` rubric; do NOT reward luxury for its own sake.

**[C4] INDEP_EVIDENCE** — criteria 1 (expert guide fit / highly regarded), 2 (depth), and 3 (small/authentic/locally connected) MUST each cite at least one source whose domain is NOT the seller's own (academic/museum/excavation affiliation, authored works / ORCID, learned-society or professional register, recognised oral-tradition / lineage / guild standing, OR a cross-platform review corpus). The seller's own domain is NEVER sole evidence for reputation/depth/authenticity → cap at PARTIAL + FLAG. (Mirrors the HARD RULE "every claim traces to a URL" but REQUIRES the URL not be the seller's domain for these three.)

CONSTANT TEST: a row or theme is publishable only when its owner rubric labels and gates support it.

## OUTPUT CONTRACT (per country → per theme)
Emit, per theme:
- Theme **ID** (`THEME_ID_GRAMMAR`, e.g. `IT-01`; overflow per `THEME_ID_OVERFLOW`; convention owned by `corpus`) + country (with arrivals rank + data-year) + theme/region + one-line capture statement.
- A ranked **Top `RANK_DEPTH`** — `RANK_DEPTH` is a **ceiling, not a quota** (`config`): list fewer if fewer clear `ADMISSION_BAR`; never pad. Each entry: operator · tour name · guide/expertise · group size · duration · price · value note · depth/access feature · source URL.
- **Price**: operator's listed currency (stated) + rough **USD-equivalent** (like-for-like comparison). **[C12] FX rule** (`config` + `sources-registry`): the USD-equivalent is a COMPUTED number — it MUST cite rate source + date, display as `~USD (rate dated YYYY-MM-DD)`, refresh on `VERIFY_CADENCE`, and NEVER come from recall. `FX_SOURCE` (e.g. ECB daily reference) is the named source.
- **Format-class flag**: if the Top-`RANK_DEPTH` mixes format-classes (`tags-registry.md`), flag it (`ranking`).
- One line: **why #1 wins**.
- **FLAG** any tour whose leader or `CURRENT_SEASON` (`config`) departure is unverified — never guess.

## ADMISSION (owned by `admission-bar`; restated here only for the output contract)
- A theme is admitted IFF its credited-product total ≥ `ADMISSION_BAR` (`config`). A product with a named guide AND a confirmed `CURRENT_SEASON` dated departure scores `FULL_PRODUCT_WEIGHT`; an unverified-date or unnamed-guide product scores `PARTIAL_PRODUCT_WEIGHT`. A near-miss total below `ADMISSION_BAR` (e.g. one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT`) fails → THIN-NOTE, never padded to admission.
- Full admission mechanics, convergence, and the loop-until-dry rule live in `admission-bar`; do not duplicate them.
- **[C9]** Convergence = search EXHAUSTION, NOT field completeness (owned by `admission-bar`): a convergence-gate axis left empty because the SOURCE base does not cover the region (not because the market is covered) does NOT count as satisfied — it raises a coverage-limitation FLAG and makes convergence PROVISIONAL.

## DECISION RULES
The checkable conditions a fresh agent applies are stated in full in the sections above; grouped here as the canonical decision set (do not restate the prose):
- **Rank** by the RANKING CRITERIA priority order using the candidate rubric in `ranking`; a pricier option wins only when the value row supports it.
- **Theme** per THEME DESIGN RULES: distinct lenses → distinct themes (single-lens); eras/regions may span but lenses split; a region with zero themes → explicitly test-and-justify, not assume empty.
- **Admit** a theme IFF its credited-product total ≥ `ADMISSION_BAR` (ADMISSION; mechanics owned by `admission-bar`); a below-bar near-miss → THIN-NOTE, never pad.
- **FLAG** per HARD RULES: if a leader or `CURRENT_SEASON` departure is unverified → say so; if a claim lacks a live URL → do not assert it.

## HARD RULES
- Never invent guides, dates, prices, claims. Unverified → say so.
- Prioritise `CURRENT_SEASON` departures; the season **rolls** — re-baseline on the cadence owned by `freshness` (`VERIFY_CADENCE` / `DISCOVERY_CADENCE` / `RERANK_CADENCE` in `config`).
- Weak theme → apply the Theme Selection / Admission Rubric; give THIN-NOTE or "best that exists"; do NOT pad.
- Cite sources; every factual claim traces to a live URL.

## ANTI-PATTERNS (failure checks)
View of `10-lessons-log.md` (open — append the check when a new lesson lands; tag Lnn).
- A theme bundles >1 lens to look comprehensive. (L11)
- Padding a Top-`RANK_DEPTH` list beyond the number that clear `ADMISSION_BAR`. (L6)
- Any invented/guessed guide, date, or price. (L1)
- Rewarding price/luxury not justified by depth.
- Mixed currencies with no USD-equivalent.
- Reputation, depth, or authenticity asserted from the seller's OWN page with no independent (non-seller-domain) source. (C4)
- A USD-equivalent shown with no dated rate source / recalled from memory instead of computed from `FX_SOURCE`. (C12)
- Selling a multi-lens itinerary as a single-expert group tour (that's `composition`, with the trade-off stated). (L11)
- Naming a literal (count, day-limit, price-tier, season, cadence) inline instead of its `config` dial, or re-listing a registry vocabulary (lenses, channels, axes) instead of pointing to the registry. (L16)
