# 06 — Corpus & Persistence

## Why
The corpus is the durable knowledge base. It is the seed for deeper rounds, the input to ranking, and the object the freshness loop refreshes. If findings live only in the orchestrator's context, the next session starts cold and relayed summaries lose detail. Persist verbatim.

## Where (per country)
```
<country>_theme_map_v0.md      ← seed (audit trail)
<country>_theme_map_v<N>.md    ← reshaped after each round (decisions)
<country>_theme_map_FINAL.md   ← converged structure
corpus/round<N>_<cluster>.md   ← raw inventories, written BY the subagents
rankings/<theme-id>.md         ← ranked Top-5 per theme (step 07)
```
(During method development these lived under `.context/`; for delivery, keep them in the repo `travel/` tree or a per-country subfolder.)

## Subagents write their own files (the key pattern)
Each discovery/verification agent uses its file-write tool to save raw findings **directly** to its corpus file, and returns to the orchestrator only a 2-line verdict + the file path. Benefits: orchestrator context stays lean (scales to 50 countries), the save is verbatim (no relay loss), and parallel agents never collide (one file each). See `09`.

## Row schema (every operator row)
`Operator | Channel | Tour name | Expert (named + credential) | Format (day/multi-day) | current-season departure? | price | group size | URL | last_checked: YYYY-MM-DD | status: verified | UNVERIFIED | stale`

- **status=verified**: named guide + dated departure + price confirmed from a live page.
- **status=UNVERIFIED**: operator/tour real, but a named per-departure guide and/or date not yet confirmed (e.g. annual catalogue, or a page that 403'd).
- **status=stale**: `last_checked` older than the refresh window (`08`).

## Carry the verification debt
The corpus must keep the list of UNVERIFIED rows and any pages that blocked fetching (some operator sites 403 direct fetches — e.g. Peter Sommer, Smithsonian in the Italy run). These are the priority queue for the verification phase and the freshness pass — never let them silently drop.

## Append, don't overwrite
Each discovery round appends a new `round<N>` file. Theme-map versions are kept (v0, v1, … FINAL) as an audit trail of how the structure evolved. This is what lets a future run *improve upon* rather than repeat.
