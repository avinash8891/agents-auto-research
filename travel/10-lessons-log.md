# 10 — Lessons Log

Append-only. Every time the method improves, record what was wrong, why, and the fix. This is what lets a future run *improve upon* rather than repeat. Each lesson came from a real correction during the Italy build (2026-06).

## L1 — Discovery-first, not confirmation
**Wrong:** Built the first ranked list from the three operators named in the prompt + recall, then "verified" them. That's confirmation, not research.
**Why it failed:** Anchoring. The famous operators get found; the field doesn't.
**Fix:** Always discover the whole field *before* filtering/ranking. Discovery is blind to priors.

## L2 — A handful of searches is not exhaustive
**Wrong:** ~4–6 searches per theme.
**Why:** Search surfaces the popular and hides the long tail; exhaustiveness can't come from search volume alone.
**Fix:** Enumerate provider **channels** (8) and search each — coverage by construction, not by luck. (Later generalised to the 5-axis matrix, L7.)

## L3 — Themes are not fixed up front
**Wrong:** Treated the seed theme list as the structure.
**Why:** The real structure is whatever operators actually sell as a trip.
**Fix:** Discovery reshapes themes in a loop — split/merge/add/demote — until stable. Seed is provisional (`02`).

## L4 — Persist what subagents gather
**Wrong:** Kept only compressed summaries in the orchestrator; raw inventories lived only in context.
**Why:** Next session starts cold; relayed summaries lose detail.
**Fix:** Save the raw corpus verbatim (`06`).

## L5 — Subagents write their own files
**Wrong:** Agents returned findings through the orchestrator, which then saved them.
**Why:** Bloats orchestrator context (won't scale to 50 countries) and loses fidelity in relay.
**Fix:** Each agent writes its raw findings directly to its own corpus file and returns only a verdict (`06`, `09`).

## L6 — Loop until dry, but with a quality bar
**Wrong (two ways):** Stopping after a fixed number of rounds; OR chasing literal "zero new" forever.
**Why:** Fixed rounds miss the tail; literal-zero pads thin themes (violates depth-over-breadth).
**Fix:** Admission bar — a new theme needs ≥2 credentialed dated expert-led products, non-overlapping, first-trip-representative. Converged when a fresh critic admits 0 clearing the bar (`05`).

## L7 — False convergence: the missing axes (the big one)
**Wrong:** Declared "converged" after 4 rounds — all searched in **English**, largely on **recalled operator names**.
**Why:** Convergence was axis-limited. A 5th sweep on two unsearched axes — **native-language** (IT/DE/FR) and **authority-index** (awards, AITO/Virtuoso, university-alumni & museum travel partners) — found **26 operators** the four prior rounds missed (Intermèdes, Clio, Gebeco, Arrangements Abroad/Met curators, Distant Horizons, …). Several could plausibly be a theme's #1 — fatal for a "best tour" ranking.
**Fix:** The 5-axis coverage matrix (`03`); convergence valid only when EVERY axis is dry, language and authority-index included (`05`). Use training knowledge to build the axes; use the web to populate/verify.

## L8 — Freshness is part of the method, not an afterthought
**Wrong:** Treated the corpus as a static deliverable.
**Why:** Dates/prices/departures churn; "correct in June" is wrong by August.
**Fix:** Two refresh loops (cheap monthly VERIFY by re-fetching known URLs + diffing; expensive quarterly/on-trigger DISCOVERY), row stamping, and a scheduled cron for the VERIFY pass (`08`).

## L9 — Document the operative HOW, not just the WHAT (found by self-audit)
**Wrong:** The first playbook draft captured every named practice (36/37 PRESENT) but left the *operative decision rules* implicit — the actual tests the run applied lived only in the Italy corpus verdicts, not the method docs.
**Why:** A practice named ("admission bar ≥2 products") isn't reproducible without its arithmetic ("how do you count an undated annual-catalogue product?"). A fresh agent would diverge.
**How found:** ran a Workflow audit — 6 coverage agents (37 practices) + 2 adversarial gap-critics comparing method docs vs the Italy worked example. It surfaced 15 procedural gaps that were back-filled:
- promotion = standalone multi-day spine + distinct buyer + supplier base (not bare non-overlap)
- FOLD-INTO-NEW (reframe-and-absorb) reshape action; first-trip clause can fail on framing/reputational grounds
- admission-bar counting arithmetic (1.0 / 0.5 / "1.5 = THIN"); annual-catalogue exception
- inclusion/exclusion definitions (tour-not-course; operator-not-aggregator; named-not-title-only expert)
- 403/404 fallback (snippet-as-secondary-source, keep UNVERIFIED) + verification-debt artifact with HTTP status
- de-dup guards (aggregators, absorbed sub-brands) + how the cumulative known-list is assembled
- format-class field (group / bespoke-year-round / hybrid-course) affecting rankability
- theme-ID convention (assign at seed, never renumber, IT-05a/b on split)
- lens-completeness = enumerate-and-diff; pre-sweep overlap declaration
- concrete native-language example queries per axis
- closing reconciliation: FINAL must list all axis rounds + state two-level convergence
**Fix:** all 15 folded into docs 03–09; convention (anti-patterns + worked example per doc) made uniform.

## L10 — The deliverable itself reproduced the false convergence it warned against
**Wrong:** `italy_theme_map_FINAL.md` was written after round 4 and said "converged after 4 rounds"; round 5 (language+authority, +26 operators) ran later and was never folded back into FINAL. The method taught L7 but the artifact violated it.
**Why:** No step forced the FINAL artifact to reconcile against all later rounds.
**Fix:** Added the **closing-reconciliation rule** (`06`): FINAL must list every corpus round incl. axis-proofs and state convergence as two-level. FINAL header corrected to "theme-converged R4; operator-converged after R5."

## L11 — Two consumption modes: single-lens ranking vs multi-lens composition
**Insight (from reviewing `00`):** a traveller genuinely wants a multi-lens trip (Sicily = history + food + coast), but an expert-led *group tour* cannot be multi-lens without degrading to a generalist — that's breadth-over-depth. The two needs are different products, not a contradiction.
**Resolution:** the **theme stays single-lens** (the ranking unit, where depth + expert live). Multi-lens is a separate **trip-composition layer** (`11`) that stitches several ranked themes, with depth recovered only per-segment or via a bespoke designer — trade-off stated. A multi-era/multi-region single-subject theme (Sicily layered civilisations) is NOT multi-lens.
**Fix:** clarified the theme definition in `00` (one coherent subject; eras/regions OK, lenses split), added a "Two consumption modes" section, and created `11-trip-composition.md`. Earlier draft wording ("composite themes") risked inviting lens-bundling — corrected.

## L12 — N is a dial; refresh is additive; pilot small (from reviewing `01`)
**Decisions:** (1) "50 countries" is an example target, not fixed — N grows 10 → 50 → 100 → 150 as the method proves out; current scope is **top 10 only** until 100% confident. (2) Country-ranking refresh is **additive**: each run fetches the current top-N and ADDS new entrants but NEVER removes a country already researched (coverage only grows; raising N later just appends). (3) Define "country" explicitly (UN Tourism entity list; SAR handling e.g. HK/Macau) so the tail is reproducible; cite ranks with their data-year.
**Fix:** `01` rewritten — N-as-dial, additive-refresh rule, country-definition + boundary-churn + metric-caveat rules, canonical list in a stamped `country_ranking.md` (live pull, never hand-typed). README scope section added.

## L13 — Docs are agent execution specs, not human essays
**Reframe (user):** the playbook is consumed by a coding/AI agent. Optimise for execution, not reading.
**Form:** every step doc = INPUT/OUTPUT/NEXT contract · ordered imperative PROCEDURE · DECISION RULES as checkable conditions · concrete input→output EXAMPLE · ANTI-PATTERNS as failure checks. Drop motivational prose; keep "why" only to disambiguate.
**Fix:** `02-theme-seeding.md` rewritten as the reference shape; README "Document conventions" updated. Remaining docs (00, 01, 03–11) to be converted to the same shape.

## L14 — The axis set is itself a convergence target, and it's per-country & evolving
**Challenge (user):** "How are you sure it's only 5 axes?" Answer: not sure. 5 is empirical, not proven — exactly the L7 trap one level up (we thought 3 was enough → false convergence → found 2 more; a 6th can exist).
**Decisions:**
- Treat the axis list as a convergence target. Add an **axis-completeness critic** that asks "what DIMENSION are we blind to?" Promote a candidate only when it provably surfaces tours no existing axis finds.
- Axes are **per-country and evolving** — an island nation needs Format/vehicle (cruise); a pilgrimage-heavy country needs Affinity. The active set differs and updates as the country's discovery runs.
- This knowledge lives in **committed files, not session memory**: global `axes-registry.md` (baseline 5 + watchlist, seeds every country) + per-country `axes.md` (active/promoted/pending). Read at session start, append at end, promote upward — compounding like the lessons log.
- Caveat: current axes mix target-properties (channel/lens/region) and search-method (language/authority) — taxonomy may be re-cut later.
**Candidates on the watchlist:** format/vehicle, affinity/audience, media/creator, season/time, price-tier.
**Fix:** `03` axis-set-not-final + per-country/evolving sections; new `axes-registry.md`; `italy/axes.md` ledger (records that Italy converged on the baseline 5 only — format/affinity/media are known un-run dimensions); `04` axis-completeness critic; `06` persistence updated.

## L15 — Every enumeration is an evolving registry; nothing in memory through step 03
**Generalised from L14 (axes):** the same shape applies to *all* enumerations the method leans on — not just axes. Lenses and theme-archetypes were still static and in-memory → the same omission risk that missed "nature".
**Decisions:**
- **Lens list → `lens-registry.md`** (global, evolving): baseline lenses + candidate watchlist + promotion log. "nature/wildlife" recorded as the promotion that justifies the registry's existence.
- **Theme-archetype library → `theme-archetypes.md`** (global): recurring cross-country patterns (wine region, wildlife circuit, pilgrimage circuit, layered-civilisations…). Seeding *walks* it so free-recall gaps are caught.
- **Seed-completeness diff** added to `02`: enumerate baseline lenses + archetypes vs the country's draft themes; every one maps to a theme or a justified `thin/none`. Catches systemic misses at SEED, not discovery round 2.
- **Memory invariant (steps 01–03):** nothing the method depends on lives in session memory — all read from committed files and appended back; a fresh session reproduces the same seed. Training knowledge proposes; registries + corpus are the source of truth.
**Meta-shape:** an evolving registry = (not final) + (per-country) + (file-persisted) + (compounds via read→run→append→promote). Applies to axes, lenses, archetypes — and any future enumeration.
**Fix:** `lens-registry.md`, `theme-archetypes.md` created; `02` reads registries + runs the diff; README principle 9; this entry.

## L16 — The playbook must obey its own data-driven/self-evolving rule
**Found by self-audit (workflow):** the playbook preached data-driven + evolving registries but its OWN values were hardcoded literals duplicated across docs — 81 issues / 17 clusters. "5 axes", channels "A–H", `<21 days`, "Top 5", admission `2.0`, `2026-27`, cadences, N (10/50), lens lists, registry protocol, sources, aliases — all restated in prose, so registry growth didn't propagate and edits drifted (format-class already diverged; README self-contradicted on "50").
**Fix (foundation + workflow rewrite):**
- Single-source homes: `travel-config.md` (every named dial), `REGISTRY-PROTOCOL.md` (shared mechanics), `channel-registry.md` (stable ids, not letters), `sources-registry.md`, `operator-aliases.md`, `doc-manifest.md` (role→file slugs). Axis stage/role became multi-valued **tags** in `axes-registry.md`; `theme-archetypes.md` got the registry machinery it claimed; `lens-registry.md` vocabulary reconciled (+living-culture).
- All docs rewritten to reference names/tags/slugs, never literals; duplicated rules single-sourced + cross-referenced.
**Principle:** any tunable → a named dial in config; any enumeration → a registry under REGISTRY-PROTOCOL; any stage/role applicability → a tag, filtered not hand-named; any cross-ref → a manifest slug. Count/identity derived from the registry, never asserted in prose.
**Caveat:** residual literals slip in (bad "1.5" arithmetic; hand-listed axis names) — verify with a banned-literal check after each rewrite.

## L17 — Finish the medium/low hardcoding clusters
Closed the remaining audit clusters (12–17):
- **Tag vocabularies → `tags-registry.md`** (row `status`, `format-class`+rankability, `watch/leisure`, `strength`). Fixed the live drift between `corpus` and `ranking` (`private/bespoke/year-round` vs `private-bespoke-year-round`). Both now reference the registry.
- **Operative rules single-sourced**: promotion test owned by `discovery-loop`, cross-referenced (not restated) by `admission-bar`; L7 pinned in `lessons`, cross-referenced not re-narrated.
- **Theme-ID grammar + overflow** already moved to `THEME_ID_GRAMMAR`/`THEME_ID_OVERFLOW` (`travel-config.md`); `theme-seeding`/`corpus` cite them — silent 99-cap removed.
- **Cross-refs** are manifest slugs; the data file `country_ranking.md` vs the method doc `01-country-ranking.md` disambiguated in `doc-manifest.md` (GLOBAL DATA ARTIFACTS).
- **Italy specifics** removed from global prose (`11` operator names gone; rosters cite per-country files); historical figures kept only in lessons/promotion-logs.
- **Language axis genericized**: definition is "native + relevant study-travel source languages"; per-country language set is data in `<country>/axes.md` (Italy = IT/DE/FR). DE/FR/IT in `coverage-matrix` is an example query bank only.

## L18 — Anti-patterns weren't the only static class; OPEN ENUMERATIONS convention
**Found by the static-census audit (workflow, 321 constructs read across all files):** 15 STATIC-SHOULD-EVOLVE constructs, only ~3 of them anti-patterns. The other 12 are growable domain vocabularies with no compounding wire — round types, reshape actions, off-cadence triggers, diff dimensions, metric caveats, overlap dimensions, admission exclusion lists, special-interest sub-types, delivery modes. Plus a real drift: `REGISTRY-PROTOCOL` inlined 6 registries while `doc-manifest` had more — already drifted.
**Fix (tiered, not 12 new files):**
- **OPEN ENUMERATION** convention added to `REGISTRY-PROTOCOL`: a discovered-knowledge list stays in-doc but is `(open — append on discovery)`, each entry carries `Lnn`/country provenance, grows via read→run→append; promote to a full registry only on cross-doc reuse; a closed set is STATIC-OK with an escape hatch.
- **Anti-patterns = a view of the lessons-log**: each carries `Lnn` provenance; new lesson → append its check to the owning doc's ANTI-PATTERNS.
- **Promoted** the 2 that earned it: special-interest sub-types → `channel-registry`; admission disqualifier lists → `tags-registry`.
- **Fixed the drift**: `REGISTRY-PROTOCOL` derives its governed-registry list from `doc-manifest`, not inline.
- **The census is now the recurring guard**: any STATIC-SHOULD-EVOLVE lacking an open-tag/registry/escape = regression.
**Principle:** every enumeration the method leans on is either a registry, an open-in-doc append-list, or an explicitly-closed set with an escape hatch — never an unmarked static list.

## Meta-lesson
The **process** was the real first deliverable. It matured step-by-step from user corrections (each lesson above maps to one). Output (ranked Top-5s) comes *after* the method is right, because errors in the method multiply 50×. Get the method right on one country, then scale.

## Open / next
- Produce the first ranked Top-5 (Italy IT-01 Rome antiquity) as the output template, then scale to all 35 Italy themes, then countries #2–50.
- Italy operator corpus still needs LANGUAGE + AUTHORITY axes saturated per theme before ranking (round 5 opened them; not yet exhausted).
- Consider a structured (machine-diffable) corpus format to make the VERIFY pass fully mechanical.
