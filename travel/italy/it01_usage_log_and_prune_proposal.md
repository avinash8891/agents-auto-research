# IT-01 Usage Log + Prune Proposal

Run date: 2026-06-22
Scope: IT-01 Rome & classical antiquity only.
Approval state: proposal only; no removal/demotion edits applied.

## Usage Log

| item | status | note |
|---|---|---|
| `00-overview-and-principles.md` | USED | Output contract, ranking criteria, quality core, FX rule, no-invention rule. |
| `01-country-ranking.md` | USED | Created Italy row in `country_ranking.md`; scoped to Italy only because Step 2 only needs IT-01. |
| `02-theme-seeding.md` | USED | Read existing Italy v0; no reseed needed. |
| `03-coverage-matrix.md` | USED | Used channel/lens/region/language/authority axes as saturation checklist. |
| `04-discovery-loop.md` | USED | Read prior discovery rounds; did not run broad new discovery because IT-01 already had corpus + ranking saturation. |
| `05-convergence-and-admission-bar.md` | USED | Applied admission, convergence, and CLAIMED/VERIFIED rules; IT-01 clears bar. |
| `06-corpus-and-persistence.md` | USED | Wrote a round corpus file and ledger debt; used row schema. |
| `07-verification-and-ranking.md` | USED | Main output schema and finalist re-verification rules. |
| `08-freshness-and-updates.md` | USED | Used current-date stamping and FX refresh concept; no verify loop run. |
| `09-agent-orchestration.md` | NOT-USED | No parallel subagents; main-thread web verification was enough for one theme. |
| `10-lessons-log.md` | USED | L7/L10 shaped axis-proof and false-convergence handling. |
| `11-trip-composition.md` | NOT-USED | Downstream of rankings; not relevant to a single theme Top 5. |
| `review-findings-2026-06.md` | USED | Quality-core and prune criteria. |
| `travel-config.md` | USED | RANK_DEPTH, CURRENT_SEASON, SMALL_GROUP_MAX, FX_SOURCE, convergence dials. |
| `axes-registry.md` | USED | Identified convergence-gate and saturation-weight axes. |
| `channel-registry.md` | USED | Classified finalists by channel. |
| `lens-registry.md` | USED | Resolved archaeology/history credentials. |
| `sources-registry.md` | USED | Country-ranking and FX source rule. |
| `tags-registry.md` | USED | `verified`, `UNVERIFIED`, `CLAIMED`, format-class, group-size flags. |
| `operator-aliases.md` | USED-LIGHTLY | No IT-01 alias collision found; checked to avoid aggregator double-counting. |
| `theme-archetypes.md` | NOT-USED | Seed already existed; no new archetype surfaced in this one-theme ranking. |
| `REGISTRY-PROTOCOL.md` append/promote mechanics | NOT-USED | No new registry item promoted. |
| `REGISTRY-PROTOCOL.md` typed leads bus | GOT-IN-THE-WAY | The run surfaced two simple notes (Context day-format; Darius private-fit). A full typed leads/routing table added friction beyond the ledger note. |
| `REGISTRY-PROTOCOL.md` INVALIDATION/dirty fixed-point | NOT-USED | No promotion occurred, so no dirty unit existed. |
| `AUDIT-CHECKLIST.md` | NOT-USED | The run itself was more useful than the meta-audit; no audit pass was needed to rank IT-01. |
| `role:`/`stage:` tag filtering | USED | Helpful enough for deriving axis-proof/convergence-gate axes; some ceremony, but not blocking. |
| `role:saturation-weight` | USED | Language and authority-index got explicit weight; unlike Step 1 concern, the tag now has producers. |
| corpus schema-versioning | NOT-USED | No schema change occurred. |
| `leads.md` file | NOT-USED | Not created; ledger handled the two actionable notes. |
| `italy/SEARCH_PROTOCOL.md` | NOT-USED | Read as superseded only; not followed. |
| `italy/tours_proof_of_concept.md` | USED-AS-NEGATIVE-CONTROL | Showed naive ranking; real run demoted Context/AIA treatment and added Peter Sommer/Andante evidence. |

## Prune Proposal Table

| item | doc(s) | proposed action | evidence from run | classification | reversibility |
|---|---|---|---|---|---|
| Dirty-propagation / INVALIDATION fixed-point | `REGISTRY-PROTOCOL.md`, `README.md`, `travel-config.md`, `06`, `09`, `11` | DEMOTE-TO-SCALE-NOTE | No axis/lens/channel promotion happened during IT-01; no dirty flag was created or consumed. Absence of use is not proof of uselessness because a one-theme ranking cannot exercise cross-run promotions. | SCALE-DEFERRED by design | Git history preserves full text. |
| Typed leads bus + routing table | `REGISTRY-PROTOCOL.md`, `README.md`, `07`, `09`, `11`, manifest `leads.md` row | REMOVE from one-theme workflow; DEMOTE routing table to scale note | The run produced only two tangential notes, both handled in `italy/ledger.md`. Creating `leads.md`, choosing lead types, and routing them would add steps without changing IT-01. | FRICTION at this scale; scale-deferred for multi-country learning | Git history preserves; can reintroduce when notes routinely outgrow ledger. |
| Corpus schema-versioning | `06-corpus-and-persistence.md`, `README.md` principle 12 | DEMOTE-TO-SCALE-NOTE | No schema migration occurred. The row schema was simply used as-is. A one-theme run cannot prove migration machinery useless. | SCALE-DEFERRED by design | Git history preserves. |
| `AUDIT-CHECKLIST.md` + static-census/doc-currency guards | `AUDIT-CHECKLIST.md`, `REGISTRY-PROTOCOL.md`, README removal note | REMOVE `AUDIT-CHECKLIST.md`; DEMOTE static-census/doc-currency to a lessons-log maintenance note | The checklist was not used to produce the ranking; reading the actual step docs and running the output exposed the real friction directly. It would have been meta-work before a user artifact. | FRICTION at this scale | Git history preserves. |
| Surplus tag-filtering ceremony | `axes-registry.md`, `07`, `09`, `travel-config.md` | KEEP | Contrary to Step 1 suspicion, tag filtering was used: `role:saturation-weight` now exists on language and authority-index, and `role:convergence-gate` kept the dry-check explicit. | USED | n/a |
| `italy/SEARCH_PROTOCOL.md` superseded shadow doc | `italy/SEARCH_PROTOCOL.md`, `doc-manifest.md` legacy table | REMOVE | Read only to confirm superseded status; following it would have violated current quality core and path rules. It did not contribute to output. | FRICTION / shadow-doc risk | Git history preserves. |
| `italy/tours_proof_of_concept.md` | `italy/tours_proof_of_concept.md`, `doc-manifest.md` legacy table | DEMOTE-TO-SCALE-NOTE or keep as historical negative control | It helped identify what changed: Context was demoted due format/assigned-guide gap; AIA became verified but value-sensitive; Peter Sommer/Andante entered from live evidence. It should not be an executable template. | USED as negative-control, not workflow | Git history preserves. |
| Per-theme one-agent orchestration | `09-agent-orchestration.md` | DEMOTE-TO-SCALE-NOTE | Main-thread verification was enough for one theme; no need to spawn agents or manage raw-output relay. | FRICTION at this scale | Git history preserves. |
| `country_ranking.md` full top-N materialization | `01-country-ranking.md` | KEEP partial output rule | Italy row was enough for IT-01; full top 10 would be scope beyond the task. | Lazy but sufficient | n/a |

Decision guard applied: uncertain removals are demotions, not deletes.

## Part C - Lessons + Quality Verdict

Quality core changed the ranking versus naive existence-verification:
- Context Travel was #3 in the old proof-of-concept. In the real run it is not ranked because the listing does not assign a named expert to a dated departure and the format is a day tour, not a multi-day fixed-departure product.
- AIA is no longer a vague unverified placeholder: Crispin Corrado and the 14-25 Oct 2026 departure were verified, but its high price and broader itinerary put it below Rome-focused peers.
- Peter Sommer moved into the ranked list because the live page now exposes a 2026 dated departure, price, max group size, and named leaders.

New gap surfaced:
- The output schema needs a clean way to rank mixed format-classes separately. The best day/private experts in Rome may be better for some travellers than the #5 fixed-departure tour, but forcing them into one Top 5 makes the comparison muddy.

Honest verdict:
- IT-01 is good enough to trust for a traveller seeking a fixed-departure, expert-led Rome antiquity trip. It is not yet a complete "best Rome expert experience of any format" because private/day-format experts need a separate comparison table with price, assigned guide, and availability captured consistently.
