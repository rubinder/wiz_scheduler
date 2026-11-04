# Retros

One file per factory miss, `<date>-<repo>-<n>.md`, written by the Retro action
(`.claude/workflows/factory-retro.js`). TOML front matter carries `miss`
(`<repo>#<n>`), `kind` (`revert`, `blocked` or `label`), `class` (the
kebab-case error class) and `gate` (the gate that should have caught it, or
`none`); the sections are What slipped, Why the gate missed it, and Proposed
change. The weekly digest counts misses by class so recurring classes stand
out.

This repo is public. A security miss gets no file here and no PR: its full
retro and a patch of the proposed factory change stay in the app clone's
private `.git/wizfactory-tmp/`, and the owner reviews and applies the patch.

A retro's proposed change ships in the same PR, touching only `factory/` or
`.claude/`. Those paths are protected, so the owner merges every retro PR; the
factory never does.
