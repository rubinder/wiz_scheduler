+++
name = "correctness"
always = true
+++

## Reads

The full diff (`origin/main..HEAD`), the spec and plan for the issue, and the surrounding code of every changed file. Lens: correctness (wrong behaviour, unhandled error paths, tenancy leaks (queries missing company_id), race conditions, migrations without downgrade). Report only findings in this lens.

## Checks

- Behaviour that is wrong for any input the spec names or the code accepts.
- Error paths that raise unhandled or are silently swallowed.
- Tenancy leaks: any query missing `company_id` scoping.
- Race conditions around check-then-write.
- Migrations without a downgrade.

Cite the architecture pattern id (aggregate-root, single-evaluator, anti-corruption-layer, thin-router, archive-never-delete, location-local-time, fail-closed, utc-today) when a finding maps to one.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

No critical or important finding survives the refute pass.
