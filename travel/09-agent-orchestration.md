# 09 — Agent Orchestration

AGENT SPEC. Orchestrator role: dispatch parallel subagents to do breadth-discovery and convergence, keep only the conclusions, never the file dumps. Subagents write raw output to their own files and return only short verdicts — this keeps orchestrator context lean enough to scale to 50 countries.

INPUT: prior `<abs path>/corpus/round*.md` files (all earlier rounds), plus the per-cluster/per-theme scope and the global registries (`lens-registry.md`, `axes-registry.md`, `theme-archetypes.md`) for the axis lists. The orchestrator READS these — it does not work from memory.
OUTPUT: new `<abs path>/corpus/round<N>_<cluster>.md` (one file per discovery agent) and `rankings/<theme-id>.md` (one file per ranking agent). The orchestrator's own working state is the cumulative already-known list, assembled mechanically from files (see PROCEDURE), not retained in session memory.
NEXT: `05` (ranking) consumes corpus + saturation; `06` (schema/audit-trail) governs row format and ID stability; `08` (verify loop) re-checks the corpus far more often than discovery re-runs.

MEMORY INVARIANT: nothing the orchestration depends on lives in session memory. Every agent input (already-known list, axis lists, scope) is READ from a committed file; every agent output is WRITTEN to a committed corpus/ranking file via the file-write tool. A fresh session reproduces the same dispatch from the corpus + registries alone. "Extend, don't re-discover" is reproducible because the already-known list is recomputed from files each round, not remembered.

COMPOUNDING / SELF-LEARNING: corpus accrues across rounds. Read prior corpus → run new agents → APPEND new operators to `corpus/round<N>_*.md` → the next round's already-known list (per-country ledger) PROMOTES them so no operator is re-discovered. New lenses/archetypes/channels an agent surfaces are appended to the global registries (`lens-registry.md`, `axes-registry.md`, `theme-archetypes.md`) so future countries inherit them.

## DISPATCH PATTERNS
- **Cluster fan-out** (discovery): one agent per macro-region cluster, dispatched together in a single batch so they run concurrently.
- **Critic fan-out** (convergence): parallel adversarial agents, each owning one axis — lenses, channels/operators, regions, borderline validation.
- **Axis-proof** (anti-false-convergence): dedicated LANGUAGE and AUTHORITY-INDEX agents.
- **Per-theme ranking**: one agent per theme (or small cluster) — saturates axes + verifies + ranks, writing to `rankings/<theme-id>.md`.

## PROCEDURE

### A. Build the cumulative already-known list (source of truth — done before every round)
The orchestrator assembles the known-operator list mechanically. It is NOT from memory.
1. Grep/concatenate the **Operator column** from every prior `corpus/round*.md`.
2. Dedup into a flat name list (collapse absorbed brands → parent, e.g. Dr. Tigges → Gebeco).
3. Pass it **verbatim** to every agent as the `ALREADY KNOWN — build beyond, don't re-report` block.
4. Require each agent to close its file with a **de-dup guards** note (`06`): aggregators excluded, sub-brands collapsed, prior-captured excluded.
This is what makes "extend, don't re-discover" reproducible rather than a hope.

### B. Size and dispatch the fleet
5. Scale the fleet to the task: a few agents for a small country, more for a big diverse one. Do not over-spawn.
6. Give each agent its own corpus file (`round<N>_<cluster>.md`) — never a shared file.
7. Dispatch the batch concurrently (cluster fan-out for discovery; critic/axis-proof fan-out for convergence).

### C. Discovery agent prompt template (issue verbatim, fill the `<…>`)
```
Travel-research discovery agent. EXHAUSTIVE discovery of expert-led tours for <cluster/theme>, then report theme-structure changes. Use real web search/fetch. ONLY report operators/tours found via a live URL. Flag UNVERIFIED where named guide or current-season dated departure unconfirmed. Invent nothing.

CLUSTER/THEME: <list themes + scope>
METHOD: search ALL relevant matrix axes — every CHANNEL (A–H), the LENSES <list>, every REGION <list>, plus LANGUAGE (<native + DE/FR/…>) and AUTHORITY-INDEX (awards, AITO/Virtuoso, university-alumni & museum travel partners, UNESCO). Multiple searches per axis; do NOT stop at a handful.
ALREADY KNOWN (build beyond, don't re-report): <captured operator list>
RESHAPE QUESTIONS: <split/merge/promote/demote tests for this cluster>
OUTPUT: Write raw findings DIRECTLY to <abs path>/corpus/round<N>_<cluster>.md via the file-write tool. Schema: Operator | Channel | Tour | Expert(named) | Format | current-season? | URL. End each section with a VERDICT line.
RETURN TO ME ONLY: the verdict(s), one line each, + confirm file written.
```

### D. Collect and integrate
8. Receive only the one-line verdict(s) + file-written confirmation from each agent. Do NOT let raw dumps flow back through the orchestrator.
9. Treat every returned finding as a **hypothesis** (`L`): spot-check named guides / current-season dated departures against a live URL before any operator enters a ranking.
10. APPEND new operators to the corpus; the next round's already-known list (step 1) will pick them up automatically.
11. If an agent surfaced a new lens / archetype / channel not in the registries → APPEND it to the relevant global registry (promotion) so future countries inherit it.

## DECISION RULES
- Agent gets the already-known list IFF you want extension not re-discovery — so ALWAYS pass it. Omit it → agents re-discover.
- Corpus write path is **absolute** IFF the write is to land anywhere; relative/omitted paths → corpus writes land nowhere.
- All agents use the **same row schema** (`06`) IFF files are to merge cleanly. Divergent schema → un-mergeable corpus.
- A finding enters a ranking IFF its named guide/date was spot-checked against a live URL (verify, don't trust).
- One file per agent IFF parallel — shared file → write collisions.
- Fleet size scales with country breadth: small/uniform → few agents; large/diverse → more. Neither over- nor under-spawn.
- Re-run DISCOVERY (expensive) rarely; re-run VERIFY (cheap) often (`08`).

## COST DISCIPLINE
Discovery is the expensive phase. Reuse the corpus as the seed (don't re-discover), merge operator-saturation into ranking (`05`), and run the cheap VERIFY loop (`08`) far more often than the expensive DISCOVERY loop.

## EXAMPLE (Italy)
Round 2 of Italy discovery. Orchestrator first builds the already-known list by grepping the Operator column of `italy/corpus/round0_*.md` and `italy/corpus/round1_*.md`, dedupes (collapses Dr. Tigges → Gebeco), and gets a flat verbatim list. It then fans out one discovery agent per macro-region cluster (e.g. North, Centre, South+Islands), each given:
- CLUSTER/THEME: the cluster's seed themes (`IT-01` Imperial Rome, `IT-03` Tuscan Renaissance, `IT-04` Chianti/Brunello wine, `IT-06` Etruscan cross-regional, …).
- ALREADY KNOWN: the deduped operator list, verbatim.
- OUTPUT path: `italy/corpus/round2_centre.md` (absolute), schema `Operator | Channel | Tour | Expert(named) | Format | current-season? | URL`.

Each agent writes its file and returns one verdict line, e.g. "Centre cluster: +6 new operators (4 verified, 2 UNVERIFIED guide); confirms IT-03/IT-04 split holds; file written." The orchestrator never sees the raw rows. Italy went from a 19-theme seed (`italy/italy_theme_map_v0.md`) to 35 themes at convergence (`italy/italy_theme_map_FINAL.md`) through these rounds — the corpus, not memory, carried operators forward between them.

## ANTI-PATTERNS (checks — fail the step if true)
- Over-spawning agents for a small country, or under-spawning for a big diverse one (scale the fleet to the task).
- Omitting the already-known list → agents re-discover instead of extending.
- Omitting absolute paths → corpus writes land nowhere.
- Letting agents return raw dumps through the orchestrator → bloats context, loses verbatim fidelity.
- Trusting agent findings as fact → they are hypotheses; spot-check named guides/dates before ranking (`L`).
- Letting parallel agents share one file → write collisions; one file each.
- Building the already-known list from session memory instead of grepping the corpus → breaks the memory invariant and "extend, don't re-discover."
- Agents using divergent row schemas → un-mergeable corpus.
- Discovering a new lens/archetype/channel and not appending it to the global registry → no compounding.
