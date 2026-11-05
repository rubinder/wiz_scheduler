+++
name = "conventions"
always = true
+++

## Reads

The full diff (`origin/main..HEAD`), the spec and plan for the issue, and the surrounding code of every changed file. Lens: conventions (the CLAUDE.md rules). Report only findings in this lens.

## Checks

- No hardcoded role names outside `seed.py`.
- `assert_can_add` on any new `Employee` or `Location` creation path.
- `email_verified_at` set (or a verification mail sent) on any new `User` path.
- "Today" is UTC: `datetime.now(timezone.utc).date()`, never `date.today()`.
- Timestamps are timezone-aware, derived from `location.timezone`.
- Logical Tailwind direction utilities, not physical ones.
- No new dependencies.
- Type hints on all Python code.

Cite the architecture pattern id (aggregate-root, single-evaluator, anti-corruption-layer, thin-router, archive-never-delete, location-local-time, fail-closed, utc-today) when a finding maps to one.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

No critical or important finding survives the refute pass.
