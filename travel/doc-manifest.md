# Doc Manifest (role → file)

Single map of stage role → file. Reference docs by **role/slug** (e.g. "admission-bar step") resolved here, so renumbering/renaming updates ONE place and a broken ref is detectable. Lessons are pinned by stable Lnn id in `10-lessons-log.md`.

## STEP DOCS
| slug | role | file |
|------|------|------|
| overview | scope, definitions, output contract | 00-overview-and-principles.md |
| country-ranking | build top-N country list | 01-country-ranking.md |
| theme-seeding | seed v0 theme map | 02-theme-seeding.md |
| coverage-matrix | discovery axes (rigor core) | 03-coverage-matrix.md |
| discovery-loop | run discovery rounds | 04-discovery-loop.md |
| admission-bar | convergence + admission bar | 05-convergence-and-admission-bar.md |
| corpus | persistence, schema, IDs | 06-corpus-and-persistence.md |
| ranking | verify + rank Top-N | 07-verification-and-ranking.md |
| freshness | refresh loops | 08-freshness-and-updates.md |
| orchestration | agent dispatch | 09-agent-orchestration.md |
| lessons | append-only lessons log | 10-lessons-log.md |
| composition | multi-lens trip composition | 11-trip-composition.md |

## CONFIG + REGISTRIES (single sources)
| slug | role | file |
|------|------|------|
| config | named dials | travel-config.md |
| registry-protocol | shared registry mechanics | REGISTRY-PROTOCOL.md |
| axes-registry | discovery axes + stage/role tags | axes-registry.md |
| channel-registry | provider sub-types | channel-registry.md |
| lens-registry | lens vocabulary | lens-registry.md |
| archetypes | theme-archetype library | theme-archetypes.md |
| sources-registry | ranking data sources | sources-registry.md |
| operator-aliases | de-dup aliases/exclusions | operator-aliases.md |
| tags-registry | row/theme tag vocabularies (status, format-class, watch/leisure, strength) | tags-registry.md |
| manifest | this file | doc-manifest.md |
| audit-checklist | 17 known issue-classes (view of lessons-log); recurring cross-doc audit | AUDIT-CHECKLIST.md |

## SHARED STORES (the complete set — what compounds across runs/sessions)
Global: `travel-config.md` (dials) · `REGISTRY-PROTOCOL.md` (mechanics) · the 7 registries (`axes-registry`, `channel-registry`, `lens-registry`, `theme-archetypes`, `sources-registry`, `operator-aliases`, `tags-registry`) · `10-lessons-log.md` (the append-only **failure store**; anti-patterns are its view). Per-country: `<country>/axes.md`, `<country>/ledger.md`, `<country>/corpus_FINAL.md` (+ the artifacts table below). Every step writes its learnings to one of these — none are dead-ends.

## GLOBAL DATA ARTIFACTS (outputs, not docs — don't confuse with the like-named method doc)
| file | what | produced by |
|------|------|-------------|
| country_ranking.md | the ordered top-N country list (DATA) | the `country-ranking` step (01-country-ranking.md) — the METHOD doc |

## PER-COUNTRY ARTIFACTS (canonical scheme — single source)
All per-country files live under `<country>/`. NORMALIZATION: a `<country>_X` reference anywhere in the docs denotes the same file as `<country>/X` — canonical form is `<country>/X`; treat the prefix style as shorthand.

| file | what | written by |
|------|------|-----------|
| `<country>/theme_map_v0..FINAL.md` | seed → reshaped → converged theme map + convergence tracker | theme-seeding, discovery-loop, admission-bar |
| `<country>/axes.md` | per-country axis ledger (active/promoted/pending axes + language set + region-axis values) | theme-seeding (first-populates region values), coverage-matrix, discovery-loop |
| `<country>/ledger.md` | **the single per-country ledger** — thin-notes + re-test triggers (admission-bar) · verification debt / UNVERIFIED + 403-blocked (corpus, ranking) · changelog (freshness). Replaces `_ledger`/`_verification_ledger`/`_changelog`. | admission-bar, ranking, freshness |
| `<country>/corpus/round<N>_<cluster>.md` | working raw inventories (per round) | discovery-loop subagents |
| `<country>/corpus_FINAL.md` | consolidated, locked corpus (round files merged, `first_seen_round` stamped) | freshness consolidation |
| `<country>/rankings/<theme-id>.md` | ranked Top-`RANK_DEPTH` per theme | ranking |
| `<country>/compositions/<label>.md` | multi-lens itineraries | composition |
| `<country>/verify_<date>.md` | dated VERIFY-pass diff report | freshness |
| `<country>/leads.md` | typed intelligence capture (tangential signals from verification/discovery) + routing to the step/registry each fine-tunes | discovery-loop, ranking (emit); seeding/freshness/registries (consume) — see `REGISTRY-PROTOCOL.md` INTELLIGENCE CAPTURE & ROUTING |

Rule: when a step doc cites another, use the slug; resolve via this table. Renaming a file = edit one row here.
