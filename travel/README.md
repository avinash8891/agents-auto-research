# Travel Research Method — ranked, expert-led tours by theme-within-region

A reproducible method for building **ranked Top-`RANK_DEPTH` tour lists** (`travel-config.md`) for the most-visited countries, organised by **focused regional theme** (not whole-country), favouring depth over breadth and expert-led authenticity over checklists. Scope today is `CURRENT_SCOPE_N` countries; it scales along `GROWTH_LADDER` toward `TARGET_SCALE` (an illustrative example target, NOT a fixed contract).

This folder is the **playbook**. Each numbered file is one step. Follow it, or improve it — and when you improve it, append to the `lessons` doc (`doc-manifest.md`) so gains compound.

## The one-paragraph version
For each country: split it into distinct, non-overlapping **themes** (a theme = a focused regional experience sold as a single trip — e.g. "Rome & classical antiquity," not "Italy") using the admission/reshape rubrics in `admission-bar` and `discovery-loop`. Discover the *whole field* of expert-led tours per theme using the baseline-axis coverage matrix in `axes-registry.md` (so nothing is missed), loop until discovery is dry, verify every finalist against live sources, then rank the Top `RANK_DEPTH` using the rubric in `ranking`. Persist everything to a durable corpus and refresh it on the cadences in `travel-config.md`.

## Core principles
1. **Depth over breadth.** A focused theme done deeply beats a thin nationwide overview; depth is labeled by the `ranking` rubric and theme fit by the `admission-bar` rubric.
2. **First-trip lens.** Within a theme, favour the experience most representative for a first-time visitor only when the `admission-bar` rubric supports that label — not obscure hyper-niche.
3. **No invention.** Never fabricate guides, dates, prices, claims. Unverified = flagged, never guessed.
4. **Frames beat keywords.** Discovery is driven by a coverage matrix, not free-associated search terms. Empty matrix cells are visible gaps, not silent misses.
5. **Training knowledge builds the frames; the web populates and verifies.** Use latent knowledge to enumerate channels/lenses/regions/languages/authorities and pre-fill candidates; use live search to confirm and to catch what you didn't know.
6. **Convergence is earned, not asserted.** Stop only when a fresh adversarial critic adds nothing clearing `ADMISSION_BAR`, AND every axis tagged `role:convergence-gate` (`axes-registry.md`) returns dry, AND zero units carry a `dirty` flag (`DONE`, `travel-config.md`; `REGISTRY-PROTOCOL.md` INVALIDATION). Never a frozen axis count. **"dry" is evidence of search exhaustion, not field completeness** — a gate empty because the source base does not cover the region (not because the market is covered) raises a coverage-limitation FLAG and makes convergence PROVISIONAL, it does not count as satisfied.
7. **Value, not luxury.** Price is not a barrier, but cost must satisfy the `ranking` value row: justified by verified depth/expertise/access/small-group/duration/rare logistics, otherwise flag premium-for-thin-substance.
8. **Single-lens themes are the ranking unit; multi-lens is a composition layer.** Group tours go deep on one subject with one expert. A traveller's multi-lens trip is built by combining ranked themes (`composition` doc, `doc-manifest.md`), accepting that whole-trip expert depth is recovered only per-segment or via a bespoke designer.
9. **Nothing in memory through the corpus step.** Country list, lenses, theme-archetypes, axes, seed themes — all read from committed files (`country_ranking.md`, `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md`, the `<country>/` artifacts) and appended back. A fresh session reproduces the same state from files alone. Every enumeration the method leans on is an **evolving registry** governed by `REGISTRY-PROTOCOL.md` (append-only, per-country, file-persisted, compounding) — applies to axes, lenses, channels, archetypes, sources, and aliases alike.
10. **The pipeline is a fixed-point computation, not one pass.** A downstream promotion (new axis/lens/archetype/channel) marks every dependent unit `dirty` and triggers a **scoped re-sweep of only that axis/unit**, never a global restart. A unit is not converged/done while any `dirty` flag remains (`DONE`, `travel-config.md`; `REGISTRY-PROTOCOL.md` INVALIDATION). Within a country this is a strict fixed point; across countries it is eventual-consistency on the DISCOVERY cadence.
11. **Tangential intelligence is captured, not lost.** Page-reading steps (discovery — `discovery-loop`, verification — `ranking`) surface more than the row schema holds. Emit it as **typed leads** to `<country>/leads.md` with provenance and route each to the step/registry it fine-tunes per the routing table (`REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING). A lead implying new coverage dirties the affected unit (principle 10).
12. **The corpus row schema is a versioned contract.** `first_seen_round` is stamped at consolidation; older rows are backfilled via the VERIFY pass, not guessed (`corpus`, `freshness`). Writer and consumers share one schema; field changes are announced, not silent.

## Steps
Files are cited by **slug** throughout the playbook; the slug → file map (and renaming policy) lives in `doc-manifest.md`. Listed here in run order:

- `overview` — goal, scope, output contract
- `country-ranking` — establish the country list at scale `CURRENT_SCOPE_N` (source: `sources-registry.md`, `tier=primary`)
- `theme-seeding` — seed the theme map v0 (channel × lens × region)
- `coverage-matrix` — the baseline-axis discovery frame (the rigor core; axes from `axes-registry.md`)
- `discovery-loop` — exhaustive discovery, round mechanics, write-to-corpus; **owns the registry promotion test** (run per country, results append to `<country>/axes.md`)
- `admission-bar` — when to stop; the theme bar (`ADMISSION_BAR`); loop-until-dry
- `corpus` — corpus schema, subagent file-writing, stamping, theme IDs (`THEME_ID_GRAMMAR`)
- `ranking` — verify finalists; rank Top `RANK_DEPTH`; output format
- `freshness` — keep it current (`VERIFY_CADENCE` / `DISCOVERY_CADENCE` / `RERANK_CADENCE`, cron)
- `orchestration` — how to dispatch parallel agents efficiently
- `lessons` — the maturation history + every future improvement (lessons pinned by stable `Lnn` id; e.g. L7)
- `composition` — consumption layer: stitch single-lens themes into a multi-lens itinerary (downstream of ranking)

### Single-source registries (referenced, never re-listed)
- `travel-config.md` — every named dial (scope, ranking, trip shape, season, cadence, identifiers, convergence). No literal lives anywhere else.
- `REGISTRY-PROTOCOL.md` — the shared mechanics (append-only, structure, update cycle, promotion bar, multi-valued tags) PLUS the cross-cutting INTELLIGENCE CAPTURE & ROUTING (leads bus + routing table) and INVALIDATION (promotion → dirty-propagation fixed-point) sections for every registry below. Don't restate it — link it.
- `lens-registry.md` — sole vocabulary of lenses (subject types); seeds theme seeding. "wildlife/nature" lives here because it was a systemic miss.
- `theme-archetypes.md` — library of recurring cross-country theme patterns (wine region, wildlife circuit, pilgrimage circuit…) with its own promotion bar + log; seeding walks it so free-recall gaps get caught.
- `axes-registry.md` — discovery axes (baseline + candidate watchlist + cross-country promotions) carrying multi-valued `stage`/`role` tags; seeds every country. Per-country deviations live in `<country>/axes.md`. The axis set is a convergence target that grows, not a frozen count — derive the count from this file.
- `channel-registry.md` — provider sub-types of the `channel` axis, as stable string ids (`academic-operator` … `special-interest`), not positional letters. Count derived from the file.
- `sources-registry.md` — ranking/verification data sources with `tier`/`lag` tags.
- `operator-aliases.md` — sub-brand→parent absorptions + aggregator exclusions for consistent de-dup.
- `tags-registry.md` — small tag vocabularies (row `status` incl. `CLAIMED`, `format-class` + rankability, `watch/leisure`, theme `strength`, `country.outcome`, the `junk-saturated`/`low-signal` cell-state); writer (`corpus`) and consumer (`ranking`) reference it so spellings never drift.

### Per-country store scheme (canonical map in `doc-manifest.md`, never re-listed)
All per-country state lives under `<country>/`: one `<country>/ledger.md` (the single per-country ledger), one locked `<country>/corpus_FINAL.md`, plus `<country>/axes.md`, `<country>/leads.md`, `<country>/rankings/`, `<country>/compositions/`, and dated `<country>/verify_<date>.md`. NORMALIZATION: a `<country>_X` reference denotes the same file as `<country>/X` (canonical form `<country>/X`). The authoritative artifacts table is `doc-manifest.md` — reference it, don't restate.

## Document conventions
**These docs are AGENT EXECUTION SPECS, not human essays.** The reader is a coding/AI agent. Optimise for unambiguous execution, not narrative. Each step doc uses this structure:
- `INPUT` / `OUTPUT` / `NEXT` — the I/O contract (what it consumes, what file it emits, who consumes that).
- `PROCEDURE` — ordered, imperative, deterministic steps.
- `DECISION RULES` — checkable conditions (`X IFF Y`, `if X → Y`), not prose.
- `EXAMPLE` — concrete input → output (Italy), not abstract description.
- `ANTI-PATTERNS` — failure checks (fail the step if true).
Keep "why" only where needed to disambiguate. The `theme-seeding` doc is the reference shape.

## Per-country run order (the loop)
1. Establish/confirm country's arrivals rank (`country-ranking`).
2. Seed theme map v0 (`theme-seeding`).
3. Run the discovery loop with the baseline-axis matrix until theme-converged (`coverage-matrix` → `discovery-loop` → `admission-bar`). Theme-converged = `THEME_CONVERGED` (`travel-config.md`): a fresh adversarial completeness-critic admits 0 themes clearing `ADMISSION_BAR`.
4. Per theme: saturate operators across every baseline axis (`axes-registry.md`) + verify finalists → rank Top `RANK_DEPTH` (`ranking`). Operator-converged = `OPERATOR_CONVERGED` (`travel-config.md`): every axis tagged `role:convergence-gate` (`axes-registry.md`) returns dry for the theme AND the theme carries no `dirty` flag.
5. Country DONE only per `DONE` (`travel-config.md`): both convergences AND zero `dirty` units (re-sweep any dirty unit first — `REGISTRY-PROTOCOL.md` INVALIDATION). Then stamp corpus; register in refresh cadence (`corpus`, `freshness`).

Note on axis stages: seeding (`theme-seeding`) consumes only axes tagged `stage:seed`; discovery sweeps (`discovery-loop`) consume axes tagged `stage:discovery`; the axes tagged `role:axis-proof` each get a dedicated sweep (the false-convergence gate). Never name axes by hand — filter by tag.

## Scope (current)
**Pilot = `CURRENT_SCOPE_N` countries only.** Fine-tune the method and prove it 100% before scaling. `TARGET_SCALE` is an illustrative example, not a hard contract; the live count is `CURRENT_SCOPE_N`, raised only along `GROWTH_LADDER` once trusted. Refresh is **additive** — adding countries never removes existing ones (`country-ranking`).

## Status (as of `CURRENT_SEASON`, 2026-06)
The method has four real proof artifacts:
- `italy/rankings/IT-01.md` — Rome/classical antiquity fixed-departure ranking.
- `italy/rankings/IT-07.md` — Pompeii/Herculaneum ranking with a hybrid-format flag.
- `japan/rankings/JP-01.md` — non-Western first-trip Japan ranking.
- `italy/thin-notes/IT-THIN-molise-samnite.md` — thin/failed theme note, not padded into Top-`RANK_DEPTH`.

The method is now proven for a small set of audited rankings, not for global scale. Italy has real outputs beyond theme mapping; Japan has one non-Western proof run; destination-side authority, local-direct, native-idiom, and non-Western credential handling still need more countries before global trust.

## POST-RUN PRUNE EVIDENCE
**Evidence, not automatic deletion.** Repeated runs show the core that earns its keep: broad discovery, hard finalist verification, `VERIFIED`/`CLAIMED`/`PARTIAL`/`FAIL` rubric labels, independent credential evidence, format lanes, ledgers, and thin notes.

Scale-deferred or friction at current scale:
- **Dirty-propagation / INVALIDATION fixed-point** (`REGISTRY-PROTOCOL.md` INVALIDATION; principle 10) — no run promoted a new global axis/lens/channel after prior units were finalized, so no dirty re-sweep fired. Keep as scale-deferred.
- **Typed `leads.md` bus + routing table** (`REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING; principle 11) — repeated runs used `<country>/ledger.md` for retest debt and format/credential notes; no `leads.md` file was created. Demote or fold into ledger unless routed leads outgrow the ledger.
- **`corpus_FINAL.md` consolidation / schema-versioning** (`corpus`; `freshness`; principle 12) — proof runs used round files and rankings, not a locked refreshable corpus. Keep as scale-deferred for country-level refresh.
- **Audit/static-census/doc-currency guards** — useful for batch doc work, but not part of per-theme ranking execution.
