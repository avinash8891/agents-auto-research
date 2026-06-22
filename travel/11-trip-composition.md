# 11 — Trip Composition (multi-lens itineraries from single-lens themes)

AGENT SPEC. Compose a multi-lens journey by sequencing completed single-lens ranked themes into one itinerary. This is the consumption layer, downstream of and dependent on completed rankings (`ranking` step, `doc-manifest.md`). It does NOT change how themes are discovered or ranked.

INPUT:
- The country's ranked theme files `<country>/rankings/<theme-id>.md` (one per theme; theme-id follows `THEME_ID_GRAMMAR`, `travel-config.md`; produced by the step docs from `overview` through `ranking`). These are single-lens, expert-led themes — the building blocks.
- The single-source foundation: `lens-registry.md` (sole lens vocabulary), `theme-archetypes.md`, `axes-registry.md`, `channel-registry.md`, `travel-config.md` (named dials). Read for lens/channel identity; do NOT compose from memory.
- The per-country ledger of prior compositions `<country>/compositions/*.md` (read so a new composition does not silently duplicate an existing one).
- The traveller's constraints: total days (must fit `MAX_TRIP_DAYS`, `travel-config.md`), region focus, lens priorities, budget, group vs private.

OUTPUT: `<country>/compositions/<label>.md` — the itinerary as an ordered list of segments. Each segment cites its source theme + ranked tour + expert, plus total days, delivery mode, the expertise/cost trade-off, and any flagged gap.

NEXT: the traveller (or local fixer / bespoke designer) consumes this file to book the trip. No downstream method step depends on it; it is a leaf artifact in the per-country ledger.

MEMORY INVARIANT: nothing this step depends on lives in session memory. Themes, experts, lens identity, prior compositions, traveller constraints — all READ from committed files (`<country>/rankings/<theme-id>.md`, the foundation registries, `<country>/compositions/*.md`). The composition is WRITTEN back to `<country>/compositions/<label>.md`. A fresh session reproduces the same itinerary from the files alone. Rankings are the source of truth; composition never edits them.

COMPOUNDING / SELF-LEARNING: read `<country>/compositions/*.md` (per-country ledger) before composing; run the procedure; APPEND the new itinerary as a new ledger file. Surfaced intelligence routes through the **leads bus** (`REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING), not ad-hoc: emit a typed lead to `<country>/leads.md` with provenance — a new lens → routes to `lens-registry.md`; a reusable region-anchor / glue pattern → a `composition-pattern` lead (promoted to a shared pattern note if it recurs across countries). Knowledge accrues via the ledger + the routed leads.

INVALIDATION (downstream of the fixed point): a composition is a leaf that cites specific `<country>/rankings/<theme-id>.md` segments. When a cited ranking changes (a theme re-swept after a promotion — `REGISTRY-PROTOCOL.md` INVALIDATION), the composition is **stale**: mark it `dirty` and rebuild before re-use. Composition never edits rankings; it only re-reads them.

## CORE TRADE-OFF (state it in every composition)
A single expert guide cannot lead a multi-lens trip without degrading to a generalist. Whole-trip multi-lens therefore **forfeits unified expert depth**. There are exactly two ways to keep depth (STATIC-OK — append if a new case emerges; `REGISTRY-PROTOCOL.md`):
1. **Per-segment specialists** — the traveller (or a local fixer) takes each theme's ranked expert for its leg; a generalist/driver bridges the gaps. Depth preserved *per segment*, not across the trip. Cheapest path to multi-lens-with-depth; most self-assembly effort.
2. **Bespoke designer (`luxury-bespoke` channel, `channel-registry.md`)** — luxury-bespoke operators sequence multiple theme-experts into one private itinerary. Multi-lens *with* expertise, logistics handled; you pay a premium for the orchestration, not for the expertise itself — flag that premium explicitly (same value rule as `overview`).

## PROCEDURE (start = country ranked theme files + traveller constraints)
1. READ the country's `<country>/rankings/<theme-id>.md` files, the foundation registries, and the existing `<country>/compositions/*.md` ledger.
2. **Pick a region anchor** to bound logistics (e.g. "Sicily" or "Bay of Naples + Rome"), so segments are geographically stitchable without long transfers. (The `region` axis vocabulary lives in `axes-registry.md` / the country's `axes.md` ledger.)
3. **Select themes within the composition bounds** — at least `MIN_LENSES_PER_TRIP` and at most `MAX_LENSES_PER_TRIP` (`travel-config.md`) — whose ranked tours overlap that geography. Exceeding `MAX_LENSES_PER_TRIP` in one trip under `MAX_TRIP_DAYS` is breadth-over-depth — cap it.
4. **For each chosen theme, pull its ranked #1 (or best-fit) as a segment**, noting that segment's expert, duration, and the depth/access feature (from `<country>/rankings/<theme-id>.md`).
5. **Sequence** by geography and pace; insert generalist/transfer days only as glue (glue days are leisure, not themes — they cite no expert).
6. **Choose the delivery mode** (per-segment specialists vs bespoke designer) and state the expertise/cost trade-off for this specific itinerary. If bespoke designer, flag the orchestration premium explicitly.
7. **Flag gaps**: any segment where no ranked expert is available for the dates → say so. Do NOT substitute a generic guide silently.
8. If composing surfaced a new lens or a reusable pattern → emit a typed lead to `<country>/leads.md` with provenance and route it (`REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING): new lens → `lens-registry.md`; `composition-pattern` (region-anchor / glue) → composition ledger, promoted if it recurs.
9. WRITE the itinerary to `<country>/compositions/<label>.md`. Stop.

## DECISION RULES
- COMPOSE IFF *different lenses* (`lens-registry.md`) are combined in one trip. A multi-**era** or multi-**region** single-subject trip (Sicily layered civilisations; Etruscan Italy) is NOT multi-lens — it is one theme, keeps its single expert, and needs no composition.
- LENS BOUNDS: between `MIN_LENSES_PER_TRIP` and `MAX_LENSES_PER_TRIP` per trip (`travel-config.md`). Exceeding `MAX_LENSES_PER_TRIP` → breadth-over-depth → cap it.
- DURATION: the whole composed trip must fit `MAX_TRIP_DAYS` (`travel-config.md`).
- SEGMENT SOURCE: every depth segment maps to exactly one ranked theme's #1 (or best-fit) tour from `<country>/rankings/<theme-id>.md`. Glue days map to no theme.
- DELIVERY MODE (STATIC-OK — append if a new case emerges; `REGISTRY-PROTOCOL.md`): depth-with-multi-lens is available ONLY via (1) per-segment specialists or (2) bespoke designer (`luxury-bespoke`, `channel-registry.md`). Whole-trip single-guide depth across multiple lenses is NOT available — state this.
- BESPOKE PREMIUM: if delivery mode = bespoke designer → flag the orchestration premium explicitly (the premium buys sequencing, not the expertise).
- MISSING EXPERT: if no ranked expert covers a segment's dates → flag the gap. Never silently substitute a generic guide.
- NO UPSTREAM EDITS: composition never bundles lenses into a theme upstream and never re-ranks. Themes stay single-lens (`theme-seeding`, `doc-manifest.md`).

## EXAMPLE (input → output, illustrative — build against live rankings)
Input: Sicily, ~10 days (within `MAX_TRIP_DAYS`), history + food lenses (see Italy rankings under `italy/` for the live roster).
Output `italy/compositions/sicily-history-food.md`:
- Segment A (5–6 days): `IT-11` layered civilisations — the ranked #1 archaeologist-led tour (Greek temples → Roman villa → Arab-Norman Palermo).
- Segment B (1–2 days): `IT-27` Etna food & wine — the ranked sommelier-led day(s).
- Glue: 1–2 coast/leisure days (no expert — leisure, not a theme).
- Delivery: per-segment specialists if self-assembling; or a `luxury-bespoke` designer to sequence both experts privately (orchestration premium flagged).
- Trade-off stated: whole-trip single-guide depth is NOT available across both lenses.

## ANTI-PATTERNS (checks — fail the step if true)
(open — append the check when a new lesson lands; tag `Lnn`. This block is a VIEW of `10-lessons-log.md`; `REGISTRY-PROTOCOL.md`.)
- Composing from memory instead of reading `<country>/rankings/<theme-id>.md`, the foundation registries, and the existing `<country>/compositions/*.md` ledger (violates the memory invariant). (L15)
- Bundling lenses into one "theme" upstream to avoid composition (corrupts the ranking unit — keep themes single-lens). (L11)
- Presenting a multi-lens trip as expert-led-throughout (it isn't; name the trade-off). (L11)
- Stitching geographically scattered themes into one trip under `MAX_TRIP_DAYS` (transfer-heavy, shallow).
- Substituting a generic guide for a missing segment expert without flagging it.
- Treating a multi-era or multi-region single-subject trip as multi-lens (it is one theme — no composition needed). (L11)
- Bespoke-designer delivery without flagging the orchestration premium.
- Surfacing a new lens or reusable pattern and promoting it ad-hoc instead of emitting a typed lead to `<country>/leads.md` routed per `REGISTRY-PROTOCOL.md` (no compounding / bypasses the leads bus). (L22)
- Re-using a composition whose cited `<country>/rankings/<theme-id>.md` segment has since changed, without rebuilding — a stale leaf (`dirty`). Rebuild when a source ranking changes. (L19)
