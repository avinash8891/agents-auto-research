# Travel Research Method — ranked, expert-led tours by theme-within-region

A reproducible method for building **ranked Top-5 tour lists for the 50 most-visited countries**, organised by **focused regional theme** (not whole-country), favouring depth over breadth and expert-led authenticity over checklists.

This folder is the **playbook**. Each numbered file is one step. Follow it, or improve it — and when you improve it, append to `10-lessons-log.md` so gains compound.

## The one-paragraph version
For each country: split it into distinct, non-overlapping **themes** (a theme = a focused regional experience sold as a single trip — e.g. "Rome & classical antiquity," not "Italy"). Discover the *whole field* of expert-led tours per theme using a 5-axis coverage matrix (so nothing is missed), loop until discovery is dry, verify every finalist against live sources, then rank the Top 5 on guide-expertise, depth, authenticity, and value-for-money. Persist everything to a durable corpus and refresh it on a cadence.

## Core principles
1. **Depth over breadth.** A focused theme done deeply beats a thin nationwide overview.
2. **First-trip lens.** Within a theme, favour the experience most iconic/representative for a first-time visitor, done with real depth — not obscure hyper-niche.
3. **No invention.** Never fabricate guides, dates, prices, claims. Unverified = flagged, never guessed.
4. **Frames beat keywords.** Discovery is driven by a coverage matrix, not free-associated search terms. Empty matrix cells are visible gaps, not silent misses.
5. **Training knowledge builds the frames; the web populates and verifies.** Use latent knowledge to enumerate channels/lenses/regions/languages/authorities and pre-fill candidates; use live search to confirm and to catch what you didn't know.
6. **Convergence is earned, not asserted.** Stop only when a fresh adversarial critic adds nothing clearing the bar, across every axis.
7. **Value, not luxury.** Price is not a barrier, but cost must be justified by depth/expertise delivered. Flag premium-for-thin-substance.
8. **Single-lens themes are the ranking unit; multi-lens is a composition layer.** Group tours go deep on one subject with one expert. A traveller's multi-lens trip is built by combining ranked themes (`11`), accepting that whole-trip expert depth is recovered only per-segment or via a bespoke designer.

## Steps
- `00-overview-and-principles.md` — goal, scope, output contract
- `01-country-ranking.md` — establish the 50 countries (UN Tourism)
- `02-theme-seeding.md` — seed the theme map v0 (channel × lens × region)
- `03-coverage-matrix.md` — the 5-axis discovery frame (the rigor core)
- `04-discovery-loop.md` — exhaustive discovery, round mechanics, write-to-corpus
- `05-convergence-and-admission-bar.md` — when to stop; the theme bar; loop-until-dry
- `06-corpus-and-persistence.md` — corpus schema, subagent file-writing, stamping
- `07-verification-and-ranking.md` — verify finalists; rank Top 5; output format
- `08-freshness-and-updates.md` — keep it current (verify + discovery cadences, cron)
- `09-agent-orchestration.md` — how to dispatch parallel agents efficiently
- `10-lessons-log.md` — the maturation history + every future improvement
- `11-trip-composition.md` — consumption layer: stitch single-lens themes into a multi-lens itinerary (downstream of ranking)

## Document conventions
Every step doc (00–10) carries: an **Anti-patterns** block (what not to do) and at least one **Italy worked example** (the real instance). When you add a step or a rule, keep both — the rule plus the worked instance is what makes it reproducible.

## Per-country run order (the loop)
1. Establish/confirm country's arrivals rank (step 01).
2. Seed theme map v0 (step 02).
3. Run discovery loop with the 5-axis matrix until theme-converged (steps 03–05).
4. Per theme: saturate operators (5 axes) + verify finalists → rank Top 5 (step 07).
5. Stamp corpus; register in refresh cadence (steps 06, 08).

## Status (as of 2026-06)
Method matured and proven on **Italy** (converged at 35 themes; ~150+ operators in corpus). Output (ranked Top-5s) not yet produced — next action is IT-01 Rome antiquity as the output template. See `10-lessons-log.md` for how the method evolved.
