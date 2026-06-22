# 07 — Verification & Ranking

AGENT SPEC. Turn the discovered field for one theme into a verified, ranked **Top 5**, every claim traced to a live URL.

INPUT:
- `<country>_theme_map_FINAL.md` — the discovered field (themes + candidate operators) from `04`.
- The corpus rows for this theme (`06`) — including UNVERIFIED rows and fetch-blocked (403/404) operator pages, with their HTTP status notes.
- The 5-axis matrix definitions (`03`/`05`) — LANGUAGE and AUTHORITY-INDEX axes especially.
- Per-country verification ledger (`<country>_verification_ledger.md`, `06`) and global registries (`axes-registry.md`) — READ, do not work from memory.

OUTPUT: `rankings/<theme-id>.md` — verified ranked Top 5 + a FLAGS block (schema in Step D).
NEXT: `11` (composition layer) and any country-level roll-up consume `rankings/<theme-id>.md`. Verified specifics + new operators APPEND back to the corpus (`06`) and verification ledger.

MEMORY INVARIANT: nothing here lives in session memory. The candidate field, corpus rows, HTTP-status notes, and axis definitions are all READ from committed files; every verified specific and ranking is WRITTEN to `rankings/<theme-id>.md` and APPENDED to the corpus/ledger. A fresh session reproduces the same Top 5 from the files alone. "Verified" = pasted/cited specifics in a committed file, never an in-session assertion.

COMPOUNDING: verification accrues. READ the corpus + per-country verification ledger → RUN verification on finalists → APPEND each confirmed specific (guide, dated departure, price, group size, depth feature, format class) back to the corpus row (`06`) and the per-country ledger → PROMOTE any reusable verification source or new operator into the corpus so later themes/countries inherit it. UNVERIFIED and 403/404 rows stay in the ledger with their status, never discarded — a later session retries them.

## PROCEDURE (start = one theme-id)

1. Take the theme-id. READ its rows from the corpus (`06`), the discovered field (`04`), and the per-country verification ledger.
2. **Operator saturation (merge discovery here).** Run the 5-axis check (`03`/`05`) scoped to this theme so the candidate set is complete. Give special weight to the LANGUAGE and AUTHORITY-INDEX axes — they carry the operators keyword search misses.
3. Build the finalist set: every candidate that could plausibly make the Top 5. Order the verification queue by priority: corpus UNVERIFIED rows first, then fetch-blocked (403/404) operator pages.
4. **Verify each finalist from a live source.** Confirm and record:
   - Named guide + their real credential (historian / Egyptologist / naturalist / sommelier / etc.) — confirm it fits the theme.
   - A current-season (2026–27) dated departure.
   - Price per person — note basis: sharing/single, with/without flights.
   - Group size, and whether a private-departure option exists.
   - The specific depth/access feature (exclusive site access, underground, after-hours, dig viewing, etc.).
5. **On 403/404:** harvest date/price/guide from the search snippet; keep the row UNVERIFIED with the HTTP status + "confirmed in snippet" noted (`06`). Never silently drop a blocked page; never promote it to verified without an unblocked confirmation.
6. **On unverifiable guide or 2026–27 departure:** flag the tour in the output. Never guess, never drop silently.
7. **Tag format class** on each finalist (`06`): `fixed-departure group` / `private-bespoke-year-round` / `hybrid-course`.
8. **Rank** the verified finalists on the criteria in priority order (see DECISION RULES → ranking).
9. APPEND every confirmed specific back to the corpus row and the per-country verification ledger (`06`). PROMOTE any new operator or reusable verification source.
10. **Write the output** to `rankings/<theme-id>.md` per Step D schema, including the FLAGS block. Stop.

## DECISION RULES

- **Verified IFF** a named guide, a 2026–27 dated departure, price, group size, and the depth feature are all confirmed from a live source URL. Missing the guide OR the departure → row stays UNVERIFIED and the tour is flagged in output.
- **403/404 → keep UNVERIFIED** with HTTP status + "confirmed in snippet"; never drop, never promote without unblocked confirmation.
- **Ranking priority (strict order):**
  1. Expert guide fit — real, theme-appropriate, highly regarded. (Reject figureheads and generic coach-guides.)
  2. Depth within the theme — beneath the surface, not a checklist.
  3. Small / authentic / locally connected — private option a plus.
  4. Value for money (tie-break only): when two are comparably excellent, better value ranks higher.
- **Value rule:** price is not a barrier — the best wins even if pricier — but cost MUST be justified by depth/expertise. A large premium for thin substance → flag it. Never reward price/luxury for its own sake.
- **Format-class mixing:** if the Top-5 mixes format classes (e.g. a multi-day escorted tour alongside a city-based day-scholar or a bespoke private), flag the difference explicitly so the reader compares like with unlike knowingly. A private/bespoke/year-round product cannot be ranked on the same "dated departure" basis as a fixed-departure tour.
- **Weak theme:** if the theme cannot fill a strong Top 5, say so and give the closest strong fits — never pad to five.

## EXAMPLE (input → output)

Theme: `IT-01` (Lazio, history/archaeology — Imperial Rome on the ground).

Worked tie-break: a name-brand archaeologist (Simon Elliott) with more exclusive access at ~30% lower price was ranked **above** a comparably-credentialed competitor (Martin Randall / Mark Grahame) — decided on **value**, not cheapest, not luxury, best-justified.

Worked format-class flag: the Context Travel day-format entry was a city-based day-scholar product alongside escorted multi-day tours; the proof-of-concept flagged the format difference explicitly rather than ranking it on the same dated-departure basis.

Output (`rankings/IT-01.md`) contains: country + arrivals rank, theme/region, one-line capture; the ranked Top 5 (operator · tour name · guide/expertise · group size · duration · approx price · value note · depth/access feature · source link); one line on why #1 wins; a FLAGS block.

## OUTPUT SCHEMA (Step D — `rankings/<theme-id>.md`)

- Country (arrivals rank) + theme/region + one-line capture statement.
- Ranked Top 5, each row: operator · tour name · guide/expertise · group size · duration · approx price · value note · depth/access feature · source link.
- One line: **why #1 wins**.
- FLAGS block: any unverified leader/departure; any premium-for-thin-substance; any format-class mix; if the theme is weak, say so and give closest strong fits rather than padding.

## ANTI-PATTERNS (checks — fail the step if true)

- Ranking on memory/reputation instead of a live-verified specific (violates the memory invariant).
- Rewarding price/luxury for its own sake instead of cost justified by depth/expertise.
- Mixing format classes (group vs bespoke vs day) in one Top-5 without flagging it.
- Dropping a 403/404 finalist instead of keeping it UNVERIFIED with snippet evidence.
- Padding a weak theme to five instead of saying so and giving closest strong fits.
- Skipping operator saturation / the LANGUAGE + AUTHORITY-INDEX axes, so the candidate set misses operators keyword search would not surface.
- Verifying specifics in-session and not APPENDING them back to the corpus/ledger (no compounding — next session re-verifies from scratch).

## QUALITY GATE

If a Top-5 entry would survive having its specifics replaced by a plausible guess, it isn't verified. Every claim traces to a URL. "Verified" requires the pasted/cited specifics in the committed file, not an assertion.
