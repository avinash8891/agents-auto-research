# Audit Checklist — known issue-classes (a VIEW of the lessons-log)

This is the reusable cross-document audit. It is a **view of `10-lessons-log.md`** (same as anti-patterns): each issue-class generalizes one or more lessons. It is **open — append a class when a new generalizable lesson lands**, tagged `Lnn`. Keep in sync per the doc-currency guard (L25): a lessons-log change → update the anti-pattern views AND this checklist.

Run this after any batch of doc changes, before declaring the playbook healthy. Two recurring doc-health guards: (1) **static-census** (unmarked static enumerations), (2) **this audit** (the 17 classes). Both are reports — read the files, ground every finding, do NOT fix in the audit pass.

## ISSUE-CLASSES (definition → check)
1. **Anchoring** (L1) — search confirms prompt/memory priors instead of discovering the field. Check discovery/search steps for "verify the named operators" framing.
2. **Non-exhaustive coverage** (L2, L7, L14) — any axis/lens/channel/region/language/archetype as a closed set, or a sweep allowed to stop early.
3. **Fixed-when-should-evolve** (L3, L14, L15, L18) — an enumeration that should be a registry/open-list but is a frozen prose list (no append/promote wire).
4. **Lost work / not persisted** (L4, L22) — a step reads/produces useful info but doesn't write it to a committed file.
5. **Relay loss** (L5) — a subagent returns findings through the orchestrator instead of writing its own file.
6. **Weak convergence** (L6, L7, L10) — a "done/converged" claim not gated on the quality bar + every gate-axis dry + zero dirty.
7. **Hardcoded literals** (L16-B) — a magic number/date/threshold/count inline instead of a `travel-config.md` named dial.
8. **Duplicated / drifting content** (L16-D, L20, L25, L26) — a rule/value/enum in ≥2 docs that could drift (should be single-sourced + cross-ref).
9. **Classification in prose** (L16-C, L18) — a status/format/tag/role narrated instead of a data tag in a registry.
10. **Stale / fragile cross-refs** (L16-E, L20, L26) — a bare doc-number, hardcoded path, or filename not resolved via `doc-manifest.md` slug/scheme.
11. **Memory reliance** (L15) — an input/state used from session memory rather than READ from a committed file.
12. **Missing provenance / source-of-truth** (L16, L20) — a registry/store without a promotion log or single home, or an entry without `Lnn`/country provenance.
13. **Not a fixed point** (L19, L26) — a step where an upstream promotion wouldn't invalidate/dirty the dependent units it consumed.
14. **Schema / contract rigidity** (L21) — an evolving data field with no versioning or backfill path.
15. **Intelligence leak** (L22, L24) — a step that reads live external pages but doesn't emit typed leads to `<country>/leads.md`.
16. **Form / consumer mismatch** (L13, L25) — a doc not in agent-spec form, or a contract change not propagated to its consumers in lockstep.
17. **Scale fit** (L23) — premature complexity, or a known scale ceiling (verbatim list, single-region assumption) not marked with an upgrade path.

## HOW TO RUN
For EACH class, scan EVERY document (`00`–`11`, README, all registries/config/manifest). Report per instance: `class # · file:line · the problem · proposed fix`. Dedupe into prioritized clusters. Ground every finding in actual file text (read it). Flag false positives. Report only — do not fix in this pass.

## KEEPING THIS UPDATED
- This checklist is a VIEW of `10-lessons-log.md`. When a new lesson generalizes to a recurring issue-class → append it here with its `Lnn`.
- Enforcement is the **rule above + the doc-currency guard (L25)**, not a tool — so it travels with the repo across agents/sessions. A git pre-commit check ("lessons-log changed → was this checklist updated?") is an optional belt-and-suspenders, not required.
