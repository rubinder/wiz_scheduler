+++
name = "accessibility"
[triggers]
app = ["frontend/src/{pages,components}/**", "frontend/src/theme*", "**/*.css"]
marketing = ["src/{views,components,pages,layouts}/**", "src/styles/**", "src/content/guides/**"]
[mechanical]
app = ['cd {worktree} && bash factory/stack.sh up && { . "${FACTORY_HOME:-$HOME/factory}/stack.env"; (cd frontend && VERIFY_BASE=http://localhost:5173 node scripts/verify/axe.mjs {urls}); s=$?; bash factory/stack.sh down; exit $s; }', "cd {worktree}/frontend && node scripts/verify/contrast.mjs"]
marketing = ['cd {worktree} && npm run build && { pf="$(git -C {worktree} rev-parse --absolute-git-dir)/wizfactory-preview.pid"; (npx --no-install astro preview --port 4321 >/dev/null 2>&1 & echo $! > "$pf"); sleep 5; cd {worktree} && VERIFY_BASE=http://localhost:4321 node {app}/frontend/scripts/verify/axe.mjs {urls}; s=$?; [ -f "$pf" ] && { pkill -P $(cat "$pf"); kill $(cat "$pf"); rm -f "$pf"; }; exit $s; }']
+++

## Reads

The diff, the affected routes, and the rendered pages the axe run covers.

## Checks

- WCAG 2.2 AA.
- Keyboard paths, focus order and visible focus.
- Labels and accessible names.
- RTL layout in ar and ur.
- Contrast of any new colour pair.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

The axe and contrast commands exit 0 and no blocking finding survives the refute pass.
