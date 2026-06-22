# 05 — Convergence & the Admission Bar

AGENT SPEC. Decide, deterministically, whether a candidate subject is admitted as a THEME, demoted to a sub-tag/thin-note, and whether a country's research run is DONE. "Loop until no new information" is infinite (someone always sells one obscure single-operator tour); convergence is a **quality bar**, not a zero-count. Chasing literal zero pads the map with thin themes — violates depth-over-breadth and the "don't pad" rules.

INPUT:
- `<country>_corpus.md` — per-theme operator/product rows with axis dry-status (from the discovery-loop step).
- `<country>_theme_map_*.md` — current theme map (from theme-seeding / discovery-loop).
- Global registries (read for names/definitions; do NOT operate from memory): `axes-registry.md` (the baseline axes and their `stage`/`role` tags — axis count is DERIVED there), `theme-archetypes.md`, `lens-registry.md` (the sole lens vocabulary).
- `<country>_ledger.md` — per-country lessons/thin-notes/re-test triggers (append target).

OUTPUT: `<country>_theme_map_FINAL.md` containing a **convergence tracker** (per round: themes added/folded; per axis: dry status) + an explicit two-level `CONVERGED` statement citing (a) the completeness-critic's empty result AND (b) the axis-proof sweep — every axis tagged `role:axis-proof` in `axes-registry.md` shown dry. Thin-notes + re-test triggers APPENDED to `<country>_ledger.md`; any new operative rule PROMOTED to the owning registry per `REGISTRY-PROTOCOL.md`.

NEXT: the corpus step (audit trail) and the ranking step (consumes admission/count status; UNVERIFIED rows are admitted but not ranked) consume this file. Resolve slugs via `doc-manifest.md`.

MEMORY INVARIANT: nothing here lives in session memory. Admission counts, per-axis dry status, thin-notes, and re-test triggers are READ from `<country>_corpus.md`/`<country>_ledger.md` and WRITTEN back to `<country>_theme_map_FINAL.md`/`<country>_ledger.md`. A fresh session reproduces the same CONVERGED verdict from files alone. Convergence must be **auditable, not asserted** — if it is not in the corpus's per-axis dry tracker, it did not happen.

COMPOUNDING: thin-notes carry a **re-test trigger** (read → run → APPEND to `<country>_ledger.md` → re-evaluate next session when the operator publishes a dated/named departure). A new admission/count rule discovered for one country is PROMOTED to the owning registry (global registry + per-country ledger pattern; mechanics in `REGISTRY-PROTOCOL.md`) so future countries inherit it.

## PROCEDURE (start = a candidate subject + its corpus rows)
1. READ `<country>_corpus.md` rows for the candidate, `axes-registry.md` (the baseline axes), and `<country>_ledger.md` (existing thin-notes/re-test triggers).
2. **Score each product** toward `ADMISSION_BAR` (see DECISION RULES: `FULL_PRODUCT_WEIGHT` / `PARTIAL_PRODUCT_WEIGHT` / annual-catalogue exception). Apply the inclusion/exclusion definitions (qualifying product / operator / expert) before scoring — a non-qualifying row scores 0.
3. **Sum the score.** If below `ADMISSION_BAR` after applying the rules → the subject does NOT clear the bar. Record a THIN-NOTE with a re-test trigger in `<country>_ledger.md`; do NOT pad it into a theme. Stop for this candidate.
4. If the sum reaches `ADMISSION_BAR`, apply the remaining admission tests: **non-overlapping** (not a sub-fold of an existing theme) and **first-trip-representative** (iconic + deep, not hyper-niche). Fail either → fold/demote to a sub-tag under the existing theme.
5. If admitting a sub-tag → theme (promotion), apply the promotion test owned by the discovery-loop step (cross-ref via `doc-manifest.md`): a **standalone multi-day spine** AND a **distinct buyer + supplier base** (bare non-overlap is insufficient).
6. For each admitted theme, **fold operator-saturation into ranking**: run the baseline-axes check scoped to that theme and record per-axis dry/not-dry in `<country>_corpus.md`. Do not defer to a separate giant final sweep — discovery and ranking merge.
7. **False-convergence check (mandatory):** before declaring the country DONE, confirm every axis tagged `role:axis-proof` in `axes-registry.md` has been run as its own dedicated sweep and returned dry. Theme convergence inside one axis (e.g. an English-only / operator-keyword sweep) is NOT global convergence.
8. **Two-level convergence test:** declare DONE only when BOTH `THEME_CONVERGED` and `OPERATOR_CONVERGED` hold (see DECISION RULES). Run a fresh adversarial completeness-critic for the THEME level.
9. WRITE the convergence tracker + the two-level `CONVERGED` statement to `<country>_theme_map_FINAL.md`. APPEND thin-notes/re-test triggers to `<country>_ledger.md`. PROMOTE any newly discovered rule to the owning registry per `REGISTRY-PROTOCOL.md`.

## DECISION RULES
- ADMIT a new THEME IFF ALL THREE hold: (1) score reaches `ADMISSION_BAR` in credentialed, dated `CURRENT_SEASON` expert-led products — a named scholar/guide (not a figurehead), a real departure (not "ongoing"); (2) non-overlapping (not a sub-fold of an existing theme); (3) first-trip-representative (iconic + deep for the region, not hyper-niche).
- COUNT a product as `FULL_PRODUCT_WEIGHT` IFF it has BOTH a named credentialed guide AND a confirmed `CURRENT_SEASON` dated departure, simultaneously.
- COUNT a product as `PARTIAL_PRODUCT_WEIGHT` IFF it has the right structure but an UNVERIFIED date OR an unnamed guide.
- A score of one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT` (i.e. below `ADMISSION_BAR`) FAILS the bar → record a THIN-NOTE (not a theme) with a re-test trigger (re-evaluate if the operator later publishes a dated/named departure). Failing-the-bar items become **sub-tags** under an existing theme or are folded/demoted.
- ANNUAL-CATALOGUE EXCEPTION: an operator that demonstrably runs the trip *every year* but has not yet published the specific `CURRENT_SEASON` date counts as a **full product for THEME admission** (the theme clearly exists), but the row stays **UNVERIFIED for RANKING** (the ranking step) until a dated departure is confirmed. Note the basis explicitly ("annual catalogue, date pending"). Resolves the apparent conflict between "dated `CURRENT_SEASON`" and admitting annual-catalogue houses.
- QUALIFYING PRODUCT IFF it is a sold leisure tour. NOT a university course / master-class / lecture-residency / retreat / maker-workshop / day-activity / pilgrimage-without-study-content.
- QUALIFYING OPERATOR IFF it is a tour operator, NOT an aggregator/marketplace reselling other operators' trips (count the underlying operator, not the platform). Collapse absorbed sub-brands into the parent operator per `operator-aliases.md` to avoid double-counting.
- QUALIFYING EXPERT IFF a NAMED, credentialed scholar/specialist whose expertise fits the theme. NOT a title-only listing ("a Professor of X, name not published"), NOT a generic licensed city guide, NOT an artisan/trifolao — unless the theme is the craft itself.
- PROMOTE sub-tag → theme IFF non-overlapping AND has a standalone multi-day spine AND has a distinct buyer + supplier base (test owned by the discovery-loop step; resolve via `doc-manifest.md`).
- COUNTRY IS DONE IFF BOTH: (a) `THEME_CONVERGED` — a fresh adversarial completeness-critic admits 0 themes clearing `ADMISSION_BAR`; AND (b) `OPERATOR_CONVERGED` — every BASELINE axis (`axes-registry.md`, count derived) returns dry for that theme (no new credentialed operator).
- If declaring convergence → every axis tagged `role:axis-proof` AND every axis tagged `role:convergence-gate` in `axes-registry.md` must show dry in the per-axis tracker. Missing any = NOT converged.

## EXAMPLE (Italy worked example)
- **Molise Samnite** and **Cremona violins**: each scored one `FULL_PRODUCT_WEIGHT` + one `PARTIAL_PRODUCT_WEIGHT` (below `ADMISSION_BAR`) → FAILED the bar → recorded as THIN-NOTES with re-test triggers, not themes. Cremona violins became a sub-tag under opera. (Full Italy roster: per-country files.)
- **Annual-catalogue houses** (Intermèdes / Clio / Arts et Vie): admitted as full products for theme admission ("annual catalogue, date pending"), held UNVERIFIED for the ranking step.
- **False-convergence trap fired:** the Italy run declared convergence at round 4 — wrong. Round 5 ran the axes tagged `role:axis-proof` (per the Italy promotion log in `axes-registry.md`, Language + Authority-index) and added 26 operators. Verdict: always run every `role:axis-proof` and `role:convergence-gate` axis to dry before declaring DONE.

## ANTI-PATTERNS (checks — fail the step if true)
- Declaring convergence from a single axis (an English-only / operator-keyword sweep) — the false-convergence trap; axes tagged `role:axis-proof` not proven dry.
- Counting an unnamed-guide or undated product as a full (`FULL_PRODUCT_WEIGHT`) product toward `ADMISSION_BAR`.
- Padding a theme that fails the bar instead of recording a THIN-NOTE (+ re-test trigger).
- Asserting "converged" without the auditable per-axis dry tracker in the corpus.
- Counting an aggregator/platform instead of the underlying operator, or double-counting an absorbed sub-brand against its parent (apply `operator-aliases.md`).
- Admitting on bare non-overlap without a standalone multi-day spine + distinct buyer/supplier base.
- Running a separate giant final operator sweep instead of folding the baseline-axes check into ranking per theme.
- Failing to APPEND a thin-note's re-test trigger to `<country>_ledger.md`, or not PROMOTING a new rule to the owning registry per `REGISTRY-PROTOCOL.md` (no compounding).
