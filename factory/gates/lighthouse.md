+++
name = "lighthouse"
reviewer = false
[triggers]
app = ["frontend/src/pages/Login*", "frontend/src/pages/Register*"]
marketing = ["**"]
[mechanical]
app = ['cd {worktree}/frontend && npm run build && { pf="$(git -C {worktree} rev-parse --absolute-git-dir)/wizfactory-preview.pid"; (npx --no-install vite preview --port 4173 >/dev/null 2>&1 & echo $! > "$pf"); sleep 5; cd {app} && {py} -m factory.lighthouse --repo app --base http://localhost:4173 {urls}; s=$?; [ -f "$pf" ] && { pkill -P $(cat "$pf"); kill $(cat "$pf"); rm -f "$pf"; }; exit $s; }']
marketing = ['cd {worktree} && npm run build && { pf="$(git -C {worktree} rev-parse --absolute-git-dir)/wizfactory-preview.pid"; (npx --no-install astro preview --port 4321 >/dev/null 2>&1 & echo $! > "$pf"); sleep 5; cd {app} && {py} -m factory.lighthouse --repo marketing --base http://localhost:4321 {urls}; s=$?; [ -f "$pf" ] && { pkill -P $(cat "$pf"); kill $(cat "$pf"); rm -f "$pf"; }; exit $s; }']
+++

## Reads

Mechanical only; no reviewer runs for this gate. Baselines live in `factory/baselines/lighthouse.json`.

## Checks

Mobile preset, 3 runs, median. Fails if any category is below the stored baseline minus 2 points. A new page gets a floor of 90 for every category, except Performance, which gets 80.

The gate never edits `factory/baselines/lighthouse.json`. A run that beats a baseline records the proposed raise in `~/factory/baselines-proposed.json` and notifies the owner, who raises baselines by hand.

## Pass

The command exits 0.
