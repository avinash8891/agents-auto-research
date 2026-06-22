# 07 — Verification & Ranking

AGENT SPEC. Turn the discovered field for one theme into a verified, ranked **Top-`RANK_DEPTH`** (`travel-config.md`; ceiling, not a quota — list fewer if fewer clear the bar), every claim traced to a live URL.

INPUT:
- `<country>_theme_map_FINAL.md` — the discovered field (themes + candidate operators) from the **theme-seeding**/**discovery-loop** docs (`doc-manifest.md`).
- The corpus rows for this theme (**corpus** doc) — including UNVERIFIED rows and fetch-blocked (403/404) operator pages, with their HTTP status notes.
- The discovery axes (**coverage-matrix** / **admission-bar** docs) — the baseline axes in `axes-registry.md`, especially the axes tagged `role:axis-proof` and `role:saturation-weight` (these carry the operators keyword search misses; identities are READ from the registry, never named from memory).
- Per-country verification ledger (`<country>/ledger.md`, **corpus** doc) and global registries (`axes-registry.md`, `lens-registry.md`) — READ, do not work from memory. The credential→lens table (`lens-registry.md`) is the authoritative lookup for whether a credential string satisfies the theme's lens (D.4); READ it, never judge fit from memory.

OUTPUT: `rankings/<theme-id>.md` — intent/lane block + verified ranked Top-`RANK_DEPTH` + evidence digest + FLAGS block (schema in Step D). `<theme-id>` follows `THEME_ID_GRAMMAR` (`travel-config.md`).
NEXT: the **composition** doc (`doc-manifest.md`) and any country-level roll-up consume `rankings/<theme-id>.md`. Verified specifics + new operators APPEND back to the corpus (**corpus** doc) and verification ledger.

MEMORY INVARIANT: nothing here lives in session memory. The candidate field, corpus rows, HTTP-status notes, and axis definitions are all READ from committed files; every verified specific and ranking is WRITTEN to `rankings/<theme-id>.md` and APPENDED to the corpus/ledger. A fresh session reproduces the same Top-`RANK_DEPTH` from the files alone. "Verified" = pasted/cited specifics in a committed file, never an in-session assertion.

COMPOUNDING: verification accrues. READ the corpus + per-country verification ledger → RUN verification on finalists → APPEND each confirmed specific (guide, dated departure, price, group size, depth feature, format class) back to the corpus row (**corpus** doc) and the per-country ledger → PROMOTE any reusable verification source or new operator into the corpus so later themes/countries inherit it. Registry promotion mechanics (append-only, promotion bar, log) are owned by `REGISTRY-PROTOCOL.md` — follow it, don't restate. UNVERIFIED and 403/404 rows stay in the ledger with their status, never discarded — a later session retries them.

## PROCEDURE (start = one theme-id)

1. Take the theme-id. READ its rows from the corpus (**corpus** doc), the discovered field (**theme-seeding**/**discovery-loop** docs), and the per-country verification ledger.
1a. Write the run's **intent/lane block** before ranking: traveller intent, ranking lane, excluded formats, primary success metric, and tie-breakers. If these cannot be stated, STOP; the ranking target is underspecified.
2. **Operator saturation (merge discovery here).** Run the axis check (**coverage-matrix** / **admission-bar** docs) over the baseline axes in `axes-registry.md`, scoped to this theme, so the candidate set is complete. Give special weight to the axes tagged `role:saturation-weight` — and confirm the axes tagged `role:convergence-gate` are dry for this theme — since those carry the operators keyword search misses. (Convergence requires every `role:convergence-gate` axis dry, not a frozen axis count; the count is derived from `axes-registry.md`.)
3. Build the finalist set: every candidate with no known `FAIL` in tuple, credential fit, depth, or format under the rubric below, plus any UNVERIFIED/fetch-blocked row that could clear those gates if fetched. Order the verification queue by priority: corpus UNVERIFIED rows first, then fetch-blocked (403/404) operator pages.
4. **Verify each finalist from a live source.** This step is the AUTHORITATIVE 100%-finalist-re-verify gate: **every DISPLAYED finalist is re-fetched against a live URL, no exceptions.** Spot-check SAMPLING (the **orchestration** doc) applies only to the broad corpus — never as the sole gate before ranking; the **orchestration** doc DEFERS to this step for displayed rows. Confirm and record:
   - **D.4 — Named guide + their real credential** (historian / Egyptologist / naturalist / sommelier / temple-lineage holder / master-tracker / nationally-licensed specialist guide / etc.). The credential string MUST resolve to the theme's lens via the credential→lens table in `lens-registry.md` (both academic AND non-academic / local credential types) — else FLAG `credential-mismatch` and cap the row at `PARTIAL_PRODUCT_WEIGHT`.
   - **Credential corroboration (operator-deception guard).** The credential MUST be corroborated by ≥1 source whose domain is NOT the operator's own (academic/museum/excavation affiliation, authored works / ORCID, learned-society or professional register, recognised oral-tradition / lineage / guild standing, or a cross-platform review corpus). Record that URL as `credential_source` (DISTINCT from the operator/tour URL). Corroborated ⇒ FULL; operator-page-only ⇒ status `CLAIMED` + "credential uncorroborated" FLAG, cap `PARTIAL_PRODUCT_WEIGHT` — and the credential is NEVER displayed as a verified ranking specific. Empty `credential_source` ⇒ CLAIMED.
   - **Figurehead / who-leads sub-check (operator-deception guard).** Confirm the NAMED expert is assigned to THIS dated departure as an explicit sub-check — never inferred from a name + a date merely co-occurring on the page. If the operator only guarantees "one of our scholars", FLAG `figurehead-risk` and hold the row UNVERIFIED / PARTIAL.
   - A current-season (`CURRENT_SEASON`, `travel-config.md`) dated departure.
   - Price per person — note basis: sharing/single, with/without flights. Display the USD-equivalent per the FX rule (`travel-config.md` + `sources-registry.md`): it is a COMPUTED number, cite rate source + date as "~USD (rate dated YYYY-MM-DD)" from `FX_SOURCE`, refresh on `VERIFY_CADENCE`, never from recall.
   - Group size, and whether a private-departure option exists. Treat the stated group size as a CLAIM to verify against `SMALL_GROUP_MAX` (`travel-config.md`); FLAG `group-size-unstated` when only a marketing adjective ("intimate", "small") is given with no number.
   - The specific depth/access feature (exclusive site access, underground, after-hours, dig viewing, etc.).
5. **On 403/404:** record only operator/tour EXISTENCE and a coarse "date noted in snippet (403, not fetched)". A snippet-harvested GUIDE NAME or PRICE may NEVER be displayed as a ranked-row specific — a snippet is engine-summarised, the weakest source; guide/price read "unread (403) — not displayed" until an unblocked fetch. Keep the row UNVERIFIED with the HTTP status (**corpus** doc). Never silently drop a blocked page; never promote it to verified without an unblocked confirmation.
6. **On unverifiable guide or `CURRENT_SEASON` departure:** flag the tour in the output. Never guess, never drop silently.
7. **Tag format class** on each finalist — values in `tags-registry.md` (written per **corpus** doc).
8. **Rank** the verified finalists on the criteria in priority order (see DECISION RULES → ranking).
9. APPEND every confirmed specific back to the corpus row and the per-country verification ledger (**corpus** doc). PROMOTE any new operator or reusable verification source per `REGISTRY-PROTOCOL.md`.
9a. **Capture leads (don't lose tangential intelligence).** Verification reads whole operator pages — emit every signal that doesn't fit the row schema as a **typed lead** in `<country>/leads.md` with provenance (URL + theme-id + run), per `REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING: theme/sub-lens hints, a guide who leads other themes, channel/affinity signals, authority leads, new archetype instances, disqualifier patterns, seasonality/access quirks. Route each per the table; a lead implying new coverage **dirties** the affected unit.
10. **Write the output** to `rankings/<theme-id>.md` per Step D schema, including the FLAGS block. Stop.

## DECISION RULES

- The method constrains judgment and makes it auditable; it does not pretend to automate truth. Every qualitative call below MUST be reduced to the fixed rubric labels in `tags-registry.md` `candidate.evidence-rating`: `VERIFIED`, `CLAIMED`, `PARTIAL`, or `FAIL`.
- **Credited-product accounting (the admission link).** Each verified finalist contributes weight toward this theme: `FULL_PRODUCT_WEIGHT` for a product with a named guide AND a confirmed `CURRENT_SEASON` dated departure; `PARTIAL_PRODUCT_WEIGHT` for an UNVERIFIED-date or unnamed-guide product (`travel-config.md`). A theme below `ADMISSION_BAR` / `MIN_CREDENTIALED_PRODUCTS` in credited weight is THIN (e.g. one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT` stays under `ADMISSION_BAR` → THIN-NOTE). Bar definition is owned by the **admission-bar** doc; this step only records the credited specifics.
- **Verified IFF** a named guide, a `CURRENT_SEASON` dated departure, price, group size, and the depth feature are all confirmed from a live source URL. Missing the guide OR the departure → row stays UNVERIFIED (`PARTIAL_PRODUCT_WEIGHT` at most) and the tour is flagged in output.
- **Existence-verification ≠ quality-verification.** A load-bearing claim (credential / who-leads / group-size / depth / reputation) whose ONLY evidence is the operator's own domain is `CLAIMED` (`tags-registry.md`), not VERIFIED. `CLAIMED` sits BELOW `UNVERIFIED`: it caps the row at `PARTIAL_PRODUCT_WEIGHT`, carries a FLAG, and is NEVER displayed as a verified ranking specific. "Verified" requires independent (non-seller-domain) corroboration; seller-page-only = CLAIMED.
- **INDEP_EVIDENCE (non-seller-domain required for criteria 1–3).** Reputation (ranking criterion 1, highly regarded), depth (criterion 2), and authenticity (criterion 3) MUST EACH cite at least one source whose domain is NOT the seller's own — academic/museum/excavation affiliation, authored works / ORCID, learned-society or professional register, recognised oral-tradition / lineage / guild standing, OR a cross-platform review corpus. The seller's own domain is NEVER sole evidence for reputation/depth/authenticity. Missing ⇒ cap at `PARTIAL_PRODUCT_WEIGHT` + FLAG (the claim is CLAIMED, not VERIFIED).
- **403/404 → keep UNVERIFIED** with HTTP status + "date noted in snippet (403, not fetched)"; never drop, never promote without unblocked confirmation. A snippet-harvested guide name or price is NEVER a displayed ranked specific.
- **Candidate Evidence Rubric (mandatory):**

| dimension | VERIFIED | CLAIMED | PARTIAL | FAIL |
|---|---|---|---|---|
| tuple | date + price + group size + operator URL visible from live source | one non-guide/non-date field missing but product is bookable | two fields missing or source split is ambiguous | not current/bookable, or guide/date both missing |
| credential fit | named expert's credential independently corroborated and resolves to the theme lens in `lens-registry.md` | seller page names a credential string that would resolve if independently corroborated | named expert exists, but credential resolves only to an adjacent lens or is not in the table yet | no named expert, generic guide only, or `credential-mismatch` |
| depth | itinerary has >=3 theme-specific stops/access/interpretation points, or >=2 plus a named specialist lecture/site-access element | seller-only depth claim with >=3 concrete itinerary points | theme appears as one segment or <3 concrete theme points | adjectives only, or generic sightseeing not tied to the theme |
| reputation | >=1 independent institutional bio, publication/register, award, specialist press, or >=2-platform review corpus supports operator/expert standing | third-party directory/review only, or seller-only reputation | one stale/unclear independent mention; log source and cap | no independent signal |
| format | same `format-class` as the ranking lane | same trip type but one comparability field missing; disclose in FLAGS | different lane (`day-format`, `private-bespoke`, `custom-multi-day`, `cruise-shore`, `hybrid-course`) but relevant as closest-fit | wrong product type for this ranking |
| value | premium is backed by VERIFIED expertise/access/small group/duration/rare logistics; or price is lower than a comparably verified row | price is seller-justified only | price is materially higher than cleaner rows and has only PARTIAL support | premium-for-thin-substance: high price with FAIL/PARTIAL depth or credential |

- **Rubric gate:** a ranked row may not contain `FAIL` for tuple, credential fit, depth, or format. `FAIL` on any of those means closest-fit or THIN-NOTE, not ranking.
- **Rubric ordering:** two or more `PARTIAL` dimensions cannot outrank an otherwise comparable candidate with fewer `PARTIAL`s. A `CLAIMED` load-bearing claim always carries a FLAG and caps the row at `PARTIAL_PRODUCT_WEIGHT`.
- **Format lanes:** rank inside one lane by default: `fixed-departure-group`, `private-bespoke`, `day-format`, `custom-multi-day`, `cruise-shore`, or `hybrid-course`. Mixing lanes is allowed only when the output explicitly says it is comparing unlike formats; otherwise split into separate ranking/thin-note sections.
- **Ambiguous pages:** missing fields stay missing. Do not infer date, price, guide, group size, or role assignment from snippets, co-located text, old cached copies, or marketing adjectives.
- **Ranking priority (strict order):** sort by rubric labels in this order: credential fit, reputation, depth, format, then value. A `VERIFIED` label beats `CLAIMED`, `CLAIMED` beats `PARTIAL`, and any `FAIL` in tuple/credential/depth/format removes the row from the ranked list.
- **Value rule:** value is the final tie-break only, using the value row above. Price/luxury alone never improves rank.
- **Format-class mixing:** if the Top-`RANK_DEPTH` mixes format classes (e.g. a multi-day escorted tour alongside a city-based day-scholar or a bespoke private), flag the difference explicitly so the reader compares like with unlike knowingly. A non-`fixed-departure-group` product (`tags-registry.md`) cannot be ranked on the same "dated departure" basis as a `fixed-departure-group` tour.
- **Thin/failed theme:** if rankable rows < `RANK_DEPTH`, state the rankable count and put closest-fit rows below the ranked list — never pad to `RANK_DEPTH`.
- **Trip-fit sanity:** a ranked theme must still fit one trip under `MAX_TRIP_DAYS` (`travel-config.md`); if a finalist's product implies a longer single itinerary, note it for the **composition** doc.

## EXAMPLE (input → output)

Theme: `IT-01` (Lazio, history/archaeology — Imperial Rome on the ground). (`IT-01` follows `THEME_ID_GRAMMAR`.)

Worked tie-break: a name-brand archaeologist (Simon Elliott) with more exclusive access at ~30% lower price was ranked **above** a comparably-credentialed competitor (Martin Randall / Mark Grahame) — decided by the value row, not cheapest and not luxury.

Worked format-class flag: the Context Travel day-format entry (a `scholar-dmc` channel, `channel-registry.md`) was a city-based day-scholar product alongside escorted multi-day tours; the proof-of-concept flagged the format difference explicitly rather than ranking it on the same dated-departure basis.

ILLUSTRATIVE de-bias outcome (schematic — NOT a verified ranking; the real worked example is owed by the first non-Western run): for a Kenya wildlife theme, a named **KPSGA Gold-rated local master tracker** running his own `local-direct` departures (credential corroborated off-domain via the KPSGA register, lifelong in-country lineage, guides employed-not-subcontracted) ranks **above** a Western `academic-operator` whose trip subcontracts a generic driver-guide — decided on criteria 1–3 (expert guide-fit + depth + authentic/locally-connected), each on INDEP_EVIDENCE, the value tie-break never reached. Teaching point: the intended, CORRECT outcome is the local master winning the slot; a thin English-review footprint is NOT a reason to down-rank a credential-corroborated, theme-fit local master below a better-marketed Western operator (`tags-registry.md` anti-pattern; L32). Replace this schematic with a fully-verified non-Western `rankings/<theme-id>.md` once the first non-Western country is run.

(Full Italy roster lives in the per-country `rankings/` files — see `italy/`. Keep this global doc example-light.)

Output (`rankings/IT-01.md`) contains: country + arrivals rank, theme/region, one-line capture; the ranked Top-`RANK_DEPTH` (operator · tour name · guide/expertise · group size · duration · approx price · value note · depth/access feature · source link); one line on why #1 wins; a FLAGS block.

## OUTPUT SCHEMA (Step D — `rankings/<theme-id>.md`)

- Country (arrivals rank) + theme/region + one-line capture statement.
- Intent/lane block:
  - traveller intent
  - ranking lane (`format-class`)
  - excluded formats
  - primary success metric
  - tie-breakers
- Ranked Top-`RANK_DEPTH`, each row: operator · tour name · guide/expertise · group size · duration · approx price (with USD-equivalent + rate date, FX rule) · value note · depth/access feature · source link · `credential_source` (the independent, non-operator-domain URL corroborating the guide's credential — DISTINCT from the operator/tour URL; empty ⇒ credential is CLAIMED, capped PARTIAL).
- One line: **why #1 wins**.
- Evidence digest: compact table of load-bearing claims used for ranking.
  | claim | evidence URL | rubric label | used for rank? |
  |---|---|---|---|
- FLAGS block: any unverified leader/departure; any `CLAIMED` load-bearing claim (seller-page-only evidence); any `credential-mismatch` (credential does not resolve to the theme's lens via the `lens-registry.md` table); any `figurehead-risk` (named expert not confirmed on this departure / only "one of our scholars"); any "credential uncorroborated" (empty `credential_source`); any `group-size-unstated` (marketing adjective, no number); any premium-for-thin-substance; any format-class mix; any THIN-NOTE (credited weight below `ADMISSION_BAR`); if rankable rows < `RANK_DEPTH`, state the count and give closest-fit rows only below the ranked list.

This output schema is a versioned contract (same rule as the corpus row schema — `corpus` SCHEMA EVOLUTION): a field add/rename is lesson-tracked, the `composition` consumer is updated in lockstep, and older `rankings/<theme-id>.md` files are re-emitted (marked `dirty` for rebuild from the corpus) rather than left with a stale field set.

## ANTI-PATTERNS (checks — fail the step if true)

(open — this block is a VIEW of `10-lessons-log.md`; append the check when a new lesson lands, tag `Lnn`. The lessons-log is the source, this block the projection — `REGISTRY-PROTOCOL.md`.)

- Ranking on memory/reputation instead of a live-verified specific (violates the memory invariant). (L1, L15)
- Writing a ranking without an intent/lane block, or ranking a candidate whose load-bearing claims are absent from the evidence digest.
- Rewarding price/luxury for its own sake instead of applying the value row in the Candidate Evidence Rubric.
- Mixing format classes (group vs bespoke vs day) in one Top-`RANK_DEPTH` without flagging it. (L9)
- Dropping a 403/404 finalist instead of keeping it UNVERIFIED with snippet evidence. (L9)
- Padding to `RANK_DEPTH` when rankable rows < `RANK_DEPTH`, instead of stating the count and separating closest-fit rows. (L6)
- Skipping operator saturation / the `role:axis-proof`+`role:saturation-weight` axes (`axes-registry.md`), so the candidate set misses operators keyword search would not surface. (L7)
- Naming or counting axes by hand instead of filtering `axes-registry.md` by `stage`/`role` tag (the count is derived; convergence needs every `role:convergence-gate` axis dry). (L14, L16)
- Verifying specifics in-session and not APPENDING them back to the corpus/ledger (no compounding — next session re-verifies from scratch). (L4, L15)
- Displaying a snippet-harvested guide name or price as a ranked-row specific (a snippet is the weakest source; only existence + "date noted in snippet (403, not fetched)" may appear). (L14)
- Ranking on a credential whose only evidence is the seller's own page (seller-page-only = `CLAIMED`, not VERIFIED — needs a non-seller-domain `credential_source`). (L4, L6, L7)
- Treating a named expert as confirmed for a departure when the operator only co-locates a name and a date, or only guarantees "one of our scholars" (figurehead-risk unverified). (L6)
- Letting a credential ride that does not resolve to the theme's lens via the `lens-registry.md` credential→lens table (credential-mismatch — cap PARTIAL, FLAG). (L5)
- Citing the seller's own domain as sole evidence for reputation/depth/authenticity (criteria 1–3 each require a non-seller-domain source — INDEP_EVIDENCE). (L4, L6)
- Displaying an approx price without the FX rule's USD-equivalent + rate source + date, or computing it from recall. (L12)

## QUALITY GATE

If a Top-`RANK_DEPTH` entry would survive having its specifics replaced by invented-but-well-formed fields, it isn't verified. Every claim traces to a URL — and for reputation/depth/authenticity (criteria 1–3) that URL is NOT the seller's own domain (INDEP_EVIDENCE). "Verified" requires the pasted/cited specifics in the committed file, not an assertion.

Second clause: **if the only evidence for a load-bearing claim is the seller's own page, it is `CLAIMED`, not VERIFIED** (`tags-registry.md`). Existence-verification ≠ quality-verification: a credential / who-leads / group-size / depth / reputation claim with seller-page-only evidence caps the row at `PARTIAL_PRODUCT_WEIGHT`, carries a FLAG, and is never displayed as a verified ranking specific.
