"""Tests for Alembic migrations."""
import os
from pathlib import Path

from alembic import config as alembic_config
from alembic.runtime.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect, create_engine


def get_migrations_directory():
    """Get the migrations directory path."""
    repo_root = Path(__file__).parent.parent
    return repo_root / "backend" / "alembic" / "versions"


def test_migration_chain_integrity():
    """Verify all migration revisions reference valid down_revisions."""
    migrations_dir = get_migrations_directory()
    revisions = {}

    # Load all migration files
    for filepath in sorted(migrations_dir.glob("*.py")):
        if filepath.name.startswith("_"):
            continue

        with open(filepath) as f:
            content = f.read()

        # Extract revision and down_revision (handle both old and new format)
        import re
        # New format: revision: str = "0005" | Old format: revision = '0005'
        rev_match = re.search(r"revision\s*(?::\s*\w+)?\s*=\s*['\"]([^'\"]+)['\"]", content)
        down_match = re.search(r"down_revision\s*(?::\s*[^=]+)?\s*=\s*(?:['\"]([^'\"]+)['\"]|None)", content)

        if rev_match:
            revision = rev_match.group(1)
            down_revision = down_match.group(1) if down_match and down_match.group(1) else None
            revisions[revision] = {
                "file": filepath.name,
                "down_revision": down_revision,
            }

    # Check for duplicates
    revision_ids = [r for r in revisions.keys() if not r.startswith("_")]
    duplicates = [r for r in revision_ids if revision_ids.count(r) > 1]
    assert not duplicates, f"Duplicate revision IDs found: {duplicates}"

    # Check all down_revisions reference valid revisions (except None for first migration)
    for revision, data in revisions.items():
        down_rev = data["down_revision"]
        if down_rev and down_rev != "None":
            assert down_rev in revisions, (
                f"Migration {revision} ({data['file']}) references invalid "
                f"down_revision '{down_rev}'"
            )


def test_no_duplicate_filenames():
    """Verify no two migration files have the same numeric prefix."""
    migrations_dir = get_migrations_directory()
    filenames = [f.name for f in migrations_dir.glob("*.py") if not f.name.startswith("_")]

    # Extract numeric prefix (e.g., "0038" from "0038_add_something.py")
    import re
    prefixes = []
    for filename in filenames:
        match = re.match(r"(\d+[a-z]?)", filename)
        if match:
            prefixes.append(match.group(1))

    duplicates = [p for p in prefixes if prefixes.count(p) > 1]
    assert not duplicates, f"Duplicate migration prefixes: {duplicates}. {filenames}"


def test_foreign_key_column_type_compatibility():
    """Validate that all foreign key columns have compatible types with their target columns.

    This test catches schema mismatches like UUID referencing String(8) by analyzing the
    SQLAlchemy models directly — it doesn't require a database, just Python introspection.

    This approach:
    - Runs fast (no container startup)
    - Works in all environments (dev, CI, prod)
    - Validates before any database operations
    """
    from backend.database import Base

    # Extract all ForeignKey constraints from the models
    mismatches = []

    for mapper in Base.registry.mappers:
        model_class = mapper.class_
        table = mapper.persist_selectable

        # Check each column for foreign key constraints
        for column in table.columns:
            for fk in column.foreign_keys:
                ref_table = fk.column.table
                ref_column = fk.column

                # Get the column types as SQLAlchemy sees them
                source_type = str(column.type).upper()
                target_type = str(ref_column.type).upper()

                # Normalize for comparison (VARCHAR(8) → VARCHAR, STRING → VARCHAR, etc.)
                def normalize_type(type_str):
                    return type_str.split('(')[0].strip()

                source_normalized = normalize_type(source_type)
                target_normalized = normalize_type(target_type)

                # Special case: UUID ≠ STRING is a mismatch we care about
                if source_normalized != target_normalized:
                    mismatches.append(
                        f"{table.name}.{column.name} ({source_type}) → "
                        f"{ref_table.name}.{ref_column.name} ({target_type})"
                    )

    assert not mismatches, (
        f"Foreign key column type mismatches found (these will cause constraint violations):\n"
        + "\n".join(f"  • {m}" for m in mismatches)
    )


# ---------------------------------------------------------------------------
# Running the chain against real Postgres
# ---------------------------------------------------------------------------
# The checks above only read the migration files. 0044 passed all of them and
# still failed the production deploy on a table that did not exist, so this
# runs `alembic upgrade head` the way the ECS entrypoint does — as a table
# owner that, like the RDS master user, is neither SUPERUSER nor BYPASSRLS.

import subprocess
import sys


def _alembic(url: str, *args: str) -> None:
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=repo_root / "backend",
        env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture(scope="module")
def rds_like_postgres():
    """A Postgres 15 (production's major version) whose schema owner has the
    RDS master user's limits: CREATEROLE, but no SUPERUSER and no BYPASSRLS."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:15-alpine", driver=None) as pg:
        pg.exec([
            "psql", "-U", pg.username, "-d", pg.dbname, "-c",
            "CREATE ROLE owner LOGIN PASSWORD 'owner' CREATEROLE NOSUPERUSER NOBYPASSRLS;"
            f" ALTER DATABASE {pg.dbname} OWNER TO owner;"
            " ALTER SCHEMA public OWNER TO owner;",
        ])
        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        yield (
            f"postgresql+asyncpg://owner:owner@{host}:{port}/{pg.dbname}",
            pg,
        )


def _psql(pg, sql: str) -> str:
    result = pg.exec(["psql", "-U", "owner", "-d", pg.dbname, "-At", "-c", sql])
    assert result.exit_code == 0, result.output.decode()
    return result.output.decode().strip()


def test_upgrade_head_downgrade_and_upgrade_again(rds_like_postgres):
    url, pg = rds_like_postgres
    _alembic(url, "upgrade", "head")
    _alembic(url, "downgrade", "0042")
    _alembic(url, "upgrade", "head")


def test_every_tenant_table_has_rls_enabled_but_not_forced(rds_like_postgres):
    """ENABLE, never FORCE: the app connects as the table owner, and FORCE
    with no owner policy makes every tenant table read as empty."""
    url, pg = rds_like_postgres
    _alembic(url, "upgrade", "head")

    rows = _psql(pg, """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname)
        FROM pg_class c
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
          AND EXISTS (SELECT 1 FROM information_schema.columns ic
                      WHERE ic.table_schema = 'public' AND ic.table_name = c.relname
                        AND ic.column_name = 'company_id')
        ORDER BY 1
    """).splitlines()

    assert rows, "expected tenant tables"
    problems = [r for r in rows if r.split("|")[1:] != ["t", "f", "3"]]
    assert not problems, (
        "each company_id table needs RLS enabled, not forced, with the "
        "app/analytics/admin policies (table|rls|force|policies):\n"
        + "\n".join(problems)
    )


def test_the_table_owner_still_sees_and_writes_rows(rds_like_postgres):
    url, pg = rds_like_postgres
    _alembic(url, "upgrade", "head")
    _psql(pg, """
        INSERT INTO ownership_groups (id, name) VALUES ('ogmig001', 'G')
            ON CONFLICT DO NOTHING;
        INSERT INTO companies (id, name, slug, ownership_group_id)
            VALUES ('comig001', 'C', 'c-mig-1', 'ogmig001') ON CONFLICT DO NOTHING;
        INSERT INTO regions (id, company_id, name) VALUES ('rgmig001', 'comig001', 'R')
            ON CONFLICT DO NOTHING;
    """)
    assert _psql(pg, "SELECT count(*) FROM regions WHERE id = 'rgmig001'") == "1"


def _alembic_fails(url: str, *args: str) -> str:
    """Run alembic expecting failure; return its combined output."""
    repo_root = Path(__file__).parent.parent
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=repo_root / "backend",
        env={**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, f"alembic {' '.join(args)} unexpectedly succeeded"
    return result.stdout + result.stderr


def test_0047_downgrade_refuses_while_an_open_shift_exists(rds_like_postgres):
    url, pg = rds_like_postgres
    _alembic(url, "upgrade", "head")
    _psql(pg, (
        "INSERT INTO companies (id, name, slug) VALUES ('mgc00001', 'M', 'mig-open-1');"
        "INSERT INTO regions (id, company_id, name) VALUES ('mgr00001', 'mgc00001', 'R');"
        "INSERT INTO locations (id, company_id, region_id, name, timezone)"
        " VALUES ('mgl00001', 'mgc00001', 'mgr00001', 'L', 'UTC');"
        "INSERT INTO roles (id, company_id, name) VALUES ('mgo00001', 'mgc00001', 'X');"
        "INSERT INTO shift_schedules (id, company_id, location_id, week_start_date, status)"
        " VALUES ('mgs00001', 'mgc00001', 'mgl00001', '2026-08-31', 'approved');"
        "INSERT INTO shifts (id, company_id, shift_schedule_id, location_id, employee_id,"
        " role_id, role_name, date, start_time, end_time)"
        " VALUES ('mgf00001', 'mgc00001', 'mgs00001', 'mgl00001', NULL, 'mgo00001', 'X',"
        " '2026-08-31', '2026-08-31 09:00+00', '2026-08-31 17:00+00');"
    ))
    try:
        out = _alembic_fails(url, "downgrade", "0046")
        assert "open shift(s) exist" in out
    finally:
        _psql(pg, (
            "DELETE FROM shifts WHERE id = 'mgf00001';"
            "DELETE FROM shift_schedules WHERE id = 'mgs00001';"
            "DELETE FROM roles WHERE id = 'mgo00001';"
            "DELETE FROM locations WHERE id = 'mgl00001';"
            "DELETE FROM regions WHERE id = 'mgr00001';"
            "DELETE FROM companies WHERE id = 'mgc00001';"
        ))
    _alembic(url, "downgrade", "0046")
    _alembic(url, "upgrade", "head")
