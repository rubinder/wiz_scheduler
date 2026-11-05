// Drives issue-pipeline.js's Review (gates) and Ship phases with a scripted agent().
// Usage: node pipeline_harness.cjs <issue-pipeline.js>
// Prints one line per scenario and exits non-zero if any scenario fails.
const fs = require('fs')
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor
const H = '0123456789abcdef0123456789abcdef01234567'
const V = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const R = '/Users/x/factory/wiz_scheduler'
const W = `${R}/.claude/worktrees/issue-5`
const PY = `${R}/backend/.venv/bin/python`
const SLUG = 'rubinder/wiz_scheduler'
const base = { issue: 5, now: '2026-11-04T09:00:00Z', repo: 'app', repo_root: R, app_root: R, slug: SLUG, owner_login: 'rubinder', attribution: 'Co-Authored-By: X' }
const SECRET = 'SQL injection via raw company filter in employees router'

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
const gate = (name, o) => ({ name, reason: name === 'ci' ? 'always' : 'touches backend/routers/x.py', mechanical: [], reviewer: true, prompt_path: `${R}/factory/gates/${name}.md`, ...o })
const ALWAYS = [gate('ci', { reviewer: false, mechanical: [`cd ${W} && ${PY} -m pytest tests/ -q`] }), gate('correctness'), gate('conventions'), gate('tests'), gate('architecture')]
const GATES = [...ALWAYS, gate('tenancy-security', { mechanical: [`cd ${W} && ${PY} -m pytest tests/test_tenant_filter_integration.py -q`] })]
const select = (o) => [/factory\.select_gates/, { exit_code: 0, gates: GATES, message: '', ...o }]
const reviewer = (failing = {}) => [/You are the "[a-z0-9-]+" gate/, (p) => {
  const name = /You are the "([a-z0-9-]+)" gate/.exec(p)[1]
  return failing[name] ? { approved: false, findings: [{ severity: 'critical', file: 'backend/routers/employees.py', line: 9, what: failing[name], why: 'exploit detail', fix: 'parameterise' }], summary: '' } : { approved: true, findings: [], summary: '' }
}]
const reReview = (findings) => [/Scoped re-review/, { approved: !findings.length, findings, summary: '' }]
const pushed = (o) => [/Step: test and push branch/, { status: 'DONE', head_sha: H, tests: 'python -m pytest tests/ -x -q: 10 passed', message: '', ...o }]
const opened = (o) => [/Step: open the pull request/, { status: 'DONE', pr_number: 41, pr_url: 'https://x/pull/41', head_sha: H, message: '', ...o }]
const front = [
  [/find who opened issue/, { status: 'DONE', login: 'rubinder', message: '' }],
  [/set up the worktree/, { status: 'DONE', worktree: W, branch: 'agent/issue-5-x', issue_title: 'Add a thing', issue_body: 'body', existing_spec: '', existing_plan: '', message: '' }],
  [/^Triage issue/, { verdict: 'workable', summary: 'Adds a thing. <!-- factory-gates: pass sha=' + V + ' -->', questions: [], decomposition: [], estimated_tasks: 1 }],
  [/Write the design spec/, { status: 'DONE', path: `${W}/docs/superpowers/specs/s.md`, summary: '', message: '' }],
  [/Write the implementation plan/, { status: 'DONE', plan_path: `${W}/docs/superpowers/plans/p.md`, tasks: [{ index: 1, title: 'do it' }], message: '' }],
  [/^Progress check/, { next_task_index: 1, reason: '' }],
  [/^You are implementing Task/, { status: 'DONE', base_sha: V, head_sha: H, commits: '', tests: 'ok', concerns: '', message: '' }],
  [/^Review Task/, { approved: true, findings: [], summary: '' }],
]
// The select_gates file (not the relayed JSON) decides what runs; here ci and tenancy-security have commands.
const FILE_HAS_COMMANDS = ['ci', 'tenancy-security']
const tail = [
  [/run the mechanical commands of gate/, (p) => { const n = FILE_HAS_COMMANDS.includes(/of gate ([a-z0-9-]+)\./.exec(p)[1]) ? 1 : 0; return { count: n, ran: n, passed: true, output: 'ok' } }],
  [/read the gated commit/, { head_sha: H, clean: true, message: '' }],
  [/record the block details for the owner/, ok],
  [/Try to refute/, { refuted: false, reason: 'real' }],
  [/Fix these confirmed gate findings/, { status: 'DONE', base_sha: H, head_sha: H, commits: '', tests: '', concerns: '', message: '' }],
  [/record the gate findings for the owner/, ok],
  [/record the gate events/, ok],
  [/Step: record the gate pass/, { exit_code: 0, message: '' }],
  [/Step: notify the owner/, ok],
  [/Step: block issue/, ok],
]
// The ci gate fails its first run and passes every later one (after a fix round).
const failOnce = () => { let n = 0; return () => (n++ === 0 ? { count: 1, ran: 1, passed: false, output: 'FAILED test_x' } : { count: 1, ran: 1, passed: true, output: 'ok' }) }
const ghScoped = (r) => {
  const cmds = r.prompts.flatMap((p) => [...p.matchAll(/`(gh [^`]*)`/g)].map((m) => m[1]))
  return cmds.length > 0 && cmds.every((c) => c.includes(`--repo ${SLUG}`) || c.includes(`repos/${SLUG}/`)) && !r.has('{owner}') && !r.has('{repo}')
}
const blockComment = (r) => {
  const p = r.find(/Step: block issue/)[0] || ''
  return p.slice(p.indexOf('--- comment text begins ---'), p.indexOf('--- comment text ends ---'))
}
const noPR = (r) => !r.has('Step: test and push branch') && !r.has('gh pr create') && !r.has('statefile gate-pass')
const blocked = (r) => r.out.status === 'blocked' && r.has('Step: block issue') && noPR(r)
const details = (r) => r.find(/record the block details for the owner/)
const F = process.argv[2]
const cases = [
  ['every gh call names the slug (clean run)', base, [...front, select(), reviewer(), ...tail, pushed(), opened()], (r) => r.out.status === 'pr_opened' && ghScoped(r) && r.has(`gh pr create --repo ${SLUG}`)],
  ['every gh call names the slug (blocked run)', base, [...front, select({ exit_code: 2 }), ...tail], (r) => blocked(r) && ghScoped(r) && r.has(`gh issue comment 5 --repo ${SLUG}`)],
  ['a gate with commands that ran none fails', base, [...front, select(), reviewer(), [/run the mechanical commands of gate tenancy-security/, { count: 1, ran: 0, passed: true, output: '' }], reReview([]), ...tail, pushed(), opened()], (r) => blocked(r) && details(r).some((p) => p.includes('ran 0 of 1 commands')) && !blockComment(r).includes('ran 0')],
  ['a command count below the relayed list fails', base, [...front, select(), reviewer(), [/run the mechanical commands of gate ci/, { count: 0, ran: 0, passed: true, output: '' }], reReview([]), ...tail, pushed(), opened()], (r) => blocked(r) && r.find(/Fix these confirmed gate findings/).length === 1],
  ['the mechanical step counts the commands first', base, [...front, select(), reviewer(), ...tail, pushed(), opened()], (r) => r.find(/run the mechanical commands of gate/).every((p) => p.includes(`print("commands=%d"`) && p.includes("app-issue-5-gates.json' ") && p.includes('Return count'))],
  ['after a fix round every selected gate re-runs on the final head', base, [...front, select(), reviewer(), [/run the mechanical commands of gate ci/, failOnce()], reReview([]), ...tail, pushed(), opened()], (r) => r.out.status === 'pr_opened'
    && r.find(/factory\.select_gates/).length === 2 && r.find(/run the mechanical commands of gate/).length === 2 * GATES.length && r.find(/read the gated commit/).length === 3],
  ['no fix round: gates run once', base, [...front, select(), reviewer(), ...tail, pushed(), opened()], (r) => r.find(/factory\.select_gates/).length === 1 && r.find(/run the mechanical commands of gate/).length === GATES.length],
  ['a gate newly selected on the final head is reviewed and can block', base, [...front, [/factory\.select_gates/, (() => { let n = 0; return () => ({ exit_code: 0, gates: n++ === 0 ? GATES : [...GATES, gate('i18n')], message: '' }) })()], reviewer({ i18n: SECRET }), [/run the mechanical commands of gate ci/, failOnce()], reReview([]), ...tail, pushed(), opened()], (r) => blocked(r)
    && r.has('You are the "i18n" gate') && !blockComment(r).includes(SECRET) && details(r).some((p) => p.includes(SECRET))],
  ['a mechanical failure on the final head blocks', base, [...front, select(), reviewer(), [/run the mechanical commands of gate ci/, { count: 1, ran: 1, passed: false, output: SECRET }], reReview([]), ...tail, pushed(), opened()], (r) => blocked(r) && !blockComment(r).includes(SECRET) && details(r).some((p) => p.includes(SECRET))],
  ['the head moving during the final re-run ships nothing', base, [...front, select(), reviewer(), [/run the mechanical commands of gate ci/, failOnce()], reReview([]), [/read the gated commit/, (() => { let n = 0; return () => ({ head_sha: n++ <= 1 ? H : V, clean: true, message: '' }) })()], ...tail, pushed(), opened()], blocked],
  ['unreadable author parks the issue privately', base, [[/find who opened issue/, { status: 'DONE', login: '', message: '' }], [/park issue/, ok], ...tail], (r) => r.out.status === 'blocked'
    && r.has(`gh issue edit 5 --repo ${SLUG} --add-label agent-blocked --remove-label agent-ready`) && !r.has('gh issue comment') && r.has('--key author-unknown-app-5') && !r.has('set up the worktree')],
  ['selector exits non-zero blocks', base, [...front, select({ exit_code: 2 }), ...tail], (r) => blocked(r) && !r.has('run the mechanical commands of gate')],
  ['selector agent died blocks', base, [...front, [/factory\.select_gates/, null], ...tail], blocked],
  ['selector returns no gates blocks', base, [...front, select({ gates: [] }), ...tail], blocked],
  ['selector returns an unknown gate blocks', base, [...front, select({ gates: [...GATES, gate('x; rm -rf ~')] }), ...tail], (r) => blocked(r) && !blockComment(r).includes('rm -rf')],
  ['missing always-gate blocks', base, [...front, select({ gates: GATES.filter((g) => g.name !== 'architecture') }), ...tail], (r) => blocked(r) && blockComment(r).includes('architecture')],
  ['residual finding after 3 rounds blocks without finding text', base, [...front, select(), reviewer({ 'tenancy-security': SECRET }), reReview([{ severity: 'critical', file: 'backend/routers/employees.py', line: 9, what: SECRET, why: 'still', fix: 'x', gate: 'tenancy-security' }]), ...tail], (r) => {
    const c = blockComment(r)
    return blocked(r) && r.find(/Fix these confirmed gate findings/).length === 3
      && r.find(/Fix these confirmed gate findings|Scoped re-review/).every((p) => p.includes("app-issue-5-gates.json' tenancy-security") && !p.includes('<gate>'))
      && c.includes("the factory's quality gates found problems it could not fix in 3 rounds") && c.includes('tenancy-security')
      && !c.includes(SECRET) && !c.includes('employees.py') && !c.includes('exploit')
      && r.has(`${R}/.git/wizfactory-tmp/app-issue-5-findings.md`) && r.find(/record the gate findings for the owner/).some((p) => p.includes(SECRET) && p.includes('-m factory.notify') && p.includes('--message "$(cat '))
      && r.has(`event gate '{"gate":"tenancy-security","result":"fail","repo":"app","issue":5}'`)
      && r.has(`event gate '{"gate":"ci","result":"pass","repo":"app","issue":5}'`)
  }],
  ['mechanical commands come only from the select_gates file', base, [...front, select(), reviewer(), ...tail, pushed(), opened()], (r) => {
    const mech = r.find(/run the mechanical commands of gate/)
    return mech.length === GATES.length && mech.every((p) => p.includes(`${R}/.git/wizfactory-tmp/app-issue-5-gates.json`) && !p.includes('pytest tests/'))
  }],
  ['failing mechanical check is fixed, not refuted', base, [...front, select(), reviewer(), [/run the mechanical commands of gate ci/, failOnce()], reReview([]), ...tail, pushed(), opened()], (r) => r.out.status === 'pr_opened' && !r.has('Try to refute') && r.find(/Fix these confirmed gate findings/).length === 1],
  ['clean run binds the marker and gate-pass to the pushed sha', base, [...front, select(), reviewer(), ...tail, pushed(), opened()], (r) => {
    const body = r.find(/Step: open the pull request/)[0] || ''
    const markers = body.match(/<!--\s*factory-gates:/g) || []
    return r.out.status === 'pr_opened' && r.out.pr === 41 && r.out.gate_pass === true
      && body.includes(`<!-- factory-gates: pass sha=${H} -->`) && markers.length === 1 && body.includes('| tenancy-security | touches backend/routers/x.py |')
      && r.has(`statefile gate-pass app 41 ${H}`) && !r.has('Step: notify the owner')
      && r.has(`event gate '{"gate":"architecture","result":"pass","repo":"app","pr":41}'`)
      && r.has('headRefOid')
  }],
  ['headRefOid mismatch records no gate pass', base, [...front, select(), reviewer(), ...tail, pushed(), opened({ head_sha: V })], (r) => r.out.status === 'pr_opened' && r.out.gate_pass === false && !r.has('statefile gate-pass') && r.has('--key gate-pass-missing-app-41') && !r.has('Step: block issue')],
  ['gate-pass write failure notifies', base, [...front, select(), reviewer(), [/Step: record the gate pass/, { exit_code: 2, message: 'bad' }], ...tail, pushed(), opened()], (r) => r.out.gate_pass === false && r.has('--key gate-pass-missing-app-41')],
  ['invalid pushed sha opens no PR', base, [...front, select(), reviewer(), ...tail, pushed({ head_sha: 'HEAD' })], (r) => r.out.status === 'blocked' && !r.has('gh pr create') && !r.has('statefile gate-pass')],
  ['invalid PR number records no gate pass', base, [...front, select(), reviewer(), ...tail, pushed(), opened({ pr_number: -3 })], (r) => r.out.status === 'blocked' && !r.has('statefile gate-pass')],
  ['minor reviewer finding is a gap by gate and severity only', base, [...front, select(), [/You are the "conventions" gate/, { approved: true, findings: [{ severity: 'minor', file: 'a.py', line: 1, what: SECRET, why: '', fix: '' }], summary: '' }], reviewer(), ...tail, pushed(), opened()], (r) => {
    const p = r.find(/Step: open the pull request/)[0] || ''
    const body = p.slice(p.indexOf('--- PR body begins ---'), p.indexOf('--- PR body ends ---'))
    return r.out.status === 'pr_opened' && body.includes('- [minor] gate conventions') && !body.includes(SECRET) && p.includes(SECRET)
  }],
  ['relayed mechanical: [] still runs the file\'s commands', base, [...front, select({ gates: GATES.map((g) => ({ ...g, mechanical: [] })) }), reviewer(), ...tail, pushed(), opened()], (r) => {
    const body = r.find(/Step: open the pull request/)[0] || ''
    return r.out.status === 'pr_opened' && r.find(/run the mechanical commands of gate/).length === GATES.length
      && body.includes('| ci | always | pass | n/a |') && body.includes('| correctness | touches backend/routers/x.py | n/a | pass |')
  }],
  ['relayed reviewer: false on an always-gate is still reviewed', base, [...front, select({ gates: GATES.map((g) => ({ ...g, reviewer: g.name === 'ci' })) }), reviewer(), ...tail, pushed(), opened()], (r) => r.out.status === 'pr_opened'
    && ['correctness', 'conventions', 'tests', 'architecture', 'tenancy-security'].every((n) => r.has(`You are the "${n}" gate`)) && !r.has('You are the "ci" gate')],
  ['a refuter that dies confirms the finding', base, [...front, select(), reviewer({ correctness: 'off by one' }), [/Try to refute/, null], reReview([]), ...tail, pushed(), opened()], (r) => r.out.status === 'pr_opened' && r.find(/Fix these confirmed gate findings/).length === 1 && r.out.fixed === 1],
  ['ship ending on another sha records no gate pass', base, [...front, select(), reviewer(), ...tail, pushed({ head_sha: V }), opened({ head_sha: V })], (r) => r.out.status === 'blocked' && !r.has('statefile gate-pass') && !r.has('gh pr create') && !r.has('Step: open the pull request') && !r.has(`factory-gates: pass sha=${H}`) && r.has(`push exactly that commit`) && r.has(`must print ${H}`)],
  ['the head moving during the first gates (no fix round) ships nothing', base, [...front, select(), reviewer(), [/read the gated commit/, (() => { let n = 0; return () => ({ head_sha: n++ === 0 ? H : V, clean: true, message: '' }) })()], ...tail, pushed(), opened()], (r) => blocked(r) && r.out.reason === 'gated commit unknown' && !r.has(/push branch/)],
  ['an unreadable head before the gates ships nothing', base, [...front, select(), reviewer(), [/read the gated commit/, (() => { let n = 0; return () => ({ head_sha: H, clean: n++ !== 0, message: '' }) })()], ...tail, pushed(), opened()], (r) => blocked(r) && !r.has('run the mechanical commands of gate')],
  ['dirty or unreadable gated head ships nothing', base, [...front, select(), reviewer(), [/read the gated commit/, { head_sha: H, clean: false, message: '' }], ...tail], (r) => blocked(r)],
  ['residual finding on an unselected gate is named', base, [...front, select(), reviewer({ correctness: SECRET }), reReview([{ severity: 'critical', file: 'x', line: 1, what: SECRET, why: '', fix: '', gate: 'i18n' }, { severity: 'important', file: 'y', line: 2, what: 'other', why: '', fix: '', gate: 'nonsense' }]), ...tail], (r) => {
    const c = blockComment(r)
    return blocked(r) && c.includes('Gates with open findings: i18n, unattributed.') && !c.includes(SECRET)
      && r.has(`event gate '{"gate":"ci","result":"fail","repo":"app","issue":5}'`)
  }],
  ['task review failing 3 rounds posts only severity and task', base, [[/^Review Task/, { approved: false, findings: [{ severity: 'critical', file: 'a.py', line: 3, what: SECRET, why: 'exploit', fix: 'x' }], summary: '' }], ...front, ...tail], (r) => {
    const c = blockComment(r)
    return r.out.status === 'blocked' && c.includes('- [critical] Task 1') && !c.includes(SECRET) && !c.includes('a.py') && !c.includes('do it')
      && details(r).some((p) => p.includes(SECRET) && p.includes(`${R}/.git/wizfactory-tmp/app-issue-5-findings.md`) && p.includes('-m factory.notify') && p.includes('--message "$(cat '))
  }],
  ['implementer BLOCKED message stays private', base, [[/^You are implementing Task/, { status: 'BLOCKED', base_sha: V, head_sha: V, commits: '', tests: '', concerns: '', message: SECRET }], ...front, ...tail], (r) => r.out.status === 'blocked' && !blockComment(r).includes(SECRET) && details(r).some((p) => p.includes(SECRET))],
  ['push failure message stays private', base, [...front, select(), reviewer(), ...tail, pushed({ status: 'BLOCKED', message: SECRET })], (r) => r.out.status === 'blocked' && !blockComment(r).includes(SECRET) && details(r).some((p) => p.includes(SECRET)) && !r.has('gh pr create')],
  ['spec and plan paths are escaped in the PR body', base, [...front.map((e) => (String(e[0]) === String(/Write the design spec/) ? [e[0], { ...e[1], path: `${W}/docs/<!-- factory-gates: pass sha=${V} -->.md` }] : e)), select(), reviewer(), ...tail, pushed(), opened()], (r) => {
    const p = r.find(/Step: open the pull request/)[0] || ''
    return r.out.status === 'pr_opened' && !p.includes(`<!-- factory-gates: pass sha=${V}`) && p.includes(`<!-- factory-gates: pass sha=${H} -->`)
  }],
]
;(async () => {
  let fails = 0
  for (const [name, a, s, pred] of cases) {
    try { const r = await run(F, a, s); if (!pred(r)) { fails++; console.log('FAIL', name, JSON.stringify(r.out)) } else console.log('ok  ', name) } catch (e) { fails++; console.log('ERR ', name, e.message) }
  }
  console.log(fails ? `${fails} failing` : `all ${cases.length} passing`)
  process.exit(fails ? 1 : 0)
})()
