"""Add indexes on company_id columns for RLS query performance.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-13 00:00:00.000000

RLS policies filter by company_id (e.g., `WHERE company_id = ANY(...)`).
Without an index whose leading column is company_id, every RLS-filtered query
on that table becomes a full table scan.

Most tenant tables already have one (models declare `index=True`, or a
composite index starts with company_id), so the targets are read from the
catalog at migration time rather than listed by hand: an index is created
only where none leads with company_id. As of 0044 that is user_consents.

Indexes this migration creates are named ix_rls_<table>_company_id, so
downgrade drops exactly those and never an index a model declares.

Background: Issue #125 Part 2 (Postgres RLS).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a company_id index to each tenant table that has none leading on it."""
    tables = op.get_bind().execute(sa.text("""
        SELECT c.table_name
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = 'public'
          AND c.column_name = 'company_id'
          AND t.table_type = 'BASE TABLE'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_index i
              JOIN pg_attribute a
                ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]
              WHERE i.indrelid = format('public.%I', c.table_name)::regclass
                AND a.attname = 'company_id'
          )
        ORDER BY c.table_name
    """)).scalars().all()

    for table in tables:
        op.create_index(f"ix_rls_{table}_company_id", table, ["company_id"])


def downgrade() -> None:
    """Remove the company_id indexes this migration created."""
    indexes = op.get_bind().execute(sa.text("""
        SELECT indexname, tablename
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname LIKE 'ix\\_rls\\_%\\_company\\_id'
    """)).all()

    for index_name, table in indexes:
        op.drop_index(index_name, table_name=table)
