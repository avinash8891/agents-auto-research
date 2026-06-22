# Travel Playbook — 17-Issue-Class Audit (report only, no fixes)

Scope: all `travel/*.md` (step docs 00–11, README, AUDIT-CHECKLIST, and every registry/config/manifest). Method: one finder per issue-class scanned all 24 docs; each candidate was re-read at its cited location and adversarially verified (false-positives killed). Classes 11/12/14 verifiers hit socket errors mid-run and were adjudicated by hand against the file text.

**Verdict counts:** 37 real instances · 1 borderline · 12 flagged false-positives. Clean classes (zero real): **1 Anchoring, 2 Non-exhaustive coverage, 5 Relay loss, 17 Scale fit.**

Every quote below is verbatim from the current files (HEAD `91bdd86`).

---

## PER-INSTANCE FINDINGS (real)

### Class 6 — Weak convergence
- **6 · README.md:16** — Principle 6 defines convergence inline as *"Convergence is 'all convergence-gate axes dry,' never a frozen axis count"* and the stop rule gates on only critic-bar + convergence-gate-axes-dry. **Omits the third leg (zero `dirty` units)** required by `OPERATOR_CONVERGED`/DONE (travel-config.md:33-34), REGISTRY-PROTOCOL.md:69, 05:40-41. An agent treating this headline principle as the definition declares convergence with un-re-swept dirty units → the L19/Italy-R4 trap. **Fix:** add the no-dirty leg, or replace the inline gloss with a reference to the DONE dial.
- **6 · README.md:67 (run-order step 4)** — *"Operator-converged = `OPERATOR_CONVERGED`: every axis tagged `role:convergence-gate` returns dry."* Names the dial but restates a **narrower** definition than the dial carries (drops the no-dirty leg), and the run order has no step requiring zero dirty before step 5 stamps corpus. Following the loop literally reproduces the false-convergence trap. **Fix:** make the gloss match the dial and add an explicit DONE/zero-dirty gate before stamping.

### Class 13 — Not a fixed point (channel-promotion hole)
- **13 · REGISTRY-PROTOCOL.md:63-67** — INVALIDATION header says a promotion of *"a new axis/lens/archetype/**channel**"* dirties dependents, but the enumerated re-sweep rules cover only **`New axis`** and **`New lens/archetype`** — there is **no `channel` case**. A new channel sub-type (e.g. `media-creator`, or a new `special-interest` vertical) adds no top-level axis (channel already IS a baseline axis) and is not a lens/archetype, so neither rule fires — yet discovery sweeps per channel sub-type (04:26, 09:44), so prior themes were genuinely under-swept and never marked dirty. **Fix:** add a `New channel (sub-type) promoted` case — dirty every swept theme on the CHANNEL axis restricted to the new sub-type id, re-sweep only that sub-type.
- **13 · 04-discovery-loop.md:31** — the INVALIDATION wire fires only *"on the new axis"*; step 7/OUTPUT promote `lens/archetype/channel/axis`, so a promoted channel sub-type leaves rounds 1–4 themes stale with no dirty flag. Mirrors the root gap. **Fix:** generalize the sentence to dirty+re-sweep the new channel sub-type (and re-run seed-diff for a new lens/archetype).
- **13 · 09-agent-orchestration.md:20** — the Dirty-unit re-sweep DISPATCH PATTERN enumerates only `new baseline axis` and `new lens/archetype`; **no channel sub-type dispatch**. Step 12 (line 57) names channel and points here, but the concrete executor is undefined. **Fix:** add the channel-sub-type dispatch case, driven off the REGISTRY-PROTOCOL root fix.

### Class 16 — Form / consumer mismatch
- **16 · 05-convergence-and-admission-bar.md:6** — INPUT names corpus_FINAL.md *"(from the discovery-loop step)"*, but the producer per doc-manifest.md:52 and 08:13-14 is **freshness consolidation**, run *"once before the first VERIFY"* — i.e. **after** convergence. Discovery-loop (04 OUTPUT) emits only round files + theme_map_v\<N\>, never corpus_FINAL. So 05 reads/writes (05:6/20/25) a file that, per the lifecycle, does not exist yet. Real producer + ordering contradiction across 04/05/08/manifest. **Fix:** either point 05 at the per-round corpus files, or move consolidation before convergence and reconcile manifest:52 + 08 ordering — single-source the producer.
- **16 · 00-overview-and-principles.md** — `overview` is a STEP doc (manifest:8, in the 00–11 range) but has **no `## PROCEDURE` and no `## DECISION RULES`** headers (its steps/conditions live under DEFINITIONS / THEME DESIGN RULES / RANKING CRITERIA / HARD RULES). Siblings carry both (04:23/34, 05:19/30); no exemption is declared. **Fix:** add the two canonical sections, OR declare 00 an exempt contract doc in manifest/README so the form audit doesn't flag it.

### Class 8 — Duplicated / drifting content
- **8 · 05-convergence-and-admission-bar.md:39** and **05:24** — both restate the sub-tag→theme promotion-test conditions (*"standalone multi-day spine AND distinct buyer + supplier base"*) although 04:54 explicitly says *"This promotion test is owned here; other docs cross-ref it rather than restating it."* Two restatements in 05 directly contradict the single-source claim. **Fix:** reduce both to a bare cross-ref to `discovery-loop`; drop the restated conditions.
- **8 · 06-corpus-and-persistence.md:82** — restates `THEME_ID_GRAMMAR` literal (*"2-letter country code + sequential number"*) owned by travel-config.md:28. **Fix:** reference the dial name; don't restate the format.
- **8 · 06-corpus-and-persistence.md:84** and **02-theme-seeding.md:35** — the SPLIT-suffix literal (`IT-05a`/`IT-05b`) lives in **three** places (config:28 + 06:84 + 02:35). **Fix:** own it in `THEME_ID_GRAMMAR`; cross-ref from 06/02.
- **8 · 06-corpus-and-persistence.md:86** — paraphrases `THEME_ID_OVERFLOW` content (*"exceeding the two-digit range widens digits"*); vocabulary already differs from config:29's *"99 themes"* — the drift is live. **Fix:** cite the dial only; drop the paraphrase.
- **8 · 09-agent-orchestration.md:29** — re-lists the three de-dup guards inline (aggregators / sub-brands / prior-captured) though it cross-refs `corpus` (06:88-92 is the home). **Fix:** keep the cross-ref, drop the inline re-listing. *(06:88-92 is NOT the bug — it's the home; see false-positives.)*
- **8 · 08-freshness-and-updates.md:44** — re-encodes the admission-bar credited-weight downgrade rule (UNVERIFIED-date product → `PARTIAL_PRODUCT_WEIGHT`) owned by 05:33. 08 legitimately owns the season-roll *effect*; restating the scoring *mapping* couples it to admission-bar. **Fix:** state only the season-roll effect; reference the weight rule.

### Class 9 — Classification in prose
- **9 · 08-freshness-and-updates.md:21** — *"Set `status` ∈ {verified, stale, withdrawn}"* narrates the set inline AND **drifts from the registry**: tags-registry.md row.status = {verified, UNVERIFIED, stale}; `UNVERIFIED` dropped, `withdrawn` invented (it's a diff *dimension* at 08:20, not a status value). **Fix:** *"Set `status` per `tags-registry.md` row.status"*; if `withdrawn` is needed, add it to the registry with a PROMOTION LOG entry first.
- **9 · 08-freshness-and-updates.md:70** — worked example assigns `status: withdrawn`, an off-registry status value. **Fix:** use only registry values; promote `withdrawn` first if genuinely needed.
- **9 · 06-corpus-and-persistence.md:34** — re-lists all four `format-class` values inline though it cites tags-registry.md (whose header names the exact format-class drift it exists to stop). The same doc shows the correct pattern at 06:54 (reference by name, no list). **Fix:** reference by name; drop the inline list.
- **9 · 06-corpus-and-persistence.md:36** — re-lists row.status values (`verified`/`UNVERIFIED`/`stale`) inline (matches registry today, but matching-today is the silent-drift risk). **Fix:** reference `tags-registry.md` row.status by name only.
- **9 · 02-theme-seeding.md:22** — re-lists theme.strength values (`Strong`/`Medium`/`Thin`) inline though it cites the registry. **Fix:** reference the named tag set without inlining values.

### Class 10 — Stale / fragile cross-refs
Bare doc-numbers (do not resolve via the manifest's slug→file table; break on renumber):
- **10 · axes-registry.md:11** — `02` and `04` → should be slugs `theme-seeding`, `discovery-loop`.
- **10 · axes-registry.md:12** — `07` → `ranking`.
- **10 · axes-registry.md:24** — `02` → `theme-seeding`. *(finder mis-located this to channel-registry; actual line is axes-registry:24.)*
- **10 · sources-registry.md:3** — `01` → `country-ranking`.
- **10 · theme-archetypes.md:3** — `02` → `theme-seeding`.
- **10 · operator-aliases.md:3** — `09` → `orchestration`.
- **10 · AUDIT-CHECKLIST.md:27** — operative HOW-TO-RUN uses bare range `00–11`; closed/fragile to a doc 12. **Fix:** *"every step doc (the STEP DOCS table in `doc-manifest.md`)…"*.

Stale / broken paths (higher severity — these resolve to wrong/retired files):
- **10 · 11-trip-composition.md:49** — example path `compositions/italy-sicily-history-food.md` **drops the `<country>/` prefix** and folds country into the label; canonical is `<country>/compositions/<label>.md` (this doc's own OUTPUT:11, step 9:35). Not covered by NORMALIZATION (which only equates `<country>_X` ≡ `<country>/X`). **Fix:** `italy/compositions/sicily-history-food.md`.
- **10 · 08-freshness-and-updates.md:71** — writes changelog to `italy/italy_changelog.md`, the **retired `_changelog`** filename; manifest:50 says `<country>/ledger.md` *"Replaces … `_changelog`"*, and this doc's own OUTPUT:6 / step 5:33 use ledger.md. **Fix:** `italy/ledger.md`.

### Class 3 — Fixed-when-should-evolve
- **3 · REGISTRY-PROTOCOL.md:48-60** — the **Lead-types → destination routing table** is the single home of a demonstrably-growing taxonomy (L26 added the `composition-pattern` row), yet its heading carries **no `(open — append on discovery)` tag**, rows carry **no Lnn/country provenance**, and "lead types" is **absent from the OPEN ENUMERATIONS catalogue (line 28)** — inside the very file that defines the open-enumeration protocol. **Fix:** tag the heading open, give rows provenance (base = L22, composition-pattern = L26), add "lead types" to the line-28 catalogue.

### Class 4 — Lost work / not persisted (dual class 15)
- **4/15 · 01-country-ranking.md:23** — leads-capture step routes a new source straight to a *"sources-registry candidate"* and **never names the committed capture file `<country>/leads.md`** — unlike every peer (04:5a, 07:9a, 08:6) which writes the typed row to leads.md first. The surfaced source intelligence has no declared written home. **Fix:** *"APPEND a typed authority/source lead to `<country>/leads.md` with provenance, then route to a `sources-registry` candidate."*

### Class 11 — Memory reliance *(hand-adjudicated; LOW, partly philosophical)*
- **11 · 02:15 + 03:26 + 03:47 (one root)** — the **REGION axis** is the one per-country axis whose values are enumerated from geographic recall (*"list every first-level admin region as a checklist"*) with **no committed home**, whereas LANGUAGE's per-country set is *"data in `<country>/axes.md`"* (03:48) and AUTHORITY-INDEX mines `sources-registry.md` (03:28). This contradicts the docs' own MEMORY INVARIANT (02:11, 03:15) that *"a fresh session reproduces the same matrix from the files alone."* **Mitigation (why LOW):** admin regions are near-deterministic public facts derivable from the country name (unlike lenses/operators), so practical reproducibility risk is small. **Fix:** treat region like language — read/append the checklist from `<country>/axes.md`.

### Class 12 — Missing provenance *(hand-adjudicated)*
- **12 · tags-registry.md:37** — the `non-qualifying operator` disqualifier entry **lacks the inline `(provenance: …)`** its two siblings (lines 35-36, both `Italy R4`) carry, violating OPEN-ENUMERATIONS rule 2 and the section's own *"Append a case … with provenance."* (Provenance does exist in the PROMOTION LOG line 41.) **Fix:** add `(provenance: Italy R4)` to the entry.
- **12 · lens-registry.md:24-25** — PROMOTION LOG has **no `(seed)` baseline entry**; the 20 baseline lenses have no logged origin, unlike channel/sources/archetypes/tags/operator-aliases registries which all log `(seed) Baseline … established`. **Extension (I add):** **axes-registry.md:37-38** has the **same gap** — no `(seed)` line for the baseline channel/lens/region axes. **Fix:** add a `(seed) Baseline … established` line to both logs.

### Class 14 — Schema / contract rigidity *(hand-adjudicated)*
- **14 · 01-country-ranking.md:6** — `country_ranking.md` is a persisted, additively-merged (never-removed) table with **no versioned contract / backfill**, and the drift is **already concrete**: the declared 8-column header omits `last_checked`, which step 8 (line 22) writes as a 9th column. **Fix:** give it the 06 SCHEMA-EVOLUTION treatment — `schema-version` stamp, reconcile the header with the columns actually written, state a backfill rule for older rows, cross-ref `corpus`.
- **14 · 07-verification-and-ranking.md:64-69** — the `rankings/<theme-id>.md` output schema is a typed contract a hard consumer (`composition`, 11) reads by field, but it carries **no `schema-version`, no lockstep-consumer rule, no backfill**, unlike the corpus row schema (06). (It's also duplicated against the 00 OUTPUT CONTRACT, 00:42-48 — a class-8 risk too.) **Mitigation:** rankings are regenerable from corpus, so a `dirty` rebuild covers backfill — but the lockstep-consumer + version discipline is still undeclared. **Fix:** declare it a versioned contract cross-ref'ing 06; backfill = re-emit/dirty-rebuild on bump.

### Class 7 — Hardcoded literals *(borderline)*
- **7 · channel-registry.md:29** — PROMOTION LOG seed entry says *"Baseline **8** established"* while line 3 of the same file bans *"never assert '8 channels' in prose."* **Borderline:** it's a one-off historical `(seed)` fact co-determined by "the A–H taxonomy" (A–H = 8), so it won't actually drift on a future promotion (that's a new log line). But the file's own rule arguably catches the phrasing. **Fix (trivial):** drop the bare count — *"Baseline established from the original A–H taxonomy."*

---

## PRIORITIZED CLUSTERS

**P0 — Method-correctness (false-convergence + lifecycle)**
1. **Convergence under-gated in README** (6 · README:16, :67) — headline principle and run-order both drop the zero-dirty leg. Highest blast radius: README is the entry point a fresh agent reads.
2. **Channel-promotion fixed-point hole** (13 · REGISTRY-PROTOCOL:63-67 root → 04:31, 09:20) — channel sub-type promotions never dirty-propagate; prior themes ship under-swept. Fix the root, propagate to 04+09.
3. **corpus_FINAL producer/lifecycle break** (16 · 05:6 vs manifest:52 / 08:13-14) — convergence consumes a file freshness produces afterward; wrong producer attribution. Single-source the producer + ordering.

**P1 — Single-sourcing drift (live or imminent)**
4. **Promotion-test restated despite single-source claim** (8 · 05:24, 05:39 vs 04:54).
5. **Status vocabulary drift — invents `withdrawn`** (9 · 08:21, 08:70) — the only class-9 with *actual* drift (off-registry value). Higher than the matches-today re-lists.
6. **THEME_ID grammar/overflow/split restated** (8 · 06:82/84/86, 02:35) — overflow paraphrase already diverged.
7. **Inline value-set re-lists** (9 · 06:34/36, 02:22) + **de-dup guards re-listed** (8 · 09:29) + **season-roll re-encodes weight rule** (8 · 08:44).

**P2 — Cross-ref + schema hygiene**
8. **Stale/broken paths** (10 · 11:49 missing `<country>/`; 08:71 retired `_changelog`) — these resolve wrong; fix before the bare-number nits.
9. **Bare doc-numbers in registries** (10 · axes:11/12/24, sources:3, theme-archetypes:3, operator-aliases:3, AUDIT-CHECKLIST:27) — renumber-fragile; registries lag the slug convention.
10. **Schemas not versioned contracts** (14 · 01:6 country_ranking — has concrete drift; 07:64-69 rankings).

**P3 — Provenance / form / consistency (low, cheap)**
11. **Lead-types table not open-tagged + no provenance** (3 · REGISTRY-PROTOCOL:48-60) — protocol file violating its own protocol.
12. **Missing provenance entries** (12 · tags-registry:37; lens-registry & axes-registry seed-log gap).
13. **00 lacks PROCEDURE/DECISION RULES headers** (16 · 00) — or declare it exempt.
14. **REGION axis lacks committed home** (11 · 02:15, 03:26/47) — consistency fix; near-deterministic so low risk.
15. **`Baseline 8` literal** (7 · channel-registry:29) — borderline; trivial.

---

## FLAGGED FALSE-POSITIVES (do NOT act)
- **4 · 01:5-6 (OUTPUT line)** — leads.md belongs in the PROCEDURE step, never the OUTPUT contract in any peer; the real omission is the procedure step (counted once, above).
- **8 · 06:88-92** — this IS the de-dup-guards home (corpus owns the axis-proof file schema); the duplicate is 09:29.
- **10 · lens-registry:5 ("discovery round 2")** — historical illustration of why the registry exists, not a doc/path cross-ref.
- **10 · tags-registry:3 (`corpus`,`ranking`)** — correct manifest slugs (finder included for contrast).
- **10 · 09:54 (`L7`,`lessons`)** — correct stable lesson-id + slug.
- **10 · 09:80 (`italy/theme_map_*`)** — canonical `<country>/X` form; finder inverted canonical vs shorthand.
- **10 · 08:69 (`italy/italy_corpus_FINAL.md`)** — sanctioned `<country>_X` shorthand (NORMALIZATION), same live file.
- **13 · 01:53-54 (re-rank)** — a new `tier=primary` source DOES trigger a full additive re-rank via freshness DECISION RULE 08:58; not a fixed-point hole.
- **15 · 03:30** — 03 *defines* the matrix; live execution + lead emission is owned by 04/07/09; L24 explicitly classifies 02/03/06/09 as non-live-readers. Overturning it would introduce a bug.
- **14 · REGISTRY-PROTOCOL:47 (leads row schema)** — leads.md is a deliberately-open append sink (L22); its lead-TYPE vocabulary is correctly open. Forcing a versioned-contract + sentinel backfill on the row schema is the premature complexity class 17 warns against.

---

## NOTES
- Clean classes (zero real instances): **1 Anchoring, 2 Non-exhaustive coverage, 5 Relay loss, 17 Scale fit** — the discovery/exhaustiveness/relay/scale machinery is internally consistent.
- A recurring meta-pattern: the **reference docs (registries) lag the slug + open-tag + seed-provenance conventions** that the step docs already follow (classes 3, 10, 12). A single pass over the 7 registries closes most of P3 and half of P2.
- Workflow: 30 agents, ~27 min, ~2.9M subagent tokens. 3 verifiers (11/12/14) socket-failed; their finders' output was recovered from the transcript and adjudicated by hand.
