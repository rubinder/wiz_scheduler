// Drives factory-merge.js and factory-verify.js with a scripted agent().
// Usage: node workflow_harness.cjs <factory-merge.js> <factory-verify.js>
// Prints one line per scenario and exits non-zero if any scenario fails.
const fs = require('fs')
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor
const H = '0123456789abcdef0123456789abcdef01234567'
const M = 'fedcba9876543210fedcba9876543210fedcba98'
const V = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const base = { repo: 'app', repo_root: '/Users/x/factory/wiz_scheduler', slug: 'rubinder/wiz_scheduler', py: '/Users/x/factory/wiz_scheduler/backend/.venv/bin/python', owner_login: 'Rubinder', attribution: 'Co-Authored-By: X' }

async function run(file, args, script) {
  const src = fs.readFileSync(file, 'utf8').replace(/^export const meta/m, 'const meta')
  const prompts = []
  const agent = async (p) => {
    prompts.push(p)
    for (const [re, resp] of script) if (re.test(p)) return typeof resp === 'function' ? resp(p) : resp
    throw new Error('unscripted prompt: ' + p.slice(0, 80))
  }
  const fn = new AsyncFn('args', 'agent', 'parallel', 'phase', 'log', src)
  const out = await fn(args, agent, null, () => {}, () => {})
  return { out, prompts, has: (s) => prompts.some((p) => (s instanceof RegExp ? s.test(p) : p.includes(s))) }
}
const ok = { status: 'DONE', message: '' }
const check = (o) => [/re-check .* before merging/, { status: 'DONE', state: 'OPEN', is_draft: false, head_sha: H, cross_repo: false, author: 'rubinder', base: 'main', branch: 'agent/issue-3-x', checks: ['SUCCESS', 'SKIPPED'], gate: ['pass', H], protected_exit: 0, gate_pass_exit: 0, message: '', ...o }]
const merged = (o) => [/Step: merge /, { status: 'DONE', state: 'MERGED', merge_sha: M, message: '', ...o }]
const deploy = (c, covered = false) => [/is deployed on/, { status: 'DONE', conclusion: c, run_id: 5, covered, message: 'job x' }]
const smoke = (code, fails) => [/smoke-check production/, { exit_code: code, fail_lines: fails, output: 'FAIL <script> /x 500' }]
const opened = (o) => [/open the revert PR/, { status: 'DONE', pr_number: 9, url: 'https://x/pull/9', head_sha: V, message: '', ...o }]
const rest = [[/record the merge/, ok], [/record|notify the owner/, ok], [/revert commit/, { status: 'DONE', revert_sha: V, message: '' }], opened()]
const info = (o) => [/two derived fields/, { status: 'DONE', is_revert: false, issue: '', message: '', ...o }]
const MF = process.argv[2], VF = process.argv[3]
const A = { ...base, pr: 7, issue: 3, head_sha: H, branch: 'agent/issue-3-x' }
const notMerged = (r) => r.out.status === 'stopped' && !r.has('gh pr merge')
const cases = [
  ['merge pass', MF, A, [check(), merged(), deploy('success'), smoke(0, 0), ...rest], (r) => r.out.status === 'merged' && r.out.smoke === 'pass' && r.has(`--match-head-commit ${H}`) && r.has(`add-verify app 7 ${M}`) && r.has('verified app 7 pass') && r.has('factory.protected_check app 7 agent/issue-3-x;') && r.has(`has-gate-pass app 7 ${H}`) && r.has('use only the newest') && r.has('watch that newest run')],
  ['merge head moved', MF, A, [check({ head_sha: V })], notMerged],
  ['merge stale marker', MF, A, [check({ gate: ['pass', V] })], notMerged],
  ['merge pending check', MF, A, [check({ checks: ['SUCCESS', ''] })], notMerged],
  ['merge refuses fork', MF, A, [check({ cross_repo: true })], notMerged],
  ['merge refuses non-owner author', MF, A, [check({ author: 'mallory' })], notMerged],
  ['merge refuses non-main base', MF, A, [check({ base: 'release' })], notMerged],
  ['merge refuses non-factory branch', MF, A, [check({ branch: 'feat/x' })], notMerged],
  ['merge refuses a head branch other than the chosen one', MF, A, [check({ branch: 'agent/issue-4-y' })], notMerged],
  ['merge refuses protected paths', MF, A, [check({ protected_exit: 1 })], notMerged],
  ['merge refuses unknown protected check', MF, A, [check({ protected_exit: 2 })], notMerged],
  ['merge refuses body marker without local gate pass', MF, A, [check({ gate_pass_exit: 1 })], notMerged],
  ['merge command errored but PR merged', MF, A, [check(), merged({ message: 'failed to delete branch' }), deploy('success'), smoke(0, 0), ...rest], (r) => r.out.status === 'merged' && r.has(`add-verify app 7 ${M}`)],
  ['merge did not happen', MF, A, [check(), merged({ state: 'OPEN', merge_sha: '' })], (r) => r.out.status === 'stopped' && !r.has('add-verify')],
  ['merged without sha notifies', MF, A, [check(), merged({ merge_sha: '' }), ...rest], (r) => r.out.status === 'stopped' && r.has('--key merge-untracked-app-7') && !r.has('add-verify')],
  ['add-verify failure notifies', MF, A, [check(), merged(), [/record the merge/, { status: 'BLOCKED', message: 'disk' }], deploy('cancelled'), ...rest], (r) => r.out.status === 'merged' && r.has('--key add-verify-failed-app-7') && r.has('NOT retrying')],
  ['cancelled deploy is unknown', MF, A, [check(), merged(), deploy('cancelled'), ...rest], (r) => r.out.status === 'merged' && r.out.smoke === 'skipped' && !r.has('smoke-check production') && !r.has(/verified app 7/) && r.has('--key deploy-unknown-app-7') && !r.has('revert commit') && r.has('retries the check every tick')],
  ['missing deploy run is unknown', MF, A, [check(), merged(), deploy('none'), ...rest], (r) => r.out.smoke === 'skipped' && r.has('--key deploy-unknown-app-7') && !r.has('revert commit')],
  ['cancelled deploy covered by newer deploy smokes', MF, A, [check(), merged(), deploy('cancelled', true), smoke(0, 0), ...rest], (r) => r.out.status === 'merged' && r.out.smoke === 'pass' && r.has(`merge-base --is-ancestor ${M}`)],
  ['smoke unknown', MF, A, [check(), merged(), deploy('success'), smoke(2, 0), ...rest], (r) => r.out.status === 'merged' && r.out.smoke === 'unknown' && !r.has(/verified app 7/) && r.has('--key smoke-unknown-app-7') && !r.has('revert commit')],
  ['smoke traceback exit 1', MF, A, [check(), merged(), deploy('success'), smoke(1, 0), ...rest], (r) => r.out.smoke === 'unknown' && !r.has('revert commit')],
  ['smoke fail reverts', MF, A, [check(), merged(), deploy('success'), smoke(1, 2), ...rest], (r) => r.out.status === 'reverted' && r.has('verified app 7 fail') && r.has(`factory-gates: pass sha=${V}`) && r.has(`gate-pass app 9 ${V}`) && r.has('gh pr edit 7 --repo rubinder/wiz_scheduler --add-label factory-miss') && !r.has('gh issue edit') && r.has('&lt;script>') && !r.has('<script>') && r.has('/Users/x/factory/wiz_scheduler/.git/wizfactory-tmp/app-pr-7-revert-body.md')],
  ['revert PR head mismatch records no gate pass', MF, A, [check(), merged(), deploy('success'), smoke(1, 2), [/record the merge/, ok], [/record the failed/, ok], [/revert commit/, { status: 'DONE', revert_sha: V, message: '' }], opened({ head_sha: H })], (r) => r.out.status === 'stopped' && !r.has('gate-pass app 9')],
  ['deploy fail reverts', MF, A, [check(), merged(), deploy('failure'), ...rest], (r) => r.out.status === 'reverted' && !r.has('smoke-check production')],
  ['deploy agent died', MF, A, [check(), merged(), [/is deployed on/, null], ...rest], (r) => r.out.status === 'merged' && r.out.smoke === 'skipped' && !r.has('revert commit')],
  ['revert of revert stops', MF, { ...A, pr: 8, is_revert: true }, [check(), merged(), deploy('success'), smoke(1, 1), ...rest], (r) => r.out.status === 'stopped' && r.has('touch ~/factory/STOP') && !r.has('revert commit')],
  ['bad revert sha stops', MF, A, [check(), merged(), deploy('success'), smoke(1, 1), [/record the merge/, ok], [/record the failed/, ok], [/notify the owner/, ok], [/revert commit/, { status: 'DONE', revert_sha: 'nope', message: '' }]], (r) => r.out.status === 'stopped' && !r.has('open the revert PR') && r.has('touch ~/factory/STOP')],
  ['verify pass', VF, { ...base, pr: 7, sha: M }, [info({ issue: '3' }), deploy('success'), smoke(0, 0), ...rest], (r) => r.out.status === 'pass'],
  ['verify cancelled deploy keeps pending', VF, { ...base, pr: 7, sha: M }, [info(), deploy('cancelled'), ...rest], (r) => r.out.status === 'stopped' && !r.has(/verified app 7/) && r.has('--key deploy-unknown-app-7')],
  ['verify revert-of-revert', VF, { ...base, pr: 8, sha: M }, [info({ is_revert: true }), deploy('success'), smoke(1, 1), ...rest], (r) => r.out.status === 'stopped' && r.has('touch ~/factory/STOP')],
  ['verify unknown', VF, { ...base, pr: 7, sha: M }, [info(), deploy('success'), smoke(2, 0), ...rest], (r) => r.out.status === 'stopped' && r.out.smoke === 'unknown' && !r.has(/verified app 7/)],
  ['verify unreadable PR keeps pending', VF, { ...base, pr: 7, sha: M }, [[/two derived fields/, null]], (r) => r.out.status === 'stopped' && r.prompts.length === 1],
  ['verify marketing reverts', VF, { ...base, repo: 'marketing', slug: 'rubinder/wiz_scheduler_marketing', repo_root: '/Users/x/factory/m', app_root: '/Users/x/factory/wiz_scheduler', pr: 7, sha: M }, [info({ issue: '4' }), deploy('success'), smoke(1, 1), ...rest], (r) => r.out.status === 'reverted' && r.has('"Deploy"') && r.has('cd /Users/x/factory/wiz_scheduler && /Users/x/factory/wiz_scheduler/backend/.venv/bin/python -m factory.smoke --repo marketing') && r.has('/Users/x/factory/m/.git/wizfactory-tmp/marketing-pr-7') && r.has(`gate-pass marketing 9 ${V}`)],
]
const throwsCases = [
  [MF, { ...A, head_sha: 'abc' }], [MF, { ...A, pr: -1 }], [MF, { ...A, slug: 'a/b; rm' }], [MF, { ...A, repo: 'x' }],
  [MF, { ...A, issue: 'x' }], [MF, { ...A, branch: '' }], [MF, { ...A, branch: 'feat/x' }],
  [MF, { ...A, branch: 'agent/x; rm' }], [MF, { ...A, branch: 'agent/../x' }], [MF, { ...A, py: 'python3' }], [MF, { ...A, owner_login: '' }], [MF, { ...A, owner_login: 'a b' }],
  [VF, { ...base, pr: 7, sha: 'zz' }], [VF, { ...base, repo: 'marketing', pr: 7, sha: M }],
]
;(async () => {
  let fails = 0
  for (const [name, f, a, s, pred] of cases) {
    try { const r = await run(f, a, s); if (!pred(r)) { fails++; console.log('FAIL', name, JSON.stringify(r.out)) } else console.log('ok  ', name) } catch (e) { fails++; console.log('ERR ', name, e.message) }
  }
  for (const [f, a] of throwsCases) {
    try { await run(f, a, []); fails++; console.log('FAIL no throw', JSON.stringify(a)) } catch (e) { console.log('ok   throws:', e.message) }
  }
  console.log(fails ? `${fails} failing` : `all ${cases.length + throwsCases.length} passing`)
  process.exit(fails ? 1 : 0)
})()
