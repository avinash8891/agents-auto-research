# Global Lens Registry

The shared, evolving list of **lenses** (subject types a theme can be built on). Seeds every country's theme seeding (`theme-seeding`). Mechanics (append-only, structure, update cycle): `REGISTRY-PROTOCOL.md`.

Lens = a subject type that can anchor a single-lens theme. A missing lens = a systemic miss (e.g. "nature" was missed on Italy until discovery round 2).
Promotion bar = the lens anchors ≥1 real expert-led theme (≥2 credentialed dated products) in some country.

Protocol (append-only, structure, update cycle): `REGISTRY-PROTOCOL.md`. This is the sole controlled vocabulary for lenses — other docs point here, never re-list. `theme-archetypes.md` Lens column values MUST be ids from this list.

## BASELINE LENSES (check every country)
history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic heritage (e.g. Jewish) · military · music · wildlife/nature · geology/volcanology · gardens · maritime · literary · cinema · crafts · living-culture

## CREDENTIAL → LENS TABLE
Data lookup: each BASELINE lens → the credential strings that satisfy it (academic AND non-academic / local). Open enumeration — append on discovery, never collapse. Per-country specialisations (e.g. ASI-approved, KPSGA-graded) live in `<country>/axes.md` (or an instance column), not here. `07` step D.4 and `05` QUALIFYING EXPERT resolve credential-fit via this table; a credential string MUST resolve to the theme's lens here, else FLAG `credential-mismatch` and cap at PARTIAL (`travel-config.md` `PARTIAL_PRODUCT_WEIGHT`). If a credential is adjacent but not listed, mark the rubric dimension `PARTIAL`, record the source in the ledger, and promote the exact string only after it proves theme-fit in a real run.

| Lens | Satisfying credential strings (academic ∪ non-academic / local) |
|------|------------------------------------------------------------------|
| history | historian, academic/published historian, archivist, museum-affiliated researcher, recognised local historian, authored works / ORCID holder |
| archaeology | archaeologist, Egyptologist (Egypt), classical-archaeologist, ancient-historian, excavation-affiliated, ASI-approved (India), INAH-authorised (Mexico) |
| art | art historian, museum curator, gallery-affiliated scholar, practising master artist, restorer/conservator |
| architecture | architectural historian, licensed architect, heritage-conservation specialist, recognised restorer |
| design | design historian, museum-affiliated design scholar, recognised practising designer |
| science | research scientist, university faculty, field-station researcher, published practitioner / ORCID holder |
| food | food historian, recognised chef/culinary-school master, gastronomy scholar, slow-food / heritage-cuisine practitioner |
| wine | sommelier, oenologist, DOC/AOC winemaker, wine scholar, recognised estate vintner |
| religion/pilgrimage | religious-studies scholar, theologian, ordained clergy/monastic, temple/lineage title-holder, recognised pilgrimage authority |
| ethnic heritage | heritage-studies scholar, community-recognised heritage holder, recognised oral-tradition holder, lineage title-holder |
| military | military historian, veteran-specialist guide, battlefield-archaeology specialist, regimental/museum-affiliated researcher |
| music | musicologist, conservatory-trained performer, recognised master musician, ethnomusicologist, recognised oral-tradition holder |
| wildlife/nature | naturalist, zoologist, ornithologist, field-ecologist, master tracker, KPSGA-graded (Kenya) |
| geology/volcanology | geologist, volcanologist, observatory-affiliated researcher, nationally-licensed volcano/specialist guide |
| gardens | botanist, horticulturist, garden historian, recognised master gardener |
| maritime | maritime historian, marine archaeologist, master mariner, recognised shipwright/practitioner |
| literary | literary scholar, published author, literary-society-affiliated researcher |
| cinema | film historian, film scholar, recognised industry practitioner |
| crafts | guild master, recognised oral-tradition holder, lineage title-holder, master artisan, recognised practising craftsperson |
| living-culture | guild master, recognised oral-tradition holder, lineage title-holder, community-recognised culture-bearer, nationally-licensed specialist guide |
| **(cross-lens) NON-ACADEMIC / LOCAL standing** | master tracker · temple/lineage title · guild mastership · published practitioner · recognised oral-tradition holder · nationally-licensed specialist guide — QUALIFY without a Western degree (resolve to the theme's lens above) |

### ANTI-PATTERNS
- Requiring a Western academic degree and FLAGGING a named local/indigenous master with verifiable standing (master tracker, temple/lineage title, guild mastership) as `credential-mismatch` — they QUALIFY per the cross-lens row.
- Treating this table as closed and FLAGGING an unlisted-but-valid credential instead of appending it on discovery.
- Hardcoding per-country credential strings (ASI-approved, KPSGA-graded) inline here instead of in `<country>/axes.md` / an instance column.

## CANDIDATE LENSES (watchlist — test per country, promote on evidence)
| Candidate | Would anchor | Status | Evidence |
|-----------|-------------|--------|----------|
| wellness/spa/thermal | thermal-bath & wellness circuits | candidate | none yet |
| adventure/trekking | expert-led trekking with naturalist/cultural depth | candidate | borders Dolomites (Italy, folded into nature) |
| diving/marine | reef/marine-ecology expert tours | candidate | relevant to island nations |
| festivals/events | festival-anchored cultural tours (opera already a music sub-case) | candidate | Syracuse INDA surfaced as event-pairing on Italy |
| textile/fashion heritage | silk/leather/craft-production tours | candidate | tested thin on Italy |
| industrial/engineering heritage | rail, mining, automotive (Ferrari/Modena) | candidate | Modena Motor Valley surfaced under Italy food |
| dark tourism | conflict/disaster sites with historian | candidate | overlaps military |

## PROMOTION LOG
- (seed) Baseline lenses established from the initial cross-country pattern scan.
- (Italy R2) **wildlife/nature** promoted to baseline — was absent from the seed and surfaced as a systemic miss (Apennine bear/wolf safari, volcanology). The reason this registry exists.

## UPDATE
Mechanics: `REGISTRY-PROTOCOL.md`. Lens-specific notes only: seeding (`theme-seeding`) runs a completeness diff (every baseline lens → a theme or a `thin/none` justification); a candidate lens that anchors a real theme promotes CANDIDATE→BASELINE with a PROMOTION LOG entry citing the theme/tours.
