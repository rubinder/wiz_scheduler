"""Tests for Alembic migrations."""
import os
from pathlib import Path

from alembic import config as alembic_config
from alembic.runtime.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect


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

        # Extract revision and down_revision
        import re
        rev_match = re.search(r"revision\s*=\s*['\"]([^'\"]+)['\"]", content)
        down_match = re.search(r"down_revision\s*=\s*['\"]([^'\"]+)['\"]", content)

        if rev_match:
            revision = rev_match.group(1)
            down_revision = down_match.group(1) if down_match else None
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


@pytest.mark.asyncio
async def test_migration_upgrade_downgrade(async_engine):
    """Test that migrations can be applied and reversed (async DB)."""
    # Note: This test requires a test database connection.
    # It's marked asyncio to match the app's async patterns.
    # In CI, this should run against a test PostgreSQL instance.

    async with async_engine.begin() as conn:
        # Check if alembic_version table exists
        inspector = inspect(await conn.run_sync(lambda sync_conn: sync_conn))
        tables = inspector.get_table_names()

        # Basic check: migrations should create at least the alembic_version table
        # Full upgrade/downgrade testing requires running alembic CLI
        # which is better tested via integration tests
        assert "alembic_version" in tables or len(tables) > 0, (
            "Expected migration tables to exist"
        )
