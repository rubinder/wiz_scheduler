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
