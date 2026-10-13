"""Add indexes on company_id columns for RLS query performance.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-13 00:00:00.000000

RLS policies filter by company_id (e.g., `WHERE company_id = ANY(...)`).
Without indexes on company_id, every RLS-filtered query becomes a full table scan.

This migration adds B-tree indexes on company_id for all tenant tables that
don't already have it in a composite index. Tables like time_entries and
employee_check_ins already have company_id in composite indexes and are skipped.

Performance impact:
- Selective queries on single/few companies → O(log n) instead of O(n)
- JOINs via company_id → can use indexed lookups
- Composite indexes still used for their specific queries (unchanged)
- No breaking changes; existing queries benefit automatically

Index naming convention: ix_<table>_company_id

Background: Issue #125 Part 2 (Postgres RLS) uses company_id in WHERE clauses.
Without indexes, RLS filtering would degrade performance significantly for
large tables (shifts, time_entries, etc).

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add company_id indexes for RLS performance."""

    # Tables that need company_id indexes (don't already have one)
    # Format: (table_name, index_name)
    tables_needing_indexes = [
        ("regions", "ix_regions_company_id"),
        ("locations", "ix_locations_company_id"),
        ("shift_templates", "ix_shift_templates_company_id"),
        ("shift_schedules", "ix_shift_schedules_company_id"),
        ("shifts", "ix_shifts_company_id"),
        ("employees", "ix_employees_company_id"),
        ("roles", "ix_roles_company_id"),
        ("ownership_group_employees", "ix_ownership_group_employees_company_id"),
        ("employee_invites", "ix_employee_invites_company_id"),
        ("employee_availability", "ix_employee_availability_company_id"),
        ("affinities", "ix_affinities_company_id"),
        ("scheduling_preferences", "ix_scheduling_preferences_company_id"),
        ("special_hours", "ix_special_hours_company_id"),
        ("failure_logs", "ix_failure_logs_company_id"),
        ("storage_snapshots", "ix_storage_snapshots_company_id"),
        ("invoice_items", "ix_invoice_items_company_id"),
    ]

    for table, index_name in tables_needing_indexes:
        op.create_index(index_name, table, ["company_id"])

    # Note: The following tables already have company_id in composite indexes:
    # - time_entries: ix_time_entries_company_pay_date, etc
    # - employee_check_ins: ix_employee_check_ins_location_date, etc
    # - feedback_submissions: ix_feedback_company_id_created_at
    # - payroll_exports: ix_payroll_exports_company_created
    # These don't get additional single-column indexes to avoid duplication


def downgrade() -> None:
    """Remove company_id indexes."""

    tables_needing_indexes = [
        ("regions", "ix_regions_company_id"),
        ("locations", "ix_locations_company_id"),
        ("shift_templates", "ix_shift_templates_company_id"),
        ("shift_schedules", "ix_shift_schedules_company_id"),
        ("shifts", "ix_shifts_company_id"),
        ("employees", "ix_employees_company_id"),
        ("roles", "ix_roles_company_id"),
        ("ownership_group_employees", "ix_ownership_group_employees_company_id"),
        ("employee_invites", "ix_employee_invites_company_id"),
        ("employee_availability", "ix_employee_availability_company_id"),
        ("affinities", "ix_affinities_company_id"),
        ("scheduling_preferences", "ix_scheduling_preferences_company_id"),
        ("special_hours", "ix_special_hours_company_id"),
        ("failure_logs", "ix_failure_logs_company_id"),
        ("storage_snapshots", "ix_storage_snapshots_company_id"),
        ("invoice_items", "ix_invoice_items_company_id"),
    ]

    for table, index_name in tables_needing_indexes:
        op.drop_index(index_name, table_name=table)
