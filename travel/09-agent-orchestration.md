# 09 — Agent Orchestration

AGENT SPEC. Orchestrator role: dispatch parallel subagents to do breadth-discovery and convergence, keep only the conclusions, never the file dumps. Subagents write raw output to their own files and return only short verdicts — this keeps orchestrator context lean enough to scale up the `GROWTH_LADDER` (`config`) toward `TARGET_SCALE`.

PATHS: every per-country file lives under `<country>/` per the canonical scheme in `doc-manifest.md` (PER-COUNTRY ARTIFACTS) — `<country>/corpus/round<N>_<cluster>.md` (working rounds), `<country>/corpus_FINAL.md` (consolidated/locked), `<country>/ledger.md` (single ledger), `<country>/leads.md`, `<country>/axes.md`, `<country>/rankings/`, `<country>/compositions/`, `<country>/verify_<date>.md`. A `<country>_X` reference is shorthand for `<country>/X` (`doc-manifest.md` NORMALIZATION) — do not invent a second path style.

INPUT: prior `<country>/corpus/round*.md` files (all earlier rounds), plus the per-cluster/per-theme scope, the leads routed to this step from `<country>/leads.md` (`REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING), and the global registries (`lens-registry`, `axes-registry`, `archetypes`, `channel-registry`) for the axis/lens/channel vocabularies. The orchestrator READS these — it does not work from memory. The set of axes to sweep is the baseline axes in `axes-registry` (count derived from that file, never asserted), filtered by their `stage`/`role` tags — never a hand-typed axis list.
OUTPUT: new `<country>/corpus/round<N>_<cluster>.md` (one file per discovery agent), `<country>/rankings/<theme-id>.md` (one file per ranking agent), and appended typed leads in `<country>/leads.md` (every page-reading agent emits them — see DISPATCH/PROCEDURE). The orchestrator's own working state is the cumulative already-known list, assembled mechanically from files (see PROCEDURE), not retained in session memory.
NEXT: `ranking` consumes corpus + saturation; `corpus` (schema/audit-trail) governs row format, the versioned row contract, and ID stability; `freshness` consolidates round files into `<country>/corpus_FINAL.md` (stamping `first_seen_round`) and re-checks the corpus far more often than discovery re-runs (`VERIFY_CADENCE` vs `DISCOVERY_CADENCE`, `config`).

MEMORY INVARIANT: nothing the orchestration depends on lives in session memory. Every agent input (already-known list, axis lists, scope) is READ from a committed file; every agent output is WRITTEN to a committed corpus/ranking file via the file-write tool. A fresh session reproduces the same dispatch from the corpus + registries alone. "Extend, don't re-discover" is reproducible because the already-known list is recomputed from files each round, not remembered. (Memory invariant shared across the method — `registry-protocol`.)

COMPOUNDING / SELF-LEARNING: corpus accrues across rounds. Read prior corpus → run new agents → APPEND new operators to `<country>/corpus/round<N>_*.md` → the next round's already-known list (the single `<country>/ledger.md`) PROMOTES them so no operator is re-discovered. New lenses/archetypes/channels/axes an agent surfaces are appended to the relevant global registry under the shared append→promote cycle (`registry-protocol`) so future countries inherit them. (Promotion bar and mechanics are owned by `registry-protocol`; the discovery promotion test is owned by `discovery-loop`.)

## DISPATCH PATTERNS
- **Cluster fan-out** (discovery): one agent per macro-region cluster, dispatched together in a single batch so they run concurrently.
- **Critic fan-out** (convergence): parallel adversarial agents, each owning one axis from `axes-registry` (lenses, channels/operators, regions, borderline validation).
- **Axis-proof fan-out** (anti-false-convergence): a dedicated agent per axis tagged `role:axis-proof` in `axes-registry` — these are the false-convergence gate. Do not name the proof axes by hand; read them off the tag filter so a newly promoted axis-proof axis is swept automatically.
- **Per-theme ranking**: one agent per theme (or small cluster) — saturates axes + verifies + ranks, writing to `<country>/rankings/<theme-id>.md` (theme-id follows `THEME_ID_GRAMMAR`, `config`). Saturation runs per theme until the theme's axes return dry (`OPERATOR_CONVERGED`, `config`).
- **Dirty-unit re-sweep** (INVALIDATION): when a promotion marks units dirty (`REGISTRY-PROTOCOL.md` INVALIDATION), the orchestrator DISPATCHES a re-sweep agent **scoped to the new axis/unit only** — a new baseline axis → one agent per already-swept theme sweeping just that axis; a new lens/archetype → re-run the seed-completeness diff. Never a full re-discovery. A unit stays not-converged while it carries a `dirty` flag (`config` DONE).

## PROCEDURE

### A. Build the cumulative already-known list (source of truth — done before every round)
The orchestrator assembles the known-operator list mechanically. It is NOT from memory.
1. Grep/concatenate the **Operator column** from every prior `<country>/corpus/round*.md`.
2. Dedup into a flat name list, collapsing absorbed/sub-brands → parent and dropping aggregators per `operator-aliases`.
3. Pass it **verbatim** to every agent as the `ALREADY KNOWN — build beyond, don't re-report` block.
4. Require each agent to close its file with a **de-dup guards** note (`corpus`): aggregators excluded, sub-brands collapsed, prior-captured excluded (alias/exclusion authority = `operator-aliases`).
This is what makes "extend, don't re-discover" reproducible rather than a hope.

SCALE NOTE (embeddings — named upgrade path, not built at pilot scale): the already-known list is passed **verbatim** today (fine at `CURRENT_SCOPE_N`). When coverage grows along `GROWTH_LADDER` and the cumulative operator list outgrows verbatim passing (too large for an agent prompt — roughly the 100+ country / thousands-of-operators range), switch to an **embedding dedup index**: vector lookup over operator names/descriptions answers "is this already known?" without shipping the whole list, and can suggest fuzzy `operator-aliases` matches the exact list misses (e.g. "Martin Randall Travel" ≈ "Martin Randall"). Embeddings are for *retrieval/dedup at scale only* — never for judgment steps (overlap, expert-fit, value), where LLM reasoning is stronger. Until that scale, do NOT add it (YAGNI).

### B. Size and dispatch the fleet
5. Scale the fleet to the task: a few agents for a small country, more for a big diverse one. Do not over-spawn.
6. Give each agent its own corpus file (`<country>/corpus/round<N>_<cluster>.md`) — never a shared file. The `<country>/leads.md` sink is append-only, so concurrent agents appending leads is safe.
7. Dispatch the batch concurrently (cluster fan-out for discovery; critic/axis-proof fan-out for convergence).

### C. Discovery agent prompt template (issue verbatim, fill the `<…>`)
```
Travel-research discovery agent. EXHAUSTIVE discovery of expert-led tours for <cluster/theme>, then report theme-structure changes. Use real web search/fetch. ONLY report operators/tours found via a live URL. Flag UNVERIFIED where named guide or CURRENT_SEASON-dated departure unconfirmed. Invent nothing.

CLUSTER/THEME: <list themes + scope>
METHOD: search EVERY baseline axis in axes-registry (filter the discovery sweep to axes tagged stage:discovery; run a dedicated extra sweep for each axis tagged role:axis-proof). For the CHANNEL axis sweep every channel sub-type id in channel-registry; for the LENS axis use the lenses in lens-registry; for the REGION axis use every first-level region <list>; for any axis whose values are per-country data (e.g. language set, authority-index directories) read them from <country>/axes.md. Multiple searches per axis; do NOT stop at a handful.
ALREADY KNOWN (build beyond, don't re-report): <captured operator list>
RESHAPE QUESTIONS (open — append on discovery; REGISTRY-PROTOCOL.md; reshape actions owned by `discovery-loop`): <split/merge/promote/demote/fold-into-new tests for this cluster>
OUTPUT: Write raw findings DIRECTLY to <country>/corpus/round<N>_<cluster>.md via the file-write tool. Schema (corpus doc): Operator | Channel | Tour | Expert(named) | Format | CURRENT_SEASON? | URL. Do NOT fill first_seen_round — it is stamped at consolidation (`corpus`/`freshness`). End each section with a VERDICT line.
LEADS: you read whole operator pages — any signal that doesn't fit the row schema (theme-hint, new lens, archetype-instance, channel/affinity signal, authority lead, disqualifier, seasonality quirk, price–quality signal) → APPEND a typed row to <country>/leads.md with provenance (source URL + theme-id + run/round). Lead types and destinations: see REGISTRY-PROTOCOL.md INTELLIGENCE CAPTURE & ROUTING. Capture typed signals, not raw page dumps.
RETURN TO ME ONLY: the verdict(s), one line each, + confirm corpus + leads files written.
```

### D. Collect and integrate
8. Receive only the one-line verdict(s) + file-written confirmation from each agent. Do NOT let raw dumps flow back through the orchestrator.
9. Treat every returned finding as a **hypothesis** (lesson `L7`, `lessons`): spot-check named guides / `CURRENT_SEASON`-dated departures against a live URL before any operator enters a ranking.
10. APPEND new operators to the corpus; the next round's already-known list (step 1) will pick them up automatically. Round files stay as working inventories; `freshness` consolidates them into `<country>/corpus_FINAL.md` and stamps `first_seen_round` there — agents never fill it.
11. ROUTE the emitted leads: each lead in `<country>/leads.md` goes to the step/registry it fine-tunes per the `REGISTRY-PROTOCOL.md` routing table. A new lens / archetype / channel / axis → APPEND to that registry's per-country watchlist and promote on evidence (`registry-protocol`; the discovery-side promotion test is owned by `discovery-loop`) so future countries inherit it.
12. On any PROMOTION (axis/lens/archetype/channel) or a lead that implies new coverage → mark the affected units `dirty` and DISPATCH a scoped re-sweep (DISPATCH PATTERNS → Dirty-unit re-sweep; `REGISTRY-PROTOCOL.md` INVALIDATION). The country is not DONE while any unit is dirty (`config`).

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
- OUTPUT path: `italy/corpus/round<N>_centre.md` (absolute), schema `Operator | Channel | Tour | Expert(named) | Format | CURRENT_SEASON? | URL` (`corpus`); `first_seen_round` left blank (stamped later at consolidation into `italy/corpus_FINAL.md`).
- LEADS: tangential signals (e.g. "guide Andrea Carlino leads both an Etruscan and a Renaissance tour" → guide-leads-multiple-themes; "operator partners with the Uffizi" → authority lead) appended to `italy/leads.md` with provenance, routed per `REGISTRY-PROTOCOL.md`.

Each agent writes its file and returns one verdict line, e.g. "Centre cluster: +6 new operators (4 verified, 2 UNVERIFIED guide); 3 leads emitted (1 candidate affinity axis); confirms the Tuscan/wine seed split holds; files written." The orchestrator never sees the raw rows. If the affinity-axis lead later promotes, every already-swept Italy theme is marked `dirty` on that axis and re-swept on that axis alone (INVALIDATION) before Italy can be DONE. Italy grew from its v0 seed (`italy/theme_map_v0.md`) to the converged map (`italy/theme_map_FINAL.md`) through these rounds — the corpus, not memory, carried operators forward between them.

## ANTI-PATTERNS (checks — fail the step if true)
(open — append the check when a new lesson lands; tag `Lnn`. This block is a VIEW of `10-lessons-log.md`; the lessons-log is the source. See REGISTRY-PROTOCOL.md "Anti-patterns are a view of the lessons-log.")
- Over-spawning agents for a small country, or under-spawning for a big diverse one (scale the fleet to the task).
- Omitting the already-known list → agents re-discover instead of extending (`L1`, `lessons`).
- Omitting absolute paths → corpus writes land nowhere.
- Letting agents return raw dumps through the orchestrator → bloats context, loses verbatim fidelity (`L5`, `lessons`).
- Trusting agent findings as fact → they are hypotheses; spot-check named guides/dates before ranking (`L7`, `lessons`).
- Letting parallel agents share one file → write collisions; one file each (`L5`, `lessons`).
- Building the already-known list from session memory instead of grepping the corpus → breaks the memory invariant and "extend, don't re-discover" (`L4`, `lessons`).
- Agents using divergent row schemas → un-mergeable corpus (`corpus`).
- Hardcoding which axes to sweep/prove instead of filtering `axes-registry` by `stage`/`role` tags → a newly promoted axis silently goes unswept (`L7`, `L14`, `L16`, `lessons`).
- Discovering a new lens/archetype/channel/axis and not appending it to the relevant registry watchlist → no compounding (`L15`, `registry-protocol`).
- A page-reading agent surfacing tangential intelligence but not emitting it as a typed lead to `<country>/leads.md` → intelligence lost (`registry-protocol` INTELLIGENCE CAPTURE & ROUTING).
- Dumping raw operator pages into `<country>/leads.md` instead of typed signals with provenance → hoarding, not capture (`registry-protocol`).
- Declaring a unit/country converged while it carries a `dirty` flag, or answering a promotion with a full re-discovery instead of a scoped re-sweep of the new axis/unit → breaks the fixed-point computation (`registry-protocol` INVALIDATION; `config` DONE).
- Agents filling `first_seen_round` in round files → it is stamped once at consolidation into `<country>/corpus_FINAL.md`; agent-written values drift the versioned row contract (`corpus`).
- Inventing a per-country path style instead of `<country>/X` (the `<country>_X` prefix is shorthand for the same file) → split state across two locations (`doc-manifest.md` NORMALIZATION).
