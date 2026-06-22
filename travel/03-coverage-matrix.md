# 03 — The Coverage Matrix (the rigor core)

AGENT SPEC. Build a per-country search grid (5 baseline axes) so unknown-unknowns become *visible, provable gaps* — an empty matrix cell is a proven miss, not a silent one. Search the grid, never free-associated keywords.

INPUT:
- `<country>_theme_map_v0.md` (from `02`) — the seed themes/cells to search.
- Global registry `travel/axes-registry.md` — baseline 5 axes + candidate watchlist + axes proven cross-country. SEEDS every new country.
- Per-country ledger `travel/<country>/axes.md` — axes active for this country, prior promotions (with evidence), pending candidates. See `06`.
OUTPUT:
- Populated coverage matrix for `<country>` (cells = candidate operators/lenses/regions per axis intersection; empty cell = explicit gap).
- APPENDED axis findings in `travel/<country>/axes.md`; PROMOTED axes (if any pass the promotion test) in `travel/axes-registry.md`.
NEXT: `04` (discovery loop + axis-completeness critic) consumes the matrix and the ledger.

MEMORY INVARIANT: nothing the method depends on lives in session memory. The country list, seed map, axis set, promotions, and pending candidates are all READ from committed files and APPENDED back. A fresh session reproduces the same matrix from `axes-registry.md` + `<country>/axes.md` + the seed map alone. Training knowledge proposes; the registry + ledger are the source of truth.

> WHY (one failure that grounds this): searching only in English, only on operator names recalled from memory, produced a *false convergence*. A later sweep on two missing axes found 26 operators the prior four rounds missed. See `10-lessons-log.md`.

## PROCEDURE

1. At session start, READ `travel/axes-registry.md` + `travel/<country>/axes.md` (the starting knowledge) + `<country>_theme_map_v0.md`. The active axis set = baseline 5 ∪ country-promoted axes ∪ this country's pending candidates.
2. Use training knowledge to BUILD the axes and PRE-POPULATE candidate cells: name the likely operators/lenses/regions per cell. This is where latent knowledge adds most value.
3. For each axis below, enumerate its values and fill cells:
   - **Axis 1 CHANNEL** (provider type, 8 values A–H, see DECISION RULES).
   - **Axis 2 LENS** (experience type — prune the list to what is real per country).
   - **Axis 3 REGION** (every first-level admin region as a checklist).
   - **Axis 4 LANGUAGE** (native + study-travel-market languages; commonly missed).
   - **Axis 5 AUTHORITY-INDEX** (mine curated directories for operator names; commonly missed).
4. Use the WEB to populate and verify every cell — confirm candidates exist, run current departures, surface unknowns. Never rank on memory alone; every operator/guide/date/price enters only via a live source.
5. Run the LANGUAGE sweep with the segment's own idioms (Studienreise, conférencier, "con l'archeologo") — these surface operators no English query returns. Vary queries per theme (see Axis 4 query bank).
6. Run the AUTHORITY-INDEX sweep: enumerate the curated lists, then extract operator names from them (see Axis 5 source list).
7. Leave a cell EMPTY only as a deliberate, visible gap. A region with 0 themes must be explicitly tested-and-justified (carry the `thin/none` row from `02`).
8. Run the axis-completeness critic (`04`) against the filled matrix.
9. APPEND new axis findings to `travel/<country>/axes.md`.
10. If a candidate axis passes the promotion test (DECISION RULES), PROMOTE it to `travel/axes-registry.md` so future countries inherit it.
11. Hand the matrix + ledger to `04`. Stop.

Compounding cycle (read → run → append → promote): READ registry + ledger at start → RUN discovery + axis-completeness critic → APPEND findings to the country ledger → PROMOTE proven axes to the global registry. This works exactly like the lessons log — via git-committed files, not chat memory.

## AXIS VALUES (enumerations)

### Axis 1 — CHANNEL (provider type, 8)
- A. Specialist academic cultural/archaeology operators (e.g. Martin Randall, Andante, Peter Sommer, ACE, Ciceroni, Kirker).
- B. US learned-society / university / museum travel (AIA Tours, Smithsonian Journeys, Nat Geo Expeditions, Road Scholar, Far Horizons; alumni operators: Gohagan, AHI, Arrangements Abroad).
- C. In-country scholar-guide DMCs (Context Travel, and local PhD-guide outfits).
- D. Named individual experts (the genuine figurehead who personally guides).
- E. Credentialed-guide marketplaces (ToursByLocals, Withlocals; archaeologist-led-only on GetYourGuide/Viator).
- F. Luxury bespoke designers (Abercrombie & Kent, Scott Dunn, Butterfield & Robinson, scholar-founded DMCs e.g. Imago Artis).
- G. Lifelong-learning / continuing-ed (Road Scholar, university extension trips).
- H. Special-interest (food/wine, military history, nature/wildlife, music, religious/pilgrimage).

### Axis 2 — LENS (experience type)
history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic/Jewish heritage · military · music · wildlife · geology/volcanology · gardens · maritime · literary · cinema · crafts. Prune to what's real per country.

### Axis 3 — REGION (geography)
Every first-level admin region as a checklist. A region with 0 themes must be explicitly tested-and-justified.

### Axis 4 — LANGUAGE (native + study-travel source languages; commonly missed)
Search in the destination's language AND the major study-travel markets' languages. For Europe add **German** (Studienreisen — Studiosus, Gebeco, Karawane) and **French** (maisons culturelles — Intermèdes, Clio). For LATAM add Spanish/Portuguese; for East Asia the local language; etc. English-only search misses entire high-quality segments.

Query bank (what a real language sweep looks like — vary per theme):
- DE: `Studienreise <region> Archäologie 2026`, `Studienreiseleiter Italien Kunsthistoriker`, `wissenschaftliche Reiseleitung Sizilien`
- FR: `voyage culturel <region> conférencier`, `Étrusques voyage accompagné archéologue`, `voyage conférencier historien de l'art Italie`
- IT: `viaggi con l'archeologo <region>`, `viaggi studio archeologia`, `viaggi culturali accompagnati da storico dell'arte`

### Axis 5 — AUTHORITY-INDEX (mine directories, don't guess operators; commonly missed)
Enumerate the lists that already curate quality operators, then extract names:
- Awards: Condé Nast Traveller / Travel+Leisure / Wanderlust / Telegraph "best tour operator" winners.
- Membership directories: AITO, Virtuoso, ETOA, ASTA.
- University alumni travel-partner lists (Oxford/Cambridge/Ivy) — most productive single source in testing.
- Museum travel partners (the Met / British Museum / National Gallery / Art Fund) — best named-curator model.
- National tourism-board licensed-DMC registries.
- UNESCO site → specialist-operator cross-references; learned-society member trips.

## DECISION RULES

- LATENT-KNOWLEDGE SPLIT: training knowledge MAY build axes and pre-populate cells; it MUST NOT supply the final ranking. An operator/guide/date/price is admitted IFF a live source confirms it.
- GAP VISIBILITY: an empty cell is valid IFF it is recorded as an explicit gap (not silently skipped). A 0-theme region is valid IFF it is tested-and-justified with a `thin/none` row.
- LANGUAGE COVERAGE: for a European country the matrix is incomplete until DE and FR sweeps run; for LATAM until ES/PT; for East Asia until the local language. English-only → step fails.
- AXIS SET IS NOT FINAL — it is itself a convergence target. The 5 are current-best, empirically derived, NOT proven complete. L7 proved a smaller set (3 axes) gave a false convergence; the same can happen at 6. Do not treat "5" as axiomatic.
- WHAT QUALIFIES AS AN AXIS: an independent dimension where *failing to search it causes systematic (not random) misses*.
- PROMOTION TEST: a candidate becomes a real axis IFF it **provably surfaces tours no existing axis finds** (same evidence bar as a new theme/operator — show the tours it uniquely caught). Pass → APPEND to `<country>/axes.md` and PROMOTE to `axes-registry.md`.
- TAXONOMY CAVEAT (disambiguation): the current axes mix two kinds — *target-properties* (channel/lens/region = what the tour is) and *search-method* (language/authority = how you look). A future refactor may re-cut them; that is expected, not a defect.
- PER-COUNTRY + EVOLVING: the active axis set differs by country (an island nation needs Format/vehicle for cruises; a pilgrimage-heavy country needs Affinity) and keeps updating as that country's runs proceed. The active set always = global registry ∪ that country's ledger; never reconstruct it from memory.

### Candidate axes (watchlist — not yet validated; live in `axes-registry.md`)
- **Format/vehicle** — expedition-cruise, rail, gulet/sailing, walking-active operators who also do culture (Peter Sommer gulets were found by luck, not an axis).
- **Affinity/audience** — alumni, religious, women-only, accessible/seniors, family (distinct operators per segment).
- **Media/creator** — TV/author experts selling private tours (Darius Arya found via his own site, no channel).
- **Season/time** — biennial or winter-only departures a single-season search misses.
- **Price-tier** — ultra-luxury bespoke vs accessible expert-led.

## EXAMPLE (Italy)

Input: `italy/italy_theme_map_v0.md` + `axes-registry.md` (baseline 5) + (fresh country → empty `italy/axes.md`).

Run: pre-populate channel/lens/region cells from training knowledge; then sweep.
- LANGUAGE sweep (Axis 4) using DE/FR/IT idioms surfaces German Studienreisen houses (Studiosus, Gebeco) and French maisons culturelles (Intermèdes, Clio) that English queries never returned.
- AUTHORITY-INDEX sweep (Axis 5) via Oxford/Cambridge alumni partner lists + Met/British Museum travel partners extracts named-curator operators.
- A prior run that searched only 3 axes in English declared false convergence; sweeping the two missing axes (language + authority-index) caught 26 operators (`10-lessons-log.md`).

Output: populated Italy matrix; `italy/axes.md` records the active set + any pending candidates with the tours each uniquely caught; an axis that passes the promotion test is copied up to `axes-registry.md` for the next country.

## ANTI-PATTERNS (checks — fail the step if true)
- Keyword spray not derived from a matrix cell.
- English-only searching.
- Anchoring on recalled operator names without an axis behind them.
- Ranking on memory — admitting any operator/guide/date/price not confirmed by a live source.
- Declaring convergence from a single axis.
- Treating "5 axes" as final / not running the axis-completeness critic (`04`).
- Holding evolving axis knowledge in session memory instead of the committed registry/ledger.
- Discovering a useful new axis and not APPENDING it to `<country>/axes.md` (no per-country accrual) or not PROMOTING a proven one to `axes-registry.md` (no cross-country compounding).
- A skipped region or empty cell that is not recorded as an explicit gap.
