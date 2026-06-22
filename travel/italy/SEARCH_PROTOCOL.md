# Discovery Search Protocol — rigor + freshness (reusable across all 50 countries)

Purpose: make discovery academic and reproducible, minimise unknown-unknowns, and keep the corpus fresh. Built after a real failure (see Lesson below).

## LESSON: false convergence (Italy, 2026-06)
Declared "converged" at round 4 after 4 rounds. A 5th sweep on two new axes (native-language, authority-index) found 26 operators the prior rounds missed (Intermèdes, Clio, Gebeco, Arrangements Abroad/Met curators, Distant Horizons, etc.). Root cause: rounds 1-4 searched in ENGLISH and largely on operator names I recalled. Convergence was axis-limited, not global. Fix = the 5-axis matrix below; convergence is only valid when EVERY axis is individually dry.

## THE COVERAGE MATRIX (5 axes — search the grid, not keywords)
Unknown-unknowns hide where an axis is unsearched. An empty matrix cell is a VISIBLE gap (provable), not a silent miss. Use latent/training knowledge to BUILD the axes + pre-populate candidate cells; use the web to POPULATE + VERIFY. Never free-associate keywords.

1. **CHANNEL** (provider type): A academic cultural ops · B US learned-society/university/museum · C in-country scholar-guide DMC · D named individual expert · E credentialed-guide marketplace · F luxury bespoke · G lifelong-learning · H special-interest (food/wine/military/nature).
2. **LENS** (experience type): history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic/Jewish heritage · military · music · wildlife · geology/volcanology · gardens · maritime · literary · cinema · crafts. (Per-country, prune to what's real.)
3. **REGION** (geographic): every first-level admin region as a checklist; a region with 0 themes must be explicitly tested-and-justified, not assumed empty.
4. **LANGUAGE** (NEW — was the big miss): search native language(s) + the major study-travel source languages (for Europe: + German, French; for LATAM: + Spanish/Portuguese; etc.). German Studienreisen and French maisons culturelles are entire segments invisible to English search.
5. **AUTHORITY-INDEX** (NEW): don't guess operators — mine the directories that LIST them: AITO/Virtuoso/ETOA membership; Condé Nast/Travel+Leisure/Wanderlust/Telegraph "best operator" awards; university alumni travel-partner lists (Oxford/Cambridge/Ivy); museum travel partners (Met/British Museum/National Gallery/Art Fund); national tourism-board licensed-DMC registries; UNESCO site → specialist-operator cross-refs; learned-society member trips. Most productive single source in testing: university alumni partner lists + museum-curator travel.

## CONVERGENCE (when to STOP a country)
- THEME convergence: a fresh adversarial critic admits 0 themes clearing the BAR (>=2 credentialed dated current-season expert-led products, non-overlapping, first-trip-representative).
- OPERATOR convergence (per theme): each of the 5 axes returns dry for that theme.
- Country is DONE only when BOTH hold. Track per-axis dry/not-dry in the corpus so convergence is auditable, not asserted.
- Efficiency: fold operator-saturation into the RANKING phase — when ranking a theme, run the 5-axis check scoped to that theme so discovery + ranking merge (no giant separate sweep).

## ANTI-PATTERNS (banned)
- Free-associated keyword spray. Searches must derive from a matrix cell.
- English-only. Anchoring on recalled operator names without an axis behind them.
- Declaring convergence from one axis. Padding a theme that fails the bar.

## FRESHNESS — keeping the corpus current
Corpus is a point-in-time snapshot (stamp it). Two refresh loops, different cost/cadence:

A. **VERIFY pass (cheap, monthly + before each booking window).** URLs are known → re-fetch each, DIFF against stored row: date moved · price changed · tour withdrawn · new departure added · guide changed. No discovery. Mechanical: read URLs from corpus → agent fetches → flag diffs → update `last_checked`. This is what catches "new info after 1 month."
B. **DISCOVERY pass (expensive, quarterly or on-trigger).** Re-run the final adversarial critic + a dry-check on all 5 axes. Catches genuinely new operators/themes/entrants. If anything admits → full round for that slice.

TRIGGERS (run B off-cadence): new UNESCO inscription · major site/museum reopening · new excavation · anniversary/jubilee years that spawn tours (e.g. 2025 Catholic Jubilee, 800th St Francis 2026) · a major operator launch/closure.

STAMPING: every corpus row carries `last_checked: YYYY-MM-DD` and `status: verified | UNVERIFIED | stale`. Rows older than the cadence window auto-flag `stale` for the next verify pass.

EMBEDDING IT (automation): the monthly VERIFY pass is a perfect scheduled cloud agent (`/schedule`) — reads corpus URLs, re-fetches, posts a diff report; the quarterly DISCOVERY pass likewise. Until scheduled, run on demand with the same two prompts. Recommend wiring the monthly verify as a routine once the first country's rankings are locked.

## PER-COUNTRY RUN ORDER (the template)
1. Seed theme map v0 from training knowledge (channel×lens×region).
2. Discovery loop rounds (all 5 axes) until THEME-converged (critic dry).
3. Per theme: operator-saturation (5-axis scoped) + verify finalists → rank Top 5.
4. Stamp corpus; register in monthly VERIFY + quarterly DISCOVERY refresh.
