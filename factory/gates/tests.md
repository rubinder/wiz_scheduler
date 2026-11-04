+++
name = "tests"
always = true
+++

## Reads

The full diff (`origin/main..HEAD`), the spec and plan for the issue, and the surrounding code of every changed file. Lens: tests (test adequacy). Report only findings in this lens.

## Checks

- Every behaviour the spec names has a test that would fail without the change.
- Tests assert real behaviour, not mocks.
- The frontend build and test run is green.

Cite the architecture pattern id (aggregate-root, single-evaluator, anti-corruption-layer, thin-router, archive-never-delete, location-local-time, fail-closed, utc-today) when a finding maps to one.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

No critical or important finding survives the refute pass.
