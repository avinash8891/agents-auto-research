# 09 — Agent Orchestration

## Principle
Parallel subagents do the breadth; the orchestrator keeps the conclusions, not the file dumps. Agents write raw output to their own files and return only short verdicts — this keeps orchestrator context lean enough to scale to 50 countries.

## Patterns
- **Cluster fan-out** (discovery): one agent per macro-region cluster, dispatched together in a single batch so they run concurrently.
- **Critic fan-out** (convergence): parallel adversarial agents, each owning one axis — lenses, channels/operators, regions, borderline validation.
- **Axis-proof** (anti-false-convergence): dedicated LANGUAGE and AUTHORITY-INDEX agents.
- **Per-theme ranking**: one agent per theme (or small cluster), saturating axes + verifying + ranking, writing to `rankings/<theme-id>.md`.

## Discovery agent prompt template
```
Travel-research discovery agent. EXHAUSTIVE discovery of expert-led tours for <cluster/theme>, then report theme-structure changes. Use real web search/fetch. ONLY report operators/tours found via a live URL. Flag UNVERIFIED where named guide or current-season dated departure unconfirmed. Invent nothing.

CLUSTER/THEME: <list themes + scope>
METHOD: search ALL relevant matrix axes — every CHANNEL (A–H), the LENSES <list>, every REGION <list>, plus LANGUAGE (<native + DE/FR/…>) and AUTHORITY-INDEX (awards, AITO/Virtuoso, university-alumni & museum travel partners, UNESCO). Multiple searches per axis; do NOT stop at a handful.
ALREADY KNOWN (build beyond, don't re-report): <captured operator list>
RESHAPE QUESTIONS: <split/merge/promote/demote tests for this cluster>
OUTPUT: Write raw findings DIRECTLY to <abs path>/corpus/round<N>_<cluster>.md via the file-write tool. Schema: Operator | Channel | Tour | Expert(named) | Format | current-season? | URL. End each section with a VERDICT line.
RETURN TO ME ONLY: the verdict(s), one line each, + confirm file written.
```

## Building the cumulative "already-known" list (source of truth)
Before each round, the orchestrator assembles the known-operator list mechanically — it is not from memory:
1. Grep/concatenate the **Operator column** from every prior `corpus/round*.md`.
2. Dedup into a flat name list (collapse absorbed brands → parent, e.g. Dr. Tigges → Gebeco).
3. Pass it **verbatim** to every agent as the "ALREADY KNOWN — build beyond, don't re-report" block.
4. Each agent must close its file with a **de-dup guards** note (`06`): aggregators excluded, sub-brands collapsed, prior-captured excluded.
This is what makes "extend, don't re-discover" reproducible rather than a hope.

## Rules
- Always pass the **already-known operator list** so agents extend rather than re-discover.
- Always pass **absolute file paths** for the corpus write.
- Demand the **same row schema** (`06`) so files merge cleanly.
- Treat agent findings as **hypotheses**: spot-check named guides/dates before they enter a ranking (verify, don't trust).
- Scale the fleet to the task: a few agents for a small country, more for big diverse ones. Don't over-spawn.

## Cost discipline
Discovery is the expensive phase. Reuse the corpus as the seed (don't re-discover), merge operator-saturation into ranking (`05`), and run the cheap VERIFY loop far more often than the expensive DISCOVERY loop (`08`).

## Anti-patterns
- Over-spawning agents for a small country (scale the fleet to the task).
- Omitting the already-known list (agents re-discover instead of extending) or absolute paths (corpus writes land nowhere).
- Letting agents return raw dumps through the orchestrator (bloats context; lose verbatim fidelity).
- Trusting agent findings as fact — they are hypotheses; spot-check named guides/dates before ranking.
- Letting parallel agents share one file (collisions) instead of one file each.
