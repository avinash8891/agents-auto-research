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

## GLOBAL DATA ARTIFACTS (outputs, not docs — don't confuse with the like-named method doc)
| file | what | produced by |
|------|------|-------------|
| country_ranking.md | the ordered top-N country list (DATA) | the `country-ranking` step (01-country-ranking.md) — the METHOD doc |

## PER-COUNTRY ARTIFACTS
`<country>/` holds: `<country>_theme_map_v0..FINAL.md`, `axes.md` ledger, `corpus/round*.md`, `rankings/<theme-id>.md`, and (freshness) `<country>_corpus_FINAL.md`, `<country>_verify_<date>.md`, `<country>_changelog.md`.

Rule: when a step doc cites another, use the slug; resolve via this table. Renaming a file = edit one row here.
