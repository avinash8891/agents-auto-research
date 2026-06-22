# Registry Protocol (shared by all registries)

One protocol for every evolving enumeration. The registries it governs are the CONFIG + REGISTRIES rows in `doc-manifest.md` (single source — do NOT re-list them here; that list already drifted once). Each registry states only its registry-specific evidence definition and points here for the mechanics — do NOT restate the protocol per file.

## INVARIANTS
- **Append-only.** Never remove an entry. If it stops paying off in a country, mark it inactive in that country's ledger (`<country>/axes.md` etc.), not here.
- **Global + per-country.** The registry is the global baseline + candidate watchlist; per-country deviations live in `travel/<country>/`.
- **Source of truth = the file, not memory** (memory invariant, `README` principle 9).

## STRUCTURE (every registry)
1. **BASELINE** — entries active for all countries.
2. **CANDIDATE WATCHLIST** — entries to test per country; promote on evidence.
3. **PROMOTION LOG** — append-only record of each candidate→baseline move, citing the justifying country + instance.

## PROMOTION BAR
- Default = `MIN_CREDENTIALED_PRODUCTS` (`travel-config.md`): the entry is backed by ≥ that many credentialed, dated current-season products (or the registry's own evidence definition where it differs — e.g. an axis must surface tours no existing axis finds; an archetype must recur across ≥2 countries with cited instances).

## UPDATE CYCLE (read → run → append → promote)
1. New country inherits BASELINE + WATCHLIST.
2. The consuming step runs a completeness diff (every baseline entry → used or justified `thin/none`).
3. New entry surfaced → APPEND to the per-country ledger.
4. Passes the promotion bar → PROMOTE to this registry's BASELINE with a PROMOTION LOG entry.

## TAGS
- Entries may carry multi-valued, growable tags (e.g. an axis `stage:[seed,discovery]`, `role:[axis-proof,convergence-gate]`). Consuming docs filter by tag — they never hardcode which entries apply where. New tag values may be added as the method grows.

## OPEN ENUMERATIONS (lists of discovered knowledge that live in-doc, not in a full registry)
Not every growable list earns its own registry file. A list of *discovered domain knowledge* — exclusion lists, off-cadence triggers, reshape actions, round types, metric caveats, overlap dimensions, special-interest sub-types, ANTI-PATTERNS — is an **OPEN ENUMERATION**: it stays in its owning doc but is explicitly append-on-discovery.
*(This catalogue of open-enumeration kinds is itself open — append a new growable list-type here when one surfaces; provenance `Lnn`.)*
Rules for an open enumeration:
1. Mark it `(open — append on discovery)` so it never reads as a closed set.
2. Each entry carries **provenance**: the lesson id (`Lnn`) or the country/run that surfaced it.
3. When a run/lesson surfaces a new entry → APPEND it to the list (compounding), same read→run→append cycle as a registry; the committed doc is the store.
4. **Promote to a full registry** only when the list earns cross-doc reuse (referenced by ≥2 docs) — then move it out and reference by name.
5. A genuinely closed set (intended not to grow) is `STATIC-OK` but still carries an escape hatch: "append if a new case emerges."

### Anti-patterns are a view of the lessons-log
ANTI-PATTERNS blocks are the failure-check projection of `10-lessons-log.md` (the append-only failure store). Each anti-pattern carries its `Lnn` provenance where one exists. Protocol: **a new lesson → append its check to the owning doc's ANTI-PATTERNS, tagged `Lnn`**; a lesson with no propagated check is a gap. The lessons-log is the source; the per-doc blocks are the view.

### Self-check
The static-census audit is the recurring guard: re-run it; any STATIC-SHOULD-EVOLVE construct lacking an open-tag, registry, or escape hatch is a regression.

## INVALIDATION (promotion → dirty-propagation)
The pipeline is a **fixed-point computation**, not a one-pass: a downstream promotion can invalidate upstream coverage. Whenever an entry is PROMOTED (a new axis/lens/archetype/channel), it **marks every dependent unit dirty** — units that were finalized before the new entry existed and were therefore covered on a smaller set:
- New **axis** promoted → every already-swept theme is `dirty` on that axis (it was never swept on it). Re-sweep **only that axis** for each theme (targeted, not a full re-discovery).
- New **lens/archetype** promoted → re-run the seed-completeness diff; it may spawn new themes (existing themes stay clean unless the lens overlaps).
Rules:
1. A unit cannot be declared converged/done while it carries a `dirty` flag (see `travel-config.md` DONE).
2. Re-processing is **scoped to the dirty axis/unit**, never a global restart.
3. **Termination is guaranteed** because state is append-only and the admission bar is a fixed filter over finite real-world supply — coverage is monotone toward a ceiling, so dirty-propagation cannot oscillate.
4. **Scope:** *within a country* this is a strict fixed point (re-sweep dirty themes before convergence). *Across countries* it is eventual-consistency — a promotion marks prior finished countries dirty, re-swept lazily on the DISCOVERY cadence (`freshness`), not immediately (cost trade-off).
