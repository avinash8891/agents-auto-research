# 10 — Lessons Log

Append-only. Every time the method improves, record what was wrong, why, and the fix. This is what lets a future run *improve upon* rather than repeat. Each lesson came from a real correction during the Italy build (2026-06).

## L1 — Discovery-first, not confirmation
**Wrong:** Built the first ranked list from the three operators named in the prompt + recall, then "verified" them. That's confirmation, not research.
**Why it failed:** Anchoring. The famous operators get found; the field doesn't.
**Fix:** Always discover the whole field *before* filtering/ranking. Discovery is blind to priors.

## L2 — A handful of searches is not exhaustive
**Wrong:** ~4–6 searches per theme.
**Why:** Search surfaces the popular and hides the long tail; exhaustiveness can't come from search volume alone.
**Fix:** Enumerate provider **channels** (8) and search each — coverage by construction, not by luck. (Later generalised to the 5-axis matrix, L7.)

## L3 — Themes are not fixed up front
**Wrong:** Treated the seed theme list as the structure.
**Why:** The real structure is whatever operators actually sell as a trip.
**Fix:** Discovery reshapes themes in a loop — split/merge/add/demote — until stable. Seed is provisional (`02`).

## L4 — Persist what subagents gather
**Wrong:** Kept only compressed summaries in the orchestrator; raw inventories lived only in context.
**Why:** Next session starts cold; relayed summaries lose detail.
**Fix:** Save the raw corpus verbatim (`06`).

## L5 — Subagents write their own files
**Wrong:** Agents returned findings through the orchestrator, which then saved them.
**Why:** Bloats orchestrator context (won't scale to 50 countries) and loses fidelity in relay.
**Fix:** Each agent writes its raw findings directly to its own corpus file and returns only a verdict (`06`, `09`).

## L6 — Loop until dry, but with a quality bar
**Wrong (two ways):** Stopping after a fixed number of rounds; OR chasing literal "zero new" forever.
**Why:** Fixed rounds miss the tail; literal-zero pads thin themes (violates depth-over-breadth).
**Fix:** Admission bar — a new theme needs ≥2 credentialed dated expert-led products, non-overlapping, first-trip-representative. Converged when a fresh critic admits 0 clearing the bar (`05`).

## L7 — False convergence: the missing axes (the big one)
**Wrong:** Declared "converged" after 4 rounds — all searched in **English**, largely on **recalled operator names**.
**Why:** Convergence was axis-limited. A 5th sweep on two unsearched axes — **native-language** (IT/DE/FR) and **authority-index** (awards, AITO/Virtuoso, university-alumni & museum travel partners) — found **26 operators** the four prior rounds missed (Intermèdes, Clio, Gebeco, Arrangements Abroad/Met curators, Distant Horizons, …). Several could plausibly be a theme's #1 — fatal for a "best tour" ranking.
**Fix:** The 5-axis coverage matrix (`03`); convergence valid only when EVERY axis is dry, language and authority-index included (`05`). Use training knowledge to build the axes; use the web to populate/verify.

## L8 — Freshness is part of the method, not an afterthought
**Wrong:** Treated the corpus as a static deliverable.
**Why:** Dates/prices/departures churn; "correct in June" is wrong by August.
**Fix:** Two refresh loops (cheap monthly VERIFY by re-fetching known URLs + diffing; expensive quarterly/on-trigger DISCOVERY), row stamping, and a scheduled cron for the VERIFY pass (`08`).

## Meta-lesson
The **process** was the real first deliverable. It matured step-by-step from user corrections (each lesson above maps to one). Output (ranked Top-5s) comes *after* the method is right, because errors in the method multiply 50×. Get the method right on one country, then scale.

## Open / next
- Produce the first ranked Top-5 (Italy IT-01 Rome antiquity) as the output template, then scale to all 35 Italy themes, then countries #2–50.
- Italy operator corpus still needs LANGUAGE + AUTHORITY axes saturated per theme before ranking (round 5 opened them; not yet exhausted).
- Consider a structured (machine-diffable) corpus format to make the VERIFY pass fully mechanical.
