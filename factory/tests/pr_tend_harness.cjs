// Drives pr-tend.js with a scripted agent().
// Usage: node pr_tend_harness.cjs <pr-tend.js>
// Prints one line per scenario and exits non-zero if any scenario fails.
const fs = require('fs')
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor
const H = '0123456789abcdef0123456789abcdef01234567'
const V = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const APP = '/Users/x/factory/wiz_scheduler'
const MK = '/Users/x/factory/wiz_scheduler_marketing'
const SLUG = 'rubinder/wiz_scheduler_marketing'
const SECRET = 'SQL injection via raw company filter in employees router'
const base = { pr: 7, mode: 'ci', repeat_failure: false, repo: 'marketing', repo_root: MK, app_root: APP, slug: SLUG, owner_login: 'rubinder', now: '2026-11-04T09:00:00Z', attribution: '' }

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
  const find = (re) => prompts.filter((p) => re.test(p))
  return { out, prompts, find, has: (s) => prompts.some((p) => (s instanceof RegExp ? s.test(p) : p.includes(s))) }
}
const ok = { status: 'DONE', message: '' }
const meta = [/read the identifying fields/, { status: 'DONE', pr_url: 'https://x/pull/7', branch: 'agent/issue-3-x', issue_text: '3', message: '' }]
const read = (o) => [/prepare the worktree and read/, { status: 'DONE', failures: [{ check: 'build', summary: 'red' }], threads: [], message: '', ...o }]
const fix = (o) => [/^Step: fix PR/, { status: 'DONE', base_sha: V, head_sha: H, commits: 'fix: x', tests: 'ok', threads: [], message: '', ...o }]
const review = (o) => [/^Review the new commits/, { approved: true, findings: [], summary: '', ...o }]
const tail = [[/record the block details for the owner/, ok], [/Step: block PR/, ok], [/push and reply/, ok], [/address these critical review findings/, { status: 'BLOCKED', base_sha: H, head_sha: H, commits: '', tests: '', threads: [], message: SECRET }]]
const blockComment = (r) => {
  const p = r.find(/Step: block PR/)[0] || ''
  return p.slice(p.indexOf('--- comment begins ---'), p.indexOf('--- comment ends ---'))
}
// Every gh command a prompt gives must name the repository explicitly.
const ghCommands = (r) => r.prompts.flatMap((p) => [...p.matchAll(/`(gh [^`]*)`/g)].map((m) => m[1]))
const allScoped = (r) => {
  const cmds = ghCommands(r)
  return cmds.length > 0 && cmds.every((c) => c.includes(`--repo ${SLUG}`) || c.includes(`repos/${SLUG}/`)
    || (c.startsWith('gh api graphql') && c.includes("owner='rubinder'") && c.includes("repo='wiz_scheduler_marketing'")))
    && !r.has('{owner}') && !r.has('{repo}')
}
const privately = (r) => r.out.status === 'blocked' && !blockComment(r).includes(SECRET) && blockComment(r).includes('not posted here')
  && r.find(/record the block details for the owner/).some((p) => p.includes(SECRET) && p.includes(`${APP}/.git/wizfactory-tmp/marketing-pr-7-blocked.md`)
    && p.includes('-m factory.notify') && p.includes('--message "$(cat '))
const F = process.argv[2]
const cases = [
  ['ci fix: every gh call names the slug', base, [meta, read(), fix(), review(), ...tail], (r) => r.out.status === 'pushed' && allScoped(r) && r.has(`gh pr comment 7 --repo ${SLUG}`)],
  ['review fix: graphql and replies use the slug', { ...base, mode: 'review' }, [meta, read({ failures: [], threads: [{ comment_id: 5, path: 'a.md', line: 1, body: 'x' }] }), fix({ threads: [{ comment_id: 5, action: 'changed', reply: 'done' }] }), review(), ...tail], (r) => r.out.status === 'pushed' && allScoped(r) && r.has(`gh api repos/${SLUG}/pulls/7/comments/<comment_id>/replies`)],
  ['repeat failure blocks on the slug', { ...base, repeat_failure: true }, [...tail], (r) => r.out.status === 'blocked' && allScoped(r) && r.has(`gh pr edit 7 --repo ${SLUG} --add-label agent-blocked`)],
  ['read failure message stays private', base, [[/read the identifying fields/, { status: 'BLOCKED', pr_url: '', branch: '', issue_text: '', message: SECRET }], ...tail], privately],
  ['worktree read failure stays private', base, [meta, read({ status: 'BLOCKED', message: SECRET }), ...tail], privately],
  ['fix failure message stays private', base, [meta, read(), fix({ status: 'BLOCKED', message: SECRET }), ...tail], privately],
  ['failed verification findings stay private', base, [meta, read(), fix(), review({ approved: false, findings: [{ severity: 'critical', file: 'backend/routers/employees.py', line: 9, what: SECRET, why: 'exploit', fix: 'x' }] }), ...tail], (r) => privately(r) && !blockComment(r).includes('employees.py') && !blockComment(r).includes('exploit')],
  ['push failure message stays private', base, [meta, read(), fix(), review(), [/push and reply/, { status: 'BLOCKED', message: SECRET }], ...tail], privately],
]
const throwsCases = [
  { ...base, slug: '' }, { ...base, slug: 'a/b; rm -rf ~' }, { ...base, slug: 'a' }, { ...base, slug: '../x' },
  { ...base, repo_root: 'relative' }, { ...base, app_root: '/x/../y' }, { ...base, pr: -1 }, { ...base, pr: 'x' },
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
