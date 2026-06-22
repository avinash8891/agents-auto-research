# 11 — Trip Composition (multi-lens itineraries from single-lens themes)

## Purpose
The ranking method (`00`–`08`) produces **single-lens, expert-led themes** — the building blocks. This step is the consumption layer for travellers who want a **multi-lens journey** (e.g. a single Sicily trip mixing layered-civilisations + Etna wine + coast). Composition is downstream of, and depends on, completed rankings. It does NOT change how themes are discovered or ranked.

## The core trade-off (state it every time)
A single expert guide cannot lead a multi-lens trip without degrading to a generalist. So whole-trip multi-lens **forfeits unified expert depth**. There are exactly two ways to keep depth:
1. **Per-segment specialists** — the traveller (or a local fixer) takes each theme's ranked expert for its leg; a generalist/driver bridges the gaps. Depth is preserved *per segment*, not across the trip. Cheapest path to multi-lens-with-depth; most self-assembly effort.
2. **Bespoke designer (channel F)** — A&K / Imago Artis / Scott Dunn / IC Bellagio sequence multiple theme-experts into one private itinerary. Multi-lens *with* expertise, logistics handled; you pay a premium for the orchestration, not for the expertise itself — flag that premium explicitly (same value rule as `00`).

A multi-**era** or multi-**region** single-subject trip (Sicily layered civilisations; Etruscan Italy) is NOT multi-lens — it's one theme and keeps its single expert. Composition is only needed when *different lenses* are combined.

## Inputs
- The country's ranked theme files (`rankings/<theme-id>.md`).
- The traveller's constraints: total days (< 21 per trip), region focus, lens priorities, budget, group vs private.

## Method
1. **Pick a region anchor** to bound logistics (e.g. "Sicily" or "Bay of Naples + Rome"), so segments are geographically stitchable without long transfers.
2. **Select 2–4 themes** whose ranked tours overlap that geography. More than ~4 lenses in one sub-21-day trip is breadth-over-depth — cap it.
3. **For each chosen theme, pull its ranked #1 (or best-fit) as a segment**, noting that segment's expert, duration, and the depth/access feature.
4. **Sequence** by geography and pace; insert generalist/transfer days only as glue.
5. **Choose the delivery mode** (per-segment specialists vs bespoke designer) and state the expertise/cost trade-off for this specific itinerary.
6. **Flag gaps**: any segment where no ranked expert is available for the dates → say so (don't substitute a generic guide silently).

## Output
`compositions/<country>-<label>.md`: the itinerary as an ordered list of segments, each citing its source theme + ranked tour + expert, total days, delivery mode, the expertise/cost trade-off, and any flagged gap.

## Worked example (illustrative — build against live rankings)
Sicily, ~10 days, history + food lenses:
- Segment A (5–6 days): IT-11 layered civilisations — the ranked #1 archaeologist-led tour (Greek temples → Roman villa → Arab-Norman Palermo).
- Segment B (1–2 days): IT-27 Etna food & wine — the ranked sommelier-led day(s).
- Glue: 1–2 coast/leisure days (no expert — leisure, not a theme).
- Delivery: per-segment specialists if self-assembling; or a bespoke designer to sequence both experts privately (premium flagged). Whole-trip single-guide depth is not available across both lenses — stated.

## Anti-patterns
- Bundling lenses into one "theme" upstream to avoid composition (corrupts the ranking unit — keep themes single-lens).
- Presenting a multi-lens trip as expert-led-throughout (it isn't; name the trade-off).
- Stitching geographically scattered themes into one sub-21-day trip (transfer-heavy, shallow).
- Substituting a generic guide for a missing segment expert without flagging it.
