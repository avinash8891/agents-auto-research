# 03 — The Coverage Matrix (the rigor core)

AGENT SPEC. Build a per-country search grid over the baseline axes in `axes-registry.md` so unknown-unknowns become *visible, provable gaps* — an empty matrix cell is a proven miss, not a silent one. Search the grid, never free-associated keywords.

INPUT:
- `<country>_theme_map_v0.md` (from `theme-seeding`) — the seed themes/cells to search.
- Global `axes-registry.md` — baseline axes + candidate watchlist + axes proven cross-country (`stage`/`role` tags + count both derived from that file). SEEDS every new country.
- Per-country ledger `travel/<country>/axes.md` — axes active for this country, prior promotions (with evidence), pending candidates. See `corpus`.
- Supporting registries cited by the axes: `channel-registry.md` (CHANNEL sub-types), `lens-registry.md` (LENS vocabulary), `sources-registry.md` (AUTHORITY-INDEX sources), `operator-aliases.md` (de-dup on the LANGUAGE sweep).
OUTPUT:
- Populated coverage matrix for `<country>` (cells = candidate operators/lenses/regions per axis intersection; empty cell = explicit gap).
- APPENDED axis findings in `travel/<country>/axes.md`; PROMOTED axes (if any pass the promotion bar) in `axes-registry.md`.
NEXT: `discovery-loop` (discovery rounds + axis-completeness critic) consumes the matrix and the ledger.

MEMORY INVARIANT: nothing the method depends on lives in session memory. The country list, seed map, axis set, promotions, and pending candidates are all READ from committed files and APPENDED back. A fresh session reproduces the same matrix from `axes-registry.md` + `<country>/axes.md` + the seed map alone. Training knowledge proposes; the registries + ledger are the source of truth. (Mechanics of read→run→append→promote: `REGISTRY-PROTOCOL.md`.)

> WHY (one failure that grounds this): searching only in English, only on operator names recalled from memory, produced a *false convergence*. A later sweep on two missing axes (the axes now tagged `role:axis-proof`) found 26 operators the prior four rounds missed. See `lessons` (L7).

## PROCEDURE

1. At session start, READ `axes-registry.md` + `travel/<country>/axes.md` (the starting knowledge) + `<country>_theme_map_v0.md`. The active axis set = registry BASELINE ∪ country-promoted axes ∪ this country's pending candidates (per `REGISTRY-PROTOCOL.md` UPDATE CYCLE).
2. Use training knowledge to BUILD the axes and PRE-POPULATE candidate cells: name the likely operators/lenses/regions per cell. This is where latent knowledge adds most value.
3. For each baseline axis, enumerate its values and fill cells. Each axis defines its own enumeration in its registry — do not inline-list here:
   - **CHANNEL** (`channel`): provider sub-types are the stable IDs in `channel-registry.md` (count derived there, never asserted). See DECISION RULES.
   - **LENS** (`lens`): experience type — the vocabulary in `lens-registry.md`; prune to what is real per country.
   - **REGION** (`region`): every first-level admin region as a checklist.
   - **LANGUAGE** (`language`): native + study-travel-market languages; commonly missed. Tagged `role:axis-proof` — gets its own sweep.
   - **AUTHORITY-INDEX** (`authority-index`): mine curated directories (`sources-registry.md`) for operator names; commonly missed. Tagged `role:axis-proof` — gets its own sweep.
   (Run the dedicated sweeps for every axis tagged `role:axis-proof` in `axes-registry.md` — do not name them by hand; filter by tag.)
4. Use the WEB to populate and verify every cell — confirm candidates exist, run current departures, surface unknowns. Never rank on memory alone; every operator/guide/date/price enters only via a live source.
5. Run the LANGUAGE sweep with the segment's own idioms (Studienreise, conférencier, "con l'archeologo") — these surface operators no English query returns. Vary queries per theme (see LANGUAGE query bank below). De-dup sub-brands/aggregators via `operator-aliases.md`.
6. Run the AUTHORITY-INDEX sweep: enumerate the curated lists in `sources-registry.md`, then extract operator names from them.
7. Leave a cell EMPTY only as a deliberate, visible gap. A region with 0 themes must be explicitly tested-and-justified (carry the `thin/none` row from `theme-seeding`).
8. Run the axis-completeness critic (`discovery-loop`) against the filled matrix.
9. APPEND new axis findings to `travel/<country>/axes.md`.
10. If a candidate axis passes the promotion bar (DECISION RULES), PROMOTE it to `axes-registry.md` so future countries inherit it — declaring its `stage`/`role` tags in the PROMOTION LOG so the gates/sweeps pick it up automatically.
11. Hand the matrix + ledger to `discovery-loop`. Stop.

Compounding cycle (read → run → append → promote) is the shared registry mechanic — see `REGISTRY-PROTOCOL.md`. It works via git-committed files, not chat memory, exactly like the lessons log.

## AXIS VALUES (where each enumeration lives)

The axes themselves and their `stage`/`role` tags are defined once in `axes-registry.md`. The values under each axis are owned by the registry the axis points to — this doc never re-lists them:

- **CHANNEL** (`channel`) → provider sub-types are stable IDs in `channel-registry.md`. Cross-reference channels by id (e.g. `luxury-bespoke`), never by a positional letter.
- **LENS** (`lens`) → the lenses in `lens-registry.md`. Prune to what's real per country.
- **REGION** (`region`) → every first-level admin region of `<country>` as a checklist. A region with 0 themes must be explicitly tested-and-justified.
- **LANGUAGE** (`language`) → the destination's language AND the major study-travel markets' languages. The per-country language set is *data* in `<country>/axes.md`, not a literal here. (For Europe this typically adds German — Studienreisen — and French — maisons culturelles; for LATAM Spanish/Portuguese; for East Asia the local language.) English-only search misses entire high-quality segments.

  LANGUAGE query bank (what a real language sweep looks like — vary per theme):
  - DE: `Studienreise <region> Archäologie <season>`, `Studienreiseleiter Italien Kunsthistoriker`, `wissenschaftliche Reiseleitung Sizilien` (where `<season>` is `CURRENT_SEASON`).
  - FR: `voyage culturel <region> conférencier`, `Étrusques voyage accompagné archéologue`, `voyage conférencier historien de l'art Italie`
  - IT: `viaggi con l'archeologo <region>`, `viaggi studio archeologia`, `viaggi culturali accompagnati da storico dell'arte`

- **AUTHORITY-INDEX** (`authority-index`) → mine directories, don't guess operators. Enumerate the lists that already curate quality operators (`sources-registry.md`: awards, membership directories such as AITO/Virtuoso/ETOA/ASTA, university-alumni & museum travel partners, national licensed-DMC registries, UNESCO site → specialist-operator cross-references), then extract names. University-alumni partner lists were the most productive single source in testing; museum travel partners gave the best named-curator model.

## DECISION RULES

- LATENT-KNOWLEDGE SPLIT: training knowledge MAY build axes and pre-populate cells; it MUST NOT supply the final ranking. An operator/guide/date/price is admitted IFF a live source confirms it.
- GAP VISIBILITY: an empty cell is valid IFF it is recorded as an explicit gap (not silently skipped). A 0-theme region is valid IFF it is tested-and-justified with a `thin/none` row.
- LANGUAGE COVERAGE: the matrix is incomplete until every language in this country's `language`-axis set (`<country>/axes.md`) has been swept — for a European country that includes DE and FR; for LATAM ES/PT; for East Asia the local language. English-only → step fails.
- AXIS SET IS NOT FINAL — it is itself a convergence target. The baseline axes are current-best, empirically derived, NOT proven complete. L7 (`lessons`) proved a smaller axis set gave a false convergence; the same can happen if the set grows by one. Never treat the axis count as axiomatic — derive it from `axes-registry.md`, and require every axis tagged `role:convergence-gate` to be dry (not a frozen number).
- WHAT QUALIFIES AS AN AXIS: an independent dimension where *failing to search it causes systematic (not random) misses*.
- PROMOTION BAR: shared mechanics in `REGISTRY-PROTOCOL.md`; the axis-specific evidence is that a candidate **provably surfaces tours no existing axis finds** (cite the tours it uniquely caught — same spirit as `MIN_CREDENTIALED_PRODUCTS`). Pass → APPEND to `<country>/axes.md` and PROMOTE to `axes-registry.md` with declared `stage`/`role` tags.
- TAXONOMY CAVEAT (disambiguation): the current axes mix two kinds — *target-properties* (channel/lens/region = what the tour is) and *search-method* (language/authority = how you look). A future refactor may re-cut them; that is expected, not a defect.
- PER-COUNTRY + EVOLVING: the active axis set differs by country (an island nation needs Format/vehicle for cruises; a pilgrimage-heavy country needs Affinity) and keeps updating as that country's runs proceed. The active set always = registry BASELINE ∪ that country's ledger; never reconstruct it from memory.

### Candidate axes
The watchlist (Format/vehicle, Affinity/audience, Media/creator, Season/time, Price-tier, with their per-candidate evidence) lives in `axes-registry.md` CANDIDATE WATCHLIST — not duplicated here. Test each candidate per country via the axis-completeness critic (`discovery-loop`); a pass promotes it there with declared tags.

## EXAMPLE (Italy)

Input: `italy/italy_theme_map_v0.md` + `axes-registry.md` (BASELINE axes) + (fresh country → empty `italy/axes.md`).

Run: pre-populate channel/lens/region cells from training knowledge; then sweep.
- LANGUAGE sweep (axis `language`) using DE/FR/IT idioms surfaces German Studienreisen houses (Studiosus, Gebeco) and French maisons culturelles (Intermèdes, Clio) that English queries never returned. (Full Italy roster lives in the per-country corpus, not here.)
- AUTHORITY-INDEX sweep (axis `authority-index`) via university-alumni partner lists + museum travel partners (`sources-registry.md`) extracts named-curator operators.
- A prior run that searched only the target-property axes in English declared false convergence; sweeping the two axes tagged `role:axis-proof` (language + authority-index) caught 26 operators (`lessons`, L7).

Output: populated Italy matrix; `italy/axes.md` records the active set + any pending candidates with the tours each uniquely caught; an axis that passes the promotion bar is copied up to `axes-registry.md` for the next country.

## ANTI-PATTERNS (checks — fail the step if true)
(open — a VIEW of `10-lessons-log.md`; append the check when a new lesson lands, tag `Lnn`; `REGISTRY-PROTOCOL.md`.)
- Keyword spray not derived from a matrix cell.
- English-only searching. (L7)
- Anchoring on recalled operator names without an axis behind them. (L1)
- Ranking on memory — admitting any operator/guide/date/price not confirmed by a live source. (L1)
- Declaring convergence from a single axis. (L7)
- Treating the axis count as final / not running the axis-completeness critic (`discovery-loop`) / skipping any axis tagged `role:axis-proof`. (L7)
- Holding evolving axis knowledge in session memory instead of the committed registry/ledger. (L16)
- Cross-referencing a channel by positional letter instead of its `channel-registry.md` stable id. (L16)
- Discovering a useful new axis and not APPENDING it to `<country>/axes.md` (no per-country accrual) or not PROMOTING a proven one to `axes-registry.md` (no cross-country compounding). (L16)
- A skipped region or empty cell that is not recorded as an explicit gap.
