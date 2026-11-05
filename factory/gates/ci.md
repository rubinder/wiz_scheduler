+++
name = "ci"
always = true
reviewer = false
[mechanical]
app = ["cd {worktree} && {py} -m pytest tests/ -q", "cd {worktree} && {py} -m pytest factory/tests -q", "cd {worktree}/frontend && npm run build && npm test"]
marketing = ["cd {worktree} && npm run check && npm test && npm run build && npm run check:dist"]
+++

## Reads

Mechanical only; no reviewer runs for this gate. The backend suite needs Docker.

## Checks

- App: backend suite, factory suite, frontend build and frontend tests.
- Marketing: `astro check`, vitest, build, `check:dist`.

## Pass

Every command exits 0.
