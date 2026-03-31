"""replace uuid with 8char alphanumeric ids

Revision ID: bfbb0671ec23
Revises: 0010
Create Date: 2026-03-30 18:54:21.713585

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bfbb0671ec23'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every (table, column) pair that needs UUID -> String(8) conversion.
# Ordered so that tables with foreign keys come after the tables they reference.
UUID_COLUMNS = [
    # Parent tables first
    ("ownership_groups", "id"),
    ("companies", "id"),
    ("companies", "ownership_group_id"),
    ("users", "id"),
    ("users", "company_id"),
    ("regions", "id"),
    ("regions", "company_id"),
    ("locations", "id"),
    ("locations", "company_id"),
    ("locations", "region_id"),
    ("departments", "id"),
    ("departments", "company_id"),
    ("departments", "location_id"),
    ("roles", "id"),
    ("roles", "company_id"),
    ("employees", "id"),
    ("employees", "company_id"),
    ("employees", "user_id"),
    ("employee_roles", "id"),
    ("employee_roles", "company_id"),
    ("employee_roles", "employee_id"),
    ("employee_roles", "role_id"),
    ("employee_affinities", "id"),
    ("employee_affinities", "company_id"),
    ("employee_affinities", "employee_id"),
    ("employee_affinities", "target_employee_id"),
    ("employee_availability", "id"),
    ("employee_availability", "company_id"),
    ("employee_availability", "employee_id"),
    ("employee_companies", "id"),
    ("employee_companies", "employee_id"),
    ("employee_companies", "company_id"),
    ("employee_invites", "id"),
    ("employee_invites", "company_id"),
    ("employee_invites", "employee_id"),
    ("shift_templates", "id"),
    ("shift_templates", "company_id"),
    ("shift_templates", "location_id"),
    ("shift_schedules", "id"),
    ("shift_schedules", "company_id"),
    ("shift_schedules", "location_id"),
    ("shifts", "id"),
    ("shifts", "company_id"),
    ("shifts", "shift_schedule_id"),
    ("shifts", "location_id"),
    ("shifts", "employee_id"),
    ("shifts", "role_id"),
    ("condensed_roles", "id"),
    ("condensed_roles", "company_id"),
    ("condensed_role_mappings", "id"),
    ("condensed_role_mappings", "condensed_role_id"),
    ("condensed_role_mappings", "role_id"),
    ("token_usage", "id"),
    ("token_usage", "ownership_group_id"),
    ("failure_logs", "id"),
    ("failure_logs", "company_id"),
]


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Drop ALL foreign key constraints so we can freely alter column types
    fk_rows = conn.execute(sa.text("""
        SELECT tc.table_name, tc.constraint_name
        FROM information_schema.table_constraints tc
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
        ORDER BY tc.table_name
    """)).fetchall()

    for table_name, constraint_name in fk_rows:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    # 2. Convert all UUID columns to String(8), taking first 8 hex chars
    for table, col in UUID_COLUMNS:
        # Remove server_default before altering (gen_random_uuid() is invalid for varchar)
        conn.execute(sa.text(
            f'ALTER TABLE "{table}" ALTER COLUMN "{col}" DROP DEFAULT'
        ))
        conn.execute(sa.text(
            f'ALTER TABLE "{table}" ALTER COLUMN "{col}" TYPE VARCHAR(8) '
            f"USING substr(replace(\"{col}\"::text, '-', ''), 1, 8)"
        ))

    # 3. Convert employees.location_ids from UUID[] to JSON (array of short strings)
    #    Can't use subquery in USING, so do it in two steps:
    #    a) Add a temp JSON column, populate it, drop old, rename
    conn.execute(sa.text(
        'ALTER TABLE employees ADD COLUMN location_ids_new JSON'
    ))
    conn.execute(sa.text("""
        UPDATE employees SET location_ids_new = (
            SELECT json_agg(substr(replace(elem::text, '-', ''), 1, 8))
            FROM unnest(location_ids) AS elem
        )
        WHERE location_ids IS NOT NULL
    """))
    conn.execute(sa.text('ALTER TABLE employees DROP COLUMN location_ids'))
    conn.execute(sa.text(
        'ALTER TABLE employees RENAME COLUMN location_ids_new TO location_ids'
    ))

    # 4. Re-create foreign key constraints
    fk_defs = conn.execute(sa.text("""
        SELECT
            kcu.table_name,
            kcu.column_name,
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name,
            tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage AS ccu
            ON ccu.constraint_name = tc.constraint_name
            AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
    """)).fetchall()

    # FKs were already dropped, so we need to reconstruct them from the model definitions.
    # Define them explicitly:
    FOREIGN_KEYS = [
        ("companies", "ownership_group_id", "ownership_groups", "id"),
        ("users", "company_id", "companies", "id"),
        ("regions", "company_id", "companies", "id"),
        ("locations", "company_id", "companies", "id"),
        ("locations", "region_id", "regions", "id"),
        ("departments", "company_id", "companies", "id"),
        ("departments", "location_id", "locations", "id"),
        ("roles", "company_id", "companies", "id"),
        ("employees", "company_id", "companies", "id"),
        ("employees", "user_id", "users", "id"),
        ("employee_roles", "company_id", "companies", "id"),
        ("employee_roles", "employee_id", "employees", "id"),
        ("employee_roles", "role_id", "roles", "id"),
        ("employee_affinities", "company_id", "companies", "id"),
        ("employee_affinities", "employee_id", "employees", "id"),
        ("employee_affinities", "target_employee_id", "employees", "id"),
        ("employee_availability", "company_id", "companies", "id"),
        ("employee_availability", "employee_id", "employees", "id"),
        ("employee_companies", "employee_id", "employees", "id"),
        ("employee_companies", "company_id", "companies", "id"),
        ("employee_invites", "company_id", "companies", "id"),
        ("employee_invites", "employee_id", "employees", "id"),
        ("shift_templates", "company_id", "companies", "id"),
        ("shift_templates", "location_id", "locations", "id"),
        ("shift_schedules", "company_id", "companies", "id"),
        ("shift_schedules", "location_id", "locations", "id"),
        ("shifts", "company_id", "companies", "id"),
        ("shifts", "shift_schedule_id", "shift_schedules", "id"),
        ("shifts", "location_id", "locations", "id"),
        ("shifts", "employee_id", "employees", "id"),
        ("shifts", "role_id", "roles", "id"),
        ("condensed_roles", "company_id", "companies", "id"),
        ("condensed_role_mappings", "condensed_role_id", "condensed_roles", "id"),
        ("condensed_role_mappings", "role_id", "roles", "id"),
        ("token_usage", "ownership_group_id", "ownership_groups", "id"),
        ("failure_logs", "company_id", "companies", "id"),
    ]

    for table, col, ref_table, ref_col in FOREIGN_KEYS:
        fk_name = f"fk_{table}_{col}"
        op.create_foreign_key(fk_name, table, ref_table, [col], [ref_col])


def downgrade() -> None:
    # This is a destructive migration — downgrade is not supported
    # because 8-char IDs cannot be converted back to valid UUIDs.
    raise NotImplementedError(
        "Cannot downgrade from 8-char alphanumeric IDs back to UUIDs. "
        "Restore from a database backup instead."
    )
