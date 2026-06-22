# 00 — Overview, Scope & Output Contract

AGENT SPEC. Defines the deliverable, the unit of work, ranking criteria, and the output contract. Read first — every other doc references these definitions.

SCOPE: top-N most-visited countries (N is a dial; current pilot = top 10; see `01`).
UNIT: a **theme** (single-lens regional experience), not a country.
DELIVERABLE: a ranked **Top-5 tours** list per theme, expert-led, depth-first, value-justified.

## DEFINITIONS
- **THEME** = one coherent subject, doable as a single trip **< 21 days**. May span multiple **eras** (Sicily: Greek→Roman→Arab-Norman→Baroque) or **regions** (Etruscan: Lazio+Tuscany+Umbria). MUST NOT bundle multiple lenses.
- **LENS** = the subject type: history · archaeology · art · architecture · design · science · food · wine · religion/pilgrimage · ethnic heritage · military · music · wildlife · geology · gardens · maritime · literary · crafts.
- **SINGLE-LENS rule**: different lenses → different themes (Sicily layered civilisations ≠ Etna food & wine).
- **TWO CONSUMPTION MODES**:
  - Group-tour ranking = single-lens (this method's unit; where expert depth lives).
  - Trip composition = multi-lens, built by combining ranked themes (`11`); whole-trip expert depth recovered only per-segment or via a bespoke designer (channel F).

## RANKING CRITERIA (priority order)
1. **Expert guide fit** — genuinely expert, theme-appropriate, highly regarded leader (real historian/Egyptologist/naturalist/food or religion specialist). NOT a figurehead or generic coach guide.
2. **Depth** within the theme — beneath the surface, not a checklist.
3. **Small / authentic / locally connected** — private-departure option a plus.
4. **Value for money (tie-break)** — price is not a barrier; the best wins even if pricier. But cost MUST be justified by depth/expertise; do NOT reward luxury for its own sake. Comparable excellence → better value ranks higher. Flag premium-for-thin-substance.

CONSTANT TEST: *"best for a first-time visitor to this theme/region — deep, expert-led, authentic, with cost justified by what it delivers."*

## OUTPUT CONTRACT (per country → per theme)
Emit, per theme:
- Theme **ID** (`06` convention, e.g. `IT-01`) + country (with arrivals rank + data-year) + theme/region + one-line capture statement.
- A ranked **Top 5** (5 is a **ceiling, not a quota** — list 3 if only 3 clear the bar; never pad). Each entry: operator · tour name · guide/expertise · group size · duration · price · value note · depth/access feature · source URL.
- **Price**: operator's listed currency (stated) + rough **USD-equivalent** (like-for-like comparison).
- **Format-class flag**: if the Top-5 mixes fixed-departure group / private-bespoke / day-format, flag it (`07`).
- One line: **why #1 wins**.
- **FLAG** any tour whose leader or current-season (2026–2027) departure is unverified — never guess.

## HARD RULES
- Never invent guides, dates, prices, claims. Unverified → say so.
- Prioritise current/next-season (2026–2027) departures; the season **rolls** — re-baseline on the `08` cadence.
- Weak theme → say so + give closest strong fits; do NOT pad.
- Cite sources; every factual claim traces to a live URL.

## ANTI-PATTERNS (failure checks)
- A theme bundles >1 lens to look comprehensive.
- Padding a Top-5 to five when fewer clear the bar.
- Any invented/guessed guide, date, or price.
- Rewarding price/luxury not justified by depth.
- Mixed currencies with no USD-equivalent.
- Selling a multi-lens itinerary as a single-expert group tour (that's `11` composition, with the trade-off stated).
