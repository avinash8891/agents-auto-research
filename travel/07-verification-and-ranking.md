# 07 — Verification & Ranking

AGENT SPEC. Turn the discovered field for one theme into a verified, ranked **Top-`RANK_DEPTH`** (`travel-config.md`; ceiling, not a quota — list fewer if fewer clear the bar), every claim traced to a live URL.

INPUT:
- `<country>_theme_map_FINAL.md` — the discovered field (themes + candidate operators) from the **theme-seeding**/**discovery-loop** docs (`doc-manifest.md`).
- The corpus rows for this theme (**corpus** doc) — including UNVERIFIED rows and fetch-blocked (403/404) operator pages, with their HTTP status notes.
- The discovery axes (**coverage-matrix** / **admission-bar** docs) — the baseline axes in `axes-registry.md`, especially the axes tagged `role:axis-proof` and `role:saturation-weight` (these carry the operators keyword search misses; identities are READ from the registry, never named from memory).
- Per-country verification ledger (`<country>/ledger.md`, **corpus** doc) and global registries (`axes-registry.md`) — READ, do not work from memory.

OUTPUT: `rankings/<theme-id>.md` — verified ranked Top-`RANK_DEPTH` + a FLAGS block (schema in Step D). `<theme-id>` follows `THEME_ID_GRAMMAR` (`travel-config.md`).
NEXT: the **composition** doc (`doc-manifest.md`) and any country-level roll-up consume `rankings/<theme-id>.md`. Verified specifics + new operators APPEND back to the corpus (**corpus** doc) and verification ledger.

MEMORY INVARIANT: nothing here lives in session memory. The candidate field, corpus rows, HTTP-status notes, and axis definitions are all READ from committed files; every verified specific and ranking is WRITTEN to `rankings/<theme-id>.md` and APPENDED to the corpus/ledger. A fresh session reproduces the same Top-`RANK_DEPTH` from the files alone. "Verified" = pasted/cited specifics in a committed file, never an in-session assertion.

COMPOUNDING: verification accrues. READ the corpus + per-country verification ledger → RUN verification on finalists → APPEND each confirmed specific (guide, dated departure, price, group size, depth feature, format class) back to the corpus row (**corpus** doc) and the per-country ledger → PROMOTE any reusable verification source or new operator into the corpus so later themes/countries inherit it. Registry promotion mechanics (append-only, promotion bar, log) are owned by `REGISTRY-PROTOCOL.md` — follow it, don't restate. UNVERIFIED and 403/404 rows stay in the ledger with their status, never discarded — a later session retries them.

## PROCEDURE (start = one theme-id)

1. Take the theme-id. READ its rows from the corpus (**corpus** doc), the discovered field (**theme-seeding**/**discovery-loop** docs), and the per-country verification ledger.
2. **Operator saturation (merge discovery here).** Run the axis check (**coverage-matrix** / **admission-bar** docs) over the baseline axes in `axes-registry.md`, scoped to this theme, so the candidate set is complete. Give special weight to the axes tagged `role:saturation-weight` — and confirm the axes tagged `role:convergence-gate` are dry for this theme — since those carry the operators keyword search misses. (Convergence requires every `role:convergence-gate` axis dry, not a frozen axis count; the count is derived from `axes-registry.md`.)
3. Build the finalist set: every candidate that could plausibly make the Top-`RANK_DEPTH`. Order the verification queue by priority: corpus UNVERIFIED rows first, then fetch-blocked (403/404) operator pages.
4. **Verify each finalist from a live source.** Confirm and record:
   - Named guide + their real credential (historian / Egyptologist / naturalist / sommelier / etc.) — confirm it fits the theme's lens(es) per `lens-registry.md`.
   - A current-season (`CURRENT_SEASON`, `travel-config.md`) dated departure.
   - Price per person — note basis: sharing/single, with/without flights.
   - Group size, and whether a private-departure option exists.
   - The specific depth/access feature (exclusive site access, underground, after-hours, dig viewing, etc.).
5. **On 403/404:** harvest date/price/guide from the search snippet; keep the row UNVERIFIED with the HTTP status + "confirmed in snippet" noted (**corpus** doc). Never silently drop a blocked page; never promote it to verified without an unblocked confirmation.
6. **On unverifiable guide or `CURRENT_SEASON` departure:** flag the tour in the output. Never guess, never drop silently.
7. **Tag format class** on each finalist — values in `tags-registry.md` (written per **corpus** doc).
8. **Rank** the verified finalists on the criteria in priority order (see DECISION RULES → ranking).
9. APPEND every confirmed specific back to the corpus row and the per-country verification ledger (**corpus** doc). PROMOTE any new operator or reusable verification source per `REGISTRY-PROTOCOL.md`.
9a. **Capture leads (don't lose tangential intelligence).** Verification reads whole operator pages — emit every signal that doesn't fit the row schema as a **typed lead** in `<country>/leads.md` with provenance (URL + theme-id + run), per `REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING: theme/sub-lens hints, a guide who leads other themes, channel/affinity signals, authority leads, new archetype instances, disqualifier patterns, seasonality/access quirks. Route each per the table; a lead implying new coverage **dirties** the affected unit.
10. **Write the output** to `rankings/<theme-id>.md` per Step D schema, including the FLAGS block. Stop.

## DECISION RULES

- **Credited-product accounting (the admission link).** Each verified finalist contributes weight toward this theme: `FULL_PRODUCT_WEIGHT` for a product with a named guide AND a confirmed `CURRENT_SEASON` dated departure; `PARTIAL_PRODUCT_WEIGHT` for an UNVERIFIED-date or unnamed-guide product (`travel-config.md`). A theme below `ADMISSION_BAR` / `MIN_CREDENTIALED_PRODUCTS` in credited weight is THIN (e.g. one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT` stays under `ADMISSION_BAR` → THIN-NOTE). Bar definition is owned by the **admission-bar** doc; this step only records the credited specifics.
- **Verified IFF** a named guide, a `CURRENT_SEASON` dated departure, price, group size, and the depth feature are all confirmed from a live source URL. Missing the guide OR the departure → row stays UNVERIFIED (`PARTIAL_PRODUCT_WEIGHT` at most) and the tour is flagged in output.
- **403/404 → keep UNVERIFIED** with HTTP status + "confirmed in snippet"; never drop, never promote without unblocked confirmation.
- **Ranking priority (strict order):**
  1. Expert guide fit — real, theme-appropriate, highly regarded. (Reject figureheads and generic coach-guides.)
  2. Depth within the theme — beneath the surface, not a checklist.
  3. Small / authentic / locally connected — private option a plus.
  4. Value for money (tie-break only): when two are comparably excellent, better value ranks higher.
- **Value rule:** price is not a barrier — the best wins even if pricier — but cost MUST be justified by depth/expertise. A large premium for thin substance → flag it. Never reward price/luxury for its own sake (incl. the `luxury-bespoke` channel, `channel-registry.md`).
- **Format-class mixing:** if the Top-`RANK_DEPTH` mixes format classes (e.g. a multi-day escorted tour alongside a city-based day-scholar or a bespoke private), flag the difference explicitly so the reader compares like with unlike knowingly. A non-`fixed-departure-group` product (`tags-registry.md`) cannot be ranked on the same "dated departure" basis as a `fixed-departure-group` tour.
- **Weak theme:** if the theme cannot fill a strong Top-`RANK_DEPTH`, say so and give the closest strong fits — never pad to `RANK_DEPTH`.
- **Trip-fit sanity:** a ranked theme must still fit one trip under `MAX_TRIP_DAYS` (`travel-config.md`); if a finalist's product implies a longer single itinerary, note it for the **composition** doc.

## EXAMPLE (input → output)

Theme: `IT-01` (Lazio, history/archaeology — Imperial Rome on the ground). (`IT-01` follows `THEME_ID_GRAMMAR`.)

Worked tie-break: a name-brand archaeologist (Simon Elliott) with more exclusive access at ~30% lower price was ranked **above** a comparably-credentialed competitor (Martin Randall / Mark Grahame) — decided on **value**, not cheapest, not luxury, best-justified.

Worked format-class flag: the Context Travel day-format entry (a `scholar-dmc` channel, `channel-registry.md`) was a city-based day-scholar product alongside escorted multi-day tours; the proof-of-concept flagged the format difference explicitly rather than ranking it on the same dated-departure basis.

(Full Italy roster lives in the per-country `rankings/` files — see `italy/`. Keep this global doc example-light.)

Output (`rankings/IT-01.md`) contains: country + arrivals rank, theme/region, one-line capture; the ranked Top-`RANK_DEPTH` (operator · tour name · guide/expertise · group size · duration · approx price · value note · depth/access feature · source link); one line on why #1 wins; a FLAGS block.

## OUTPUT SCHEMA (Step D — `rankings/<theme-id>.md`)

- Country (arrivals rank) + theme/region + one-line capture statement.
- Ranked Top-`RANK_DEPTH`, each row: operator · tour name · guide/expertise · group size · duration · approx price · value note · depth/access feature · source link.
- One line: **why #1 wins**.
- FLAGS block: any unverified leader/departure; any premium-for-thin-substance; any format-class mix; any THIN-NOTE (credited weight below `ADMISSION_BAR`); if the theme is weak, say so and give closest strong fits rather than padding to `RANK_DEPTH`.

## ANTI-PATTERNS (checks — fail the step if true)

(open — this block is a VIEW of `10-lessons-log.md`; append the check when a new lesson lands, tag `Lnn`. The lessons-log is the source, this block the projection — `REGISTRY-PROTOCOL.md`.)

- Ranking on memory/reputation instead of a live-verified specific (violates the memory invariant). (L1, L15)
- Rewarding price/luxury for its own sake instead of cost justified by depth/expertise.
- Mixing format classes (group vs bespoke vs day) in one Top-`RANK_DEPTH` without flagging it. (L9)
- Dropping a 403/404 finalist instead of keeping it UNVERIFIED with snippet evidence. (L9)
- Padding a weak theme to `RANK_DEPTH` instead of saying so and giving closest strong fits. (L6)
- Skipping operator saturation / the `role:axis-proof`+`role:saturation-weight` axes (`axes-registry.md`), so the candidate set misses operators keyword search would not surface. (L7)
- Naming or counting axes by hand instead of filtering `axes-registry.md` by `stage`/`role` tag (the count is derived; convergence needs every `role:convergence-gate` axis dry). (L14, L16)
- Verifying specifics in-session and not APPENDING them back to the corpus/ledger (no compounding — next session re-verifies from scratch). (L4, L15)

## QUALITY GATE

If a Top-`RANK_DEPTH` entry would survive having its specifics replaced by a plausible guess, it isn't verified. Every claim traces to a URL. "Verified" requires the pasted/cited specifics in the committed file, not an assertion.
