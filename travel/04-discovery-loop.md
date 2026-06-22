# 04 — Discovery Loop

AGENT SPEC. Surface the *whole field* of expert-led tours per theme by running the coverage matrix (`coverage-matrix`) — the baseline axes in `axes-registry.md` — in rounds, reshaping the theme map as evidence arrives, until discovery is dry (`admission-bar`). Discovery is meant to outgrow the seed: a round that only confirms the seed and adds nothing has failed (the seed was wrong at the edges by design — extend it).

INPUT:
- `<country>_theme_map_v0.md` (seed, from `theme-seeding`) — the starting theme map; later rounds read the latest `<country>_theme_map_v<N>.md`.
- The coverage matrix (`coverage-matrix`) — the baseline axes in `axes-registry.md` (identity/count derived from the registry, never restated here), with channel sub-types in `channel-registry.md` and the lens vocabulary in `lens-registry.md`. These are the axes each agent runs.
- Global registries: `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md`, `channel-registry.md` (read; do NOT enumerate from memory).
- Per-country `<country>/axes.md` (candidate axes promoted for this country).

OUTPUT:
- `<country>_theme_map_v<N>.md` — rewritten after each round (decisions + rationale).
- `corpus/round<N>_<cluster>.md` — raw inventories written verbatim by each agent (the durable evidence).
- APPENDS to `<country>/axes.md` and the global `axes-registry.md` when a new axis is proven (see DECISION RULES); promotion mechanics per `REGISTRY-PROTOCOL.md`.
- APPENDS to `lens-registry.md` / `theme-archetypes.md` / `channel-registry.md` when a new lens/archetype/channel surfaces (mechanics per `REGISTRY-PROTOCOL.md`).

NEXT: `admission-bar` (convergence test) reads the latest theme map + corpus to decide whether discovery is dry. `corpus` consumes the corpus + map as the audit trail. `composition` consumes the final theme map.

MEMORY INVARIANT: nothing here lives in session memory. Seed map, registries, candidate axes — all READ from committed files. Every raw finding is WRITTEN to a `corpus/round<N>_*.md` file by the agent itself (verbatim, no relay loss); every reshape decision is WRITTEN to `<country>_theme_map_v<N>.md`. A fresh session reproduces the same round from the files alone. The orchestrator keeps only short verdict summaries in context — the corpus file is the source of truth.

COMPOUNDING: lenses, archetypes, channels, and axes follow read → run → APPEND → PROMOTE per `REGISTRY-PROTOCOL.md`. Read the registry; run the round; APPEND any new entry to the per-country ledger (`<country>/axes.md`); PROMOTE to the owning global registry (`axes-registry.md`, `lens-registry.md`, `theme-archetypes.md`, `channel-registry.md`) so future countries inherit it. The axis set is itself a convergence target (`coverage-matrix`).

## PROCEDURE (one round; repeat until `admission-bar` says dry)
1. READ the latest `<country>_theme_map_v<N>.md`, the coverage matrix (`coverage-matrix`), and the registries. Pick the round TYPE (see DECISION RULES: cluster sweep, completeness-critic, or axis-proof).
2. **Cluster the themes** (e.g. by macro-region) and dispatch one discovery agent per cluster, in parallel (`orchestration`).
3. Each agent runs the matrix for its cluster: every channel sub-type in `channel-registry.md`, the relevant lenses in `lens-registry.md`, every region, plus the axes tagged `role:axis-proof` (run as their own passes). Multiple searches per axis — be exhaustive, never stop at a handful — but BOUNDED by `MAX_SEARCHES_PER_AXIS` (`travel-config.md` COST/BUDGET): the per-axis search count is capped at the dial, not open-ended.
3a. **Per-cell search-effort budget.** Each matrix cell (axis × region) carries `PER_COUNTRY_SEARCH_BUDGET`-derived effort. When a cell hits its budget with ONLY disqualified/aggregator results (no admissible operator), mark it `junk-saturated`/`low-signal` (`tags-registry.md`) WITH provenance (queries run, what disqualified each hit) and STOP searching that cell — do NOT keep burning budget on it. `junk-saturated`/`low-signal` is distinct from empty-gap (no result at all) and covered (admissible result found). Surface every `junk-saturated` cell in the country verdict (`tags-registry.md`, country `outcome`).
4. Each agent **writes raw findings directly to its own corpus file** (`corpus/round<N>_<cluster>.md`) and returns only a short verdict summary. Keeps orchestrator context lean and the save verbatim (`corpus`).
5. Agents report operators/tours ONLY via a live URL. Named guide and/or `CURRENT_SEASON` dated departure unconfirmed → mark **UNVERIFIED**. Invent nothing.
5a. **Capture leads:** signals that don't fit the row schema (theme/sub-lens hints, channel/affinity/archetype/authority signals, a guide spanning themes) → typed rows in `<country>/leads.md` with provenance, per `REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING. Route each; new-coverage leads dirty the affected unit.
6. After the agents return, apply the **Reshape Rubric** below before any reshape action (ADD/SPLIT, MERGE, DEMOTE/FOLD, FOLD-INTO-NEW, PROMOTE, CROSS-CUT).
7. If a new lens/archetype/channel/axis surfaced, APPEND it to the per-country ledger and PROMOTE to the owning global registry (COMPOUNDING; mechanics per `REGISTRY-PROTOCOL.md`). **On promotion, apply INVALIDATION (`REGISTRY-PROTOCOL.md`): mark every already-swept theme `dirty` on the new axis (or, for a promoted **channel** sub-type, on the CHANNEL axis restricted to that sub-type) and re-sweep only the new entry for them in a later round before convergence.** A new axis at round 5 means rounds 1–4 themes were under-swept — re-sweep, don't ship them stale.
8. WRITE the result to a new `<country>_theme_map_v<N>.md` (decisions + rationale). Leave the corpus files in place. Stop; hand to `admission-bar` to test convergence.

### Loop termination (the round loop, bounded)
The round loop terminates on EITHER condition (`travel-config.md` COST/BUDGET dials — `MAX_DISCOVERY_ROUNDS`, `MAX_SEARCHES_PER_AXIS`, `MAX_AGENTS_PER_COUNTRY`, `PER_COUNTRY_SEARCH_BUDGET` — referenced here, `admission-bar`, `orchestration`):
- **discovery-dry** — `admission-bar` says dry (the convergence path); OR
- **budget-exhausted-with-logged-residual** — a dial (`MAX_DISCOVERY_ROUNDS` / `PER_COUNTRY_SEARCH_BUDGET` / `MAX_AGENTS_PER_COUNTRY`) is hit before dry. This is the explicit **NON-CONVERGENCE STOP**: it is only valid with a LOGGED residual (which axes / regions / themes remain unfinished). Budget-exhausted-before-convergence ⇒ the artifact is stamped **INCOMPLETE** (naming the unfinished axes/regions/themes).
- NOTE: the fixed-point "termination proof" (the loop provably ends because the axis set itself converges, COMPOUNDING) is a correctness argument, NOT an operational budget — it does not bound spend. The dials above are the operational stop; the proof is not a substitute for them.

## DECISION RULES

### Round type — pick per round (open — append on discovery; `REGISTRY-PROTOCOL.md`)
- **Cluster sweep** (typically rounds 1–2): broad coverage by region cluster. (provenance: Italy R1–R2)
- **Completeness-critic round**: adversarial agents whose only job is to find what's missing — one per `role:convergence-gate` axis (lenses, channels/operators, regions), plus one for borderline validation. These catch the long tail. (provenance: L6, Italy R3–R4)
- **Axis-proof round**: explicitly run every axis tagged `role:axis-proof` in `axes-registry.md` as its own sweep — the axes most often skipped and most likely to break a false convergence. (provenance: L7, Italy R5)

### Completeness-critic mechanics
- **Lens-completeness is enumerate-and-diff, not vibes**: the lens-critic must list the FULL lens inventory from `lens-registry.md` against the current theme map and flag every lens with **zero themes** for a justified test. This is the mechanism that would have caught "nature was a systemic miss" on round 1 instead of round 2.
- **Pre-sweep overlap declaration**: before counting products for a candidate lens, write an explicit overlap check distinguishing it from every adjacent existing theme along the overlap dimensions (open — append on discovery; `REGISTRY-PROTOCOL.md`): period / ideology / region / discipline (provenance: L9, Italy R3) (e.g. "Gardens ≠ Lakes villas&gardens; Galileo ≠ Florence Renaissance art; WW1 Alpine Front ≠ Sicily WWII Husky; Jewish heritage ≠ Christian/papal Rome"). Stops re-discovering a covered theme under a new label.
- **Axis-completeness critic**: one critic asks not "what operator/theme did we miss?" but **"what DIMENSION are we blind to?"** — test the candidate axes from `axes-registry.md` (the CANDIDATE WATCHLIST) for this country. PROMOTE a candidate axis IFF it provably surfaces tours no existing axis finds → record in `<country>/axes.md`, then escalate (PROMOTE) to the global `axes-registry.md` per `REGISTRY-PROTOCOL.md` (a promoted candidate must declare its `stage`/`role` tags so the gates/sweeps pick it up automatically).

### Reshape actions (apply after each round) (open — append on discovery; `REGISTRY-PROTOCOL.md`)
- **ADD / SPLIT** a theme IFF the Reshape Rubric action is `ADD/SPLIT`. (provenance: L3, Italy R1)
- **MERGE** two themes IFF the Reshape Rubric action is `MERGE`. (provenance: L3)
- **DEMOTE / FOLD** a theme IFF the Reshape Rubric action is `DEMOTE/FOLD`. (provenance: L3)
- **FOLD-INTO-NEW (reframe-and-absorb)** IFF the Reshape Rubric action is `FOLD-INTO-NEW`. Distinct from folding into an existing theme. (provenance: L9, Italy)
- **CROSS-CUT**: keep cross-regional themes IFF operators sell them as one trip within `MAX_TRIP_DAYS` (Etruscan, Magna Graecia, Caravaggio trail, opera). (provenance: Italy)

### Reshape Rubric
Apply this table using live-source rows from the corpus. If unclear, keep the current structure and log the ambiguity; do not promote on taste.

| action | required evidence | blocked by |
|---|---|---|
| ADD/SPLIT | candidate has a distinct primary lens or expert type from `lens-registry.md`, >= `MIN_CREDENTIALED_PRODUCTS` credited products, and at least one operator/product not shared with the parent theme | same expert type and same products as parent; only one partial product |
| MERGE | >=80% of rankable products/operators overlap, or the same product would rank in both themes | different primary lens, different expert type, or different buyer/supplier base |
| DEMOTE/FOLD | credited score below `ADMISSION_BAR`, or only day/private fragments where the parent theme supplies the multi-day spine | >= `ADMISSION_BAR` plus distinct lens/expert/product base |
| FOLD-INTO-NEW | candidate fails standalone but supplies a spine for a broader parent not currently present; at least one multi-day product anchors the broader parent | no multi-day spine, or an existing parent already covers the products |
| CROSS-CUT | >=2 regions are sold as one product within `MAX_TRIP_DAYS` and share one primary lens/expert type | region combination requires multiple lenses or composition |
| KEEP-AS-IS | evidence changes no gate above | unresolved ambiguity; log it instead of reshaping |

### PROMOTE a sub-tag to a theme — IFF it passes BOTH promotion tests (not bare non-overlap)
This promotion test is owned here; other docs cross-ref it rather than restating it.
1. **Standalone multi-day spine exists** — there is at least one multi-day itinerary built around the sub-tag. If the sites are only sold à la carte / as day-products / embedded inside another theme's tour → it **stays a sub-tag** (Underground Rome failed this → kept sub-tag; Naples-city art passed → promoted).
2. **Distinct buyer + distinct supplier base** from the parent (WWII Husky = military-history buyer + military-specialist operators; Etna wine = oenophile buyer + DOC-estate/sommelier suppliers; Palladio = architecture buyer + architectural-historian leaders — none served by the generalist parent theme).

### Framing override
- Framing/reputation can only override a candidate via `FOLD-INTO-NEW` when the Reshape Rubric shows a broader parent spine and the admission rubric marks the standalone frame WATCH/FAIL on representative fit.

## EXAMPLE (Italy — round-by-round input → output)
- R1 cluster sweeps (one agent per region cluster) → +6 themes/reshapes.
- R2 targeted (nature was a systemic miss) → +6 incl. Apennine wildlife + volcanology.
- R3 completeness critics → +4 admitted, ~4 folded.
- R4 borderline + final critic → +2, then theme-dry.
- R5 axis-proof (the `role:axis-proof` axes — Language + Authority-index) → +26 operators (no new themes); this round promoted Language + Authority-index from candidate to baseline (see `axes-registry.md` PROMOTION LOG).
- Full trail in `italy/italy_theme_map_v0..FINAL.md` and `italy/corpus/`.

## ANTI-PATTERNS (checks — fail the round if true)
This block is a VIEW of `10-lessons-log.md` (open — append the check when a new lesson lands; tag `Lnn`; `REGISTRY-PROTOCOL.md`).
- An agent relays findings through the orchestrator instead of writing them verbatim to its own `corpus/round<N>_<cluster>.md` (relay loss; violates the memory invariant). (L5)
- An operator/tour reported without a live URL, or with an unconfirmed named guide / `CURRENT_SEASON` dated departure not marked **UNVERIFIED**.
- Inventing an operator, tour, or departure.
- Stopping at a handful of searches per axis instead of running multiple. (L2)
- Looping past `MAX_SEARCHES_PER_AXIS` on a cell, or continuing to burn budget on a cell already marked `junk-saturated`/`low-signal` (`tags-registry.md`) instead of stopping it and surfacing it in the country verdict. (C11)
- Declaring failure-to-converge / a NON-CONVERGENCE STOP (budget-exhausted) without LOGGING the residual (which axes/regions/themes unfinished) and stamping the artifact INCOMPLETE. (C11)
- Treating the fixed-point termination proof as an operational budget (it bounds correctness, not spend — the COST/BUDGET dials bound spend). (C11)
- Lens-critic judging completeness by vibes instead of enumerating the full `lens-registry.md` inventory and diffing against the theme map. (L9, L15)
- Counting products for a candidate lens without first writing the pre-sweep overlap declaration (re-discovers a covered theme under a new label). (L9)
- PROMOTING a sub-tag to a theme on bare non-overlap, without BOTH promotion tests (standalone multi-day spine AND distinct buyer + supplier). (L9)
- Skipping the `role:axis-proof` sweeps (these break false convergence). (L7)
- Discovering a new lens/archetype/channel/axis and not appending it to the per-country ledger and promoting to the owning global registry per `REGISTRY-PROTOCOL.md` (no compounding). (L14, L15)
- A round that only confirms the seed and adds nothing (the seed is wrong at the edges by design — extend it). (L1, L10)
