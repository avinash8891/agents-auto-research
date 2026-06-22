# Global Tags Registry

Single home for the small controlled vocabularies used as row/theme tags, so writer (`corpus`) and consumer (`ranking`) docs never spell values independently (they had already drifted: `private-bespoke-year-round` vs `private/bespoke/year-round`). Protocol: `REGISTRY-PROTOCOL.md`. Docs reference a tag set by name; values live here only.

## row.status
| value | meaning |
|-------|---------|
| `verified` | named guide + confirmed `CURRENT_SEASON` dated departure + price, all from a live page; "verified" requires INDEPENDENT (non-seller-domain) corroboration of the load-bearing claim ([C2]) |
| `UNVERIFIED` | operator/tour real, but guide and/or dated departure unconfirmed (incl. 403/404 kept via snippet) |
| `CLAIMED` | [C2] a load-bearing claim (credential / who-leads / group-size / depth / reputation) whose ONLY evidence is the operator's OWN domain. Sits BELOW `UNVERIFIED`: caps the row at `PARTIAL_PRODUCT_WEIGHT` (`travel-config.md`), carries a FLAG, and is NEVER displayed as a verified ranking specific. Seller-page-only = `CLAIMED`; independent corroboration is what makes it `verified` |
| `stale` | `last_checked` older than `VERIFY_CADENCE` window (`travel-config.md`) |
| `withdrawn` | tour/departure confirmed pulled by the operator on a VERIFY re-fetch (was live, now removed) |
| `permanently-gone` | N consecutive fetch misses (`travel-config.md`) — the tour/departure is GONE, not transient; distinct from `temporarily-unreachable` |
| `temporarily-unreachable` | single / short-lived fetch failure — keep the row (incl. via snippet), retry per `VERIFY_CADENCE`; distinct from `permanently-gone` |

## candidate.evidence-rating (used by `ranking` rubric)
| value | meaning |
|-------|---------|
| `VERIFIED` | satisfies the dimension's `VERIFIED` cell in `07-verification-and-ranking.md` |
| `CLAIMED` | satisfies the dimension's `CLAIMED` cell in `07-verification-and-ranking.md`; flagged and capped where load-bearing |
| `PARTIAL` | satisfies the dimension's `PARTIAL` cell in `07-verification-and-ranking.md` |
| `FAIL` | satisfies the dimension's `FAIL` cell in `07-verification-and-ranking.md`; tuple/credential/depth/format FAIL removes the row from ranking |

## format-class (load-bearing for rankability in `ranking`)
| value | rankability note |
|-------|------------------|
| `fixed-departure-group` | rankable on a dated departure directly |
| `private-bespoke` | private/bespoke/year-round; can't be admitted on the "dated departure" basis the same way; flag when mixed in a Top-`RANK_DEPTH` |
| `day-format` | day tour / stackable; not a single immersive itinerary; flag when mixed |
| `custom-multi-day` | custom multi-day itinerary; rank only inside a custom/private lane unless explicitly flagged as closest-fit |
| `cruise-shore` | cruise/shore excursion format; rank only inside a cruise/shore lane unless explicitly flagged as closest-fit |
| `hybrid-course` | course/residency hybrid, not a classic escorted tour; flag when mixed |

Mixing format-classes in one ranked list → flag explicitly (`ranking`).

## theme.seed-tag (advisory, from `theme-seeding`)
| value | meaning |
|-------|---------|
| `theme` | a real candidate theme |
| `watch/leisure` | advisory tag — likely no expert-led market; discovery + `admission-bar` decide on live evidence and OVERRIDE this tag; never used to kill a theme at seed |

## theme.strength (seed guess, from `theme-seeding`)
| value | meaning |
|-------|---------|
| `Strong` / `Medium` / `Thin` | expected depth of the expert-led market (a guess; discovery confirms) |

## admission.disqualifiers (open — append on discovery; used by `admission-bar`)
Open enumerations (`REGISTRY-PROTOCOL.md`) of what does NOT count toward the admission bar. Append a case when discovered, with provenance.
- **non-qualifying product** = not a sold leisure tour: university course · master-class · lecture-residency · retreat · maker-workshop · day-activity · pilgrimage-without-study-content. (provenance: Italy R4)
- **non-qualifying expert** = fails the credential test. The test is NOT "is a name published?" but "is the credential CORROBORATED independent of the operator AND theme-fit (resolves to the theme's lens via the credential→lens table, `lens-registry.md` [C5])?". Disqualifiers: credential evidenced ONLY on the operator's own domain (→ `CLAIMED`, not a qualifying expert) · credential that does not resolve to the theme's lens (FLAG `credential-mismatch`) · generic licensed city guide with no theme-fit standing · artisan/trifolao (unless the craft IS the theme). (provenance: Italy R4)
  - ANTI-PATTERN: scoring a named local/indigenous master as a generic guide because the credential is non-academic. A named local/indigenous master with VERIFIABLE STANDING qualifies even without a Western degree ([C8]); the credential→lens table ([C5]) carries non-academic / local credential types as first-class. Demand independent corroboration, not academic form.
- **non-qualifying operator** = aggregator/marketplace reselling others (count the underlying operator; see `operator-aliases.md`). (provenance: Italy R4)

## country.outcome (country-level verdict, from `convergence-and-admission-bar` / `corpus-and-persistence`)
[C10] DEFINED here; `05` (convergence) and `06` (corpus) REFERENCE it. Honest country-level ceiling, not a research-gap excuse.
| value | meaning |
|-------|---------|
| `rich` | converged with a deep expert-led market (meets `MIN_THEMES_PER_COUNTRY` and `MIN_COUNTRY_CREDITED_WEIGHT`, `travel-config.md`) |
| `partial` | converged but below one of the country-level minima — emit a country-level note |
| `thin-market` | swept-and-dry yet genuinely few qualifying products exist — the real ceiling, distinct from a research gap |
| `no-expert-led-market` | swept-and-dry and no qualifying expert-led product exists at all |

DECISION: the FINAL must distinguish converged-`rich` from converged-`thin-market`/`partial` and state the honest ceiling ("could NOT do this well — here's why: …, real ceiling not a research gap"). A `thin-market`/`no-expert-led-market` verdict requires search EXHAUSTION ([C9]), not field completeness — a source-base coverage limitation raises a coverage-limitation FLAG and makes the verdict PROVISIONAL.

## coverage.cell-state (per coverage-matrix cell, from `coverage-matrix` / `discovery-loop`)
[C11] A coverage cell's terminal state, DISTINCT from `empty gap` (not yet swept) and `covered` (qualifying products found).
| value | meaning |
|-------|---------|
| `junk-saturated` / `low-signal` | the cell hit its search-effort budget (`MAX_SEARCHES_PER_AXIS` / `PER_COUNTRY_SEARCH_BUDGET`, `travel-config.md`) with ONLY disqualified/aggregator results — exhausted, not a covered cell and not an unswept empty gap |

DECISION: do NOT collapse `junk-saturated`/`low-signal` into `covered` (no qualifying product was found) nor into `empty gap` (the budget WAS spent). It is a logged NON-CONVERGENCE residual ([C11]); budget-exhausted-before-convergence stamps the artifact INCOMPLETE.

## PROMOTION LOG
- (seed) Baseline tag vocabularies (row.status, format-class, theme.seed-tag, theme.strength) established.
- (Italy R4) admission.disqualifiers seeded (non-qualifying product/operator/expert).
- (L8 / Italy freshness) `withdrawn` added to row.status — a VERIFY re-fetch can confirm a departure was pulled.
- ([C2]) `CLAIMED` added to row.status — load-bearing claim with ONLY seller-own-domain evidence; sits below `UNVERIFIED`, caps at `PARTIAL_PRODUCT_WEIGHT`, FLAG, never displayed as a verified specific.
- (freshness split) `permanently-gone` (N consecutive fetch misses — gone, not transient) and `temporarily-unreachable` (single/short fetch failure) added to row.status as distinct fetch-outcome states.
- ([C10]) `country.outcome` section added — enum `rich | partial | thin-market | no-expert-led-market` for the honest country-level verdict.
- ([C11]) `coverage.cell-state` added — `junk-saturated`/`low-signal` cell distinct from empty-gap and covered (budget spent, only disqualified/aggregator results).
- ([C5]/[C8]) admission.disqualifiers → non-qualifying expert reframed around independent credential corroboration + theme-fit via the credential→lens table; anti-pattern added (named local/indigenous master ≠ generic guide).
- (post JP-01 / IT-THIN) `candidate.evidence-rating` added for the ranking rubric: `VERIFIED | CLAIMED | PARTIAL | FAIL`.

## UPDATE
Mechanics: `REGISTRY-PROTOCOL.md`. Append a value when a new state is genuinely needed; never let two docs invent divergent spellings. The tag/value sets here are open enumerations — append-on-discovery with provenance (log additions above).
