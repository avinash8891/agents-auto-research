# Registry Protocol (shared by all registries)

One protocol for every evolving enumeration: `axes-registry.md`, `lens-registry.md`, `theme-archetypes.md`, `channel-registry.md`, `sources-registry.md`, `operator-aliases.md`. Each registry states only its registry-specific evidence definition and points here for the mechanics — do NOT restate the protocol per file.

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
