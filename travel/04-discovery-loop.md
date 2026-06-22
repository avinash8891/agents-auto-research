# 04 — Discovery Loop

AGENT SPEC. Surface the *whole field* of expert-led tours per theme by running the 5-axis matrix (`03`) in rounds, reshaping the theme map as evidence arrives, until discovery is dry (`05`). Discovery is meant to outgrow the seed: a round that only confirms the seed and adds nothing has failed (the seed was wrong at the edges by design — extend it).

INPUT:
- `<country>_theme_map_v0.md` (seed, from `02`) — the starting theme map; later rounds read the latest `<country>_theme_map_v<N>.md`.
- The coverage matrix `03` (CHANNELS A–H, LENSES, REGIONS, LANGUAGE, AUTHORITY-INDEX) — the axes each agent runs.
- Global registries: `lens-registry.md`, `theme-archetypes.md`, `axes-registry.md` (read; do NOT enumerate from memory).
- Per-country `<country>/axes.md` (candidate axes promoted for this country).

OUTPUT:
- `<country>_theme_map_v<N>.md` — rewritten after each round (decisions + rationale).
- `corpus/round<N>_<cluster>.md` — raw inventories written verbatim by each agent (the durable evidence).
- APPENDS to `<country>/axes.md` and the global `axes-registry.md` when a new axis is proven (see DECISION RULES).
- APPENDS to `lens-registry.md` / `theme-archetypes.md` when a new lens/archetype surfaces.

NEXT: `05` (convergence test) reads the latest theme map + corpus to decide whether discovery is dry. `06` consumes the corpus + map as the audit trail. `11` (composition) consumes the final theme map.

MEMORY INVARIANT: nothing here lives in session memory. Seed map, registries, candidate axes — all READ from committed files. Every raw finding is WRITTEN to a `corpus/round<N>_*.md` file by the agent itself (verbatim, no relay loss); every reshape decision is WRITTEN to `<country>_theme_map_v<N>.md`. A fresh session reproduces the same round from the files alone. The orchestrator keeps only short verdict summaries in context — the corpus file is the source of truth.

COMPOUNDING: lenses, archetypes, and axes follow read → run → APPEND → PROMOTE. Read the registry; run the round; APPEND any new lens/archetype/axis to the per-country file (`<country>/axes.md`); PROMOTE to the global registry (`axes-registry.md`, `lens-registry.md`, `theme-archetypes.md`) so future countries inherit it. The axis set is itself a convergence target (`03`).

## PROCEDURE (one round; repeat until `05` says dry)
1. READ the latest `<country>_theme_map_v<N>.md`, the `03` matrix, and the registries. Pick the round TYPE (see DECISION RULES: cluster sweep, completeness-critic, or axis-proof).
2. **Cluster the themes** (e.g. by macro-region) and dispatch one discovery agent per cluster, in parallel (`09-agent-orchestration.md`).
3. Each agent runs the matrix for its cluster: every CHANNEL, the relevant LENSES, every REGION, plus LANGUAGE and AUTHORITY-INDEX passes. Multiple searches per axis — never stop at a handful.
4. Each agent **writes raw findings directly to its own corpus file** (`corpus/round<N>_<cluster>.md`) and returns only a short verdict summary. Keeps orchestrator context lean and the save verbatim (`06`).
5. Agents report operators/tours ONLY via a live URL. Named guide and/or current-season dated departure unconfirmed → mark **UNVERIFIED**. Invent nothing.
6. After the agents return, apply the **reshape actions** (ADD/SPLIT, MERGE, DEMOTE/FOLD, FOLD-INTO-NEW, PROMOTE, CROSS-CUT — see DECISION RULES) to the theme map.
7. If a new lens/archetype/axis surfaced, APPEND it to the per-country file and PROMOTE to the global registry (COMPOUNDING).
8. WRITE the result to a new `<country>_theme_map_v<N>.md` (decisions + rationale). Leave the corpus files in place. Stop; hand to `05` to test convergence.

## DECISION RULES

### Round type — pick per round
- **Cluster sweep** (typically rounds 1–2): broad coverage by region cluster.
- **Completeness-critic round**: adversarial agents whose only job is to find what's missing — one for LENSES, one for CHANNELS/operators, one for REGIONS, one for borderline validation. These catch the long tail.
- **Axis-proof round**: explicitly run LANGUAGE and AUTHORITY-INDEX as their own sweeps — the axes most often skipped and most likely to break a false convergence.

### Completeness-critic mechanics
- **Lens-completeness is enumerate-and-diff, not vibes**: the lens-critic must list the FULL lens inventory from `03` against the current theme map and flag every lens with **zero themes** for a justified test. This is the mechanism that would have caught "nature was a systemic miss" on round 1 instead of round 2.
- **Pre-sweep overlap declaration**: before counting products for a candidate lens, write an explicit overlap check distinguishing it from every adjacent existing theme by period / ideology / region / discipline (e.g. "Gardens ≠ Lakes villas&gardens; Galileo ≠ Florence Renaissance art; WW1 Alpine Front ≠ Sicily WWII Husky; Jewish heritage ≠ Christian/papal Rome"). Stops re-discovering a covered theme under a new label.
- **Axis-completeness critic**: one critic asks not "what operator/theme did we miss?" but **"what DIMENSION are we blind to?"** — test the candidate axes from `axes-registry.md` (format/vehicle, affinity, media/creator, season, price-tier) for this country. PROMOTE a candidate axis IFF it provably surfaces tours no existing axis finds → record in `<country>/axes.md`, then escalate (PROMOTE) to the global `axes-registry.md`.

### Reshape actions (apply after each round)
- **ADD / SPLIT** a theme IFF discovery reveals a deep, distinct expert-led market the seed missed (e.g. Umbria → art vs St-Francis pilgrimage; Sicily WWII Husky as its own theme).
- **MERGE** two themes IFF their tour products are really the same.
- **DEMOTE / FOLD** a theme IFF it has no real expert-led depth (e.g. Amalfi/Capri → folds into Naples; Cinque Terre → leisure).
- **FOLD-INTO-NEW (reframe-and-absorb)**: when a candidate fails the bar standalone but is real material that belongs in a *newly-framed* parent theme that doesn't yet exist (e.g. Rationalist/Fascist architecture → folded into a new "Milan & 20th-c architecture & design" theme, keeping Como/Terragni as its spine). Distinct from folding into an existing theme.
- **CROSS-CUT**: keep cross-regional themes IFF operators sell them as one trip (Etruscan, Magna Graecia, Caravaggio trail, opera).

### PROMOTE a sub-tag to a theme — IFF it passes BOTH promotion tests (not bare non-overlap)
1. **Standalone multi-day spine exists** — there is at least one multi-day itinerary built around the sub-tag. If the sites are only sold à la carte / as day-products / embedded inside another theme's tour → it **stays a sub-tag** (Underground Rome failed this → kept sub-tag; Naples-city art passed → promoted).
2. **Distinct buyer + distinct supplier base** from the parent (WWII Husky = military-history buyer + military-specialist operators; Etna wine = oenophile buyer + DOC-estate/sommelier suppliers; Palladio = architecture buyer + architectural-historian leaders — none served by the generalist parent theme).

### Framing override
- The **first-trip-representative** clause can fail on *framing/reputational* grounds, not only on depth — a standalone "fascist architecture" theme is awkward for a first trip even where products exist, so it is reframed (FOLD-INTO-NEW), not stood up.

## EXAMPLE (Italy — round-by-round input → output)
- R1 cluster sweeps (5 agents) → +6 themes/reshapes.
- R2 targeted (nature was a systemic miss) → +6 incl. Apennine wildlife + volcanology.
- R3 completeness critics → +4 admitted, ~4 folded.
- R4 borderline + final critic → +2, then theme-dry.
- R5 axis-proof (language + authority) → +26 operators (no new themes).
- Full trail in `italy/italy_theme_map_v0..FINAL.md` and `italy/corpus/`.

## ANTI-PATTERNS (checks — fail the round if true)
- An agent relays findings through the orchestrator instead of writing them verbatim to its own `corpus/round<N>_<cluster>.md` (relay loss; violates the memory invariant).
- An operator/tour reported without a live URL, or with an unconfirmed named guide / current-season dated departure not marked **UNVERIFIED**.
- Inventing an operator, tour, or departure.
- Stopping at a handful of searches per axis instead of running multiple.
- Lens-critic judging completeness by vibes instead of enumerating the full `03` lens inventory and diffing against the theme map.
- Counting products for a candidate lens without first writing the pre-sweep overlap declaration (re-discovers a covered theme under a new label).
- PROMOTING a sub-tag to a theme on bare non-overlap, without BOTH promotion tests (standalone multi-day spine AND distinct buyer + supplier).
- Skipping the LANGUAGE / AUTHORITY-INDEX axis-proof sweeps (these break false convergence).
- Discovering a new lens/archetype/axis and not appending it to the per-country file and the global registry (no compounding).
- A round that only confirms the seed and adds nothing (the seed is wrong at the edges by design — extend it).
