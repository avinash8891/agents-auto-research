export const meta = {
  name: 'playbook-fresh-review',
  description: 'Fresh-eyes panel review of the whole travel playbook: correctness, completeness, simplicity, intent, reproducibility, consistency',
  phases: [{ title: 'Review' }, { title: 'Synthesize' }],
}

const ROOT = '/Users/avinashvankadaru/conductor/workspaces/agents-auto-research/tokyo-v1/travel'

const FILESET = `Read the WHOLE playbook under ${ROOT}/ : step docs 00-overview, 01-country-ranking, 02-theme-seeding, 03-coverage-matrix, 04-discovery-loop, 05-convergence-and-admission-bar, 06-corpus-and-persistence, 07-verification-and-ranking, 08-freshness-and-updates, 09-agent-orchestration, 11-trip-composition; README; foundation travel-config.md, REGISTRY-PROTOCOL.md, doc-manifest.md, axes-registry.md, channel-registry.md, lens-registry.md, theme-archetypes.md, sources-registry.md, operator-aliases.md, tags-registry.md, AUDIT-CHECKLIST.md; and 10-lessons-log.md for context. Read actual file text — do not assume.`

const CONTEXT = `CONTEXT (be skeptical with it): this playbook builds ranked Top-5 expert-led tour lists per regional theme for the most-visited countries. Over one session it accreted 27 lessons, 9 single-source registries/config, dirty-propagation, a leads bus, schema-versioning — but has produced ZERO actual tour rankings yet (pilot = 10 countries, currently only Italy theme-mapped). Challenge whether the machinery earns its keep at this scale. The owner explicitly wants REDO/SIMPLIFY recommendations, not patches — say so if something is wrong or over-built.`

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          location: { type: 'string', description: 'file(s)' },
          issue: { type: 'string' },
          action: { type: 'string', enum: ['redo', 'simplify', 'fix', 'keep-as-is-but-note'] },
          recommendation: { type: 'string' },
        },
        required: ['severity', 'location', 'issue', 'action', 'recommendation'],
      },
    },
    lens_verdict: { type: 'string', description: 'one-line: is the playbook sound through this lens?' },
  },
  required: ['lens', 'findings', 'lens_verdict'],
}

const LENSES = [
  { key: 'logical-correctness', brief: 'Logical correctness & coherence: contradictions between docs, conflicting rules, circular dependencies, steps that cannot actually execute, ordering bugs, a definition that contradicts its use. Trace the pipeline end-to-end and find where the logic breaks.' },
  { key: 'completeness', brief: 'Completeness & gaps: a consumer with no producer (or vice versa), a referenced artifact/field/slug never defined, an unhandled case, a step whose OUTPUT does not actually feed its stated NEXT. Missing steps for the stated goal.' },
  { key: 'simplicity-overengineering', brief: 'Simplicity / over-engineering (ponytail): is this over-built for a 10-country pilot? Which registries/mechanisms/abstractions could be merged, deferred, or deleted without losing the deliverable? Did we patch-on-patch into complexity? Recommend concrete CUTS. Be aggressive — the owner wants redo not patches.' },
  { key: 'intent-common-sense', brief: 'True-to-intent & common sense: does the method actually, practically produce GOOD ranked expert-led tour lists? Has the meta-work (registries, dirty-propagation, leads bus, lessons) drifted from the real goal? Would a normal person running this get useful tour rankings, or has it become a self-referential documentation system? Is the effort/value ratio sane?' },
  { key: 'reproducibility', brief: 'Reproducibility / executability: can a FRESH agent execute each step end-to-end from the committed files alone? Find underspecified steps, hand-wavy procedures, places where an agent would not know exactly what to do or produce. Check the agent-spec contracts are actually executable.' },
  { key: 'cross-doc-consistency', brief: 'Cross-doc consistency: do config names, registry refs, doc-manifest slugs, and per-country paths actually resolve and agree across every doc? Terminology drift, a name used two ways, a path style that varies, a rule stated differently in two places.' },
]

phase('Review')
const reviews = await parallel(LENSES.map(L => () =>
  agent(
    `You are a fresh-eyes reviewer of a documentation playbook. LENS: ${L.brief}\n\n${CONTEXT}\n\n${FILESET}\n\nReport concrete findings (severity, file location, the issue, action=redo/simplify/fix/keep-but-note, recommendation). Be specific and grounded in file text. Prefer fewer high-confidence findings over noise. Flag anything that should be REDONE or SIMPLIFIED, not patched. End with a one-line lens verdict.`,
    { label: `review:${L.key}`, phase: 'Review', schema: FINDING_SCHEMA }
  )
))

phase('Synthesize')
const all = reviews.filter(Boolean)
const flat = all.flatMap(r => (r.findings || []).map(f => ({ ...f, lens: r.lens })))
const synth = await agent(
  `Synthesize a fresh-eyes review of a travel-research playbook into a prioritized change plan. Below are findings from 6 reviewer lenses (logical-correctness, completeness, simplicity/over-engineering, intent/common-sense, reproducibility, cross-doc-consistency). Dedupe overlapping findings; cluster by root issue. For each cluster give: title, severity (blocker/major/minor), action (REDO / SIMPLIFY / FIX / KEEP), docs affected, and a crisp recommendation. Then give an overall VERDICT: is the playbook fundamentally sound (and just needs targeted changes), or is it over-engineered / drifted and needs restructuring? Be honest and decisive — the owner does not want patches over a wrong design.\n\nLENS VERDICTS:\n${all.map(r => '- ' + r.lens + ': ' + r.lens_verdict).join('\n')}\n\nFINDINGS JSON:\n${JSON.stringify(flat, null, 2)}`,
  { label: 'synthesize', phase: 'Synthesize', schema: {
    type: 'object',
    properties: {
      verdict: { type: 'string' },
      sound_or_restructure: { type: 'string', enum: ['sound-targeted-changes', 'needs-restructure', 'over-engineered-simplify'] },
      clusters: { type: 'array', items: { type: 'object', properties: {
        title: { type: 'string' }, severity: { type: 'string' }, action: { type: 'string' },
        docs: { type: 'string' }, recommendation: { type: 'string' },
      }, required: ['title','severity','action','docs','recommendation'] } },
    },
    required: ['verdict', 'sound_or_restructure', 'clusters'],
  } }
)

return {
  counts: { blocker: flat.filter(f=>f.severity==='blocker').length, major: flat.filter(f=>f.severity==='major').length, minor: flat.filter(f=>f.severity==='minor').length },
  verdict: synth ? synth.verdict : 'synthesis failed',
  sound_or_restructure: synth ? synth.sound_or_restructure : 'unknown',
  clusters: synth ? synth.clusters : [],
  lens_verdicts: all.map(r => ({ lens: r.lens, verdict: r.lens_verdict })),
}
