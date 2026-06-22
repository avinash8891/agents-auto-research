# 07 — Verification & Ranking

## Purpose
Turn the discovered field for a theme into a verified, ranked **Top 5**.

## Step A — operator saturation (merge discovery here)
For the theme, run the 5-axis check scoped to it (`03`/`05`) so the candidate set is complete — especially the LANGUAGE and AUTHORITY-INDEX axes, which carry the operators keyword search misses.

## Step B — verify each finalist
For every candidate that could plausibly make the Top 5, confirm from a **live source**:
- Named guide + their real credential (historian/Egyptologist/naturalist/sommelier/etc. — confirm it fits the theme; reject figureheads and generic coach-guides).
- A **current-season (2026–27) dated departure**.
- Price (per person, note basis: sharing/single, with/without flights).
- Group size (and whether a private-departure option exists).
- The specific depth/access feature (exclusive site access, underground, after-hours, dig viewing, etc.).

Priority queue: the corpus UNVERIFIED rows and any fetch-blocked (403/404) operator pages. **403/404 fallback:** harvest date/price/guide from the search snippet, keep the row UNVERIFIED with the HTTP status + "confirmed in snippet" noted (`06`); never silently drop a blocked page, never promote it to verified without an unblocked confirmation. If a guide or a 2026–27 departure **cannot** be verified at all, the tour is **flagged** in the output — never guessed, never dropped silently.

**Format-class matters for rankability.** Tag each finalist (`06`): fixed-departure group / private-bespoke-year-round / hybrid-course. A private/bespoke/year-round product can't be ranked on the same "dated departure" basis as a fixed-departure tour. If a Top-5 mixes format classes (e.g. a multi-day escorted tour alongside a city-based day-scholar or a bespoke private), **flag the difference explicitly** so the reader compares like with unlike knowingly — as the proof-of-concept did for the Context Travel day-format entry.

## Step C — rank on the criteria (priority order)
1. **Expert guide fit** (real, theme-appropriate, highly regarded).
2. **Depth** within the theme (beneath the surface, not a checklist).
3. **Small / authentic / locally connected** (private option a plus).
4. **Value for money** as tie-break: when two are comparably excellent, the better value ranks higher. Price is not a barrier — the best wins even if pricier — but cost must be justified by depth/expertise. Flag any tour charging a large premium for thin substance.

Worked tie-break (Italy IT-01 proof): a name-brand archaeologist (Simon Elliott) with more exclusive access at ~30% lower price was ranked above a comparably-credentialed competitor (Martin Randall / Mark Grahame) precisely on value — not cheapest, not luxury, best-justified.

## Step D — write the output
`rankings/<theme-id>.md`:
- Country (arrivals rank) + theme/region + one-line capture statement.
- Ranked Top 5: operator · tour name · guide/expertise · group size · duration · approx price · value note · depth/access feature · source link.
- One line: **why #1 wins**.
- A FLAGS block: any unverified leader/departure; any premium-for-thin-substance; if the theme is weak, say so and give closest strong fits rather than padding.

## Anti-patterns
- Ranking on memory/reputation instead of a live-verified specific.
- Rewarding price/luxury for its own sake (reward only cost justified by depth/expertise).
- Mixing format classes (group vs bespoke vs day) in one Top-5 without flagging it.
- Dropping a 403/404 finalist instead of keeping it UNVERIFIED with snippet evidence.
- Padding a weak theme to five instead of saying so and giving closest strong fits.

## Quality gate
If a Top-5 entry would survive having its specifics replaced by a plausible guess, it isn't verified. Every claim traces to a URL. "Verified" requires the pasted/cited specifics, not an assertion.
