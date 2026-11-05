// Drives factory-regate.js with a scripted agent().
// Usage: node regate_harness.cjs <factory-regate.js>
// Prints one line per scenario and exits non-zero if any scenario fails.
const fs = require('fs')
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor
const H = '0123456789abcdef0123456789abcdef01234567'
const V = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const R = '/Users/x/factory/wiz_scheduler'
const SLUG = 'rubinder/wiz_scheduler'
const SECRET = 'SQL injection via raw company filter in employees router'
const base = { pr: 7, head_sha: H, branch: 'agent/issue-3-x', repo: 'app', repo_root: R, app_root: R, slug: SLUG, py: `${R}/backend/.venv/bin/python`, owner_login: 'rubinder', now: '2026-11-04T09:00:00Z', attribution: '' }

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
  const find = (re) => prompts.filter((p) => re.test(p))
  return { out, prompts, find, has: (s) => prompts.some((p) => (s instanceof RegExp ? s.test(p) : p.includes(s))) }
}
const ok = { status: 'DONE', message: '' }
const G = (name, mechanical = []) => ({ name, reason: 'x', reviewer: true, prompt_path: '', mechanical })
const GATES = [G('ci', ['cd w && pytest']), G('correctness'), G('conventions'), G('tests'), G('architecture'), G('tenancy-security', ['cd w && pytest t'])]
const check = (o) => [/re-check .* before regating/, { status: 'DONE', state: 'OPEN', head_sha: H, branch: 'agent/issue-3-x', base: 'main', cross_repo: false, author: 'rubinder', message: '', ...o }]
const mech = (fail = {}) => [/run the mechanical commands of gate/, (p) => {
  const name = /of gate ([a-z0-9-]+)\./.exec(p)[1]
  const n = (GATES.find((g) => g.name === name) || { mechanical: [] }).mechanical.length
  return fail[name] || { count: n, ran: n, passed: true, output: 'ok' }
}]
const reviewer = (failing = {}) => [/You are the "[a-z0-9-]+" gate/, (p) => {
  const name = /You are the "([a-z0-9-]+)" gate/.exec(p)[1]
  return failing[name] ? { approved: false, findings: [{ severity: 'critical', file: 'backend/routers/employees.py', line: 9, what: failing[name], why: 'exploit', fix: 'x' }], summary: '' } : { approved: true, findings: [], summary: '' }
}]
const rest = [
  [/create a detached worktree/, { head_sha: H, clean: true, message: '' }],
  [/select the quality gates/, { exit_code: 0, gates: GATES, message: '' }],
  mech(), reviewer(),
  [/Try to refute/, { refuted: false, reason: 'real' }],
  [/read the regated commit/, { head_sha: H, clean: true, message: '' }],
  [/re-bind the gate marker/, { exit_code: 0, message: '' }],
  [/record the gate pass/, { exit_code: 0, message: '' }],
  [/record the regate details/, ok], [/Step: block/, ok], [/remove the regate worktree/, ok],
]
const blockComment = (r) => {
  const p = r.find(/Step: block /)[0] || ''
  return p.slice(p.indexOf('--- comment begins ---'), p.indexOf('--- comment ends ---'))
}
const ghScoped = (r) => {
  const cmds = r.prompts.flatMap((p) => [...p.matchAll(/`(gh [^`]*)`/g)].map((m) => m[1]))
  return cmds.length > 0 && cmds.every((c) => c.includes(`--repo ${SLUG}`)) && !r.has('{owner}')
}
const cleaned = (r) => r.has(`worktree remove --force '${R}/.claude/worktrees/regate-7'`) && /remove the regate worktree/.test(r.prompts[r.prompts.length - 1])
const noPass = (r) => !r.has('statefile gate-pass') && !r.has('re-bind the gate marker')
const privately = (r) => r.out.status === 'blocked' && noPass(r) && !blockComment(r).includes(SECRET) && !blockComment(r).includes('employees.py')
  && r.find(/record the regate details/).some((p) => p.includes(SECRET) && p.includes(`${R}/.git/wizfactory-tmp/app-pr-7-regate.md`) && p.includes('--message "$(cat '))
  && r.has(`gh pr edit 7 --repo ${SLUG} --add-label agent-blocked`) && cleaned(r)
const F = process.argv[2]
const cases = [
  ['clean regate re-binds the marker and the gate pass to the head', base, [check(), ...rest], (r) => {
    const rebind = r.find(/re-bind the gate marker/)[0] || ''
    return r.out.status === 'regated' && r.out.head_sha === H && r.has(`worktree add --detach '${R}/.claude/worktrees/regate-7' ${H}`)
      && rebind.includes(`if .headRefOid == "${H}" then .body`) && rebind.includes('factory-gates: pass sha=') && rebind.includes(`-body-new.md' ${H}`)
      && rebind.includes(`gh pr edit 7 --repo ${SLUG} --body-file`) && r.has(`statefile gate-pass app 7 ${H}`)
      && r.find(/run the mechanical commands of gate/).length === GATES.length && r.find(/run the mechanical commands of gate/).every((p) => p.includes('commands=%d'))
      && ghScoped(r) && cleaned(r)
  }],
  ['mechanical failure blocks privately', base, [check(), mech({ ci: { count: 1, ran: 1, passed: false, output: SECRET } }), ...rest], privately],
  ['a gate that ran none of its commands blocks', base, [check(), mech({ 'tenancy-security': { count: 1, ran: 0, passed: true, output: '' } }), ...rest], (r) => r.out.status === 'blocked' && noPass(r)],
  ['confirmed reviewer finding blocks privately', base, [check(), reviewer({ 'tenancy-security': SECRET }), ...rest], privately],
  ['refuted reviewer finding regates', base, [check(), reviewer({ correctness: 'nit' }), [/Try to refute/, { refuted: true, reason: 'wrong' }], ...rest], (r) => r.out.status === 'regated'],
  ['a refuter that dies confirms the finding', base, [check(), reviewer({ correctness: SECRET }), [/Try to refute/, null], ...rest], privately],
  ['missing always-on gate blocks', base, [check(), [/select the quality gates/, { exit_code: 0, gates: GATES.filter((g) => g.name !== 'ci'), message: '' }], ...rest], (r) => r.out.status === 'blocked' && noPass(r)],
  ['head moved since the snapshot: nothing runs', base, [check({ head_sha: V }), ...rest], (r) => r.out.status === 'stopped' && noPass(r) && !r.has('run the mechanical commands') && cleaned(r)],
  ['fork or stranger PR: nothing runs', base, [check({ author: 'mallory' }), ...rest], (r) => r.out.status === 'stopped' && noPass(r)],
  ['worktree not at the head: nothing runs', base, [check(), [/create a detached worktree/, { head_sha: V, clean: true, message: '' }], ...rest], (r) => r.out.status === 'stopped' && noPass(r) && cleaned(r)],
  ['marker re-bind failure records no gate pass', base, [check(), [/re-bind the gate marker/, { exit_code: 1, message: 'head moved' }], ...rest], (r) => r.out.status === 'stopped' && !r.has('statefile gate-pass')],
]
const throwsCases = [
  { ...base, slug: 'a/b; rm' }, { ...base, slug: '' }, { ...base, head_sha: 'abc' }, { ...base, pr: 0 },
  { ...base, branch: 'content/guide-2026-11-04-x' }, { ...base, branch: 'factory/retro-x' }, { ...base, branch: 'agent/../x' },
  { ...base, repo: 'x' }, { ...base, py: 'python3' }, { ...base, owner_login: 'a b' },
]
// --print-rebind <args json>: print the marker re-bind command (step 3) for a real shell test.
if (process.argv[3] === '--print-rebind') {
  run(F, JSON.parse(process.argv[4]), [[/re-check .* before regating/, (p) => ({ status: 'DONE', state: 'OPEN', head_sha: JSON.parse(process.argv[4]).head_sha, branch: JSON.parse(process.argv[4]).branch, base: 'main', cross_repo: false, author: 'rubinder', message: '' })],
    [/create a detached worktree|read the regated commit/, { head_sha: JSON.parse(process.argv[4]).head_sha, clean: true, message: '' }],
    [/select the quality gates/, { exit_code: 0, gates: GATES.map((g) => ({ ...g, mechanical: [] })), message: '' }],
    [/run the mechanical commands of gate/, { count: 0, ran: 0, passed: true, output: 'ok' }], reviewer(),
    [/re-bind the gate marker/, (p) => { console.log(/\n3\. `([^\n]*)`\n/.exec(p)[1]); return { exit_code: 1, message: '' } }],
    [/remove the regate worktree/, ok]]).then(() => process.exit(0))
} else (async () => {
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
