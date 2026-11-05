+++
name = "migration-safety"
[triggers]
app = ["backend/alembic/**"]
[mechanical]
app = ["cd {worktree} && {py} -m pytest tests/test_migrations.py tests/test_schema_7shifts_compat.py tests/test_schema_deputy_compat.py -q", "cd {worktree} && bash factory/stack.sh migrate-roundtrip"]
+++

## Reads

The new migration files, the models they change, and the import compatibility tests.

## Checks

- Data loss on upgrade or downgrade.
- Table locks on large tables.
- A downgrade is present and correct.
- 7shifts and Deputy import compatibility is preserved.

## Confidentiality

The repository is public. Security finding details never go into GitHub comments, issues or PRs. If you notice a security issue outside your lens, the gate result carries only "security finding — see owner notification", and the owner is notified locally.

## Pass

The mechanical commands exit 0 and no blocking finding survives the refute pass.
