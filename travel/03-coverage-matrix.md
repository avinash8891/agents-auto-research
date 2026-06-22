# 03 — The Coverage Matrix (starts at 5 axes) — the rigor core

This is the heart of the method. Unknown-unknowns hide wherever an axis is unsearched. Search the **grid**, never free-associated keywords. An empty matrix cell is a **visible, provable gap** — not a silent miss.

> Built after a real failure: searching only in English, only on operator names recalled from memory, produced a *false convergence*. A later sweep on two missing axes found 26 operators the prior four rounds missed. See `10-lessons-log.md`.

## How to use latent knowledge correctly
- Use training knowledge to **build the axes and pre-populate candidate cells** (name the likely operators/lenses/regions per cell). This is efficient and is where your knowledge adds most value.
- Use the **web to populate and verify** — confirm candidates exist, run current departures, and surface what you didn't know.
- Never rank on memory alone. Every operator/guide/date/price enters only via a live source.

## The 5 axes

### 1. CHANNEL — provider type (8)
- A. Specialist academic cultural/archaeology operators (e.g. Martin Randall, Andante, Peter Sommer, ACE, Ciceroni, Kirker)
- B. US learned-society / university / museum travel (AIA Tours, Smithsonian Journeys, Nat Geo Expeditions, Road Scholar, Far Horizons; alumni operators: Gohagan, AHI, Arrangements Abroad)
- C. In-country scholar-guide DMCs (Context Travel, and local PhD-guide outfits)
- D. Named individual experts (the genuine figurehead who personally guides)
- E. Credentialed-guide marketplaces (ToursByLocals, Withlocals; archaeologist-led-only on GetYourGuide/Viator)
- F. Luxury bespoke designers (Abercrombie & Kent, Scott Dunn, Butterfield & Robinson, scholar-founded DMCs e.g. Imago Artis)
- G. Lifelong-learning / continuing-ed (Road Scholar, university extension trips)
- H. Special-interest (food/wine, military history, nature/wildlife, music, religious/pilgrimage)

### 2. LENS — experience type
history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic/Jewish heritage · military · music · wildlife · geology/volcanology · gardens · maritime · literary · cinema · crafts. Prune to what's real per country.

### 3. REGION — geography
Every first-level admin region as a checklist. A region with 0 themes must be explicitly tested-and-justified.

### 4. LANGUAGE — native + study-travel source languages  *(commonly missed)*
Search in the destination's language AND the major study-travel markets' languages. For Europe add **German** (Studienreisen — Studiosus, Gebeco, Karawane) and **French** (maisons culturelles — Intermèdes, Clio). For LATAM add Spanish/Portuguese; for East Asia the local language; etc. English-only search misses entire high-quality segments.
Concrete matrix-derived queries (what a real language sweep looks like — vary per theme):
- DE: `Studienreise <region> Archäologie 2026`, `Studienreiseleiter Italien Kunsthistoriker`, `wissenschaftliche Reiseleitung Sizilien`
- FR: `voyage culturel <region> conférencier`, `Étrusques voyage accompagné archéologue`, `voyage conférencier historien de l'art Italie`
- IT: `viaggi con l'archeologo <region>`, `viaggi studio archeologia`, `viaggi culturali accompagnati da storico dell'arte`
These idioms (Studienreise, conférencier, "con l'archeologo") are how the segment self-describes; they surface operators no English query returns.

### 5. AUTHORITY-INDEX — mine the directories, don't guess operators  *(commonly missed)*
Enumerate the lists that already curate quality operators, then extract names:
- Awards: Condé Nast Traveller / Travel+Leisure / Wanderlust / Telegraph "best tour operator" winners.
- Membership directories: AITO, Virtuoso, ETOA, ASTA.
- University alumni travel-partner lists (Oxford/Cambridge/Ivy) — most productive single source in testing.
- Museum travel partners (the Met / British Museum / National Gallery / Art Fund) — best named-curator model.
- National tourism-board licensed-DMC registries.
- UNESCO site → specialist-operator cross-references; learned-society member trips.

## The axis set is NOT final (it is itself a convergence target)
The 5 above are **current-best, empirically derived — not proven complete.** L7 already proved a smaller set (3 axes) gave a false convergence; the same can happen at 6. Do not treat "5" as axiomatic.
- **What qualifies as an axis:** an independent dimension where *failing to search it causes systematic (not random) misses*.
- **Promotion test:** a candidate becomes a real axis only when it **provably surfaces tours no existing axis finds** (same evidence bar as a new theme/operator — show the tours it uniquely caught).
- **Caveat (taxonomy isn't clean):** the current axes mix two kinds — *target-properties* (channel/lens/region = what the tour is) and *search-method* (language/authority = how you look). A future refactor may re-cut them; that's expected.

### Candidate axes (watchlist — not yet validated)
- **Format/vehicle** — expedition-cruise, rail, gulet/sailing, walking-active operators who also do culture (Peter Sommer gulets were found by luck, not an axis).
- **Affinity/audience** — alumni, religious, women-only, accessible/seniors, family (distinct operators per segment).
- **Media/creator** — TV/author experts selling private tours (Darius Arya found via his own site, no channel).
- **Season/time** — biennial or winter-only departures a single-season search misses.
- **Price-tier** — ultra-luxury bespoke vs accessible expert-led.

## Axes are PER-COUNTRY and EVOLVING
The active axis set differs by country (an island nation needs Format/vehicle for cruises; a pilgrimage-heavy country needs Affinity) and keeps updating as that country's discovery runs. This knowledge lives in **committed files, not session memory**:
- **Global registry** `travel/axes-registry.md` — baseline 5 + candidate watchlist + axes proven useful across countries. **Seeds every new country.**
- **Per-country ledger** `travel/<country>/axes.md` — which axes are active for this country, promotions (with evidence: the tours only that axis found), pending candidates. See `06`.

Update cycle (read → run → append):
1. At session start, READ the global registry + this country's ledger (the starting knowledge).
2. Run discovery + the axis-completeness critic (`04`).
3. APPEND new axis findings to the country ledger.
4. If an axis proves useful (passes the promotion test), PROMOTE it to the global registry so future countries inherit it.
Compounding works exactly like the lessons log — via the git-committed files, not chat memory.

## Anti-patterns (banned)
- Keyword spray not derived from a matrix cell.
- English-only searching.
- Anchoring on recalled operator names without an axis behind them.
- Declaring convergence from a single axis.
- Treating "5 axes" as final / not running the axis-completeness critic.
- Holding evolving axis knowledge in session memory instead of the committed registry/ledger.
