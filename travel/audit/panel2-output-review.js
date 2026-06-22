export const meta = {
  name: 'playbook-fresh-review-2',
  description: 'Second fresh-eyes panel: output quality, anti-hallucination, global/cultural bias, adversarial marketing, cost-at-scale, robustness',
  phases: [{ title: 'Review' }, { title: 'Synthesize' }],
}

const ROOT = '/Users/avinashvankadaru/conductor/workspaces/agents-auto-research/tokyo-v1/travel'

const FILESET = `Read the WHOLE playbook under ${ROOT}/ : step docs 00-overview, 01-country-ranking, 02-theme-seeding, 03-coverage-matrix, 04-discovery-loop, 05-convergence-and-admission-bar, 06-corpus-and-persistence, 07-verification-and-ranking, 08-freshness-and-updates, 09-agent-orchestration, 11-trip-composition; README; foundation travel-config.md, REGISTRY-PROTOCOL.md, doc-manifest.md, axes-registry.md, channel-registry.md, lens-registry.md, theme-archetypes.md, sources-registry.md, operator-aliases.md, tags-registry.md, AUDIT-CHECKLIST.md. Read actual file text — do not assume.`

const CONTEXT = `CONTEXT: this playbook builds ranked Top-5 expert-led tour lists per regional theme for the 50 (pilot 10) most-visited countries — a GLOBAL deliverable (France, Italy, but also Mexico, Turkey, Japan, Thailand, Egypt, India, Kenya...). Core promises: genuinely expert-led, deep, authentic, value-justified, and NEVER invented (verified against live sources). The owner wants REDO/SIMPLIFY recommendations where something is wrong, not patches.`

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['blocker','major','minor'] },
      location: { type: 'string' }, issue: { type: 'string' },
      action: { type: 'string', enum: ['redo','simplify','fix','keep-as-is-but-note'] },
      recommendation: { type: 'string' },
    }, required: ['severity','location','issue','action','recommendation'] } },
    lens_verdict: { type: 'string' },
  },
  required: ['lens','findings','lens_verdict'],
}

const LENSES = [
  { key: 'output-quality-selection-bias', brief: 'Output quality & selection bias: will the method actually surface the genuinely BEST expert-led tour, or only the most discoverable (big SEO operators, English web, well-indexed)? Recency/availability bias. Does "found it" get conflated with "best"? Does the ranking reward marketing polish over real depth?' },
  { key: 'anti-hallucination-rigor', brief: 'Anti-hallucination & verification rigor: is the no-invention guarantee airtight? Find any path where an agent could fabricate/over-trust a guide name, credential, price, date, or "expert-led" claim and have it slip into a ranking. Are snippet-sourced/UNVERIFIED rules tight? Is "credential fits the theme" actually checkable?' },
  { key: 'global-cultural-bias', brief: 'Global & cultural bias (HIGH PRIORITY — deliverable is global): the axes/authority-index/channels lean Western-academic and English/European (AITO/Virtuoso, UK/Ivy alumni, Met/British Museum, Studienreisen). For India/Egypt/Kenya/Thailand/Mexico/Japan, will this under-find LOCAL expert-led operators and over-weight Western operators running trips there? Does the "expert/credential" definition privilege Western academic credentials over indigenous/local mastery (a master tracker, a temple priest-scholar, a local archaeologist)? This could systematically mis-rank the non-Western majority of the 50. Recommend fixes.' },
  { key: 'adversarial-marketing', brief: 'Adversarial / gaming resistance: operators self-describe as "archaeologist-led" / "expert" as marketing. How does the method distinguish a real credentialed expert from SEO/marketing claims? Fake or thin credentials, figurehead guides who do not actually lead, "small group" that is not. Where can the method be fooled?' },
  { key: 'cost-efficiency-scale', brief: 'Cost & efficiency at scale: estimate the token/agent/time cost per country and across 50-150. Where is the waste (re-reads, over-spawning, exhaustive sweeps with diminishing returns)? Is loop-until-dry affordable? What is the cheapest path that still meets the quality bar? Recommend cost cuts that do not break quality.' },
  { key: 'failure-robustness', brief: 'Failure modes & robustness: what happens when a country has a thin/no expert-led market, a source is down, search returns junk, a whole theme has <2 products, or discovery never converges? Is there graceful degradation at COUNTRY level (not just theme)? Stop conditions, budgets, and "we could not do this well — here is why" honesty.' },
]

phase('Review')
const reviews = await parallel(LENSES.map(L => () =>
  agent(`Fresh-eyes reviewer of a documentation playbook. LENS: ${L.brief}\n\n${CONTEXT}\n\n${FILESET}\n\nReport concrete grounded findings (severity, file, issue, action, recommendation). Prefer high-confidence findings. Flag REDO/SIMPLIFY where warranted. End with a one-line lens verdict.`,
    { label: `review:${L.key}`, phase: 'Review', schema: FINDING_SCHEMA })))

phase('Synthesize')
const all = reviews.filter(Boolean)
const flat = all.flatMap(r => (r.findings||[]).map(f => ({ ...f, lens: r.lens })))
const synth = await agent(
  `Synthesize a second fresh-eyes review (lenses: output-quality/selection-bias, anti-hallucination, global/cultural-bias, adversarial-marketing, cost-at-scale, failure-robustness) of a GLOBAL expert-led-tour playbook into a prioritized change plan. Dedupe + cluster by root issue. Per cluster: title, severity, action (REDO/SIMPLIFY/FIX/KEEP), docs, recommendation. Then an overall VERDICT focused on: will this reliably produce genuinely-best, non-invented, globally-fair tour rankings affordably? Be decisive.\n\nLENS VERDICTS:\n${all.map(r=>'- '+r.lens+': '+r.lens_verdict).join('\n')}\n\nFINDINGS:\n${JSON.stringify(flat,null,2)}`,
  { label: 'synthesize', phase: 'Synthesize', schema: { type:'object', properties: {
    verdict: { type:'string' },
    biggest_risk: { type:'string' },
    clusters: { type:'array', items: { type:'object', properties: {
      title:{type:'string'}, severity:{type:'string'}, action:{type:'string'}, docs:{type:'string'}, recommendation:{type:'string'} },
      required:['title','severity','action','docs','recommendation'] } },
  }, required:['verdict','biggest_risk','clusters'] } })

return {
  counts: { blocker: flat.filter(f=>f.severity==='blocker').length, major: flat.filter(f=>f.severity==='major').length, minor: flat.filter(f=>f.severity==='minor').length },
  verdict: synth ? synth.verdict : 'failed',
  biggest_risk: synth ? synth.biggest_risk : '',
  clusters: synth ? synth.clusters : [],
  lens_verdicts: all.map(r=>({lens:r.lens, verdict:r.lens_verdict})),
}
