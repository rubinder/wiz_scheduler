+++
name = "architecture"
always = true
[mechanical]
app = ["cd {worktree} && {py} -m pytest tests/test_architecture.py -q", "cd {worktree}/frontend && npm test -- src/architecture.test.ts", "cd {app} && {py} -m factory.allowlist_growth --worktree {worktree} --base {base}"]
+++

## Reads

`.claude/skills/architecture/SKILL.md` and the `references/<context>.md` file of every bounded context the diff touches. For marketing changes, read `.claude/skills/architecture/SKILL.md` in the marketing worktree instead.

## Checks

- Review the diff against the skill. Every finding names a pattern id: aggregate-root, single-evaluator, anti-corruption-layer, thin-router, archive-never-delete, location-local-time, fail-closed, utc-today.
- New code may not add allowlist entries (`tests/architecture_allowlist.json`, `frontend/src/architecture_allowlist.json`). The mechanical growth check fails when either list has an entry the base version lacks; report any such entry as a blocking finding. Only the owner clears it, by merging the PR.
- Moving a violation to another file is a new violation, not a fix.
- A fixed violation deletes its allowlist entry in the same PR.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

The mechanical checks exit 0 and no finding naming a pattern survives the refute pass.
