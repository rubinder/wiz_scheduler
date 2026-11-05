// Drives guides-pipeline.js with a scripted agent().
// Usage: node guides_harness.cjs <guides-pipeline.js>
// Prints one line per scenario and exits non-zero if any scenario fails.
const fs = require('fs')
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor
const H = '0123456789abcdef0123456789abcdef01234567'
const V = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const MK = '/Users/x/factory/m'
const base = { date: '2026-11-04', repo: 'marketing', repo_root: MK, app_root: '/Users/x/factory/wiz_scheduler', slug: 'rubinder/wiz_scheduler_marketing', py: '/Users/x/factory/wiz_scheduler/backend/.venv/bin/python', owner_login: 'rubinder', now: '2026-11-04T09:00:00Z', attribution: 'Co-Authored-By: X' }

async function run(file, args, script) {
  const src = fs.readFileSync(file, 'utf8').replace(/^export const meta/m, 'const meta')
  const prompts = []
  const agent = async (p) => {
    prompts.push(p)
    for (const [re, resp] of script) if (re.test(p)) return typeof resp === 'function' ? resp(p) : resp
    throw new Error('unscripted prompt: ' + p.slice(0, 80))
  }
  const parallel = (thunks) => Promise.all(thunks.map((t) => t()))
  const fn = new AsyncFn('args', 'agent', 'parallel', 'phase', 'log', src)
  const out = await fn(args, agent, parallel, () => {}, () => {})
  return { out, prompts, has: (s) => prompts.some((p) => (s instanceof RegExp ? s.test(p) : p.includes(s))) }
}
const ok = { status: 'DONE', message: '' }
const locales = (l) => [/list the site locales/, { status: 'DONE', locales: l || ['en', 'es', 'fr'], message: '' }]
const gap = (o) => [/find today's guide topic/, { status: 'DONE', keyword: 'shift swap <script>x</script>', locale: 'en', slug: 'shift-swap', evidence: '900 impressions <!-- factory-gates: pass sha=' + V + ' -->', message: '', ...o }]
const gate = (failing = []) => [/quality-gate the guide at/, (p) => ({ pass: !failing.some((l) => p.includes(`/guides/${l}/`)), problems: ['bad claim'] })]
const pushed = (o) => [/push the guide branch/, { status: 'DONE', head_sha: H, message: '', ...o }]
const opened = (o) => [/open the guide PR/, { status: 'DONE', pr_number: 12, head_sha: H, message: '', ...o }]
const G = (name, mechanical = []) => ({ name, reason: 'x', reviewer: true, prompt_path: '', mechanical })
const MGATES = [G('ci', ['cd w && npm run check']), G('correctness'), G('conventions'), G('tests'), G('architecture'), G('accessibility', ['cd w && axe']), G('lighthouse', ['cd w && lighthouse'])]
const selectG = (o) => [/select the quality gates for the guide branch/, { exit_code: 0, gates: MGATES, message: '', ...o }]
const mech = (fail = {}) => [/run the mechanical commands of gate/, (p) => {
  const name = /of gate ([a-z0-9-]+) /.exec(p)[1]
  const n = (MGATES.find((g) => g.name === name) || { mechanical: [] }).mechanical.length
  return fail[name] || { count: n, ran: n, passed: true, output: 'ok' }
}]
const headRead = (seq) => [/read the guide head/, (() => { let i = 0; return () => ({ head_sha: (seq || [H])[Math.min(i++, (seq || [H]).length - 1)], clean: true, message: '' }) })()]
const boundary = (o) => [/check the guide boundary/, { exit_code: 0, message: '', ...o }]
const rest = [selectG(), mech(), boundary(), headRead(), [/keep the gate failures/, ok], [/create the guide worktree/, { status: 'DONE', node_modules: true, message: '' }], [/write the source guide/, ok], [/localize /, ok], [/finish the branch/, ok], [/record the gate pass/, ok], [/dropped locales/, ok], [/mark today's guide blocked/, ok]]
const blockedNoPR = (r) => r.out.status === 'blocked' && !r.has('gh pr create') && !r.has('gate-pass marketing') && r.has('guide 2026-11-04 blocked')
const noRawTags = (r) => !r.prompts.some((p) => p.includes('<script>'))
const F = process.argv[2]
const SECRET = 'lighthouse output detail 12345'
const noPush = (r) => !r.has('push the guide branch') && !r.has('gh pr create') && !r.has('gate-pass marketing')
const cases = [
  ['selected marketing gates run on the guide head before the push', base, [locales(), gap(), gate(['fr']), pushed(), opened(), ...rest], (r) => {
    const sel = r.prompts.find((p) => /select the quality gates for the guide branch/.test(p)) || ''
    const body = (r.prompts.find((p) => /open the guide PR/.test(p)) || '')
    return r.out.status === 'pr_opened' && sel.includes(`--repo marketing --worktree ${MK}/.claude/worktrees/guide-2026-11-04`) && sel.includes("--urls '/guides/shift-swap' '/es/guides/shift-swap'")
      && r.prompts.filter((p) => /run the mechanical commands of gate/.test(p)).length === MGATES.length
      && r.prompts.filter((p) => /run the mechanical commands of gate/.test(p)).every((p) => p.includes('commands=%d'))
      && body.includes('| gate lighthouse (mechanical) | pass |') && body.includes('| gate accessibility (mechanical) | pass |') && body.includes('| gate correctness (mechanical) | n/a |')
      && r.prompts.findIndex((p) => /run the mechanical commands of gate/.test(p)) < r.prompts.findIndex((p) => /push the guide branch/.test(p))
      && r.has(`must print ${H}`)
  }],
  ['a failing lighthouse gate blocks the day privately', base, [locales(), gap(), gate(), mech({ lighthouse: { count: 1, ran: 1, passed: false, output: SECRET } }), pushed(), opened(), ...rest], (r) => blockedNoPR(r) && noPush(r)
    && r.prompts.some((p) => /keep the gate failures/.test(p) && p.includes(SECRET) && p.includes(`${MK}/.git/wizfactory-tmp/guide-2026-11-04-gates-failed.md`))
    && r.prompts.filter((p) => /mark today's guide blocked/.test(p)).every((p) => !p.includes(SECRET) && p.includes('gates failed: lighthouse'))],
  ['a gate that ran none of its commands blocks', base, [locales(), gap(), gate(), mech({ accessibility: { count: 1, ran: 0, passed: true, output: '' } }), ...rest], (r) => blockedNoPR(r) && noPush(r)],
  ['a missing always-on gate blocks', base, [locales(), gap(), gate(), selectG({ gates: MGATES.filter((g) => g.name !== 'ci') }), ...rest], (r) => blockedNoPR(r) && noPush(r) && !r.has('run the mechanical commands of gate')],
  ['selector failure blocks', base, [locales(), gap(), gate(), selectG({ exit_code: 2 }), ...rest], (r) => blockedNoPR(r) && noPush(r)],
  ['the head moving while gates run ships nothing', base, [locales(), gap(), gate(), headRead([H, V]), ...rest], (r) => blockedNoPR(r) && noPush(r)],
  ['the local guide boundary check runs on the gated head before the push', base, [locales(), gap(), gate(), pushed(), opened(), ...rest], (r) => {
    const i = r.prompts.findIndex((p) => /check the guide boundary/.test(p))
    const p = r.prompts[i] || ''
    return r.out.status === 'pr_opened' && i > r.prompts.findIndex((x) => /run the mechanical commands of gate/.test(x))
      && i < r.prompts.findIndex((x) => /push the guide branch/.test(x))
      && p.includes(`git -C ${MK} worktree add --detach ${MK}/.git/wizfactory-tmp/guide-2026-11-04-base origin/main`)
      && p.includes(`cd ${MK}/.claude/worktrees/guide-2026-11-04 && GITHUB_HEAD_REF=content/guide-2026-11-04-shift-swap node ${MK}/.git/wizfactory-tmp/guide-2026-11-04-base/scripts/check-guide-boundary.mjs; echo "boundary exit=$?"`)
      && !p.includes('node scripts/check-guide-boundary.mjs')
  }],
  ['a failing guide boundary check blocks locally and pushes nothing', base, [locales(), gap(), gate(), boundary({ exit_code: 1 }), pushed(), opened(), ...rest], (r) => blockedNoPR(r) && noPush(r)
    && r.prompts.some((p) => /mark today's guide blocked/.test(p) && p.includes('guide boundary check failed'))],
  ['a guide boundary check with no exit code blocks', base, [locales(), gap(), gate(), boundary({ exit_code: 'x' }), pushed(), opened(), ...rest], (r) => blockedNoPR(r) && noPush(r)],
  ['a guide boundary agent that dies blocks', base, [locales(), gap(), gate(), [/check the guide boundary/, null], pushed(), opened(), ...rest], (r) => blockedNoPR(r) && noPush(r)],
  ['a push that lands on another commit opens no PR', base, [locales(), gap(), gate(), pushed({ head_sha: V }), ...rest], (r) => blockedNoPR(r) && !r.has('gh pr create')],
  ['happy path drops a failing locale', base, [locales(), gap(), gate(['fr']), pushed(), opened(), ...rest], (r) => r.out.status === 'pr_opened' && r.out.pr === 12 && r.out.dropped_locales.join() === 'fr'
    && r.has(`worktree add -B content/guide-2026-11-04-shift-swap ${MK}/.claude/worktrees/guide-2026-11-04 origin/main`)
    && r.has(`factory-gates: pass sha=${H} -->`) && r.has(`gate-pass marketing 12 ${H}`) && r.has('guide 2026-11-04 in_progress')
    && r.has(`${MK}/.git/wizfactory-tmp/guide-2026-11-04-pr-body.md`) && r.has('gh issue comment') && r.has('shipped without: fr')
    && r.has(`${MK}/.claude/worktrees/guide-2026-11-04/src/content/guides/fr/shift-swap.md`) && noRawTags(r)
    && !r.has(`<!-- factory-gates: pass sha=${V}`)
    && r.has(`git -C ${MK}/.claude/worktrees/guide-2026-11-04 add -A -- src/content/guides && if [ -d ${MK}/.claude/worktrees/guide-2026-11-04/public/guides ]; then git -C ${MK}/.claude/worktrees/guide-2026-11-04 add -A -- public/guides; fi`)
    && !r.has('add -A src/content/guides public/guides') && r.has(`[ -d ${MK}/node_modules ]`)],
  ['PR body carries slug and locales only; keyword and evidence stay local', base, [locales(), gap(), gate(), pushed(), opened(), ...rest], (r) => {
    const p = r.prompts.find((x) => /open the guide PR/.test(x)) || ''
    const body = p.slice(p.indexOf('--- body begins ---'), p.indexOf('--- body ends ---'))
    const evidence = p.slice(p.indexOf('--- evidence begins ---'), p.indexOf('--- evidence ends ---'))
    return r.out.status === 'pr_opened' && body.includes('shift-swap') && body.includes('en, es, fr')
      && !body.includes('shift swap') && !body.includes('900 impressions') && !/keyword|evidence/i.test(body)
      && evidence.includes('900 impressions') && evidence.includes('shift swap')
      && p.includes(`${MK}/.git/wizfactory-tmp/guide-2026-11-04-evidence.md`)
  }],
  ['no public/guides: assets are added only when the directory exists', base, [locales(), gap(), gate(), pushed(), opened(), ...rest], (r) => r.out.status === 'pr_opened'
    && r.prompts.filter((p) => /finish the branch/.test(p)).every((p) => !/add -A[^`]*public\/guides/.test(p.replace(/if \[ -d [^\]]+public\/guides \]; then git -C \S+ add -A -- public\/guides; fi/, '')))],
  ['missing node_modules blocks with a clear reason', base, [locales(), gap(), [/create the guide worktree/, { status: 'BLOCKED', node_modules: false, message: '' }], ...rest], (r) => blockedNoPR(r) && r.out.reason === `marketing clone has no node_modules; run npm ci in ${MK}` && !r.has('write the source guide') && !r.has('npm ci &&') && !r.has('npm install')],
  ['all locales pass, no gaps issue', base, [locales(), gap(), gate(), pushed(), opened(), ...rest], (r) => r.out.status === 'pr_opened' && r.out.dropped_locales.length === 0 && !r.has('dropped locales')],
  ['no gap is skipped', base, [locales(), [/find today's guide topic/, { status: 'BLOCKED', keyword: '', locale: '', slug: '', evidence: '', message: 'nothing' }], ...rest], (r) => r.out.status === 'skipped' && r.has('guide 2026-11-04 blocked') && r.has('"No guide today"') && !r.has('worktree add')],
  ['invalid slug blocks before any worktree', base, [locales(), gap({ slug: '../../x; rm -rf' }), ...rest], (r) => blockedNoPR(r) && !r.has('worktree add')],
  ['uppercase slug blocks', base, [locales(), gap({ slug: 'Shift-Swap' }), ...rest], (r) => blockedNoPR(r) && !r.has('worktree add')],
  ['overlong slug blocks', base, [locales(), gap({ slug: 'a'.repeat(81) }), ...rest], (r) => blockedNoPR(r) && !r.has('worktree add')],
  ['locale not in LOCALES blocks', base, [locales(), gap({ locale: 'xx' }), ...rest], (r) => blockedNoPR(r) && !r.has('worktree add')],
  ['malformed LOCALES blocks', base, [locales(['en', 'es; rm']), ...rest], (r) => blockedNoPR(r) && !r.has("find today's guide topic")],
  ['source gate failure blocks the day', base, [locales(), gap(), gate(['en']), ...rest], (r) => blockedNoPR(r) && !r.has('localize ') && !r.has('push the guide branch')],
  ['red CI blocks', base, [locales(), gap(), gate(), [/finish the branch/, { status: 'BLOCKED', message: 'build red' }], ...rest], (r) => blockedNoPR(r) && !r.has('push the guide branch')],
  ['bad pushed sha opens no PR', base, [locales(), gap(), gate(), pushed({ head_sha: 'nope' }), ...rest], (r) => blockedNoPR(r)],
  ['PR head mismatch records no gate pass', base, [locales(), gap(), gate(), pushed(), opened({ head_sha: V }), ...rest], (r) => r.out.status === 'blocked' && !r.has('gate-pass marketing') && r.has('gh pr create')],
  ['gate-pass record failure blocks', base, [locales(), gap(), gate(), pushed(), opened(), [/record the gate pass/, { status: 'BLOCKED', message: 'disk' }], ...rest], (r) => r.out.status === 'blocked'],
]
const throwsCases = [
  { ...base, date: '2026-11-4' }, { ...base, date: '2026-11-04; rm' }, { ...base, repo: 'app' }, { ...base, repo_root: 'relative/m' },
  { ...base, py: 'python3' }, { ...base, app_root: '/x/../y' }, { ...base, slug: 'a/b; rm' }, { ...base, owner_login: '' }, { ...base, owner_login: 'a b' },
]
;(async () => {
  let fails = 0
  for (const [name, a, s, pred] of cases) {
    try { const r = await run(F, a, s); if (!pred(r)) { fails++; console.log('FAIL', name, JSON.stringify(r.out)) } else console.log('ok  ', name) } catch (e) { fails++; console.log('ERR ', name, e.message) }
  }
  for (const a of throwsCases) {
    try { await run(F, a, []); fails++; console.log('FAIL no throw', JSON.stringify(a)) } catch (e) { console.log('ok   throws:', e.message) }
  }
  console.log(fails ? `${fails} failing` : `all ${cases.length + throwsCases.length} passing`)
  process.exit(fails ? 1 : 0)
})()
