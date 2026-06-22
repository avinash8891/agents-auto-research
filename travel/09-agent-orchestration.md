# 09 — Agent Orchestration

AGENT SPEC. Orchestrator role: dispatch parallel subagents to do breadth-discovery and convergence, keep only the conclusions, never the file dumps. Subagents write raw output to their own files and return only short verdicts — this keeps orchestrator context lean enough to scale up the `GROWTH_LADDER` (`config`) toward `TARGET_SCALE`.

INPUT: prior `<abs path>/corpus/round*.md` files (all earlier rounds), plus the per-cluster/per-theme scope and the global registries (`lens-registry`, `axes-registry`, `archetypes`, `channel-registry`) for the axis/lens/channel vocabularies. The orchestrator READS these — it does not work from memory. The set of axes to sweep is the baseline axes in `axes-registry` (count derived from that file), filtered by their `stage`/`role` tags — never a hand-typed axis list.
OUTPUT: new `<abs path>/corpus/round<N>_<cluster>.md` (one file per discovery agent) and `rankings/<theme-id>.md` (one file per ranking agent). The orchestrator's own working state is the cumulative already-known list, assembled mechanically from files (see PROCEDURE), not retained in session memory.
NEXT: `ranking` consumes corpus + saturation; `corpus` (schema/audit-trail) governs row format and ID stability; `freshness` (verify loop) re-checks the corpus far more often than discovery re-runs (`VERIFY_CADENCE` vs `DISCOVERY_CADENCE`, `config`).

MEMORY INVARIANT: nothing the orchestration depends on lives in session memory. Every agent input (already-known list, axis lists, scope) is READ from a committed file; every agent output is WRITTEN to a committed corpus/ranking file via the file-write tool. A fresh session reproduces the same dispatch from the corpus + registries alone. "Extend, don't re-discover" is reproducible because the already-known list is recomputed from files each round, not remembered. (Memory invariant shared across the method — `registry-protocol`.)

COMPOUNDING / SELF-LEARNING: corpus accrues across rounds. Read prior corpus → run new agents → APPEND new operators to `corpus/round<N>_*.md` → the next round's already-known list (per-country ledger) PROMOTES them so no operator is re-discovered. New lenses/archetypes/channels/axes an agent surfaces are appended to the relevant global registry under the shared append→promote cycle (`registry-protocol`) so future countries inherit them. (Promotion bar and mechanics are owned by `registry-protocol`; the discovery promotion test is owned by `discovery-loop`.)

## DISPATCH PATTERNS
- **Cluster fan-out** (discovery): one agent per macro-region cluster, dispatched together in a single batch so they run concurrently.
- **Critic fan-out** (convergence): parallel adversarial agents, each owning one axis from `axes-registry` (lenses, channels/operators, regions, borderline validation).
- **Axis-proof fan-out** (anti-false-convergence): a dedicated agent per axis tagged `role:axis-proof` in `axes-registry` — these are the false-convergence gate. Do not name the proof axes by hand; read them off the tag filter so a newly promoted axis-proof axis is swept automatically.
- **Per-theme ranking**: one agent per theme (or small cluster) — saturates axes + verifies + ranks, writing to `rankings/<theme-id>.md` (theme-id follows `THEME_ID_GRAMMAR`, `config`).

## PROCEDURE

### A. Build the cumulative already-known list (source of truth — done before every round)
The orchestrator assembles the known-operator list mechanically. It is NOT from memory.
1. Grep/concatenate the **Operator column** from every prior `corpus/round*.md`.
2. Dedup into a flat name list, collapsing absorbed/sub-brands → parent and dropping aggregators per `operator-aliases`.
3. Pass it **verbatim** to every agent as the `ALREADY KNOWN — build beyond, don't re-report` block.
4. Require each agent to close its file with a **de-dup guards** note (`corpus`): aggregators excluded, sub-brands collapsed, prior-captured excluded (alias/exclusion authority = `operator-aliases`).
This is what makes "extend, don't re-discover" reproducible rather than a hope.

### B. Size and dispatch the fleet
5. Scale the fleet to the task: a few agents for a small country, more for a big diverse one. Do not over-spawn.
6. Give each agent its own corpus file (`round<N>_<cluster>.md`) — never a shared file.
7. Dispatch the batch concurrently (cluster fan-out for discovery; critic/axis-proof fan-out for convergence).

### C. Discovery agent prompt template (issue verbatim, fill the `<…>`)
```
Travel-research discovery agent. EXHAUSTIVE discovery of expert-led tours for <cluster/theme>, then report theme-structure changes. Use real web search/fetch. ONLY report operators/tours found via a live URL. Flag UNVERIFIED where named guide or CURRENT_SEASON-dated departure unconfirmed. Invent nothing.

CLUSTER/THEME: <list themes + scope>
METHOD: search EVERY baseline axis in axes-registry (filter the discovery sweep to axes tagged stage:discovery; run a dedicated extra sweep for each axis tagged role:axis-proof). For the CHANNEL axis sweep every channel sub-type id in channel-registry; for the LENS axis use the lenses in lens-registry; for the REGION axis use every first-level region <list>; for any axis whose values are per-country data (e.g. language set, authority-index directories) read them from <country>/axes.md. Multiple searches per axis; do NOT stop at a handful.
ALREADY KNOWN (build beyond, don't re-report): <captured operator list>
RESHAPE QUESTIONS: <split/merge/promote/demote tests for this cluster>
OUTPUT: Write raw findings DIRECTLY to <abs path>/corpus/round<N>_<cluster>.md via the file-write tool. Schema (corpus doc): Operator | Channel | Tour | Expert(named) | Format | CURRENT_SEASON? | URL. End each section with a VERDICT line.
RETURN TO ME ONLY: the verdict(s), one line each, + confirm file written.
```

### D. Collect and integrate
8. Receive only the one-line verdict(s) + file-written confirmation from each agent. Do NOT let raw dumps flow back through the orchestrator.
9. Treat every returned finding as a **hypothesis** (lesson `L7`, `lessons`): spot-check named guides / `CURRENT_SEASON`-dated departures against a live URL before any operator enters a ranking.
10. APPEND new operators to the corpus; the next round's already-known list (step 1) will pick them up automatically.
11. If an agent surfaced a new lens / archetype / channel / axis not in the registries → APPEND it to that registry's per-country watchlist and promote on evidence (`registry-protocol`; the discovery-side promotion test is owned by `discovery-loop`) so future countries inherit it.

## DECISION RULES
- Agent gets the already-known list IFF you want extension not re-discovery — so ALWAYS pass it. Omit it → agents re-discover.
- Corpus write path is **absolute** IFF the write is to land anywhere; relative/omitted paths → corpus writes land nowhere.
- All agents use the **same row schema** (`corpus`) IFF files are to merge cleanly. Divergent schema → un-mergeable corpus.
- A finding enters a ranking IFF its named guide/date was spot-checked against a live URL (verify, don't trust).
- One file per agent IFF parallel — shared file → write collisions.
- Fleet size scales with country breadth: small/uniform → few agents; large/diverse → more. Neither over- nor under-spawn.
- Axes to sweep/gate are read by tag filter from `axes-registry` (`stage:discovery` for sweeps, `role:axis-proof` for dedicated proof agents, `role:convergence-gate` for the dry-axis check), never hardcoded — a newly promoted axis is picked up automatically.
- Re-run DISCOVERY (expensive, `DISCOVERY_CADENCE`) rarely; re-run VERIFY (cheap, `VERIFY_CADENCE`) often (`freshness`).

## COST DISCIPLINE
Discovery is the expensive phase. Reuse the corpus as the seed (don't re-discover), merge operator-saturation into ranking (`ranking`), and run the cheap VERIFY loop (`freshness`) far more often than the expensive DISCOVERY loop (`VERIFY_CADENCE` ≫ `DISCOVERY_CADENCE`, `config`).

## EXAMPLE (Italy)
A discovery round for Italy. The orchestrator first builds the already-known list by grepping the Operator column of the prior `italy/corpus/round*.md` files, dedupes (collapsing absorbed/sub-brands → parent per `operator-aliases`), and gets a flat verbatim list. It then fans out one discovery agent per macro-region cluster (e.g. North, Centre, South+Islands), each given:
- CLUSTER/THEME: the cluster's seed themes (theme-ids per `THEME_ID_GRAMMAR`, e.g. the Imperial-Rome / Tuscan-Renaissance / wine / Etruscan cross-regional seeds — full roster in `italy/italy_theme_map_v0.md`).
- METHOD: sweep every `stage:discovery` axis in `axes-registry`, with a dedicated extra sweep for each `role:axis-proof` axis; channel sub-types from `channel-registry`, lenses from `lens-registry`, the Italian region checklist, and Italy's language/authority-index values from `italy/axes.md`.
- ALREADY KNOWN: the deduped operator list, verbatim.
- OUTPUT path: `italy/corpus/round<N>_centre.md` (absolute), schema `Operator | Channel | Tour | Expert(named) | Format | CURRENT_SEASON? | URL` (`corpus`).

Each agent writes its file and returns one verdict line, e.g. "Centre cluster: +6 new operators (4 verified, 2 UNVERIFIED guide); confirms the Tuscan/wine seed split holds; file written." The orchestrator never sees the raw rows. Italy grew from its v0 seed (`italy/italy_theme_map_v0.md`) to the converged map (`italy/italy_theme_map_FINAL.md`) through these rounds — the corpus, not memory, carried operators forward between them.

## ANTI-PATTERNS (checks — fail the step if true)
- Over-spawning agents for a small country, or under-spawning for a big diverse one (scale the fleet to the task).
- Omitting the already-known list → agents re-discover instead of extending.
- Omitting absolute paths → corpus writes land nowhere.
- Letting agents return raw dumps through the orchestrator → bloats context, loses verbatim fidelity.
- Trusting agent findings as fact → they are hypotheses; spot-check named guides/dates before ranking (`L7`, `lessons`).
- Letting parallel agents share one file → write collisions; one file each.
- Building the already-known list from session memory instead of grepping the corpus → breaks the memory invariant and "extend, don't re-discover."
- Agents using divergent row schemas → un-mergeable corpus (`corpus`).
- Hardcoding which axes to sweep/prove instead of filtering `axes-registry` by `stage`/`role` tags → a newly promoted axis silently goes unswept.
- Discovering a new lens/archetype/channel/axis and not appending it to the relevant registry watchlist → no compounding (`registry-protocol`).
