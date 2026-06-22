# 04 — Discovery Loop

## Purpose
Surface the *whole field* of expert-led tours per theme by running the 5-axis matrix (`03`) in rounds, reshaping the theme map as evidence arrives, until discovery is dry (`05`).

## Round mechanics
1. **Cluster the themes** (e.g. by macro-region) and dispatch one discovery agent per cluster, in parallel (see `09-agent-orchestration.md`).
2. Each agent runs the matrix for its cluster: every CHANNEL, the relevant LENSES, every REGION, plus LANGUAGE and AUTHORITY-INDEX passes. Multiple searches per axis — never stop at a handful.
3. Each agent **writes its raw findings directly to its own corpus file** (`corpus/round<N>_<cluster>.md`) and returns only a short verdict summary. This keeps the orchestrator's context lean and the save verbatim (no relay loss). See `06`.
4. Agents only report operators/tours found via a live URL. Named guide and/or current-season dated departure unconfirmed → mark **UNVERIFIED**. Invent nothing.

## Reshape actions (apply after each round)
- **ADD / SPLIT** a theme when discovery reveals a deep, distinct expert-led market the seed missed (e.g. Umbria → art vs St-Francis pilgrimage; Sicily WWII Husky as its own theme).
- **MERGE** when two themes' tour products are really the same.
- **DEMOTE / FOLD** when a theme has no real expert-led depth (e.g. Amalfi/Capri → folds into Naples; Cinque Terre → leisure).
- **PROMOTE** a sub-tag to a theme when it clears the bar (`05`).
- **CROSS-CUT**: keep cross-regional themes when operators sell them as one trip (Etruscan, Magna Graecia, Caravaggio trail, opera).

## Round types
- **Cluster sweeps** (rounds 1–2 typically): broad coverage by region cluster.
- **Completeness-critic rounds**: adversarial agents whose only job is to find what's missing — one for LENSES, one for CHANNELS/operators, one for REGIONS, one for borderline validation. These catch the long tail.
- **Axis-proof rounds**: explicitly run LANGUAGE and AUTHORITY-INDEX as their own sweeps — these are the axes most often skipped and most likely to break a false convergence.

## Worked example (Italy)
R1 cluster sweeps (5 agents) → +6 themes/reshapes. R2 targeted (nature was a systemic miss) → +6 incl. Apennine wildlife + volcanology. R3 completeness critics → +4 admitted, ~4 folded. R4 borderline + final critic → +2, then theme-dry. R5 axis-proof (language + authority) → +26 operators (no new themes). Full trail in `.context/italy_theme_map_v0..FINAL.md` and `.context/corpus/`.

## Output
- Updated `<country>_theme_map_v<N>.md` after each round (decisions + rationale).
- `corpus/round<N>_*.md` raw inventories (the durable evidence).
